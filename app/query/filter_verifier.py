import logging
import sqlglot
from sqlglot import exp, parse_one
from typing import List, Dict, Any, Optional
from collections import deque

logger = logging.getLogger(__name__)

DIALECT_MAP = {
    "sqlite":     "sqlite",
    "postgresql": "postgres",
    "mysql":      "mysql",
    "sqlserver":  "tsql",
}

# Tables that should never be checked directly for site_id filter
# but their reverse_foreign_keys (parents) are still traversed
SITE_FILTER_SKIP_TABLES = {"BNR_User_Details", "bnr_abcform"}
FILTER_BYPASS_TABLES = {
    "bnr_course",
    "bnr_questions",
    "bnr_question_group",
    "bnr_question_options",
    "bnr_operatorcourse",
    "bnr_course_result",
    "bnr_question_result",
    # Activity planner
    "bnr_activityplanner",
    "bnr_activityplanner_entry",
    "bnr_auditsetting",
}

class SQLFilterVerifier:

    def __init__(self, dialect: str = "sqlite"):
        self.dialect = dialect

    # ──────────────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ──────────────────────────────────────────────────────────────────────
    def verify_and_inject(self, sql, filter_keys, filter_values, table_schemas, column_mappings=None):
        if not filter_keys:
            # We can still run to inject soft delete filters even if filter_keys is empty
            pass

        try:
            expression = parse_one(sql, read=DIALECT_MAP.get(self.dialect.lower(), "postgres"))
        except Exception as e:
            logger.error(f"SQL parsing failed: {e}")
            return sql

        norm_filter_values = {k.lower(): v for k, v in filter_values.items()}
        column_mappings = column_mappings or {}

        # 1. Find all Select nodes in the query (walking the AST)
        select_nodes = list(expression.find_all(exp.Select))
        if not select_nodes:
            return sql

        # Let's find the main/outer select node
        outer_select = expression.find(exp.Select)

        def get_select_ancestor(node):
            curr = node.parent
            while curr is not None:
                if isinstance(curr, exp.Select):
                    return curr
                curr = curr.parent
            return None

        # 2. For each Select node, gather its local tables and inject site filters
        for select_node in select_nodes:
            # Find tables that belong directly to this select node
            local_tables = []
            for table in select_node.find_all(exp.Table):
                if get_select_ancestor(table) == select_node:
                    t_name = table.name.lower()
                    t_alias = table.alias if table.alias else t_name
                    
                    is_left_join = False
                    curr = table.parent
                    while curr is not None and curr != select_node:
                        if isinstance(curr, exp.Join):
                            if curr.side in ("LEFT", "RIGHT"):
                                is_left_join = True
                            break
                        curr = curr.parent
                    
                    local_tables.append({
                        "name": t_name,
                        "alias": t_alias,
                        "expression": table,
                        "is_left_join": is_left_join
                    })

            # If no tables in this select block, skip
            if not local_tables:
                continue

            # Prioritize table ordering: non-left-joined first, then business entity tables before lookup ones
            def get_table_priority(table_info):
                is_left = 1 if table_info["is_left_join"] else 0
                t_name_norm = table_info["name"].lower().replace("_", "").replace(" ", "")
                is_person_lookup = 1 if ("serviceuser" in t_name_norm or "userdetails" in t_name_norm) else 0
                return (is_left, is_person_lookup)

            local_tables.sort(key=get_table_priority)

            # Enforce the filter_keys
            for key in filter_keys:
                k_lower = key.lower()
                val = norm_filter_values.get(k_lower)

                # Check if resolved directly on any table in this Select block
                matched_condition = None
                matched_table_info = None
                for table_info in local_tables:
                    t_name = table_info["name"]
                    t_alias = table_info["alias"]

                    if self._is_site_filter_skip_table(t_name, key):
                        continue

                    actual_col = self._resolve_column(t_name, key, table_schemas, column_mappings)
                    if actual_col:
                        if val is not None and val != "" and val != []:
                            matched_condition = self._build_condition(t_alias, actual_col, val)
                            matched_table_info = table_info
                            break

                if matched_condition:
                    # Inject condition directly on this table
                    cond_expr = parse_one(matched_condition)
                    is_joined = False
                    join_node = None
                    curr = matched_table_info["expression"].parent
                    while curr is not None and curr != select_node:
                        if isinstance(curr, exp.Join):
                            is_joined = True
                            join_node = curr
                            break
                        curr = curr.parent

                    if is_joined and join_node:
                        existing_on = join_node.args.get("on")
                        if existing_on:
                            join_node.set("on", exp.and_(existing_on, cond_expr))
                        else:
                            join_node.set("on", cond_expr)
                    else:
                        select_node.where(cond_expr, copy=False)
                    continue

                # If not matched directly, try BFS inside this Select block
                logger.info(
                    f"Filter key '{key}' not found directly in select block tables. "
                    f"Starting BFS from select block tables: {[t['name'] for t in local_tables]}."
                )
                bfs_result = None
                existing_tables_alias_map = {t["name"]: t["alias"] for t in local_tables}
                for table_info in local_tables:
                    bfs_result = self._bfs_find_filter(
                        start_table=table_info["name"],
                        start_alias=table_info["alias"],
                        filter_key=key,
                        filter_val=val,
                        table_schemas=table_schemas,
                        column_mappings=column_mappings,
                        existing_tables_alias_map=existing_tables_alias_map
                    )
                    if bfs_result:
                        break

                if bfs_result:
                    condition, new_joins = bfs_result
                    # Inject new joins into this select_node
                    for join_table, join_alias, on_clause in new_joins:
                        join_node = exp.Join(
                            this=exp.Table(
                                this=exp.Identifier(this=join_table, quoted=False),
                                alias=exp.TableAlias(
                                    this=exp.Identifier(this=join_alias, quoted=False)
                                ),
                            ),
                            on=parse_one(on_clause),
                            kind="LEFT",
                        )
                        select_node.append("joins", join_node)
                    
                    # Inject condition into WHERE clause
                    select_node.where(parse_one(condition), copy=False)
                else:
                    # If this is the outer select block, we must raise a PermissionError
                    if select_node is outer_select:
                        logger.warning(
                            f"Security Block: mandatory filter '{key}' could not be resolved on outer block. "
                            f"Tables: {[t['name'] for t in local_tables]}"
                        )
                        raise PermissionError(
                            f"Access denied: could not enforce the mandatory '{key}' filter "
                            f"for this query. Please contact your administrator."
                        )
                    else:
                        logger.info(
                            f"Subquery filter resolution skipped for key '{key}' — table has no path to filter."
                        )

        # 3. Inject soft delete filters for all tables in all select blocks
        for select_node in select_nodes:
            local_tables = []
            for table in select_node.find_all(exp.Table):
                if get_select_ancestor(table) == select_node:
                    local_tables.append(table)

            for table_node in local_tables:
                t_name = table_node.name.lower()
                t_alias = table_node.alias if table_node.alias else t_name

                schema = self._get_schema_payload(t_name, table_schemas)
                if schema is None:
                    continue

                if isinstance(schema, dict):
                    col_list = [
                        c["name"] if isinstance(c, dict) else c
                        for c in schema.get("columns", [])
                    ]
                elif isinstance(schema, list):
                    col_list = schema
                else:
                    continue

                is_deleted_col = next(
                    (c for c in col_list if c.lower() == "isdeleted"),
                    None
                )

                if is_deleted_col:
                    condition_str = f"{t_alias}.{is_deleted_col} = 0"
                    try:
                        cond_expr = parse_one(condition_str)
                        is_joined = False
                        join_node = None
                        curr = table_node.parent
                        while curr is not None and curr != select_node:
                            if isinstance(curr, exp.Join):
                                is_joined = True
                                join_node = curr
                                break
                            curr = curr.parent

                        if is_joined and join_node:
                            existing_on = join_node.args.get("on")
                            if existing_on:
                                join_node.set("on", exp.and_(existing_on, cond_expr))
                            else:
                                join_node.set("on", cond_expr)
                            logger.info(
                                f"[soft-delete] Injected '{condition_str}' to JOIN ON clause of '{t_name}'"
                            )
                        else:
                            select_node.where(cond_expr, copy=False)
                            logger.info(
                                f"[soft-delete] Injected '{condition_str}' for table '{t_name}' (primary/WHERE)"
                            )
                    except Exception as e:
                        logger.error(
                            f"[soft-delete] Failed to inject for '{t_name}': {e}"
                        )

        result_sql = expression.sql(dialect=DIALECT_MAP.get(self.dialect.lower(), "postgres"))
        result_sql = self._fix_distinct_order_by(result_sql)
        logger.info(f"Final filtered SQL: {result_sql}")
        return result_sql

    def _inject_soft_delete_filters(
        self,
        expression,
        tables_in_query: List[Dict],
        table_schemas: Dict[str, Any],
    ) -> any:
        # Kept for backward compatibility if called from elsewhere, but verify_and_inject handles this natively now
        return expression
    
    def _get_outer_scope_aliases(self, expression) -> set:
        """
        Returns the set of aliases that belong to the OUTER query scope only.
        Tables inside subqueries are excluded.
        """
        outer_aliases = set()

        outer_select = expression.find(exp.Select)
        if outer_select is None:
            return outer_aliases

        for table in expression.find_all(exp.Table):
            alias = table.alias if table.alias else table.name

            # Walk up the parent chain manually
            in_subquery = False
            node = table.parent
            while node is not None:
                if isinstance(node, exp.Subquery):
                    in_subquery = True
                    break
                if isinstance(node, exp.Select) and node is not outer_select:
                    in_subquery = True
                    break
                node = node.parent

            if not in_subquery:
                outer_aliases.add(alias.lower() if isinstance(alias, str) else alias)

        logger.debug(f"[soft-delete] Outer scope aliases: {outer_aliases}")
        return outer_aliases

    # ──────────────────────────────────────────────────────────────────────
    # PASS 1 — check tables already present in the query
    # ──────────────────────────────────────────────────────────────────────
    def _check_tables_in_query(
        self,
        key: str,
        val: Any,
        tables_in_query: List[Dict],
        table_schemas: Dict[str, Any],
        column_mappings: Dict[str, Dict[str, str]],
    ) -> Optional[str]:
        for table_info in tables_in_query:
            t_name  = table_info["name"]
            t_alias = table_info["alias"]

            # Skip direct site filter check on skip tables
            if self._is_site_filter_skip_table(t_name, key):
                logger.info(
                    f"Skipping direct '{key}' check on table '{t_name}' "
                    f"(SITE_FILTER_SKIP_TABLES). Will still BFS its parents."
                )
                continue

            actual_col = self._resolve_column(t_name, key, table_schemas, column_mappings)
            if actual_col:
                if val is not None and val != "" and val != []:
                    condition = self._build_condition(t_alias, actual_col, val)
                    logger.info(f"Filter '{key}' resolved directly on table '{t_name}' as '{actual_col}'")
                    return condition
                else:
                    logger.warning(
                        f"Mandatory key '{key}' found in table '{t_name}' "
                        f"but NO VALUE provided in session!"
                    )
        return None

    # ──────────────────────────────────────────────────────────────────────
    # PASS 2 — BFS through reverse_foreign_keys from every in-query table
    # ──────────────────────────────────────────────────────────────────────
    def _bfs_find_filter(
        self,
        start_table: str,
        start_alias: str,
        filter_key: str,
        filter_val: Any,
        table_schemas: Dict[str, Any],
        column_mappings: Dict[str, Dict[str, str]],
        existing_tables_alias_map: Dict[str, str],  # name → alias for all in-query tables
        max_depth: int = 3,
    ) -> Optional[tuple]:
        """
        BFS upward through reverse_foreign_keys.

        Key behaviors:
          - If a reverse FK parent is already in the query (existing_tables_alias_map):
              → check it for the filter column using its EXISTING alias
              → do NOT generate a new JOIN
          - If a reverse FK parent is new (not in query):
              → enqueue it with a new alias and a JOIN step
          - SITE_FILTER_SKIP_TABLES: skip direct column check but still
              traverse their reverse_foreign_keys
        """
        visited = set()
        # Each item: (table_name, alias, path_of_new_join_steps)
        queue = deque()
        queue.append((start_table.lower(), start_alias, []))
        alias_counter = [0]

        while queue:
            current_table, current_alias, path = queue.popleft()

            if current_table in visited or len(path) > max_depth:
                continue
            visited.add(current_table)

            schema = self._get_schema_payload(current_table, table_schemas)
            if schema is None:
                logger.debug(f"BFS: no schema found for '{current_table}', skipping.")
                continue

            # Check if this table has the filter column
            # (skip if it's a site-filter-skip table)
            if not self._is_site_filter_skip_table(current_table, filter_key):
                actual_col = self._resolve_column(
                    current_table, filter_key, table_schemas, column_mappings
                )
                # Only inject if we traversed at least one step (path is non-empty)
                # For the start table itself, Pass 1 already checked it
                if actual_col and path:
                    if filter_val is not None and filter_val != "" and filter_val != []:
                        condition = self._build_condition(current_alias, actual_col, filter_val)
                        logger.info(
                            f"BFS found '{filter_key}' on '{current_table}' "
                            f"via path: {[j[0] for j in path]}"
                        )
                        return condition, path
                    else:
                        logger.warning(
                            f"BFS found '{filter_key}' on '{current_table}' "
                            f"but no value provided."
                        )
                        return None
            else:
                logger.info(
                    f"BFS: skipping direct '{filter_key}' check on '{current_table}' "
                    f"(SITE_FILTER_SKIP_TABLES). Still traversing its reverse_foreign_keys."
                )

            # Walk reverse FKs regardless of skip status
            rfks = schema.get("reverse_foreign_keys", []) if isinstance(schema, dict) else []
            logger.info(
                f"BFS at '{current_table}': checking {len(rfks)} "
                f"reverse FKs for '{filter_key}'"
            )

            for rfk in rfks:
                ref_table = rfk.get("referencing_table", "").lower()
                ref_col   = rfk.get("referencing_col", "")
                local_col = rfk.get("local_col", "")

                if not ref_table or ref_table in visited:
                    continue

                # ── Case 1: ref_table already exists in the query ─────────
                # Use its existing alias, check for filter col, NO new JOIN
                if ref_table in existing_tables_alias_map:
                    existing_alias = existing_tables_alias_map[ref_table]
                    logger.info(
                        f"BFS: '{ref_table}' already in query as '{existing_alias}', "
                        f"checking directly without new JOIN."
                    )

                    # Skip site filter check if needed
                    if not self._is_site_filter_skip_table(ref_table, filter_key):
                        actual_col = self._resolve_column(
                            ref_table, filter_key, table_schemas, column_mappings
                        )
                        if actual_col:
                            if filter_val is not None and filter_val != "" and filter_val != []:
                                condition = self._build_condition(
                                    existing_alias, actual_col, filter_val
                                )
                                logger.info(
                                    f"BFS found '{filter_key}' on already-joined "
                                    f"table '{ref_table}' as '{existing_alias}'"
                                )
                                # path stays as-is (no new join needed)
                                return condition, path
                    else:
                        logger.info(
                            f"BFS: skipping direct '{filter_key}' check on "
                            f"already-joined '{ref_table}' (SITE_FILTER_SKIP_TABLES). "
                            f"Still traversing its reverse_foreign_keys."
                        )
                        # Even though it's already in query, still enqueue
                        # so its OWN reverse_foreign_keys get traversed
                        queue.append((ref_table, existing_alias, path))

                    visited.add(ref_table)
                    continue

                # ── Case 2: ref_table is new → enqueue with a new JOIN ────
                alias_counter[0] += 1
                new_alias = f"t_anc{alias_counter[0]}"
                on_clause = f"{current_alias}.{local_col} = {new_alias}.{ref_col}"
                join_step = (ref_table, new_alias, on_clause)
                logger.info(
                    f"BFS: enqueueing new table '{ref_table}' as '{new_alias}' "
                    f"with JOIN ON {on_clause}"
                )
                queue.append((ref_table, new_alias, path + [join_step]))

        logger.info(
            f"BFS: could not find '{filter_key}' within depth "
            f"{max_depth} from '{start_table}'."
        )
        return None

    # ──────────────────────────────────────────────────────────────────────
    # INJECT JOINs INTO THE PARSED SQL EXPRESSION
    # ──────────────────────────────────────────────────────────────────────
    def _inject_joins(self, expression, joins: List[tuple]):
        for join_table, join_alias, on_clause in joins:
            try:
                join_node = exp.Join(
                    this=exp.Table(
                        this=exp.Identifier(this=join_table, quoted=False),
                        alias=exp.TableAlias(
                            this=exp.Identifier(this=join_alias, quoted=False)
                        ),
                    ),
                    on=parse_one(on_clause),
                    kind="LEFT",
                )
                expression = expression.copy()
                expression.append("joins", join_node)
                logger.info(f"Injected JOIN: {join_table} {join_alias} ON {on_clause}")
            except Exception as e:
                logger.error(f"Failed to inject JOIN for '{join_table}': {e}", exc_info=True)
        return expression

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────
    def _fix_distinct_order_by(self, sql: str) -> str:
        """Fix SQL Server error 145: ORDER BY columns must appear in SELECT with DISTINCT.

        If SELECT DISTINCT is present and ORDER BY references columns not in
        the SELECT list, those columns are appended to the SELECT list.
        """
        try:
            tree = parse_one(sql, read=DIALECT_MAP.get(self.dialect.lower(), "tsql"))
        except Exception:
            return sql

        select_node = tree.find(exp.Select)
        if select_node is None or not select_node.args.get("distinct"):
            return sql

        order = tree.find(exp.Order)
        if order is None:
            return sql

        # If SELECT * is present, all columns are already included — skip
        if any(isinstance(sel_expr, exp.Star) for sel_expr in select_node.expressions):
            return sql

        # Collect existing SELECT column expressions as normalised SQL strings
        dialect_key = DIALECT_MAP.get(self.dialect.lower(), "tsql")
        select_col_sqls = set()
        for sel_expr in select_node.expressions:
            core = sel_expr.this if isinstance(sel_expr, exp.Alias) else sel_expr
            select_col_sqls.add(core.sql(dialect=dialect_key).lower().strip())

        # Find ORDER BY columns missing from SELECT
        missing = []
        for ordered in order.find_all(exp.Ordered):
            order_expr = ordered.this
            order_sql = order_expr.sql(dialect=dialect_key).lower().strip()
            if order_sql not in select_col_sqls:
                missing.append(order_expr.copy())

        if not missing:
            return sql

        for col_expr in missing:
            select_node.append("expressions", col_expr)
            logger.info(
                f"[fix_distinct_order_by] Added '{col_expr.sql(dialect=dialect_key)}' "
                f"to SELECT for DISTINCT compatibility"
            )

        return tree.sql(dialect=dialect_key)

    def _is_site_filter_skip_table(self, table_name: str, filter_key: str) -> bool:
        """
        Returns True only when:
          - filter_key normalizes to 'siteid' (covers site_id, SiteId, SITEID)
          - AND table_name is in SITE_FILTER_SKIP_TABLES
        Other filter keys (company_id etc.) are never skipped.
        """
        normalized_key = filter_key.replace("_", "").lower()
        if normalized_key != "siteid":
            return False
        return table_name.replace("_", "").replace(" ", "").lower() in {
            t.replace("_", "").replace(" ", "").lower() 
            for t in SITE_FILTER_SKIP_TABLES
        }


    def _get_schema_payload(
        self, table_name: str, table_schemas: Dict[str, Any]
    ) -> Optional[Any]:
        for k, v in table_schemas.items():
            if k.lower() == table_name.lower():
                return v
        return None

    def _resolve_column(
        self,
        table_name: str,
        filter_key: str,
        table_schemas: Dict[str, Any],
        column_mappings: Dict[str, Dict[str, str]],
    ) -> Optional[str]:
        def normalize(s: str) -> str:
            return s.replace("_", "").lower()

        k_lower  = filter_key.lower()
        norm_key = normalize(filter_key)

        # 1. Explicit column_mappings override
        table_map = column_mappings.get(table_name, {})
        norm_map  = {k.lower(): v for k, v in table_map.items()}
        if k_lower in norm_map:
            return norm_map[k_lower]

        # 2. Normalize match against schema columns
        schema = self._get_schema_payload(table_name, table_schemas)
        if schema is None:
            return None

        if isinstance(schema, dict):
            col_list = [
                c["name"] if isinstance(c, dict) else c
                for c in schema.get("columns", [])
            ]
        elif isinstance(schema, list):
            col_list = schema
        else:
            return None

        normalized_columns = {normalize(c): c for c in col_list}
        return normalized_columns.get(norm_key)

    def _build_condition(self, alias: str, col: str, val: Any) -> str:
        """Always generates IN (...) — handles scalar, list, or comma-separated string."""
        if isinstance(val, (list, tuple)):
            values = list(val)
        elif isinstance(val, str):
            parts  = val.split(",")
            values = [p.strip().strip("'").strip('"') for p in parts]
        else:
            values = [val]

        formatted = []
        for v in values:
            if isinstance(v, str):
                escaped = v.replace("'", "''")
                formatted.append(f"'{escaped}'")
            else:
                formatted.append(str(v))

        return f"{alias}.{col} IN ({', '.join(formatted)})"

    def should_bypass_filter(self,sql: str, dialect: str = "sqlserver") -> bool:
        try:
            read_dialect = DIALECT_MAP.get(dialect.lower(), "tsql")  # "sqlserver" → "tsql"
            expression = parse_one(sql, read=read_dialect)
            for table in expression.find_all(exp.Table):
                logger.info(f"Bypass check — table: '{table.name}'")
                if table.name.lower() in FILTER_BYPASS_TABLES:
                    return True
        except Exception as e:
            logger.error(f"SQL parsing failed during bypass check: {e}", exc_info=True)
            return False  # safe fallback — don't bypass on error
        return False
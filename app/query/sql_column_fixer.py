"""
sql_column_fixer.py
────────────────────────────────────────────────────────────────────
Pre-execution SQL column validator & auto-corrector using Gemini.

Fixes two classes of bugs:
  A) Wrong column name on correct alias  →  t2.LastName (wrong) → t2.Surname (right)
  B) Correct column name on wrong alias  →  t3.Email  (wrong)  → t2.Email   (right)

Key improvement: extracts alias→table mapping from the SQL itself and passes
it to Gemini so it knows exactly which table each alias refers to.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.utils.llm_provider import generate_with_fallback

logger = logging.getLogger(__name__)


@dataclass
class ColumnFixResult:
    original_sql: str
    fixed_sql: str
    changed: bool


# ── Extract alias → table map from SQL ───────────────────────────────────────

def _extract_alias_map(sql: str) -> dict[str, str]:
    """
    Parse  FROM/JOIN ... AS alias  or  FROM/JOIN ... alias  patterns.
    Returns { alias_lower: real_table_name }
    e.g. { "t1": "BNR_UserDetailServiceUser", "t2": "BNR_UserDetails", "t3": "BNR_Service_User" }
    """
    alias_map: dict[str, str] = {}

    # Pattern: TableName AS alias  or  TableName alias  (after FROM / JOIN)
    # Handles optional dbo. / schema prefix
    pattern = re.compile(
        r'(?:FROM|JOIN)\s+(?:dbo\.)?(\w+)\s+(?:AS\s+)?(\w+)',
        re.IGNORECASE,
    )
    for m in pattern.finditer(sql):
        table_name = m.group(1)
        alias = m.group(2).lower()
        # Skip SQL keywords that can follow a table name
        if alias.upper() in ("ON", "WHERE", "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "CROSS"):
            alias_map[table_name.lower()] = table_name  # no alias — map table to itself
        else:
            alias_map[alias] = table_name

    return alias_map


# ── Build schema block for tables actually used in the SQL ───────────────────

def _build_schema_block(
    alias_map: dict[str, str],
    available_tables: list,
) -> str:
    """
    Returns a block like:
        t1 (BNR_UserDetailServiceUser): [Id, UserDetailsId, ServiceUserId, IsDeleted]
        t2 (BNR_UserDetails):           [Id, FirstName, LastName, Email, Phone, IsDeleted]
        t3 (BNR_Service_User):          [Id, FirstName, Surname, SiteId, Status, IsDeleted]

    This is the key: the LLM sees alias + table name + exact columns together,
    so it cannot confuse which column belongs to which alias.
    """
    # Build lookup: table_name_upper → column list
    table_col_map: dict[str, list[str]] = {}
    for tbl in available_tables:
        name = getattr(tbl, "table_name", None) or getattr(tbl, "name", "")
        if not name:
            continue
        raw_cols = getattr(tbl, "columns", [])
        cols: list[str] = []
        for c in raw_cols:
            if isinstance(c, str):
                cols.append(c)
            else:
                cols.append(getattr(c, "name", str(c)))
        table_col_map[name.upper()] = cols

    lines: list[str] = []
    for alias, table_name in alias_map.items():
        cols = table_col_map.get(table_name.upper(), [])
        if not cols:
            continue
        col_list = ", ".join(cols)
        lines.append(f"  {alias} ({table_name}): [{col_list}]")

    return "\n".join(lines)


# ── Main fixer ───────────────────────────────────────────────────────────────

async def fix_sql_columns(
    sql: str,
    relevant_tables: list,
    dialect: str = "tsql",
) -> ColumnFixResult:
    """
    Validates every alias.Column reference in the SQL against the real schema.

    Two types of fixes:
      A) Column doesn't exist on alias's table
         → replace with the closest matching column FROM THAT SAME TABLE
      B) Column doesn't exist on alias's table BUT exists on another alias's table
         → change the alias to the correct one  (e.g. t3.Email → t2.Email)

    Everything else in the SQL is left completely untouched.
    """

    alias_map = _extract_alias_map(sql)
    if not alias_map:
        logger.debug("[SQLColumnFixer] Could not extract alias map — skipping fix")
        return ColumnFixResult(original_sql=sql, fixed_sql=sql, changed=False)

    schema_block = _build_schema_block(alias_map, relevant_tables)
    if not schema_block:
        logger.debug("[SQLColumnFixer] No schema found for aliases — skipping fix")
        return ColumnFixResult(original_sql=sql, fixed_sql=sql, changed=False)

    logger.debug(f"[SQLColumnFixer] Alias→schema block:\n{schema_block}")

    prompt = f"""You are a SQL column validator. Your ONLY job is to fix wrong column references in a SQL query.

ALIAS → TABLE → EXACT COLUMNS MAPPING:
{schema_block}

SQL TO FIX:
{sql}

STEP-BY-STEP RULES (follow in order for every alias.Column in the SQL):

STEP 1 — Check if the column exists in the alias's own table (see mapping above).
          If YES → keep it exactly as is. Move to next column.

STEP 2 — Column does NOT exist in the alias's table.
          Check if the same column name exists in ANY OTHER alias's table in the mapping.
          If YES → change ONLY the alias prefix to the correct one.
          Example: t3.Email → t2.Email  (Email exists in t2, not t3)
          Example: t2.Surname → t3.Surname  (Surname exists in t3, not t2)

STEP 3 — Column does NOT exist in any alias's table.
          Replace with the closest matching column from the SAME alias's table.
          Use semantic similarity (e.g. LastName → Surname if they are in the same table).

STEP 4 — No reasonable match found for a SELECT column → remove it from SELECT.
          No reasonable match found for a WHERE/JOIN column → best guess from same table.

ABSOLUTE RULES:
- NEVER change a column that already exists in its alias's table (Step 1 match = keep it).
- NEVER swap column names between tables — only fix the alias prefix or the column name, not both.
- Do NOT change: table names, JOIN ON conditions (the FK columns), WHERE filter values,
  ORDER BY direction, TOP/LIMIT numbers, DISTINCT keyword, aliases themselves.
- Do NOT add new columns or new conditions.
- Do NOT add markdown, comments, or explanations.

Return ONLY the fixed SQL query, nothing else."""

    try:
        fixed = await generate_with_fallback(
            prompt,
            temperature=0.0,
            max_output_tokens=2048,
            label="sql_column_fixer"
        )

        # Strip accidental markdown fences
        fixed = re.sub(r"^```[a-zA-Z]*\n?", "", fixed).strip()
        fixed = re.sub(r"\n?```$", "", fixed).strip()
        fixed = fixed.rstrip(";")

        changed = fixed != sql

        if changed:
            logger.info(
                f"[SQLColumnFixer] Columns fixed.\n"
                f"  Before : {sql}\n"
                f"  After  : {fixed}"
            )
        else:
            logger.debug("[SQLColumnFixer] No column changes needed.")

        return ColumnFixResult(original_sql=sql, fixed_sql=fixed, changed=changed)

    except Exception as exc:
        logger.warning(f"[SQLColumnFixer] LLM call failed — returning original SQL. Error: {exc}")
        return ColumnFixResult(original_sql=sql, fixed_sql=sql, changed=False)
"""SQL generator — converts a QueryPlan into executable SQL using Gemini."""

import json
import logging
from dataclasses import dataclass

import google.generativeai as genai
from app.query.sql_column_fixer import fix_sql_columns
from app.config import get_settings
from app.query.planner import QueryPlan, TableContext
from app.utils.gemini_key_manager import get_key_manager
from app.query.enrich_schema import build_enriched_schema
from app.enums.registry import get_relevant_enums, build_enum_prompt_block

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    sql: str
    explanation: str
    chat_response: str = ""
    response_intent: str = "data" 


import re

def expand_boolean_conditions(sql: str) -> str:
    def replacer(match):
        column = match.group(1)
        value = match.group(2).lower()

        if value in ["1", "true"]:
            return f"""(
    TRY_CAST({column} AS INT) = 1
    OR LOWER(CAST({column} AS VARCHAR)) IN ('true','yes','y')
)"""
        elif value in ["0", "false"]:
            return f"""(
    TRY_CAST({column} AS INT) = 0
    OR LOWER(CAST({column} AS VARCHAR)) IN ('false','no','n')
)"""
        return match.group(0)

    pattern = r"(\w+\.\w+)\s*=\s*(1|0|true|false)"
    return re.sub(pattern, replacer, sql, flags=re.IGNORECASE)


def ensure_distinct(sql: str) -> str:
    """Ensure every SELECT statement uses DISTINCT (safety net post-processor)."""
    # Match SELECT keywords that are NOT already followed by DISTINCT
    # Handles: SELECT, SELECT TOP N, SELECT ALL
    # Skips: SELECT DISTINCT (already has it), SELECT COUNT/SUM/AVG/MIN/MAX(
    pattern = r'\bSELECT\b(?!\s+DISTINCT\b)(?!\s+(?:COUNT|SUM|AVG|MIN|MAX)\s*\()'
    return re.sub(pattern, 'SELECT DISTINCT', sql, flags=re.IGNORECASE)


def fix_distinct_order_by(sql: str) -> str:
    """Fix SQL Server error 145: ORDER BY columns must appear in SELECT when DISTINCT is used.

    If SELECT DISTINCT is present and ORDER BY references columns not in the
    SELECT list, those columns are appended to the SELECT list automatically.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        return sql  # unparseable → return as-is

    # Only applies to SELECT DISTINCT (not aggregates / UNION etc.)
    select_node = tree.find(exp.Select)
    if select_node is None:
        return sql

    # Check if DISTINCT is set
    if not select_node.args.get("distinct"):
        return sql

    order = tree.find(exp.Order)
    if order is None:
        return sql

    # Collect existing SELECT column expressions as normalised SQL strings
    select_col_sqls = set()
    for sel_expr in select_node.expressions:
        # Use the raw column expression (strip alias if present)
        core = sel_expr.this if isinstance(sel_expr, exp.Alias) else sel_expr
        select_col_sqls.add(core.sql(dialect="tsql").lower().strip())

    # Walk ORDER BY expressions and find missing ones
    missing = []
    for ordered in order.find_all(exp.Ordered):
        order_expr = ordered.this
        order_sql = order_expr.sql(dialect="tsql").lower().strip()
        if order_sql not in select_col_sqls:
            missing.append(order_expr.copy())

    if not missing:
        return sql

    # Append missing columns to the SELECT list
    for col_expr in missing:
        select_node.append("expressions", col_expr)
        logger.info(f"[fix_distinct_order_by] Added '{col_expr.sql(dialect='tsql')}' to SELECT for DISTINCT compatibility")

    return tree.sql(dialect="tsql")


def enforce_explicit_limit(sql: str, question: str, dialect: str = "sqlserver") -> str:
    """
    If the user explicitly asked for N records, enforce TOP N / LIMIT N
    in the final SQL regardless of what the LLM generated.
    """
    # Extract explicit number from question
    pattern = r'\b(?:top|first|last|show|give\s+me|limit|only)\s+(\d+)\b'
    match = re.search(pattern, question, re.IGNORECASE)
    if not match:
        return sql  # no explicit number found — leave as-is

    requested_n = int(match.group(1))

    if dialect.lower() == "sqlserver":
        # Replace TOP <any_number> with TOP <requested_n>
        sql = re.sub(
            r'\bSELECT\s+DISTINCT\s+TOP\s+\d+\b',
            f'SELECT DISTINCT TOP {requested_n}',
            sql,
            flags=re.IGNORECASE
        )
        sql = re.sub(
            r'\bSELECT\s+TOP\s+\d+\b',
            f'SELECT TOP {requested_n}',
            sql,
            flags=re.IGNORECASE
        )
        # If no TOP at all, inject it
        if not re.search(r'\bTOP\s+\d+\b', sql, re.IGNORECASE):
            sql = re.sub(
                r'\bSELECT\s+DISTINCT\b',
                f'SELECT DISTINCT TOP {requested_n}',
                sql,
                flags=re.IGNORECASE,
                count=1
            )
    else:
        # For non-SQL Server: replace or append LIMIT
        if re.search(r'\bLIMIT\s+\d+\b', sql, re.IGNORECASE):
            sql = re.sub(r'\bLIMIT\s+\d+\b', f'LIMIT {requested_n}', sql, flags=re.IGNORECASE)
        else:
            sql = sql.rstrip().rstrip(';') + f' LIMIT {requested_n}'

    logger.info(f"[enforce_explicit_limit] Enforced TOP/LIMIT {requested_n} from question")
    return sql


class SQLGenerator:
    """Generates dialect-aware SQL from a query plan using Gemini."""

    def __init__(self):
        self.settings = get_settings()

    async def generate(
        self,
        plan: QueryPlan,
        conversation_context: str = "",
        session_id: str = '07acccf1-21fb-47d4-bf90-aaa83f047cfd',
    ) -> GenerationResult:
        # schema_context = build_enriched_schema(plan, session_id)
        schema = await build_enriched_schema(
            plan=plan,
            qdrant_collection=session_id,
            resolved_user_table=plan.resolved_user_table
        )
        schema_context = schema
        history_block = f"\n{conversation_context}\n" if conversation_context else ""
        logger.info(f"Hstory block for SQL generation (length {len(history_block)} chars): {history_block}")
        # history_block = ""
        max_rows = self.settings.query.max_rows_per_query
        # Pre-compute dialect syntax OUTSIDE the f-string to avoid expression-in-brace bugs
        if plan.dialect.lower() == "sqlserver":
            limit_syntax = f"Place TOP {max_rows} immediately after SELECT: SELECT TOP {max_rows} col1, col2 ..."
        else:
            limit_syntax = f"Place LIMIT {max_rows} at the end of the query: SELECT col1, col2 ... LIMIT {max_rows}"

        relevant_enums = get_relevant_enums(plan.relevant_tables)
        enum_block = build_enum_prompt_block(relevant_enums)
        logger.info(f"Enum block for SQL generation (length {len(enum_block)} chars): {enum_block}")
        ENUM_PRIORITY = """
            ENUM PRIORITY RULE (CRITICAL):
            When the user says "suspended", ALWAYS map to 4.
            When the user says "inactive", "deactivated", or "disabled", map to 5.
            Never confuse "suspended" with "inactive".
        """

        _ANTI_HALLUCINATION_BLOCK = """
            =====================
            ANTI-HALLUCINATION — SCHEMA ENFORCEMENT (HIGHEST PRIORITY — READ FIRST)
            =====================
            The DATABASE SCHEMA above is the ONLY source of truth.
            Every table, every column, every FK — if it is not in the SCHEMA, it does not exist.

            BEFORE writing any SQL, follow this checklist for EVERY column and JOIN:

            COLUMN CHECKLIST (run for each column you plan to write):
            ┌─────────────────────────────────────────────────────────────────┐
            │ Step 1: Which alias are you using? (t1, t2, t3 …)              │
            │ Step 2: Which real table does that alias map to?                │
            │ Step 3: Find that table in the SCHEMA                           │
            │ Step 4: Does the exact column name appear in that table's list? │
            │         YES → use it    NO → REMOVE IT, find the correct one   │
            └─────────────────────────────────────────────────────────────────┘

            FK / JOIN CHECKLIST (run for each JOIN you plan to write):
            ┌─────────────────────────────────────────────────────────────────┐
            │ Step 1: Find the foreign_keys section for the FROM table        │
            │ Step 2: Find the FK column that points to the target table      │
            │ Step 3: Use ONLY that exact FK column name in the ON clause     │
            │ Step 4: If no FK exists between two tables → join via a         │
            │         junction/link table that IS in the SCHEMA               │
            │         NEVER invent a direct FK that is not listed             │
            └─────────────────────────────────────────────────────────────────┘

            FORBIDDEN PATTERNS (these cause runtime errors — never generate them):
            ⛔ Using a column name from Table A while querying Table B
            e.g. t2.LastName when t2=BNR_Service_User (correct is t2.Surname)
            e.g. t1.Surname  when t1=BNR_UserDetails  (correct is t1.LastName)
            ⛔ Inventing FK columns: t1.Id = t2.UserDetailsId (if UserDetailsId not in SCHEMA)
            ⛔ Skipping a junction table: direct JOIN A→C when A→B→C is the real path
            ⛔ Using columns from a schema you memorised — ALWAYS read the SCHEMA above
            ⛔ Assuming Email, Phone, PreferredName etc. exist — check the SCHEMA first
            ⛔ Selecting ANY column not present in the SCHEMA for that specific table

            COLUMN NAME COLLISION RULE:
            Similar columns exist in multiple tables with DIFFERENT names.
            You MUST use the column name for the table you are actually querying:
            • Last name  → BNR_UserDetails uses "LastName",  BNR_Service_User uses "Surname"
            • Person ref → always verify FK column name per table in SCHEMA; never assume
            • Status     → exists in multiple tables; always qualify with alias (t1.Status)
            • IsDeleted  → exists in many tables; always qualify with alias

            SELF-CHECK before finalising SQL:
            ☐ Every column in SELECT is verified in SCHEMA for its specific table alias
            ☐ Every column in WHERE  is verified in SCHEMA for its specific table alias
            ☐ Every column in JOIN ON is a real FK from SCHEMA foreign_keys section
            ☐ No column from Table A is used while aliased to Table B
            ☐ No junction table skipped — path verified in SCHEMA
        """

        _STATIC_SQL_RULES = """
            =====================
            STRICT SQL GENERATION RULES
            =====================

            ── RULE 1: SCHEMA-ONLY (ABSOLUTE) ──────────────────────────────
            Use ONLY tables and columns that appear in the DATABASE SCHEMA above.
            Column names are PER-TABLE. A column valid in one table may not exist
            in another. Always verify against the correct table in SCHEMA.

            ── RULE 2: TABLE ALIASES ────────────────────────────────────────
            Use aliases t1, t2, t3, t4 … in order of appearance.
            Always qualify every column: t1.ColumnName (never bare ColumnName).

            ── RULE 3: SELECT DISTINCT (MANDATORY) ─────────────────────────
            Every SELECT must use SELECT DISTINCT.
            ✅ SELECT DISTINCT t1.FirstName, t1.LastName
            ✅ SELECT DISTINCT TOP 10 t1.Title
            ⛔ SELECT t1.FirstName                          ← missing DISTINCT

            ── RULE 4: STRING FILTERS ───────────────────────────────────────
            VARCHAR/NVARCHAR columns → always LIKE '%value%'
            INT / BIT / DATE columns → always use =
            Check the datatype in SCHEMA — do NOT guess from column name.

            ── RULE 5: BOOLEAN / BIT COLUMNS ───────────────────────────────
            Columns with BIT type OR names starting with Is/Has → use = 1 or = 0
            ✅ WHERE t1.IsDeleted = 0
            ⛔ WHERE t1.IsDeleted LIKE '%false%'

            ── RULE 6: ENUM COLUMNS ────────────────────────────────────────
            Enum columns store integers. Use = or IN with the integer from ENUM VALUE MAP.
            ✅ WHERE t1.Status = 2
            ⛔ WHERE t1.Status = 'Maternity Leave'
            ⛔ WHERE t1.Status LIKE '%active%'
            Enum columns are NOT foreign keys — never use them in JOIN conditions.

            ── RULE 7: JOINS — FK ONLY ──────────────────────────────────────
            Join tables ONLY using FK relationships listed in SCHEMA foreign_keys.
            STEP 1: Find the FK column in the source table that points to the target.
            STEP 2: Use that exact column name — never guess or invent.
            STEP 3: If no direct FK exists, find the junction/link table in SCHEMA.
            ✅ JOIN via BNR_UserDetailServiceUser when linking BNR_UserDetails ↔ BNR_Service_User
            ⛔ Direct JOIN BNR_UserDetails ON BNR_Service_User.UserDetailsId (if not in SCHEMA)

            ── RULE 8: UNION PARITY ─────────────────────────────────────────
            All UNION branches must have identical column count.
            Use NULL AS alias to pad branches that have fewer natural columns.

            ── RULE 9: DISTINCT + ORDER BY ──────────────────────────────────
            When SELECT DISTINCT is used, every ORDER BY column MUST be in SELECT.
            ✅ SELECT DISTINCT t1.Name, t1.CreationTime … ORDER BY t1.CreationTime DESC
            ⛔ SELECT DISTINCT t1.Name … ORDER BY t1.CreationTime DESC   ← error 145

            ── RULE 10: NO SQL SERVER DATE FUNCTIONS ────────────────────────
            Never use STRFTIME, DATE_FORMAT, or TO_CHAR in SQL Server dialect.

            ── RULE 11: FK TARGET VERIFICATION ─────────────────────────────
            Before every JOIN, confirm the FK column actually points to the target table.
            The enum value (e.g. RiskType=2) NEVER determines which FK column to use for JOINs.

            ── RULE 12: LAST NAME COLUMN IS TABLE-SPECIFIC ──────────────────
            BNR_UserDetails  → last name column is "LastName"
            BNR_Service_User → last name column is "Surname"
            NEVER use LastName on BNR_Service_User.
            NEVER use Surname on BNR_UserDetails.
            For ANY other table, check the SCHEMA — do not assume.

            ── RULE 13: JUNCTION TABLE RULE ────────────────────────────────
            When the SCHEMA shows no direct FK between two tables, look for a
            junction/link table (e.g. BNR_UserDetailServiceUser, BNR_User_Sites).
            Always route through it:
            ✅ t1 JOIN junction_table t_j ON t1.Id = t_j.LeftId
                JOIN target_table t2 ON t_j.RightId = t2.Id
            ⛔ t1 JOIN target_table t2 ON t1.Id = t2.InventedFKColumn

            ── RULE 14: INCIDENT TABLE ──────────────────────────────────────
            Query BNR_Incidents directly. Do NOT join to person tables unless
            the user explicitly filters by a specific person's name.
            PersonAffected enum = 1 means service user — do NOT join BNR_Service_User.

            ── RULE 15: STATUS / ACCOUNT QUERIES ───────────────────────────
            Status, employment, active/inactive, login → always use BNR_UserDetails.
            BNR_AboutMeServiceUser is for care profiles, NOT status.

            =====================
            COLUMN SELECTION RULES
            =====================
            • Select 3–6 human-readable columns relevant to the question.
            • NEVER select: Id/FK columns, IsDeleted/IsActive flags, timestamps,
            or DATE columns unless the user explicitly asks for a date.
            • NEVER use SELECT * unless the user literally writes "select *".
            • For charts/grouping: select the dimension column + the metric only.
            Never select free-text paragraph columns for charting.
            • Each column in SELECT must be verified in the SCHEMA for its table.

            =====================
            LIMIT / TOP RULES  
            =====================
            
            ⚠️ STEP 1 IS ABSOLUTE — IF IT MATCHES, STOP. DO NOT PROCEED TO STEP 2 OR 3.

            STEP 1 — Did the user explicitly state a number of records? (HIGHEST PRIORITY — OVERRIDES EVERYTHING)
            Signals: "top 10", "top 5", "first 20", "last 5", "show 15", "give me 100", "limit 50"
            → IF YES: Use EXACTLY that number. No more, no less.
            ✅ "give me last 10 incidents"  → SELECT DISTINCT TOP 10 ... ORDER BY ... DESC
            ✅ "show top 5 users"           → SELECT DISTINCT TOP 5 ...
            ⛔ NEVER use TOP 1000 when user said "top 10" — this is WRONG
            ⛔ NEVER apply the default max_rows limit when user gave an explicit number

            STEP 2 — Is the question asking for an aggregate? (count / sum / avg / how many)
            → IF YES: NO limit at all. Aggregates must process all rows.

            STEP 3 — No explicit number, not an aggregate → check primary table ROWS in SCHEMA.
            ROWS > 2000  → {limit_syntax}
            ROWS ≤ 2000  → NO limit at all.
            Use only the FROM table row count. Ignore joined tables.

            =====================

            =====================
            INTENT DETECTION
            =====================
            1. GREETING / WELLBEING / THANKS / GOODBYE / OFF_TOPIC
            → return chat_response, sql=""
            2. FOLLOWUP → signals: "give details", "show more", "tell me more",
            "its", "that", "those", "same" → reuse + modify previous SQL.
            Exception: if person/role changed, drop old entity filters, apply new ones.
            3. DATABASE → fresh question, generate new SQL.

            RULES:
            • If history exists AND current question refers to prior result → FOLLOWUP.
            • chat_response ONLY for pure social messages (intent 1). Never for SQL issues.
            • NEVER return chat_response because a query is complex or join path unclear.

            =====================
            RESPONSE INTENT
            =====================
            • "existence" → "is there", "do we have", "is X on/active/available"
            • "count"     → "how many", "total", "count of"
            • "summary"   → "summarize", "overview"
            • "data"      → "show me", "list", "get"

            =====================
            ENUM WHERE FILTER RULE
            =====================
            When the question implies a specific enum value (e.g. "on maternity leave"),
            look up the integer from ENUM VALUE MAP and add a WHERE filter with it.
            ✅ WHERE t1.Status = 2    (maternity leave)
            ⛔ WHERE t1.Status = 'maternity leave'

            =====================
            FINAL CHECK (run before returning)
            =====================
            ☐ No Id/FK column in SELECT
            ☐ No date/datetime column in SELECT (unless user asked for it)
            ☐ SELECT DISTINCT on every SELECT
            ☐ ORDER BY columns exist in SELECT list
            ☐ All columns verified to exist in SCHEMA for their specific table
            ☐ All JOIN FK columns verified in SCHEMA foreign_keys
            ☐ No column from one table used on a different table alias
            ☐ No junction table skipped
        """

        _SCHEMA_ENFORCEMENT = """
            =====================
            SCHEMA ENFORCEMENT LAYER (READ THIS FIRST — HIGHEST PRIORITY)
            =====================
            The ENRICHED SCHEMA above is the ONLY source of truth.

            RULES YOU MUST OBEY:
            1. You can ONLY use tables and columns that are explicitly listed in the ENRICHED SCHEMA.
            2. Column names are case-sensitive. Use them exactly as written.
            3. For every column you write (SELECT, WHERE, JOIN ON), you must be able to point to it in the SCHEMA for that specific table.
            4. If a column does not appear in the SCHEMA for the table you are using → you are not allowed to use it.
            5. Foreign keys must come from the "FOREIGN KEYS" section of the source table. Never invent them.
            6. When resolved_user_table is given, the name filter MUST be applied on that table using the exact column names shown in the SCHEMA.

            If you cannot find a column in the SCHEMA → do not use it. Remove it from SELECT and WHERE.
            """

        _OUTPUT_FORMAT = """\
            =====================
            OUTPUT FORMAT (MANDATORY — JSON ONLY)
            =====================
            For DATABASE questions:
            {
            "sql": "SQL_QUERY_HERE",
            "chat_response": "",
            "response_intent": "data",
            "reason": "short explanation"
            }

            For social messages (greeting/thanks/bye/off_topic):
            {
            "sql": "",
            "chat_response": "YOUR REPLY HERE",
            "reason": "intent name"
            }

            CRITICAL: Return ONLY valid JSON. No markdown, no plain text outside the JSON.
        """

        _TABLE_ROLE_REASONING = """
            =====================
            TABLE ROLE REASONING (run this BEFORE selecting tables)
            =====================
            When a person's name appears in the question, the person's table (BNR_UserDetails
            or BNR_Service_User) is ALMOST NEVER the primary/FROM table.

            Follow this 3-step process:

            STEP 1 — What does the user want to GET?
            Ask: "What entity is the answer about?"
            Examples:
            "Hardik's associated service users"   → answer is about SERVICE USERS
            "which staff work with John"          → answer is about STAFF
            "incidents of Vikas"                  → answer is about INCIDENTS
            "risk assessments for Sarah"          → answer is about RISK ASSESSMENTS
            "support plans of Krunal"             → answer is about SUPPORT PLANS
            "what is John's status"               → answer IS about the person → UserDetails IS primary

            STEP 2 — What table owns that entity?
            The table that stores the answer entity = PRIMARY/FROM table.
            service user associations  → BNR_UserDetailServiceUser
            incidents                  → BNR_Incidents
            risk assessments           → BNR_RiskAssessment
            support plans              → BNR_ServiceUserSupportPlan
            person's own status/info   → BNR_UserDetails or BNR_Service_User

            STEP 3 — The person's name is a FILTER, not a primary table signal.
            The resolved person table (BNR_UserDetails / BNR_Service_User) participates
            as a JOIN to apply the name filter — it is NOT the FROM table unless the
            answer itself is about that person's own profile/status/account.

            DECISION TABLE:
            ┌──────────────────────────────────────────┬──────────────────────────────┬─────────────────────────┐
            │ Question pattern                         │ PRIMARY table                │ Person table role       │
            ├──────────────────────────────────────────┼──────────────────────────────┼─────────────────────────┤
            │ "[person]'s associated service users"    │ BNR_UserDetailServiceUser    │ JOIN for name filter    │
            │ "staff who work with [service user]"     │ BNR_UserDetailServiceUser    │ JOIN for name filter    │
            │ "incidents of/involving [person]"        │ BNR_Incidents                │ JOIN for name filter    │
            │ "risk assessments for [person]"          │ BNR_RiskAssessment           │ JOIN for name filter    │
            │ "support plans of [person]"              │ BNR_ServiceUserSupportPlan   │ JOIN for name filter    │
            │ "safeguarding for [person]"              │ BNR_Safeguarding             │ JOIN for name filter    │
            │ "courses/training of [person]"           │ BNR_OperatorCourse           │ JOIN for name filter    │
            │ "what is [person]'s status/role/type"    │ BNR_UserDetails              │ IS the primary table    │
            │ "show me [person]'s profile/details"     │ BNR_UserDetails              │ IS the primary table    │
            │ "is [person] active/on leave/archived"   │ BNR_UserDetails              │ IS the primary table    │
            └──────────────────────────────────────────┴──────────────────────────────┴─────────────────────────┘

            RULE: If the question contains a possessive or relational phrase
            ("X's [something]", "of X", "for X", "involving X", "by X")
            where [something] is NOT the person's own profile data →
            the [something] entity's table is PRIMARY, person table is a JOIN filter.
            """
        _PRIMARY_TABLE_REASONING = """
            =====================
            PRIMARY TABLE SELECTION (MANDATORY — run before writing any SQL)
            =====================
            The resolved person table is a FILTER participant, not automatically primary.

            ASK YOURSELF: "What entity is the final answer rows about?"

            The table that OWNS that entity = your FROM table.
            ── ASSOCIATION QUERY SPECIAL CASE (READ THIS CAREFULLY) ────────────
            For "person X's associated service users" or "service users of staff X":

            IF the resolved person is STAFF (BNR_UserDetails):
            → Staff is the FILTER side. Service users are the RESULT side.
            → CORRECT tables: BNR_UserDetailServiceUser + BNR_UserDetails + BNR_Service_User
            → CORRECT pattern:
                FROM BNR_UserDetailServiceUser t1
                JOIN BNR_UserDetails t2 ON t1.UserDetailsId = t2.Id
                    WHERE t2.FirstName LIKE '%{first}%' AND t2.LastName LIKE '%{last}%'
                JOIN BNR_Service_User t3 ON t1.ServiceUserId = t3.Id
                SELECT t3.FirstName, t3.Surname, t3.Status
            ⛔ NEVER filter the staff name on BNR_Service_User
            ⛔ NEVER use a subquery on BNR_Service_User to find a staff member

            IF the resolved person is SERVICE USER (BNR_Service_User):
            → Service user is the FILTER side. Staff are the RESULT side.
            → CORRECT pattern:
                FROM BNR_UserDetailServiceUser t1
                JOIN BNR_Service_User t2 ON t1.ServiceUserId = t2.Id
                    WHERE t2.FirstName LIKE '%{first}%' AND t2.Surname LIKE '%{last}%'
                JOIN BNR_UserDetails t3 ON t1.UserDetailsId = t3.Id
                SELECT t3.FirstName, t3.LastName, t3.Email, t3.Phone
            ⛔ NEVER filter the service user name on BNR_UserDetails
            =====================
            EXAMPLES OF CORRECT REASONING:

            Q: "Hardik Pandya's associated service users"
            → Answer rows are about: service user associations
            → FROM: BNR_UserDetailServiceUser
            → JOIN BNR_UserDetails to filter by "Hardik Pandya"
            → JOIN BNR_Service_User to SELECT service user details

            Q: "incidents involving Sarah"
            → Answer rows are about: incidents
            → FROM: BNR_Incidents
            → JOIN BNR_UserDetails or BNR_Service_User to filter by "Sarah"

            Q: "show me Vikas's risk assessments"
            → Answer rows are about: risk assessments
            → FROM: BNR_RiskAssessment
            → JOIN person table to filter by "Vikas"

            Q: "what is John's employment status"
            → Answer rows are about: the person's own status
            → FROM: BNR_UserDetails  ← person table IS primary here
            → WHERE filter by "John"

            Q: "which staff work with service user Priya"
            → Answer rows are about: staff-service user links
            → FROM: BNR_UserDetailServiceUser
            → JOIN BNR_Service_User to filter by "Priya"
            → JOIN BNR_UserDetails to SELECT staff details

            RULE: A person's name in the question = apply name filter via JOIN.
                It does NOT mean that person's table is the FROM table.
                Only make person table primary when the question is about
                that person's OWN account data (status, role, type, profile).
            =====================
            """


        user_entity_block = ""
        if plan.resolved_user_table and plan.user_entity_name:
            name_parts = plan.user_entity_name.strip().split()
            first_name = name_parts[0] if name_parts else plan.user_entity_name
            last_name = name_parts[-1] if len(name_parts) >= 2 else ""
            full_name = plan.user_entity_name
            is_full = len(name_parts) >= 2

            # Get correct last name column (from cache or fallback)
            last_name_col = getattr(plan, 'resolved_last_name_column', None)
            if not last_name_col:
                last_name_col = "Surname" if plan.resolved_user_table == "BNR_Service_User" else "LastName"

            user_entity_block = f"""
                =====================
                PERSON FILTER — RESOLVED (HIGHEST PRIORITY)
                =====================
                Person mentioned : "{plan.user_entity_name}"
                Resolved table   : {plan.resolved_user_table}
                Last name column : {last_name_col}

                THIS PERSON IS IN: {plan.resolved_user_table}
                SEARCH FOR THIS PERSON ONLY IN: {plan.resolved_user_table}
                NEVER search for this person's name in any other table.

                ⛔ CRITICAL — NAME FILTER TABLE LOCK:
                The name "{plan.user_entity_name}" MUST be filtered on {plan.resolved_user_table}.
                NEVER apply this name filter on BNR_Service_User if resolved table is BNR_UserDetails.
                NEVER apply this name filter on BNR_UserDetails if resolved table is BNR_Service_User.
                The resolved table is the ONLY table where you search for this person's name.

                WHATEVER alias you assign to {plan.resolved_user_table} in your query,
                apply the name filter on THAT alias:

                FULL NAME filter (apply on whichever alias = {plan.resolved_user_table}):
                alias.FirstName LIKE '%{first_name}%' AND alias.{last_name_col} LIKE '%{last_name}%'

                SINGLE NAME filter:
                alias.FirstName LIKE '%{first_name}%' OR alias.{last_name_col} LIKE '%{first_name}%'

                EMPTY VALUE GUARD: Never generate LIKE '%%' or LIKE ''.

                COLUMN OWNERSHIP (SELECT columns):
                When {plan.resolved_user_table} = BNR_UserDetails:
                - Email, Phone, LastName → SELECT from BNR_UserDetails alias
                - Surname, SiteId        → these do NOT exist on BNR_UserDetails
                When {plan.resolved_user_table} = BNR_Service_User:
                - FirstName, Surname, SiteId, Status → SELECT from BNR_Service_User alias
                - Email, Phone, LastName             → these do NOT exist on BNR_Service_User
            """


        # ── Assemble prompt from sections — conditional, no dead sections ──────
        sections = [
            f"You are a SQL generator. Convert the user question into valid "
            f"{plan.dialect.upper()} SQL using ONLY the schema provided.\n",
        ]

        if history_block.strip():
            sections.append(
                f"=====================\nCONVERSATION HISTORY\n=====================\n"
                f"{history_block}"
            )

        sections.append(
            f"=====================\nDATABASE SCHEMA\n=====================\n"
            f"{schema_context}"
        )
        sections.append(_SCHEMA_ENFORCEMENT)

        sections.append(_TABLE_ROLE_REASONING)

        sections.append(_PRIMARY_TABLE_REASONING)

        sections.append(_ANTI_HALLUCINATION_BLOCK)

        if user_entity_block:
            sections.append(user_entity_block)

        if enum_block.strip():
            sections.append(
                f"=====================\nENUM VALUE MAP\n=====================\n"
                f"{enum_block}\n"
                "ENUM MATCHING: normalize both user input and synonym keys to lowercase, "
                "remove underscores/hyphens, then match. Use the INTEGER for that synonym in WHERE."
            )
            sections.append(ENUM_PRIORITY)

        sections.append(
            f"=====================\nUSER QUESTION\n=====================\n"
            f"{plan.question}"
        )

        # Static rules come LAST — highest recency attention from the model
        sections.append(_STATIC_SQL_RULES.format(limit_syntax=limit_syntax))
        sections.append("""
            =====================
            SCHEMA SELF-CHECK (MANDATORY — do this now before writing SQL)
            =====================
            For every table you plan to use, mentally list:
            1. The table name → confirm it exists in SCHEMA above
            2. Every column you plan to SELECT → confirm each exists in that table's SCHEMA entry
            3. Every column in WHERE → confirm each exists in that table's SCHEMA entry  
            4. Every FK in JOIN ON → confirm it appears in that table's foreign_keys in SCHEMA
            5. Every junction table needed → confirm it exists in SCHEMA

            If any check fails → remove that column/table and use only what SCHEMA provides.
            Only AFTER this check passes, write the JSON output below.
        """)

        sections.append(_OUTPUT_FORMAT)

        prompt = "\n\n".join(sections)

        logger.info(f"Prompt length: {len(prompt)} chars")
        # user_entity_block = ""
        # if plan.resolved_user_table and plan.user_entity_name:
        #     name_parts = plan.user_entity_name.strip().split()
        #     first_name = name_parts[0] if len(name_parts) >= 1 else plan.user_entity_name
        #     last_name = name_parts[-1] if len(name_parts) >= 2 else ""
        #     full_name = plan.user_entity_name
        #     is_full_name = len(name_parts) >= 2

        #     user_entity_block = f"""
        #         =====================
        #         USER ENTITY RESOLUTION (HIGHEST PRIORITY)
        #         =====================
        #         The user mentioned a person: "{plan.user_entity_name}"
        #         Resolved table: {plan.resolved_user_table}
        #         Parsed → FirstName="{first_name}"  LastName="{last_name}"  FullName="{full_name}"
        #         Is full name (first + last both present): {is_full_name}

        #         ─────────────────────────────────────────
        #         STEP 1 — CLEAN THE NAME (MANDATORY FIRST)
        #         ─────────────────────────────────────────
        #         Strip possessives and punctuation before using any name in a filter:
        #         "Louis's" → "Louis"  |  "Lois's" → "Lois"  |  "Smith," → "Smith"
        #         NEVER use the raw possessive form in a WHERE clause.

        #         ─────────────────────────────────────────
        #         STEP 2 — ALLOWED NAME COLUMNS (check schema, use ONLY if present)
        #         ─────────────────────────────────────────
        #         Preferred : PreferredName, Preferred_Name, KnownAs, NickName
        #         First     : FirstName, First_Name, Forename, GivenName
        #         Last      : LastName, Last_Name, Surname, FamilyName

        #         STRICTLY FORBIDDEN for name search (never use even if they exist):
        #         Email, EmailAddress, Phone, PhoneNumber, Mobile, Username,
        #         LoginName, any column containing Id/Code/Ref/Key,
        #         any INT / BIT / DATE / DATETIME column.
        #         ─────────────────────────────────────────
        #         EMPTY VALUE GUARD (HIGHEST PRIORITY RULE)
        #         ─────────────────────────────────────────
        #         Before writing ANY LIKE condition, check that the value being inserted is non-empty.

        #         ⛔ NEVER generate a LIKE condition where the value is empty or blank:
        #             LIKE '%%'        ← empty value — NEVER
        #             LIKE '%  %'      ← whitespace only — NEVER
        #             LIKE ''          ← empty string — NEVER

        #         ✅ RULE: If a name part (first, last, or full) is empty/blank/null,
        #         SKIP every LIKE condition that would use that part entirely.
        #         Do not write the condition at all.

        #         EXAMPLE — only "Vikas" given, last_name is empty:
        #         ⛔ WRONG:  t1.Surname LIKE '%%'                          ← skip, last_name is empty
        #         ⛔ WRONG:  t1.PreferredName LIKE '%%'                    ← skip, last_name is empty
        #         ⛔ WRONG:  t1.FirstName LIKE '%Vikas%' AND t1.Surname LIKE '%%'  ← skip entire AND block
        #         ✅ RIGHT:  t1.FirstName LIKE '%Vikas%'                  ← only non-empty parts used

        #         ─────────────────────────────────────────
        #         STEP 3 — BUILD THE WHERE FILTER
        #         ─────────────────────────────────────────

        #         ━━━ CASE A: FULL NAME GIVEN — first="{first_name}" last="{last_name}" ━━━

        #         TEMPLATE (use only columns present in schema):
        #         WHERE (
        #             (t1.FirstName LIKE '%{first_name}%' AND t1.LastName LIKE '%{last_name}%')
        #             OR t1.PreferredName LIKE '%{first_name}%'
        #             OR t1.PreferredName LIKE '%{full_name}%'
        #         )

        #         ⛔ STRICTLY FORBIDDEN — these extra OR clauses must NEVER appear when full name is given:
        #             OR t1.FirstName LIKE '%{first_name}%'   ← standalone FirstName OR — NEVER
        #             OR t1.LastName  LIKE '%{last_name}%'    ← standalone LastName OR — NEVER
        #             OR t1.FirstName LIKE '%{last_name}%'    ← last name in FirstName — NEVER
        #             OR t1.LastName  LIKE '%{first_name}%'   ← first name in LastName — NEVER

        #         The ONLY allowed pattern for FirstName and LastName when a full name is given is:
        #             (t1.FirstName LIKE '%{first_name}%' AND t1.LastName LIKE '%{last_name}%')
        #         They must ALWAYS appear together joined by AND.
        #         You MAY use OR conditions for PreferredName (e.g., OR t1.PreferredName LIKE '%{first_name}%').

        #         ━━━ CASE B: SINGLE NAME ONLY — one word="{first_name}" ━━━

        #         Rule: We don't know if it's first or last, so check ALL name columns with OR,
        #             using the SAME single word for every column.

        #         TEMPLATE (use only columns present in schema):
        #         WHERE (
        #             t1.PreferredName LIKE '%{first_name}%'
        #             OR t1.FirstName  LIKE '%{first_name}%'
        #             OR t1.LastName   LIKE '%{first_name}%'
        #         )

        #         EXAMPLE — "Vikas", schema has PreferredName + FirstName + Surname:
        #         WHERE (
        #             t1.PreferredName LIKE '%Vikas%'
        #             OR t1.FirstName  LIKE '%Vikas%'
        #             OR t1.Surname    LIKE '%Vikas%'
        #         )

        #         EXAMPLE — "Vikas", schema has ONLY FirstName + Surname (no PreferredName):
        #         WHERE (
        #             t1.FirstName LIKE '%Vikas%'
        #             OR t1.Surname LIKE '%Vikas%'
        #         )

        #         ⛔ FORBIDDEN patterns for single name:
        #             t1.FirstName LIKE '%Vikas%' AND t1.Surname LIKE '%Vikas%'  ← AND between columns — NEVER
        #             Checking first_name in LastName AND first_name in FirstName with AND — NEVER

        #         ─────────────────────────────────────────
        #         HARD RULES (never violate)
        #         ─────────────────────────────────────────
        #         1. Always use PreferredName if the column exists in the schema for {plan.resolved_user_table}.
        #         2. FULL NAME → FirstName column = first part ONLY. LastName column = last part ONLY. NO standalone OR clauses for FirstName or LastName.
        #         3. FULL NAME → You MAY use PreferredName with OR to check the first name or full name.
        #         4. SINGLE NAME → every name column gets the same single word, all joined with OR.
        #         6. NEVER use Email, Phone, or any forbidden column for name resolution.
        #         7. NEVER use columns not present in the schema for {plan.resolved_user_table}.
        #         8. NEVER invent columns (e.g. "Name", "UserName") that don't exist in the schema.
        #         9. NEVER use the possessive form in a LIKE filter.
        #         10. JOIN from {plan.resolved_user_table} to other tables using FK relationships in the schema.
        #         11. NEVER use a different user/person table for this entity. {plan.resolved_user_table} MUST be used for the person filter.
        #         12. Make sure {plan.resolved_user_table} is either the main FROM table or directly joined with the main entity.
        #     """

        # logger.info(f"Schema for AI : {schema_context}")
        # prompt = f"""
        #     You are a SQL generator.
            
        #     Your job is to convert a user question into VALID {plan.dialect.upper()} SQL using ONLY the schema provided.
            
        #     You MUST strictly follow the schema. NEVER invent tables or columns.
        #     =====================
        #     CONVERSATION HISTORY
        #     =====================
        #     {history_block}
        #     =====================
        #     DATABASE SCHEMA
        #     =====================
        #     {schema_context}
        #     =====================
        #     PERSON NAME FILTER (if applicable)
        #     =====================
        #     {user_entity_block if user_entity_block else "No specific person mentioned in this question."}
        #     If a person name filter is provided above:
        #     - The table and name columns are already identified for you
        #     - Apply the WHERE filter on that table using the name columns listed
        #     - The main query structure (FROM, JOINs, SELECT) is still decided by the question and schema
        #     - The name filter is just an additional WHERE condition — it does not change what the query is about
        #     =====================
        #     USER QUESTION
        #     =====================
        #     {plan.question}

        #     =====================
        #     ENUM 
        #     =====================
        #     {enum_block}

        #     =====================
        #     ENUM MATCHING RULE (CRITICAL — READ CAREFULLY)
        #     =====================
        #     When matching the user's words to an ENUM synonym in the ENUM VALUE MAP above:
        #     1. NORMALIZE the user's input: convert to lowercase AND remove ALL underscores and hyphens.
        #        Example: "Maternity Leave" → "maternity leave"
        #        Example: "maternity_leave" → "maternity leave"
        #        Example: "Long_Term_Leave" → "long term leave"
        #     2. NORMALIZE each synonym key the same way before comparing.
        #     3. Find the synonym whose normalized form BEST MATCHES the user's normalized input.
        #     4. Use the INTEGER value mapped to that synonym — not the integer of a different synonym.
            
        #     EXAMPLE:
        #     User says: "users who are currently on maternity leave"
        #     → Normalized user input contains: "maternity leave"
        #     → Synonym match: "maternity leave" → 2
        #     → Correct SQL: WHERE Status = 2
        #     ⛔ WRONG: WHERE Status = 1  (1 = active, NOT maternity leave)
            
        #     ALWAYS double-check the integer value you pick against the ENUM VALUE MAP.
        #     If "maternity leave" maps to 2, then Status = 2. NEVER confuse it with Status = 1 (active).

        #     =====================
        #     INTENT DETECTION (CHECK THIS FIRST)
        #     =====================
            
        #     Before generating SQL, classify the user's message intent:
            
        #     1. GREETING  → "hi", "hello", "hey", "good morning", etc.
        #     2. WELLBEING → "how are you", "how r u", etc.
        #     3. THANKS    → "thanks", "thank you", "great", "awesome", etc.
        #     4. GOODBYE   → "bye", "goodbye", "see you", etc.
        #     5. OFF_TOPIC → ONLY completely non-database topics: weather, jokes, cooking, sports, etc.
        #     6. FOLLOWUP  → user is referring to something from CONVERSATION HISTORY above.
        #                 Look at the previous SQL in history and expand/modify it to answer
        #                 the current question. Keep the same filters/WHERE conditions from
        #                 the previous SQL, EXCEPT when the new question specifies a different
        #                 person or role (e.g., switching from querying "service user" to "staff member").
        #                 If the person or role changes, you MUST drop the old entity filters and
        #                 strictly apply only the new ones from the PERSON NAME FILTER block.
        #                 Remove any TOP 1 limits. Add more columns if needed.
        #                 Examples of followup signals: "give me details", "show more", "tell me 
        #                 more", "its information", "show that", "expand", "what about that",
        #                 anything using "its", "that", "those", "it", "this", "same", "above"
        #     7. DATABASE  → a fresh independent data question with no reference to prior context.
            
        #     CRITICAL RULES:
        #     - Check CONVERSATION HISTORY first before classifying as DATABASE or FOLLOWUP.
        #     - If history exists AND current question refers to prior result → always FOLLOWUP.
        #     - For FOLLOWUP: reuse the previous SQL from history as the base, modify it to answer 
        #     the new question. NEVER start fresh ignoring history.
        #     - If you cannot find a direct join path → still return SQL with best available columns.
        #     - NEVER return chat_response because a query is complex or join path is unclear.
        #     - chat_response is ONLY for pure social messages (intents 1-5). Nothing else.
        #     - When in doubt and history exists → FOLLOWUP.
        #     - When in doubt and no history → DATABASE.
            
        #     =====================
        #     STRICT RULES
        #     =====================
            
        #     1. ONLY use tables listed in SCHEMA.
        #     2. ONLY use columns listed under each table.
        #     3. NEVER invent columns.
        #     4. NEVER invent tables.
        #     5. JOIN tables ONLY using FK relationships listed in SCHEMA.
            
        #     6. Use aliases: t1, t2, t3, t4

        #     7. ALWAYS USE SELECT DISTINCT (CRITICAL — NEVER OMIT):
        #     Every SELECT statement you generate MUST use SELECT DISTINCT.
        #     This applies universally — whether or not the query has JOINs.
        #     ✅ RIGHT: SELECT DISTINCT t1.FirstName, t1.LastName
        #     ✅ RIGHT: SELECT DISTINCT TOP 10 t1.Title, t1.Status
        #     ⛔ WRONG: SELECT t1.FirstName, t1.LastName
        #     ⛔ WRONG: SELECT TOP 10 t1.Title, t1.Status
        #     For SQL Server with TOP N: SELECT DISTINCT TOP N ...
        #     For aggregates (COUNT, SUM, AVG etc.): Still use SELECT DISTINCT if selecting raw columns alongside aggregates, but pure aggregate queries (e.g. SELECT COUNT(*)) do not need DISTINCT.
            
        #     7. STRING FILTER RULE (CRITICAL):
        #     When filtering on a text/varchar column, ALWAYS use LIKE instead of =.
        #     Wrap the value with % wildcards so partial matches are found.
            
        #     ALWAYS do this:   WHERE t1.LocationOfIncident LIKE '%Oldfield%'
        #     NEVER do this:    WHERE t1.LocationOfIncident = 'Oldfield'
            
        #     This applies to ANY column that holds text values:
        #     names, descriptions, locations, types, statuses, and any other string field.
            
        #     Exceptions — use = (not LIKE) for:
        #     - Integer / numeric columns  (e.g. Id, SiteId, Count)
        #     - Boolean columns            (e.g. IsDeleted, IsPrivate)
        #     - Date / datetime columns    (e.g. CreationTime, DateOfBirth)
            
        #     IMPORTANT DATATYPE RULE: You must purely look at the datatypes annotated in the DATABASE SCHEMA (e.g. VARCHAR, NVARCHAR, INT, FLOAT, BOOLEAN, DATE) to decide this. Do NOT guess the type based on the column name alone.
            
        #     8. NAME SEARCH RULE (CRITICAL):
        #     When filtering on a person's name (FirstName, LastName, Surname, PreferredName):
        #     - If the user provides a FULL NAME (multiple words, like "Vikas Kohli" or "Krunal Pandya"):
        #       You MUST split the name and use AND between the different name parts. 
        #       ✅ RIGHT: (t1.FirstName LIKE '%Vikas%' AND t1.LastName LIKE '%Kohli%')
        #       ✅ RIGHT: (t1.FirstName LIKE '%Vikas%' AND t1.Surname LIKE '%Kohli%')
        #       ⛔ WRONG: (t1.FirstName LIKE '%Vikas Kohli%')   -- This will fail as FirstName only contains one word.
        #       ⛔ WRONG: (t1.FirstName LIKE '%Vikas%' OR t1.LastName LIKE '%Kohli%')
        #       NEVER use OR for different parts of a single person's full name.
        #     - If the user provides a SINGLE NAME (one word, like "Vikas"):
        #       Use OR to check all candidate name columns with the SAME single word.
        #       ✅ RIGHT: (t1.FirstName LIKE '%Vikas%' OR t1.LastName LIKE '%Vikas%' OR t1.PreferredName LIKE '%Vikas%')            
        #     8. UNION COLUMN PARITY RULE (CRITICAL):
        #     When writing a UNION query, ALL SELECT branches MUST have the IDENTICAL
        #     number of columns in the SAME order.
            
        #     If one branch naturally has fewer meaningful columns, pad it with NULL
        #     placeholders using aliases that match the first branch.
            
        #     9. If PreferredName columns exist, prioritize them for user entity resolution. also if we have firstname only then also check the prefered name columns. if both given first name and lastname exist check with both
        #     RULES:
        #     - Count columns in branch 1 first, then match exactly in branch 2.
        #     - Use NULL AS <alias> for columns that don't apply to a branch.
        #     - Never produce a UNION where branch column counts differ.
        #     - Always use UNION ALL (not UNION) unless deduplication is explicitly needed.

        #     =====================
        #     ANTI-HALLUCINATION RULE (HIGHEST PRIORITY)
        #     =====================

        #     You are FORBIDDEN from inventing any column or table name.
        #     If you cannot find an EXACT column that matches the user's intent after carefully splitting every schema column name into English words, then:
        #     - Do NOT guess or create a column like "CqcNotification", "HasCqcNotification", etc.
        #     - Instead, return sql = "" and set chat_response to a polite message asking for clarification, e.g.:
        #     "I couldn't find a column related to 'CQC notification'. Could you tell me the exact field name or describe it differently?"

        #     Only use column names that appear verbatim (case-insensitive) in the provided DATABASE SCHEMA.

        #     =====================
        #     BOOLEAN COLUMN RULE (CRITICAL - Add this)
        #     =====================
            
        #     Some columns are BOOLEAN / BIT type (e.g. IsSafeguardRaised, CQCNotificationDone, 
        #     InvestigationStarted, InvestigationCompleted, etc.).
            
        #     For BOOLEAN columns:
        #     - NEVER use LIKE '%value%'
        #     - ALWAYS use = 1 or = 0  (or the expanded version)
        #     - Look at the datatype in the SCHEMA. If it says BIT, BOOLEAN, or the column name 
        #       starts with "Is", "Has", "CQCNotification", treat it as boolean.
        #     - Do NOT apply the STRING FILTER RULE to boolean columns.
            
        #     Example of GOOD boolean filter:
        #     WHERE t1.CQCNotificationDone = 1
            
        #     BAD: WHERE t1.CQCNotificationDone LIKE '%True%'
        #     =====================
        #     COLUMN SELECTION RULE (CRITICAL)
        #     =====================
        #     For select column use all tables columns you can use join tables columns also if available
        #     You must carefully choose which columns to include in the SELECT clause.
        #     9. DO NOT use STRFTIME, DATE_FORMAT, or TO_CHAR if Dialect is Mssqlserver.
        #     RULES:
            
        #     1. ONLY select columns that are directly relevant to the user's question.
        #     - EXTREMELY IMPORTANT: If the question asks for data from a specific joined table (e.g., "Sleep Chart Details"), prioritize selecting columns from THAT joined table (e.g., T2) rather than selecting all fields solely from the primary person table (T1).
        #     - MAXIMUM 3-6 COLUMNS: Pick ONLY the 3 to 5 most important core fields (e.g. Title, Name, Status, Date) across the relevant tables. Ignore the rest to avoid overwhelming the user.
        #     - Do NOT use SELECT * unless the user literally writes "select *" or "all details".
            
        #     2. NEVER include TECHNICAL, SYSTEM, or DATE columns in the SELECT output unless explicitly asked:
        #     - ID columns: Id, UserId, SiteId, LocationId, MasterFieldId, etc.
        #     - Timestamps & system dates: CreationTime, CreatedAt, UpdatedAt, DeletedAt, Time, ReviewedDate, etc.
        #     - ANY date/datetime column: DateOfBirth, DateOfIncident, StartDate, EndDate, ReportDate,
        #         IncidentDate, AdmissionDate, DischargeDate, or ANY column with a DATE/DATETIME datatype.
        #     - System flags: IsDeleted, IsPrimary, IsActive, WasIinvolved, etc.
        #     - These are internal or supplementary columns and should NEVER appear in SELECT unless
        #         the user's question explicitly mentions a date (e.g. "show me the date", "when did",
        #         "what date", "date of birth", "admission date", etc.).
        #     - If in User query if a user says give me this date or this then and then u can add date or else never add date if its birthdate of user then also dont add birthdate if user dont ask about it
            
        #     3. Technical/System/Date columns are allowed ONLY for:
        #     - JOIN conditions
        #     - WHERE / ORDER BY / GROUP BY filters (e.g. filtering by a date range the user specified)
        #     - NOT in SELECT unless the user explicitly asks for the date value itself
            
        #     4. If multiple useful columns exist, select a meaningful, HUMAN-READABLE subset:
        #     - Pick ONLY columns that an end-user actually cares about (e.g., Names, Titles, Statuses, Amounts, Descriptions, Summaries).
        #     Example:
        #     User asks: "show investigations"
        #     GOOD:
        #         SELECT t1.Title, t1.OutCome, t1.Consequence, t1.Severity
        #     BAD:
        #         SELECT t1.Id, t1.CreationTime, t1.IsDeleted, t1.UserId, t1.DateOfIncident
            
        #     5. STRICT COLUMN VALIDATION:
        #     - Every column in SELECT must exist in the SCHEMA
        #     - NEVER invent or guess column names
            
        #     6. NEVER use SELECT * unless explicitly asked. Even if the user asks for "all the Service User Support Plan", do NOT use SELECT *. Explicitly list only the relevant, human-readable, non-technical, non-date columns.
            
        #     7. CHARTING & AGGREGATION RULE:
        #     - If the user asks for a chart, graph, pie chart, or distribution (e.g., "across all months", "by category"):
        #     - You MUST select the appropriate dimension column (a date, category, or identifier).
        #     - EXTREMELY IMPORTANT: NEVER select long free-text or description paragraph columns when charting. These columns cannot be used as chart axes.
        #     - If they ask for a count or across months, ensure you group correctly or select the categorical columns explicitly.
        #     - Exception: date columns MAY be selected here only if the chart/grouping is date-based (e.g. "by month", "over time").
            
        #     =====================
        #     FK COLUMN SELECTION RULE (CRITICAL — prevents wrong JOIN columns)
        #     =====================

        #     When joining a person table to filter by name, you MUST use the FK column
        #     that ACTUALLY points to that person's table — NOT a column that merely
        #     sounds related to the context of the question.

        #     STEP-BY-STEP:
        #     1. Identify the person you need to filter by (e.g. "Vikas Kohli, service user")
        #     2. Find which FK column in the PRIMARY table points to their person table
        #     - Look at foreign_keys in the SCHEMA
        #     - Match: FK column → to_table = BNR_Service_User → that is your JOIN column
        #     3. Use ONLY that FK column for the JOIN — ignore all other FK columns

        #     EXAMPLE — BNR_RiskAssessment has these FKs:
        #     ServiceUserId        → BNR_Service_User    ← USE THIS to join a service user by name
        #     RiskAsseOtherUserId  → BNR_RiskAsseOtherUser  ← completely different table, NOT service user

        #     ⛔ WRONG: JOIN BNR_Service_User t2 ON t1.RiskAsseOtherUserId = t2.Id
        #     WHY WRONG: RiskAsseOtherUserId does NOT point to BNR_Service_User
        #     ✅ RIGHT:  JOIN BNR_Service_User t2 ON t1.ServiceUserId = t2.Id

        #     CRITICAL SEPARATION OF CONCERNS:
        #     - The ENUM column (RiskType = 2 for "other") describes WHAT CATEGORY is at risk
        #     - The FK column (ServiceUserId) describes WHICH PERSON record to join for name filtering
        #     - These are TWO DIFFERENT THINGS — the enum value NEVER determines which FK to use for JOIN

        #     REAL SCENARIO:
        #     Question: "Risk assessments of Vikas Kohli where other member is at risk"
        #     - RiskType = 2          ← because "other member is at risk" (enum filter)
        #     - JOIN via ServiceUserId ← because Vikas Kohli is a service user (name filter)
        #     - Both conditions applied independently in the same query

        #     RULE: Always verify FK target table in SCHEMA before writing any JOIN.
        #         Column name alone is NOT sufficient — check the actual foreign_key mapping.

        #     =====================
        #     COLUMN MATCHING RULE (CRITICAL)
        #     =====================
            
        #     Users describe columns in plain English. You must map their words to the real schema column
        #     by mentally splitting every column name into its English words:
            
        #     e.g.  "LocationOfIncident" → "location of incident"
        #             "TypeOfIncident"     → "type of incident"
        #             "DegreeOfHarm"       → "degree of harm"
        #             "DateOfBirth"        → "date of birth"
        #             "SiteId"             → "site id"
            
        #     Do this for EVERY column in EVERY relevant table, then match against what the user said.
            
        #     GOLDEN RULE: If a value the user is filtering/selecting already exists as a direct column
        #     on the primary table, use it directly — do NOT add a JOIN to another table just because
        #     a related table also contains similar data.
            
        #     This applies universally to all tables and all columns in the schema.
            
        #     =====================
        #     EXPLICIT LIMIT DETECTION (HIGHEST PRIORITY)
        #     =====================

        #     If the user explicitly mentions a number of records, you MUST ALWAYS apply that limit.

        #     This OVERRIDES all other rules including table row count.

        #     Detect patterns like:
        #     - "top 10"
        #     - "first 5"
        #     - "last 20"
        #     - "show 15"
        #     - "give me 100"
        #     - "limit 50"
        #     - "only 25 records"

        #     RULES:
        #     - Extract the number N from the user query
        #     - Apply TOP N (SQL Server) or LIMIT N (other dialects)
        #     - NEVER ignore this even if table has fewer than 2000 rows

        #     EXAMPLES:
        #     User: "Give me top 10 incidents"
        #     → SELECT TOP 10 ...

        #     User: "Show 5 users"
        #     → SELECT TOP 5 ...

        #     User: "List 20 records"
        #     → SELECT TOP 20 ...

        #     This rule has STRICT PRIORITY over all LIMIT/TOP logic below.
            
        #     =====================
        #     LIMIT / TOP RULE (CRITICAL)
        #     =====================
            
        #     Before adding any LIMIT or TOP, reason through these steps in order:
            
        #     STEP 1 — Is the question asking for an aggregate?
        #     Signals: "how many", "count", "total", "sum", "average", "avg",
        #             "minimum", "maximum", "min", "max", "percentage", "proportion"
        #     → If YES: Do NOT add LIMIT or TOP. Aggregates summarize all rows by design.
            
        #     STEP 2 — Did the user explicitly ask for N rows?
        #     Signals: "top N", "first N", "last N", "limit N", "show N records/rows/results"
        #     → If YES: Use exactly N as the row limit.
            
        #     STEP 3 — No aggregate, no explicit limit?
        #     → Find the ROWS count of the PRIMARY table (the table in the FROM clause) in the SCHEMA.
        #     → Compare ROWS against 2000:
            
        #     DECISION TABLE:
        #     ┌──────────────────────┬──────────────────────────┐
        #     │ Primary Table Rows   │ Action                   │
        #     ├──────────────────────┼──────────────────────────┤
        #     │ ROWS > 2000          │ Apply {limit_syntax}     │
        #     │ ROWS <= 2000         │ NO limit at all          │
        #     └──────────────────────┴──────────────────────────┘
            
        #     EXAMPLES:
        #     - BNR_Incidents    ROWS: 1722 → 1722 <= 2000 → NO limit
        #     - Player_history   ROWS: 235561 → 235561 > 2000 → apply {limit_syntax}
        #     - BNR_Safeguarding ROWS: 70   → 70 <= 2000 → NO limit
        #     - Order_tab        ROWS: 39   → 39 <= 2000 → NO limit
            
        #     JOIN RULE: Always use the FROM clause table row count ONLY. Ignore all joined tables.
        #     Example: FROM BNR_Incidents JOIN BNR_Sites → use BNR_Incidents ROWS: 1722 → NO limit
            
        #     =====================
        #     INCIDENT TABLE RULE (CRITICAL — NO UNNECESSARY JOINS)
        #     =====================
            
        #     When the user asks about incidents (e.g. "show me incidents", "last 10 incidents",
        #     "incidents at my sites"), query the BNR_Incidents table DIRECTLY.
            
        #     DO NOT join to BNR_Service_User, BNR_User_Details, BNR_Sites, or any person table
        #     UNLESS the user explicitly asks to filter by a specific person's name.
            
        #     The BNR_Incidents table already contains all the incident data you need:
        #     IncidentNumber, TimeOfIncident, IncidentDetail, TypeOfIncident, ResultOfHarm,
        #     LocationOfIncident, PreciseLocation, PersonAffected (enum), etc.
            
        #     ⛔ WRONG: User asks "show me last 10 incidents" → JOIN to BNR_Service_User
        #     ✅ RIGHT: User asks "show me last 10 incidents" → SELECT directly from BNR_Incidents
        #     ✅ RIGHT: User asks "incidents of Vikas Kohli" → JOIN to person table to filter by name
            
        #     If the user says "service user incidents", use the PersonAffected enum column
        #     (PersonAffected = 1 for service user) — do NOT join to a person table.
            
        #     =====================
        #     STATUS / ACCOUNT QUERY RULE (CRITICAL)
        #     =====================
            
        #     When the user asks about a person's STATUS, login status, employment status,
        #     whether someone is active/inactive, on leave, suspended, or archived:
        #     → ALWAYS use BNR_UserDetails (NOT BNR_AboutMeServiceUser)
            
        #     BNR_UserDetails stores: Status, UserType, login info, employment status.
        #     BNR_AboutMeServiceUser stores: care profile, diagnoses, allergies, medications.
            
        #     ⛔ WRONG: "What is the status of Vikas" → query BNR_AboutMeServiceUser
        #     ✅ RIGHT: "What is the status of Vikas" → query BNR_UserDetails
        #     ✅ RIGHT: "Is Hardik on maternity leave" → query BNR_UserDetails with Status filter
            
        #     =====================
        #     ENUM WHERE FILTER RULE (CRITICAL)
        #     =====================
            
        #     When the user's question implies a specific enum value (e.g. "on maternity leave",
        #     "suspended users", "active staff"), you MUST:
        #     1. Look up the enum value in the ENUM VALUE MAP above
        #     2. Add a WHERE condition with the integer value
            
        #     ⛔ WRONG: "Is Hardik on maternity leave?" → SELECT Status FROM BNR_UserDetails WHERE FirstName LIKE '%Hardik%'
        #        (Missing the Status filter! This just fetches the status value without checking maternity leave)
        #     ✅ RIGHT: "Is Hardik on maternity leave?" → SELECT Status FROM BNR_UserDetails WHERE FirstName LIKE '%Hardik%' AND Status = 2
        #        (Status = 2 is maternity leave in the enum map)
            
        #     The SQL must FILTER by the enum value so we get a yes/no answer, not just return raw status numbers.
            
        #     =====================
        #     DISTINCT + ORDER BY RULE (SQL Server)
        #     =====================
            
        #     When using SELECT DISTINCT with ORDER BY, the ORDER BY column MUST also
        #     appear in the SELECT list. SQL Server will reject the query otherwise.
            
        #     ⛔ WRONG: SELECT DISTINCT TOP 10 t1.Name FROM ... ORDER BY t1.CreationTime DESC
        #        (CreationTime not in SELECT → SQL Server error 145)
        #     ✅ RIGHT: SELECT DISTINCT TOP 10 t1.Name, t1.CreationTime FROM ... ORDER BY t1.CreationTime DESC
            
        #     If you want to ORDER BY a column, ALWAYS include it in SELECT.
            
        #     =====================
        #     MANDATORY REASONING STEPS (follow in order before writing SQL)
        #     =====================
            
        #     1. Apply COLUMN MATCHING RULE — does the user's phrase map to a direct column? Use it.
        #     2. Apply INCIDENT TABLE RULE — is this an incident query? Don't join person tables unless filtering by name.
        #     3. Apply STATUS/ACCOUNT RULE — is this a status question? Use BNR_UserDetails.
        #     4. Apply ENUM WHERE FILTER RULE — does the question imply a specific enum value? Add WHERE filter.
        #     5. Identify only the tables truly needed — avoid unnecessary JOINs.
        #     6. Confirm every table and column exists in SCHEMA.
        #     7. Apply LIMIT / TOP RULE.
        #     8. Apply DISTINCT + ORDER BY RULE — if ORDER BY column not in SELECT, add it.
        #     9. Write the SQL.
            
        #     =====================
        #     FINAL CHECK (MANDATORY)
        #     =====================
            
        #     Before returning SQL:
        #     - Ensure NO column ending with "Id" is present in SELECT
        #     - Ensure NO date/datetime column is present in SELECT unless the user explicitly asked for it OR it is needed for ORDER BY with DISTINCT
        #     - Ensure all selected columns are relevant to the user query
        #     - If using SELECT DISTINCT + ORDER BY, ensure ORDER BY columns are in SELECT
            
        #     =====================
        #     RESPONSE INTENT (for DATABASE questions only)
        #     =====================
            
        #     After generating SQL, also classify what kind of answer the user wants:
            
        #     - "existence" → user asks IF data exists: "is there any X", "do we have Y", "is X available", "are there any Z"
        #     - "count"     → user wants HOW MANY: "how many X", "total number of Y", "count of Z"  
        #     - "summary"   → user wants overview/insight: "summarize X", "give me overview of Y"
        #     - "data"      → user wants to see actual records: "show me X", "list all Y", "get Z"
            
        #     Set this as the "response_intent" field in your JSON output.
            
        #     =====================
        #     OUTPUT FORMAT
        #     =====================
        #     CRITICAL: Your response MUST be a single valid JSON object. NOTHING ELSE.
        #     - Do NOT write plain text explanations
        #     - Do NOT ask clarifying questions in plain text  
        #     - Do NOT say "I need more information" in plain text
        #     - Never add chat_response for no relevnt columns found or join path issues. Just return sql="" and a clear reason.
        #     - If the name cannot be found → still attempt SQL searching all name columns
        #     - EVERY response must be valid JSON, no exceptions
        #     For DATABASE intent:
        #     {{
        #     "sql": "SQL_QUERY_HERE",
        #     "chat_response": "",
        #     "response_intent": "data", # one of: existence, count, summary, data
        #     "reason": "short explanation of logic used"
        #     }}
            
        #     For ALL other intents (greeting, wellbeing, thanks, goodbye, off_topic):
        #     {{
        #     "sql": "",
        #     "chat_response": "YOUR FRIENDLY NATURAL REPLY HERE",
        #     "reason": "intent name"
        #     }}
        # """
        response_text = None
        try:
            response = await get_key_manager().generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=self.settings.gemini.max_tokens,
                    response_mime_type="application/json",
                ),
            )
            response_text = response.text.strip()

            # Model sometimes reasons out loud before the JSON block.
            # Grab the first { ... } block regardless of what surrounds it.
            import re
            json_match = re.search(r'(\{[\s\S]*?\})', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Fallback: remove common markdown and extra lines
                json_str = response_text
                for prefix in ("```json", "```", "json"):
                    json_str = json_str.replace(prefix, "")
                json_str = json_str.strip()

            # Clean up any trailing text after the JSON
            # This handles the "Extra data" error
            try:
                # Use raw_decode to get only the first valid JSON object
                decoder = json.JSONDecoder()
                data, idx = decoder.raw_decode(json_str)
                # If there's extra text after, we ignore it
            except json.JSONDecodeError:
                # Final aggressive cleanup
                json_str = re.sub(r'^.*?(\{.*\})', r'\1', json_str, flags=re.DOTALL)
                data = json.loads(json_str)
            sql = data.get("sql", "").strip().rstrip(";")
            chat_response = data.get("chat_response", "").strip()
            response_intent = data.get("response_intent", "data").strip()
            explanation = data.get("reason", data.get("explanation", ""))
            if chat_response and not sql:
                logger.info(f"Chat intent detected: {explanation}")
                return GenerationResult(sql="", explanation=explanation, chat_response=chat_response)
            fix_result = await fix_sql_columns(
                sql=sql,
                relevant_tables=plan.relevant_tables,
                dialect=plan.dialect,
            )
            logger.info(f'Fix result : {fix_result}')
            sql = fix_result.fixed_sql

            sql = expand_boolean_conditions(sql)
            sql = ensure_distinct(sql)
            sql = fix_distinct_order_by(sql)
            sql = enforce_explicit_limit(sql, plan.question, plan.dialect)
            logger.info(f"Sql -> {sql}")
            if not sql:
                raise ValueError("Gemini returned empty SQL")

            return GenerationResult(sql=sql, explanation=explanation,response_intent=response_intent)

        except json.JSONDecodeError as e:
            logger.error(f"Non-JSON generator response: {response_text}")
            raise ValueError(f"SQL generation returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"SQL generation failed: {e}", exc_info=True)
            raise

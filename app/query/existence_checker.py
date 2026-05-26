import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings
from app.utils.llm_provider import generate_with_fallback

logger = logging.getLogger(__name__)

@dataclass
class ExistenceCheckResult:
    existence_sql: Optional[str]
    entity_name: str
    explanation: str

class ExistenceChecker:
    """Analyzes a 0-row SQL query and generates a simple query to check if the root entity exists."""

    def __init__(self):
        self.settings = get_settings()

    async def check_existence_sql(
        self,
        failed_sql: str,
        user_question: str,
        dialect: str,
    ) -> ExistenceCheckResult:
        prompt = f"""
You are an expert SQL analyst debugging why a database query returned 0 rows.
The user asked: "{user_question}"
The generated SQL was:
{failed_sql}

Your task:
1. Identify the CORE ENTITY (e.g. a specific user name, product name, ID) being filtered in the WHERE clause that likely caused the 0 rows. For example, if the query joins Orders and Users, and WHERE User.Name = 'Viral', the core entity is 'Viral'.
2. Write a ridiculously simple, barebones `SELECT 1 FROM <table> WHERE <conditions_for_core_entity> LIMIT 1` (or dialect equivalent) to verify if the entity actually exists in its primary table. Let's call this the "existence check query".
3. Return a JSON object with:
    - "entity_name": The plain-English name of the specific thing being checked (e.g. "User 'Viral'").
    - "existence_sql": The simplified SQL query. If no specific entity filter exists (e.g., just filtering by status > 5, or joining 2 tables globally), return null because we don't have a singular entity to check.
    - "reason": A short explanation of your logic.

Ensure the "existence_sql" is valid {dialect.upper()} syntax.
Output ONLY valid JSON, without any markdown formatting wrappers.

Example Output:
{{
  "entity_name": "User 'Viral'",
  "existence_sql": "SELECT TOP 1 1 FROM dbo.BNR_User_Details WHERE FirstName LIKE '%Viral%' OR LastName LIKE '%Viral%'",
  "reason": "Checking if the fundamental user record exists in the UserDetails table."
}}
"""

        try:
            raw_text = await generate_with_fallback(
                prompt,
                temperature=0.1,
                max_output_tokens=500,
                label="existence_checker"
            )
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]

            data = json.loads(raw_text.strip())
            sql = data.get("existence_sql")
            if sql:
                sql = sql.strip().rstrip(";")
            return ExistenceCheckResult(
                existence_sql=sql,
                entity_name=data.get("entity_name", "the requested entity"),
                explanation=data.get("reason", "")
            )
        except Exception as e:
            logger.error(f"ExistenceChecker completely failed: {e}", exc_info=True)
            return ExistenceCheckResult(existence_sql=None, entity_name="", explanation=str(e))

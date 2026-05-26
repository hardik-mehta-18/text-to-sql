"""Describer — uses Gemini to generate plain-English descriptions for database tables.

Uses GeminiKeyManager for automatic API key rotation when quota is hit.
"""

import asyncio
import logging
from typing import List

from app.config import get_settings
from app.training.schema_extractor import TableInfo
from app.utils.llm_provider import generate_with_fallback

logger = logging.getLogger(__name__)

BATCH_SIZE = 5  # Tables described in parallel


class Describer:
    """Generates AI descriptions for database tables using Gemini with key rotation."""

    def __init__(self):
        self.settings = get_settings()

    async def describe_all_with_user_input(
        self,
        tables: List[TableInfo],
        user_descs: dict[str, str]
    ) -> dict[str, str]:

        descriptions = {}
        logger.info("describer call")
        for i in range(0, len(tables), BATCH_SIZE):
            batch = tables[i:i + BATCH_SIZE]

            tasks = [
                self._describe_table(t, user_descs.get(t.table_name))
                for t in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for table, result in zip(batch, results):
                if isinstance(result, Exception):
                    descriptions[table.table_name] = self._fallback_description(table)
                else:
                    descriptions[table.table_name] = result

        return descriptions

    async def _describe_table(self, table: TableInfo, user_desc: str | None = None) -> str:
        """Generate a 2-sentence description for a single table."""
        col_lines = []
        for col in table.columns[:20]:
            tags = []
            if col.is_primary_key:
                tags.append("PK")
            if col.is_foreign_key and col.fk_target_table:
                tags.append(f"FK->{col.fk_target_table}")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            col_lines.append(f"  - {col.name} ({col.data_type}){tag_str}")

        sample_text = ""
        if table.sample_rows:
            sample_text = "\nSample rows:\n" + "\n".join(
                f"  {r}" for r in table.sample_rows[:3]
            )
        user_context = ""
        if user_desc:
            user_context = f"\nUser description:\n{user_desc}\nUse this as additional context."

        logger.info(f"user_context : {user_context}")

        prompt = f"""You are a database documentation expert creating descriptions optimized for vector search.

            Table: {table.table_name} ({table.row_count:,} rows)

            Columns:
            {chr(10).join(col_lines)}
            {sample_text}
            User Context : {user_context}

            Instructions:
            - You MUST incorporate the USER CONTEXT into the final description if provided.
            - Treat USER CONTEXT as ground truth business logic.
            - Do NOT ignore or summarize away important rules from it.

            Write 1-2 sentences optimized for semantic search:
            - Business purpose + key entities (customers, orders, etc.)
            - Typical queries (sales totals, customer trends)
            - Include 2-3 key columns + patterns (dates, IDs, amounts)

            Max 150 chars. Concise, keyword-rich for vector embedding. Plain text.
        """

        try:
            description = await generate_with_fallback(
                prompt,
                temperature=0.1,
                max_output_tokens=200,
                label="describer"
            )
            if len(description) > 500:
                description = description[:497] + "..."
            return description
        except RuntimeError as e:
            # All keys exhausted
            logger.error(f"All Gemini keys exhausted for '{table.table_name}': {e}")
            return self._fallback_description(table)
        except Exception as e:
            logger.warning(f"Gemini failed for '{table.table_name}': {e}")
            return self._fallback_description(table)

    def _fallback_description(self, table: TableInfo) -> str:
        col_names = ", ".join(c.name for c in table.columns[:5])
        return (
            f"Table '{table.table_name}' stores data with {len(table.columns)} columns "
            f"including {col_names}. "
            f"It contains {table.row_count:,} rows."
        )

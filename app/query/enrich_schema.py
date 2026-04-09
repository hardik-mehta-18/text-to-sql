import json
from typing import Optional, Tuple
from app.query.planner import QueryPlan, USER_TYPE_OPTIONS, _normalize_table_name
import logging

logger = logging.getLogger(__name__)

# Normalized set of all user-type table names (excludes "Other")
USER_TYPE_TABLE_NORMALIZED = {
    _normalize_table_name(opt["table"])
    for opt in USER_TYPE_OPTIONS
    if opt["table"]
}


def get_user_tables_in_enriched(needed: set) -> list[str]:
    """Returns which user-type tables ended up in the enriched schema set."""
    return [
        t for t in needed
        if _normalize_table_name(t) in USER_TYPE_TABLE_NORMALIZED
    ]


async def build_enriched_schema(
    plan: QueryPlan,
    qdrant_collection: str,
    resolved_user_table: Optional[str] = None
) -> str:
    """
    Builds a clean, strict schema context for the SQL generator.
    No clarification messages anymore — we never ask the user here.
    
    Returns: schema_str (always returns string, never None)
    """
    from app.training.indexer import Indexer
    indexer = Indexer()

    effective_resolved_table = resolved_user_table or plan.resolved_user_table

    # Collect all needed tables + their FK targets
    needed = set()
    for t in plan.relevant_tables:
        needed.add(t.table_name.lower())
        for fk in t.foreign_keys:
            needed.add(fk['to_table'].lower())

    # === CRITICAL: Remove competing user-type tables if we have a resolved one ===
    if effective_resolved_table and effective_resolved_table != "__skip__":
        resolved_norm = _normalize_table_name(effective_resolved_table)
        
        tables_to_remove = {
            t for t in needed
            if _normalize_table_name(t) in USER_TYPE_TABLE_NORMALIZED
            and _normalize_table_name(t) != resolved_norm
        }
        if tables_to_remove:
            logger.info(f"[enricher] Removed competing user tables: {tables_to_remove}")
            needed -= tables_to_remove

    # Fetch full payloads from Qdrant
    try:
        scroll_results = indexer.client.scroll(
            collection_name=qdrant_collection,
            limit=500,
            with_payload=True
        )
        report = [r.payload for r in scroll_results[0]]
    except Exception as e:
        logger.warning(f"Qdrant scroll failed for collection {qdrant_collection}: {e}")
        report = []

    # Filter to only needed tables
    filtered = [t for t in report if t.get('table_name', '').lower() in needed]

    # Build strict schema prompt
    lines = [
        "ENRICHED SCHEMA — ONLY THESE TABLES AND COLUMNS EXIST",
        "=============================================================",
        "STRICT RULES FOR YOU:",
        "• You are FORBIDDEN from using any table or column not listed below.",
        "• All column names and table names are case-sensitive. Use them EXACTLY as shown.",
        "• Only use the tables and columns provided in this schema.\n"
    ]

    for t in filtered:
        table_full = f"{t.get('schema_name', 'dbo')}.{t['table_name']}"
        lines.append(f"TABLE: {table_full}  (ROWS: {t.get('row_count', 0):,})")
        lines.append("ALLOWED COLUMNS (exact case-sensitive names):")
        
        for c in t.get('columns', []):
            if isinstance(c, dict):
                name = c.get('name')
                typ = c.get('type')
                nullable = "NULL" if c.get('nullable', True) else "NOT NULL"
                lines.append(f"   {name}  ({typ})  [{nullable}]")
            else:
                lines.append(f"   {c}")

        if t.get('foreign_keys'):
            lines.append("FOREIGN KEYS (use these exact columns for JOINs):")
            for fk in t['foreign_keys']:
                lines.append(f"   {fk}")

        lines.append("")  # blank line between tables

    if not filtered:
        lines.append("WARNING: No tables were found in the schema. Query may fail.")

    return "\n".join(lines)
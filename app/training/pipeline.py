# import asyncio
# import json
# import logging
# import pathlib
# from dataclasses import dataclass
# from typing import AsyncGenerator, List, Optional

# from sqlalchemy.engine import Engine

# from app.config import get_settings
# from app.training.schema_extractor import SchemaExtractor
# from app.training.describer import Describer
# from app.training.indexer import Indexer
# from app.training.schema_extractor import TableInfo


# logger = logging.getLogger(__name__)


# @dataclass
# class TrainingProgress:
#     step: str
#     progress: int           # 0-100
#     tables_done: int
#     tables_total: int
#     message: str
#     error: str = ""


# class TrainingPipeline:
#     """Runs the 3-step training process and yields progress updates."""

#     def __init__(self):
#         self.describer = Describer()
#         self.indexer = Indexer()

#     async def run(
#         self,
#         session_id: str,
#         engine: Engine,
#         dialect: str,
#         db_key: str = "",
#         qdrant_collection: str = ""
#     ) -> AsyncGenerator[TrainingProgress, None]:

#         # ── Step 1: Extract schema (or load from cache) ───────────────────────
#         cached_data = self._load_schema_cache(db_key) if db_key else None
#         from_cache = cached_data is not None
#         if cached_data is not None:
#             logger.info("If call")
#             # ✅ Cache hit — skip extraction AND description entirely
#             tables, descriptions = cached_data
#             total = len(tables)

#             yield TrainingProgress(
#                 step="cache_hit",
#                 progress=70,
#                 tables_done=total,
#                 tables_total=total,
#                 message=f"Using cached schema + descriptions ({total} tables).",
#             )

#         else:
#             logger.info("Else Called")
#             from_cache = False
#             # ── Step 1a: Extract schema from DB ──────────────────────────────
#             yield TrainingProgress(
#                 step="extracting_schema",
#                 progress=5,
#                 tables_done=0,
#                 tables_total=0,
#                 message="Reading database structure...",
#             )

#             try:
#                 extractor = SchemaExtractor(engine)
#                 tables = await extractor.extract()
#             except Exception as e:
#                 logger.error(f"Schema extraction failed: {e}", exc_info=True)
#                 yield TrainingProgress(
#                     step="error",
#                     progress=0,
#                     tables_done=0,
#                     tables_total=0,
#                     message="Failed to read database structure. Check connection permissions.",
#                     error="Database access error",
#                 )
#                 return

#             total = len(tables)
#             if total == 0:
#                 yield TrainingProgress(
#                     step="error",
#                     progress=0,
#                     tables_done=0,
#                     tables_total=0,
#                     message="No tables found in database.",
#                     error="Empty database or no accessible tables.",
#                 )
#                 return

#             sid = session_id[:8]
#             logger.info(f"[{sid}] Schema extracted: {total} tables found")
#             for t in tables:
#                 schema_prefix = f"[{t.schema_name}]." if t.schema_name else ""
#                 logger.info(
#                     f"[{sid}]   TABLE {schema_prefix}[{t.table_name}] "
#                     f"| {t.row_count:,} rows | {len(t.columns)} columns"
#                 )

#             yield TrainingProgress(
#                 step="schema_extracted",
#                 progress=20,
#                 tables_done=0,
#                 tables_total=total,
#                 message=f"Found {total} tables. Generating descriptions...",
#             )

#             # ── Step 1b: Generate descriptions (only when no cache) ───────────
#             descriptions: dict[str, str] = {}
#             batch_size = 5

#             for i in range(0, total, batch_size):
#                 batch = tables[i:i + batch_size]
#                 batch_names = ", ".join(t.table_name for t in batch)
#                 logger.info(f"Describing batch {i // batch_size + 1}: [{batch_names}]")
#                 tasks = [self.describer._describe_table(t) for t in batch]
#                 results = await asyncio.gather(*tasks, return_exceptions=True)
#                 logger.info(f"Finished describing batch {i // batch_size + 1}")

#                 for table, result in zip(batch, results):
#                     schema_prefix = f"[{table.schema_name}]." if table.schema_name else ""
#                     if isinstance(result, Exception):
#                         descriptions[table.table_name] = self.describer._fallback_description(table)
#                         logger.warning(
#                             f"DESCRIPTION FAILED {schema_prefix}[{table.table_name}]: {result}"
#                         )
#                     else:
#                         descriptions[table.table_name] = result
#                         logger.info(
#                             f"DESCRIPTION OK   {schema_prefix}[{table.table_name}]: "
#                             f"{result[:80]}{'...' if len(result) > 80 else ''}"
#                         )

#                 done = min(i + batch_size, total)
#                 progress = 20 + int((done / total) * 50)   # 20 → 70

#                 yield TrainingProgress(
#                     step="generating_descriptions",
#                     progress=progress,
#                     tables_done=done,
#                     tables_total=total,
#                     message=f"Describing tables... {done}/{total}",
#                 )

#             # Save cache so next time we skip all of the above
#             if db_key:
#                 self._save_schema_cache(db_key, tables, descriptions)

#         # ── Step 2: Index into Qdrant (always runs — cache or not) ───────────

#         collection_exists = self.indexer.collection_exists(qdrant_collection)

#         if from_cache and collection_exists:
#             logger.info("Skipping Qdrant indexing — cache hit and collection already exists")
#             yield TrainingProgress(
#                 step="indexing",
#                 progress=90,
#                 tables_done=total,
#                 tables_total=total,
#                 message="Search index already up to date.",
#             )
#         else:
#             yield TrainingProgress(
#                 step="indexing",
#                 progress=75,
#                 tables_done=total,
#                 tables_total=total,
#                 message="Building search index...",
#             )
#             try:
#                 await self.indexer.index(
#                     db_key=db_key,
#                     tables=tables,
#                     descriptions=descriptions,
#                     dialect=dialect,
#                     qdrant_collection=qdrant_collection
#                 )
#             except Exception as e:
#                 logger.error(f"Qdrant indexing failed: {e}", exc_info=True)
#                 yield TrainingProgress(
#                     step="error",
#                     progress=75,
#                     tables_done=total,
#                     tables_total=total,
#                     message="Failed to build search index.",
#                     error="Search index error",
#                 )
#                 return

#         # ── Write schema debug JSON ───────────────────────────────────────────
#         self._write_schema_report(session_id, tables, descriptions)

#         # ── Done ─────────────────────────────────────────────────────────────
#         yield TrainingProgress(
#             step="ready",
#             progress=100,
#             tables_done=total,
#             tables_total=total,
#             message=f"Ready! {total} tables indexed and searchable.",
#         )

#         logger.info(
#             f"Training complete for session {session_id}: "
#             f"{total} tables indexed in Qdrant"
#         )

#     async def run_with_user_input(
#         self,
#         session_id: str,
#         tables: List[TableInfo],
#         user_descs: dict[str, str],
#         dialect: str,
#         db_key: str,
#         qdrant_collection: str
#     ):
#         descriptions = await self.describer.describe_all_with_user_input(
#             tables,
#             user_descs
#         )

#         await self.indexer.index(
#             db_key=db_key,
#             tables=tables,
#             descriptions=descriptions,
#             dialect=dialect,
#             qdrant_collection=qdrant_collection
#         )
#     async def extract_only(self, session_id: str, engine):
#         extractor = SchemaExtractor(engine)
#         tables = await extractor.extract()

#         if not tables:
#             raise ValueError("No tables found")

#         return tables
    
#     def _write_schema_report(self, session_id: str, tables, descriptions: dict) -> None:
#         settings = get_settings()
#         report = []
#         for table in tables:
#             desc = descriptions.get(table.table_name, "")
#             report.append({
#                 "table_name": table.table_name,
#                 "schema_name": table.schema_name,
#                 "row_count": table.row_count,
#                 "columns": [{"name": c.name, "type": c.data_type, "nullable": c.nullable} for c in table.columns],
#                 "primary_keys": table.primary_keys,
#                 "foreign_keys": table.foreign_keys,
#                 "sample_rows": table.sample_rows,
#                 "ai_description": desc,
#                 "description_source": "ai" if desc else "fallback",
#             })

#         report_path = (
#             pathlib.Path(settings.uploads.dir) / session_id / "schema_report.json"
#         )
#         try:
#             report_path.parent.mkdir(parents=True, exist_ok=True)
#             report_path.write_text(
#                 json.dumps(report, indent=2, default=str),
#                 encoding="utf-8",
#             )
#             logger.info(f"Schema report written to {report_path}")
#         except Exception as e:
#             logger.warning(f"Could not write schema report: {e}")

#     def _cache_path(self, db_key: str) -> pathlib.Path:
#         return pathlib.Path(get_settings().uploads.dir) / "cache" / db_key / "schema_cache.json"

#     def _load_schema_cache(self, db_key: str):

#         p = self._cache_path(db_key)
#         if not p.exists():
#             return None

#         try:
#             data = json.loads(p.read_text(encoding="utf-8"))
#             tables = [TableInfo.from_dict(t) for t in data["tables"]]
#             descriptions = await self.describer.describe_all_with_user_input(
#                 tables,
#                 user_descs
#             )
#             logger.info(f"Schema cache hit: loaded {len(tables)} tables from {p}")
#             return tables, descriptions
#         except Exception as e:
#             logger.warning(f"Schema cache unreadable ({p}): {e}")
#             return None

#     def _save_schema_cache(self, db_key: str, tables: List, descriptions: dict) -> None:
#         p = self._cache_path(db_key)
#         try:
#             p.parent.mkdir(parents=True, exist_ok=True)
#             data = {
#                 "tables": [t.to_dict() for t in tables],
#                 "descriptions": descriptions,
#             }
#             p.write_text(
#                 json.dumps(data, indent=2, default=str),
#                 encoding="utf-8",
#             )
#             logger.info(f"Schema+description cache saved: {len(tables)} tables to {p}")
#         except Exception as e:
#             logger.warning(f"Could not write schema cache: {e}")


import asyncio
import json
import logging
import pathlib
from dataclasses import dataclass
from typing import AsyncGenerator, List, Optional
from sqlalchemy.engine import Engine
from app.config import get_settings
from app.training.schema_extractor import SchemaExtractor, TableInfo
from app.training.describer import Describer
from app.training.indexer import Indexer
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TrainingProgress:
    step: str
    progress: int
    tables_done: int
    tables_total: int
    message: str
    error: str = ""


class TrainingPipeline:
    """Handles schema extraction, user-driven description, and indexing."""

    def __init__(self):
        self.describer = Describer()
        self.indexer = Indexer()

    # ─────────────────────────────────────────────────────────────
    # STEP 1 → ONLY EXTRACT TABLES (for UI selection)
    # ─────────────────────────────────────────────────────────────
    async def extract_only(self, session_id: str, engine: Engine) -> List[TableInfo]:
        extractor = SchemaExtractor(engine)
        tables = await extractor.extract()
        logger.info(tables)
        self._write_schema_report(
            session_id=session_id,
            tables=tables,
            descriptions={}  # empty for now
        )
        if not tables:
            raise ValueError("No tables found")

        return tables

    # ─────────────────────────────────────────────────────────────
    # STEP 2 → AFTER USER INPUT → GENERATE DESCRIPTIONS + INDEX
    # ─────────────────────────────────────────────────────────────
    async def run_with_user_input(
        self,
        session_id: str,
        tables: List[TableInfo],
        user_descs: dict[str, str],
        dialect: str,
        db_key: str,
        qdrant_collection: str,
        force_reindex: bool = False,
    ) -> AsyncGenerator[TrainingProgress, None]:
        cached_data = self._load_schema_cache(db_key) if db_key else None
        total = len(tables)
        collection_exists = self.indexer.collection_exists(qdrant_collection)
        if cached_data is not None and collection_exists and not force_reindex:
            logger.info("Skipping Qdrant indexing — cache hit and collection already exists")
            _, descriptions = cached_data
            yield TrainingProgress(
                step="indexing",
                progress=90,
                tables_done=total,
                tables_total=total,
                message="Search index already up to date.",
            )
        else:
            yield TrainingProgress(
                step="generating_descriptions",
                progress=10,
                tables_done=0,
                tables_total=total,
                message="Generating AI descriptions...",
            )

            # Generate descriptions using user input
            descriptions = await self.describer.describe_all_with_user_input(
                tables,
                user_descs
            )

            yield TrainingProgress(
                step="indexing",
                progress=70,
                tables_done=total,
                tables_total=total,
                message="Building search index...",
            )

            try:
                await self.indexer.index(
                    db_key=db_key,
                    tables=tables,
                    descriptions=descriptions,
                    dialect=dialect,
                    qdrant_collection=qdrant_collection
                )
            except Exception as e:
                logger.exception("Indexing failed")  # better logging

                yield TrainingProgress(
                    step="error",
                    progress=70,
                    tables_done=total,
                    tables_total=total,
                    message="Failed to build search index.",
                    error=str(e),  # ✅ CRITICAL FIX
                )
                return
        
        

        # Save cache
        if db_key:
            self._save_schema_cache(db_key, tables, descriptions)

        # Write debug report
        self._write_schema_report(session_id, tables, descriptions)

        yield TrainingProgress(
            step="ready",
            progress=100,
            tables_done=total,
            tables_total=total,
            message=f"Ready! {total} tables indexed.",
        )

    async def run_partial_update(
        self,
        session_id: str,
        tables_to_update: List[TableInfo],
        user_descs: dict,
        dialect: str,
        db_key: str,
        qdrant_collection: str,
    ) -> AsyncGenerator[TrainingProgress, None]:
        """
        Re-embed a subset of tables without touching the rest of the collection.
 
        Flow:
          1. Delete existing Qdrant points for these tables (by table_name filter)
          2. Generate fresh AI descriptions for only these tables
          3. Upsert the new vectors (indexer only creates collection if missing)
          4. Merge new descriptions back into the full cache
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny, FilterSelector
 
        total = len(tables_to_update)
        table_names = [t.table_name for t in tables_to_update]
 
        yield TrainingProgress(
            step="cleaning",
            progress=5,
            tables_done=0,
            tables_total=total,
            message=f"Removing old vectors for {total} table(s)...",
        )
 
        # Step 1 — Delete existing points for only these tables
        try:
            # Ensure payload index exists so delete works
            try:
                self.indexer.client.create_payload_index(
                    collection_name=qdrant_collection,
                    field_name="table_name",
                    field_schema="keyword",
                )
            except Exception as pe:
                logger.warning(f"[partial_update] Could not create payload index: {pe}")

            self.indexer.client.delete(
                collection_name=qdrant_collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="table_name",
                                match=MatchAny(any=table_names),
                            )
                        ]
                    )
                ),
            )
            logger.info(f"[partial_update] Deleted old points for: {table_names}")
        except Exception as e:
            logger.warning(f"[partial_update] Could not delete old points: {e}")
            # Non-fatal — upsert will overwrite anyway
 
        yield TrainingProgress(
            step="generating_descriptions",
            progress=15,
            tables_done=0,
            tables_total=total,
            message=f"Generating descriptions for {total} table(s)...",
        )
 
        # Step 2 — Generate AI descriptions for ONLY the subset
        new_descriptions = await self.describer.describe_all_with_user_input(
            tables_to_update,
            user_descs,
        )
 
        yield TrainingProgress(
            step="indexing",
            progress=60,
            tables_done=total,
            tables_total=total,
            message="Upserting new vectors...",
        )
        existing_cache = self._load_schema_cache(db_key) if db_key else None
        # Step 3 — collection already exists → indexer skips recreate, just upserts
        try:
            await self.indexer.index(
                db_key=db_key,
                tables=tables_to_update,
                descriptions=new_descriptions,
                dialect=dialect,
                qdrant_collection=qdrant_collection,
            )
        except Exception as e:
            logger.exception("[partial_update] Indexing failed")
            yield TrainingProgress(
                step="error",
                progress=60,
                tables_done=total,
                tables_total=total,
                message="Failed to upsert vectors.",
                error=str(e),
            )
            return
 
        # Step 4 — Merge back into the full cache
        if db_key:
            if existing_cache:
                all_tables, all_descriptions = existing_cache
                all_descriptions.update(new_descriptions)
                updated_names = set(table_names)
                merged_tables = [
                    t for t in all_tables if t.table_name not in updated_names
                ] + tables_to_update
                self._save_schema_cache(db_key, merged_tables, all_descriptions)
                logger.info(
                    f"[partial_update] Cache merged — "
                    f"{len(merged_tables)} total, {len(updated_names)} updated."
                )
            else:
                self._save_schema_cache(db_key, tables_to_update, new_descriptions)
 
        yield TrainingProgress(
            step="ready",
            progress=100,
            tables_done=total,
            tables_total=total,
            message=f"{total} table(s) updated successfully.",
        )

    async def _resolve_table_info(
        self,
        table_name: str,
        cached_table_map: dict,
        engine,
        session_id: str,
    ) -> Optional["TableInfo"]:
        """
        Get TableInfo for a single table.
        1. Try cache first (instant, no DB call)
        2. If missing, extract ONLY that one table from the engine
        Never extracts all tables.
        """
        # Cache hit
        if table_name in cached_table_map:
            return cached_table_map[table_name]
 
        # Cache miss — extract only this one table
        if engine is None:
            logger.warning(
                f"[resolve] '{table_name}' not in cache and no engine available — skipping."
            )
            return None
 
        logger.info(f"[resolve] '{table_name}' not in cache — extracting single table from DB.")
        extractor = SchemaExtractor(engine)
 
        # Detect schema from engine dialect (default dbo for SQL Server)
        schema = "dbo" if engine.dialect.name == "mssql" else None
        table_info = await extractor.extract_single_table(table_name, schema=schema)
 
        if table_info is None:
            logger.warning(f"[resolve] '{table_name}' not found in DB either — skipping.")
 
        return table_info
    
    # ─────────────────────────────────────────────────────────────
    # CACHE HANDLING
    # ─────────────────────────────────────────────────────────────
    def _cache_path(self, db_key: str) -> pathlib.Path:
        return pathlib.Path(get_settings().uploads.dir) / "cache" / db_key / "schema_cache.json"

    def _load_schema_cache(self, db_key: str):
        p = self._cache_path(db_key)
        if not p.exists():
            return None

        try:
            data = json.loads(p.read_text("utf-8"))
            tables = [TableInfo.from_dict(t) for t in data["tables"]]
            descriptions = data.get("descriptions", {})
            logger.info(f"Cache hit: {len(tables)} tables loaded")
            return tables, descriptions
        except Exception as e:
            logger.warning(f"Cache read failed: {e}")
            return None

    def _save_schema_cache(self, db_key: str, tables: List[TableInfo], descriptions: dict):
        p = self._cache_path(db_key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "tables": [t.to_dict() for t in tables],
                "descriptions": descriptions,
            }

            p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            logger.info(f"Cache saved: {len(tables)} tables")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")

    # ─────────────────────────────────────────────────────────────
    # DEBUG REPORT
    # ─────────────────────────────────────────────────────────────
    def _write_schema_report(self, session_id: str, tables, descriptions: dict):
        settings = get_settings()
        reverse_fk_map: dict[str, list] = {}
        for table in tables:
            for fk in table.foreign_keys:
                target = fk["to_table"]
                reverse_fk_map.setdefault(target, []).append({
                    "referencing_table": table.table_name,
                    "referencing_col":   fk["from"],
                    "local_col":         fk["to_col"],
                })
        report = []
        for table in tables:
            desc = descriptions.get(table.table_name, "")

            report.append({
                "table_name": table.table_name,
                "schema_name": table.schema_name,
                "row_count": table.row_count,
                "columns": [{"name": c.name, "type": c.data_type, "nullable": c.nullable} for c in table.columns],
                "primary_keys": table.primary_keys,
                "foreign_keys": table.foreign_keys,
                "reverse_foreign_keys": reverse_fk_map.get(table.table_name, []),
                "sample_rows": table.sample_rows,
                "ai_description": desc,
            })

        path = pathlib.Path(settings.uploads.dir) / session_id / "schema_report.json"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            logger.info(f"Schema report written: {path}")
        except Exception as e:
            logger.warning(f"Failed to write report: {e}")


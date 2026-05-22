"""Schema extractor — inspects any SQLAlchemy-compatible database and returns structured metadata."""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    default: Optional[str]
    is_primary_key: bool
    is_foreign_key: bool
    fk_target_table: Optional[str] = None
    fk_target_column: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ColumnInfo":
        return cls(
            name=d["name"],
            data_type=d["data_type"],
            nullable=d["nullable"],
            default=d.get("default"),
            is_primary_key=d["is_primary_key"],
            is_foreign_key=d["is_foreign_key"],
            fk_target_table=d.get("fk_target_table"),
            fk_target_column=d.get("fk_target_column"),
        )


@dataclass
class TableInfo:
    table_name: str
    schema_name: Optional[str]
    columns: List[ColumnInfo]
    row_count: int
    sample_rows: List[Dict[str, Any]]
    primary_keys: List[str]
    foreign_keys: List[Dict[str, str]]   # [{"from": col, "to_table": t, "to_col": c}]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_name": self.table_name,
            "schema_name": self.schema_name,
            "columns": [c.to_dict() for c in self.columns],
            "row_count": self.row_count,
            "sample_rows": self.sample_rows,
            "primary_keys": self.primary_keys,
            "foreign_keys": self.foreign_keys,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TableInfo":
        return cls(
            table_name=d["table_name"],
            schema_name=d.get("schema_name"),
            columns=[ColumnInfo.from_dict(c) for c in d.get("columns", [])],
            row_count=d["row_count"],
            sample_rows=d.get("sample_rows", []),
            primary_keys=d.get("primary_keys", []),
            foreign_keys=d.get("foreign_keys", []),
        )


class SchemaExtractor:
    """Extracts complete schema metadata from a connected database."""

    # Skip system/migration tables
    EXCLUDED_TABLE_PATTERNS = [
        "__efmigrations", "abpauditlogs", "abpbackgroundjobs",
        "sysdiagrams", "sqlite_", "pg_", "information_schema",
    ]

    def __init__(self, engine: Engine):
        self.engine = engine

    async def extract(self) -> List[TableInfo]:
        """Extract schema for all user tables (collects all then returns).
        Kept for compatibility — prefer extract_stream() for live progress.
        """
        tables: List[TableInfo] = []
        async for table in self.extract_stream():
            tables.append(table)
        return tables

    async def extract_single_table(
        self, table_name: str, schema: Optional[str] = None
    ) -> Optional[TableInfo]:
        """ONE table only — calls _extract_table directly, never extract_stream."""
        def _run():
            try:
                inspector = inspect(self.engine)
                if self._should_skip(table_name):
                    logger.warning(f"[extract_single] '{table_name}' is in skip list")
                    return None
                logger.info(f"[extract_single] Extracting only: [{schema}].[{table_name}]")
                result = self._extract_table(inspector, table_name, schema)
                logger.info(f"[extract_single] Done: {len(result.columns)} columns")
                return result
            except Exception as e:
                logger.error(f"[extract_single] Failed '{table_name}': {e}", exc_info=True)
                return None
 
        # run_in_executor runs _run() in a thread — never touches extract_stream
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _run)
    
    async def extract_stream(self) -> AsyncGenerator["TableInfo", None]:
        """Yield TableInfo one at a time as each table is extracted.

        Uses Thread + asyncio.Queue so the caller can yield SSE events
        after each table without waiting for the full extraction to finish.
        This is critical for large databases where full extraction takes minutes.
        """
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        logger.info("Start extracting schema")
        def _run_extraction():
            try:
                inspector = inspect(self.engine)
                schemas = self._get_schemas(inspector)

                for schema in schemas:
                    try:
                        table_names = inspector.get_table_names(schema=schema)
                        if not table_names:
                            logger.debug(f"Skipping schema '{schema}' (no tables)")
                            continue
                        logger.info(f"Schema '{schema}' has {len(table_names)} tables")
                    except Exception as e:
                        logger.warning(f"Could not list tables in schema '{schema}': {e}")
                        continue
                    total_tables = len(table_names)
                    processed = 0
                    for table_name in table_names:
                        processed += 1
                        logger.info(
                            f"[{processed}/{total_tables}] Extracting [{schema}].[{table_name}]"
                        )
                        if self._should_skip(table_name):
                            continue
                        try:
                            logger.info(f"Extracting table: [{schema}].[{table_name}]")
                            info = self._extract_table(inspector, table_name, schema)
                            logger.info(
                                f"Finished table: [{schema}].[{table_name}] "
                                f"| {info.row_count:,} rows | {len(info.columns)} columns"
                            )
                            asyncio.run_coroutine_threadsafe(
                                queue.put(info), loop
                            ).result(timeout=30)
                        except Exception as e:
                            logger.warning(f"Failed to extract '{table_name}': {e}")
            except Exception as e:
                logger.error(f"Schema extraction thread error: {e}", exc_info=True)
            finally:
                # Sentinel: None signals extraction is done
                asyncio.run_coroutine_threadsafe(
                    queue.put(None), loop
                ).result(timeout=10)

        thread = threading.Thread(target=_run_extraction, daemon=True)
        thread.start()

        while True:
            item = await queue.get()
            if item is None:   # sentinel — extraction finished
                break
            yield item

    def _extract_sync(self) -> List[TableInfo]:
        """Synchronous full extraction (used internally by extract())."""
        inspector = inspect(self.engine)
        schemas = self._get_schemas(inspector)
        tables: List[TableInfo] = []

        for schema in schemas:
            try:
                table_names = inspector.get_table_names(schema=schema)
            except Exception as e:
                logger.warning(f"Could not list tables in schema '{schema}': {e}")
                continue

            for table_name in table_names:
                if self._should_skip(table_name):
                    continue
                try:
                    info = self._extract_table(inspector, table_name, schema)
                    tables.append(info)
                except Exception as e:
                    logger.warning(f"Failed to extract table '{table_name}': {e}")
                    continue

        logger.info(f"Extracted {len(tables)} tables from database")
        return tables

    def _get_schemas(self, inspector) -> List[Optional[str]]:
        """Get list of schemas to inspect. Returns [None] for single-schema DBs."""
        try:
            schemas = inspector.get_schema_names()
            # Exclude system schemas
            system = {"information_schema", "pg_catalog", "pg_toast", "sys", "INFORMATION_SCHEMA"}
            user_schemas = [s for s in schemas if s not in system]
            return user_schemas if user_schemas else [None]
        except Exception:
            return [None]

    def _should_skip(self, table_name: str) -> bool:
        lower = table_name.lower()
        return any(lower.startswith(p) or lower == p for p in self.EXCLUDED_TABLE_PATTERNS)

    def _extract_table(
        self,
        inspector,
        table_name: str,
        schema: Optional[str]
    ) -> TableInfo:
        # Columns
        raw_columns = inspector.get_columns(table_name, schema=schema)
        pk_constraint = inspector.get_pk_constraint(table_name, schema=schema)
        pk_cols = set(pk_constraint.get("constrained_columns", []))

        raw_fks = inspector.get_foreign_keys(table_name, schema=schema)
        fk_map: Dict[str, Dict] = {}
        for fk in raw_fks:
            for local_col, ref_col in zip(
                fk.get("constrained_columns", []),
                fk.get("referred_columns", [])
            ):
                fk_map[local_col] = {
                    "to_table": fk.get("referred_table", ""),
                    "to_col": ref_col,
                }

        columns = []
        for col in raw_columns:
            col_name = col["name"]
            fk_info = fk_map.get(col_name, {})
            columns.append(ColumnInfo(
                name=col_name,
                data_type=str(col["type"]),
                nullable=col.get("nullable", True),
                default=str(col["default"]) if col.get("default") is not None else None,
                is_primary_key=col_name in pk_cols,
                is_foreign_key=col_name in fk_map,
                fk_target_table=fk_info.get("to_table"),
                fk_target_column=fk_info.get("to_col"),
            ))

        foreign_keys = [
            {"from": local_col, "to_table": info["to_table"], "to_col": info["to_col"]}
            for local_col, info in fk_map.items()
        ]

        # Row count and sample rows
        row_count, sample_rows = self._get_row_count_and_samples(table_name, schema)
        logger.info(f"All Columns : {columns}")
        return TableInfo(
            table_name=table_name,
            schema_name=schema,
            columns=columns,
            row_count=row_count,
            sample_rows=sample_rows,
            primary_keys=list(pk_cols),
            foreign_keys=foreign_keys,
        )

    def _get_row_count_and_samples(
        self,
        table_name: str,
        schema: Optional[str]
    ):
        """Get row count and 3 sample rows using dialect-correct SQL."""
        # SQL Server uses square-bracket quoting and TOP, not LIMIT
        dialect_name = self.engine.dialect.name  # "mssql", "postgresql", "sqlite", "mysql"
        is_mssql = dialect_name == "mssql"

        if is_mssql:
            # SQL Server: use square brackets and TOP syntax
            qualified = f"[{schema}].[{table_name}]" if schema else f"[{table_name}]"
        else:
            qualified = f'"{schema}"."{table_name}"' if schema else f'"{table_name}"'

        row_count = 0
        sample_rows = []

        try:
            # timeout=8: if COUNT(*) or sample query hangs > 8s on remote DB, skip it
            with self.engine.connect().execution_options(timeout=8) as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {qualified}"))
                row_count = result.scalar() or 0
        except Exception as e:
            logger.warning(f"COUNT(*) failed for '{table_name}' (skipped): {e}")

        try:
            if is_mssql:
                sample_sql = f"SELECT TOP 3 * FROM {qualified}"
            else:
                sample_sql = f"SELECT * FROM {qualified} LIMIT 3"

            with self.engine.connect().execution_options(timeout=8) as conn:
                result = conn.execute(text(sample_sql))
                cols = list(result.keys())
                for row in result:
                    sample_rows.append({
                        col: self._serialize_value(val)
                        for col, val in zip(cols, row)
                    })
        except Exception as e:
            logger.warning(f"Sample rows failed for '{table_name}' (skipped): {e}")

        return row_count, sample_rows

    def _serialize_value(self, val: Any) -> Any:
        """Convert non-JSON-serializable types to strings."""
        if val is None:
            return None
        if isinstance(val, (int, float, bool, str)):
            return val
        return str(val)

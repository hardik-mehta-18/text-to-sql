"""Query executor — runs validated SQL against the session's SQLAlchemy engine."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy import text

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    execution_time_ms: float
    error: Optional[str] = None
    service_name: str = ""


class QueryExecutor:
    """Executes SQL queries using a session-scoped SQLAlchemy engine."""

    def __init__(self):
        self.settings = get_settings()

    async def execute(self, engine: Engine, sql: str) -> QueryResult:
        """Execute SQL and return structured result.

        Args:
            engine: SQLAlchemy engine for the session's database
            sql: Validated SQL string

        Returns:
            QueryResult with rows, columns, and timing
        """
        return await asyncio.to_thread(self._execute_sync, engine, sql)

    def _validate_tables_columns(self, engine: Engine, sql: str) -> Optional[str]:
        """Pre-validate tables and columns exist before full execution.
        
        Uses sqlglot AST parsing instead of regex to correctly extract
        table names, preserving case and handling CTEs, subqueries, and
        schema-qualified names without false positives.
        """
        from sqlalchemy import inspect
        import sqlglot
        from sqlglot import exp

        DIALECT_MAP = {
            "sqlite": "sqlite",
            "postgresql": "postgres",
            "mysql": "mysql",
            "sqlserver": "tsql",
        }

        inspector = inspect(engine)
        try:
            sql_lower = sql.lower()
            if 'from' not in sql_lower:
                return "No FROM clause found"

            # Parse with sqlglot to get an accurate AST
            dialect_key = DIALECT_MAP.get(
                getattr(self.settings, 'dialect', 'sqlserver'), 'tsql'
            )
            try:
                tree = sqlglot.parse_one(sql, read=dialect_key)
            except Exception:
                # If AST parsing fails, skip pre-validation rather than blocking
                logger.warning("AST parsing failed during pre-validation; skipping.")
                return None

            # Collect CTE names so we can exclude them from physical table checks
            cte_names = set()
            for cte_node in tree.find_all(exp.CTE):
                if cte_node.alias:
                    cte_names.add(cte_node.alias.lower())

            # Extract all Table nodes from the AST (case-preserved)
            validated_tables = []
            for table_node in tree.find_all(exp.Table):
                t_name = table_node.name  # case-preserved
                if not t_name:
                    continue

                # Skip CTE references — they are not physical tables
                if t_name.lower() in cte_names:
                    continue

                # Extract schema if present (e.g. dbo.BNR_Incidents → schema=dbo)
                db_obj = table_node.args.get("db")
                schema = str(db_obj) if db_obj else None

                try:
                    if not inspector.has_table(t_name, schema):
                        continue  # Could be a function or alias
                    cols = inspector.get_columns(t_name, schema)
                    if not cols:
                        return f"Table not found: {schema + '.' + t_name if schema else t_name}"
                    validated_tables.append(
                        schema + '.' + t_name if schema else t_name
                    )
                except Exception as ex:
                    return (
                        f"Cannot access table "
                        f"{schema + '.' + t_name if schema else t_name}: {str(ex)}"
                    )

            logger.info(f"Pre-validation passed for real tables: {validated_tables}")
            return None
        except Exception as e:
            return f"Validation failed: {str(e)}"

    def _execute_sync(self, engine: Engine, sql: str) -> QueryResult:
        start = time.perf_counter()
        timeout = self.settings.query.query_timeout_seconds

        # Pre-validate schema objects
        validation_error = self._validate_tables_columns(engine, sql)
        if validation_error:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(f"Schema validation failed: {validation_error}")
            return QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=elapsed_ms,
                error=f"SCHEMA ERROR: {validation_error}",
            )

        try:
            with engine.connect() as conn:
                conn = conn.execution_options(timeout=timeout)
                df = pd.read_sql(text(sql), conn)

            elapsed_ms = (time.perf_counter() - start) * 1000

            columns = list(df.columns)
            rows: List[dict[str, Any]] = cast(List[dict[str, Any]], df.to_dict(orient="records"))

            # Convert non-serializable types (dates, decimals, etc.)
            rows = [self._serialize_row(row) for row in rows]

            logger.info(
                f"Query executed: {len(rows)} rows in {elapsed_ms:.1f}ms"
            )

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Query execution failed after {elapsed_ms:.1f}ms: {e}", exc_info=True)
            return QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=elapsed_ms,
                error=str(e),
            )

    def _serialize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert non-JSON-serializable values to strings."""
        result = {}
        for k, v in row.items():
            if pd.isna(v):
                result[k] = None
            elif isinstance(v, (int, float, bool, str)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

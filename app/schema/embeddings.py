"""Schema embeddings generator - creates vector embeddings of table/column descriptions for semantic search."""

import io
import logging
from typing import List, Optional

import numpy as np
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.metadata_models import ServiceMeta, TableMeta, ColumnMeta, get_session

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Generates descriptions and embeddings for database tables."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize embedding generator.

        Args:
            settings: Application settings (uses global if not provided)
        """
        self.settings = settings or get_settings()

        # Configure Gemini Client
        self.client = genai.Client(api_key=self.settings.gemini.api_key)

    async def generate_table_description(
        self,
        service_name: str,
        table_name: str,
        columns: List[ColumnMeta],
        foreign_keys: Optional[List[tuple]] = None
    ) -> str:
        """Use Gemini Flash to generate a 2-sentence description of what a table stores.

        Args:
            service_name: Name of the service
            table_name: Name of the table
            columns: List of ColumnMeta objects
            foreign_keys: Optional list of (source_col, target_table, target_col) tuples

        Returns:
            Generated description (2 sentences max)
        """
        # Build column list
        col_list = []
        for col in columns:
            col_str = f"{col.name} ({col.data_type})"
            if col.is_primary_key:
                col_str += " [PK]"
            if col.column_comment:
                col_str += f" - {col.column_comment}"
            col_list.append(col_str)

        # Build FK list
        fk_list = []
        if foreign_keys:
            for source_col, target_table, target_col in foreign_keys:
                fk_list.append(f"{source_col} -> {target_table}.{target_col}")

        # Build prompt
        prompt = f"""You are a database documentation expert. Generate a concise 2-sentence description of what this database table stores and its purpose.

Service: {service_name}
Table: {table_name}

Columns:
{chr(10).join(f"- {c}" for c in col_list)}
"""

        if fk_list:
            prompt += f"""
Foreign Keys:
{chr(10).join(f"- {fk}" for fk in fk_list)}
"""

        prompt += """
Provide a concise 2-sentence description. Focus on what data the table contains and its business purpose."""

        try:
            response = await self._generate_content_async(prompt)
            description = response.text.strip()

            # Ensure it's not too long
            if len(description) > 500:
                description = description[:497] + "..."

            logger.debug(f"Generated description for {table_name}: {description}")
            return description

        except Exception as e:
            logger.warning(f"Failed to generate description for {table_name}: {e}")
            return f"Table storing {table_name} data"

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector via Gemini text-embedding-004.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        try:
            import asyncio
            result = await asyncio.to_thread(
                self.client.models.embed_content,
                model=self.settings.gemini.embedding_model,
                contents=text,
                config=types.EmbedContentConfig(
                    task_type="retrieval_document"
                )
            )
            return result.embeddings[0].values

        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    async def update_embeddings_for_service(
        self,
        service_name: str,
        session: Optional[Session] = None
    ) -> None:
        """Generate/update descriptions and embeddings for all tables in a service.

        Only processes tables that are missing descriptions or embeddings.

        Args:
            service_name: Name of the service
            session: SQLAlchemy session (creates new one if not provided)
        """
        logger.info(f"Updating embeddings for service '{service_name}'")

        async def _update_in_session(sess: Session):
            # Get service
            service = sess.query(ServiceMeta).filter_by(service_name=service_name).first()
            if not service:
                logger.warning(f"Service '{service_name}' not found in metadata")
                return

            # Get tables needing embeddings
            tables = sess.query(TableMeta).filter_by(service_id=service.id).filter(
                (TableMeta.description.is_(None)) | (TableMeta.embedding.is_(None))
            ).all()

            if not tables:
                logger.info(f"All tables in '{service_name}' already have embeddings")
                return

            logger.info(f"Processing {len(tables)} tables in '{service_name}'")

            for table in tables:
                try:
                    await self._process_table_embedding(sess, service_name, table)
                except Exception as e:
                    logger.error(
                        f"Failed to process table {table.table_name}: {e}",
                        exc_info=True
                    )
                    continue

            sess.commit()
            logger.info(f"Updated embeddings for {len(tables)} tables in '{service_name}'")

        if session:
            await _update_in_session(session)
        else:
            with get_session() as sess:
                await _update_in_session(sess)

    async def _process_table_embedding(
        self,
        session: Session,
        service_name: str,
        table: TableMeta
    ) -> None:
        """Process a single table: generate description and embedding.

        Args:
            session: SQLAlchemy session
            service_name: Name of the service
            table: TableMeta object
        """
        # Get columns
        columns = session.query(ColumnMeta).filter_by(table_id=table.id).order_by(
            ColumnMeta.ordinal_position
        ).all()

        # Get foreign keys
        fk_columns = [c for c in columns if c.is_foreign_key]
        foreign_keys = [
            (c.name, c.fk_target_table, c.fk_target_column)
            for c in fk_columns
            if c.fk_target_table and c.fk_target_column
        ]

        # Generate description if missing
        if not table.description:
            description = await self.generate_table_description(
                service_name,
                table.table_name,
                columns,
                foreign_keys if foreign_keys else None
            )
            table.description = description

        # Build embedding text
        embedding_text = self._build_embedding_text(service_name, table, columns)

        # Generate embedding
        embedding_vec = await self.generate_embedding(embedding_text)

        # Serialize to bytes
        embedding_bytes = self._serialize_embedding(embedding_vec)
        table.embedding = embedding_bytes

        logger.debug(f"Updated embedding for {table.table_name}")

    def _build_embedding_text(self, service_name: str, table: TableMeta, columns: List[ColumnMeta]) -> str:
        """Build text representation for embedding.

        Args:
            service_name: Name of the service
            table: TableMeta object
            columns: List of ColumnMeta objects

        Returns:
            Text to embed
        """
        parts = []

        # Service and table name
        parts.append(f"Service: {service_name}. Table: {table.table_name}")

        # Description
        if table.description:
            parts.append(table.description)

        # Column names and types
        col_parts = []
        for col in columns:
            col_str = f"{col.name} ({col.data_type})"
            if col.column_comment:
                col_str += f": {col.column_comment}"
            col_parts.append(col_str)

        if col_parts:
            parts.append("Columns: " + ", ".join(col_parts))

        return " | ".join(parts)

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        """Serialize embedding vector to bytes.

        Args:
            embedding: Embedding vector

        Returns:
            Serialized bytes
        """
        buffer = io.BytesIO()
        np.save(buffer, np.array(embedding, dtype=np.float32), allow_pickle=False)
        return buffer.getvalue()

    async def _generate_content_async(self, prompt: str):
        """Generate content asynchronously using Gemini.

        Args:
            prompt: The prompt text

        Returns:
            Generated response
        """
        # Note: We run synchronous SDK calls in thread pools using asyncio.to_thread
        import asyncio
        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.settings.gemini.model,
            contents=prompt
        )

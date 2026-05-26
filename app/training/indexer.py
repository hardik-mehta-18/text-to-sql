import asyncio
import json
import logging
import os
import hashlib
from typing import Any, Dict, List, Optional
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

from app.config import get_settings
from app.training.schema_extractor import TableInfo
from app.utils.gemini_key_manager import get_key_manager

logger = logging.getLogger(__name__)
EMBEDDING_DIM = 3072


def build_db_hash(db_key: str) -> str:
    return hashlib.sha256(db_key.encode()).hexdigest()


class Indexer:
    def __init__(self):
        self.settings = get_settings()
        q = self.settings.qdrant

        # Connect to Qdrant server using env-based config
        self.client = QdrantClient(
            url=q.url,
            api_key=q.api_key,
            prefer_grpc=False,
        )

    # --------------------------------------------------------
    def cache_dir(self, db_hash: str):
        return f"uploads/cache/{db_hash}"

    def collection_name(self, db_hash: str):
        return f"{self.settings.qdrant.collection_prefix}_{db_hash}"

    def collection_exists(self, collection_name: str) -> bool:
        """Return True if the Qdrant collection already exists."""
        try:
            collections = self.client.get_collections().collections
            return any(c.name == collection_name for c in collections)
        except Exception as e:
            logger.warning(f"Could not check Qdrant collections: {e}")
            return False

    async def delete_collection(self, collection_name: str):
        """Deletes the collection from Qdrant if it exists."""
        try:
            if self.collection_exists(collection_name):
                self.client.delete_collection(collection_name=collection_name)
                logger.info(f"Deleted collection: {collection_name}")
        except Exception as e:
            logger.warning(f"Failed to delete collection {collection_name}: {e}")
    def _load_overrides(self) -> Dict[str, str]:
        override_file = os.path.join(os.path.dirname(__file__), "descriptions_override.json")
        if os.path.exists(override_file):
            try:
                with open(override_file, "r") as f:
                    overrides = json.load(f)
                    logger.info(f"Loaded {len(overrides)} custom table description overrides.")
                    return overrides
            except Exception as e:
                logger.warning(f"Failed to load table description overrides: {e}")
        return {}

    # --------------------------------------------------------
    async def index(
        self,
        db_key: str,
        tables: List[TableInfo],
        descriptions: Dict[str, str],
        dialect: str,
        qdrant_collection: str
    ):
        # Apply custom overrides
        overrides = self._load_overrides()
        print(f"Applying {len(overrides)} description overrides")
        for k, v in overrides.items():
            descriptions[k] = v

        db_hash = build_db_hash(db_key)
        collection = qdrant_collection

        # Create cache dir
        cache_dir = self.cache_dir(db_hash)
        os.makedirs(cache_dir, exist_ok=True)

        # Save cache
        with open(f"{cache_dir}/descriptions.json", "w") as f:
            json.dump(descriptions, f, indent=2)

        with open(f"{cache_dir}/tables.json", "w") as f:
            json.dump([t.to_dict() for t in tables], f, indent=2)

        # Check if collection exists, create if not
        if not self.collection_exists(collection):
            self.client.recreate_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE)
            )
            # ← Add this: create payload index so filter-deletes work
            self.client.create_payload_index(
                collection_name=collection,
                field_name="table_name",
                field_schema="keyword",
            )
            logger.info(f"Created collection + payload index: {collection}")
        else:
            logger.info(f"Collection already exists: {collection}")

        reverse_fk_map: dict[str, list[dict]] = {}
        for table in tables:
            for fk in table.foreign_keys:          # {"from": col, "to_table": t, "to_col": c}
                target = fk["to_table"]
                reverse_fk_map.setdefault(target, []).append({
                    "referencing_table": table.table_name,
                    "referencing_col":   fk["from"],
                    "local_col":         fk["to_col"],
                })
        logger.info(f"Built reverse FK map for {len(reverse_fk_map)} tables")

        # Build points
        points: List[PointStruct] = []
        for table in tables:
            description = descriptions.get(table.table_name, "")
            embed_text = self._build_embed_text(table, description)

            logger.info(f"Embedding table {table.table_name}")
            embedding = await self._embed(embed_text)
            logger.info(f"Vector length: {len(embedding)}")
            rfks = reverse_fk_map.get(table.table_name, [])
            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector=embedding,
                    payload={
                        "table_name": table.table_name,
                        "schema_name": table.schema_name,
                        "dialect": dialect,
                        "description": description,
                        "row_count": table.row_count,
                        "columns": [
                            {"name": c.name, "type": c.data_type, "nullable": c.nullable}
                            for c in table.columns
                        ],
                        "foreign_keys": table.foreign_keys,
                        "reverse_foreign_keys": rfks,
                    }
                )
            )

        # Upsert points in batch
        if points:
            batch_size = 50
            for i in range(0, len(points), batch_size):
                batch = points[i:i+batch_size]
                self.client.upsert(collection_name=collection, points=batch)
                logger.info(f"Upserted batch {i//batch_size+1} → {len(batch)} points")

        logger.info("Indexing complete")

    # --------------------------------------------------------
    async def search(self, collection_name:str, question: str, top_k: int = 7):

        embedding = await self._embed(question)

        results = self.client.query_points(
            collection_name=collection_name,
            query=embedding,
            limit=top_k,
            with_payload=True
        )
        return [r.payload for r in results.points]

    # --------------------------------------------------------
    async def load_cached_descriptions(self, db_key: str):
        db_hash = build_db_hash(db_key)
        desc_file = f"{self.cache_dir(db_hash)}/descriptions.json"

        if os.path.exists(desc_file):
            with open(desc_file) as f:
                logger.info("Loaded cached descriptions")
                return json.load(f)
        return None

    # --------------------------------------------------------
    async def _embed(self, text: str):
        return await get_key_manager().embed_content(
            text=text,
            task_type="retrieval_document"
        )

    # --------------------------------------------------------
    def _build_embed_text(self, table: TableInfo, description: str):
        col_names = " ".join(c.name for c in table.columns)
        fk_info = " ".join(f"{fk['from']}->{fk['to_table']}" for fk in table.foreign_keys)

        parts = [f"Table {table.table_name}", description, f"Columns {col_names}"]
        if fk_info:
            parts.append(f"Joins {fk_info}")
        return " | ".join(parts)
    
    async def get_by_table_name(
        self, collection_name: str, table_name: str
    ) -> Optional[dict]:
        """
        Fetch a table's payload by exact table_name metadata match.
        Much more reliable than semantic search for known table names.
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        try:
            results, _ = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="table_name",
                            match=MatchValue(value=table_name),
                        )
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False,  # we don't need the vector, saves bandwidth
            )
            if results:
                logger.info(
                    f"[indexer] get_by_table_name: found '{table_name}' "
                    f"with {len(results[0].payload.get('columns', []))} columns"
                )
                return results[0].payload
            logger.warning(f"[indexer] get_by_table_name: '{table_name}' not found in '{collection_name}'")
            return None
        except Exception as e:
            logger.error(f"[indexer] get_by_table_name failed for '{table_name}': {e}", exc_info=True)
            return None
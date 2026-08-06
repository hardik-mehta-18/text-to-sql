import asyncio
import logging
from typing import List, Dict, Any, Optional

from app.db.metadata_models import AppDBSession, LLMConfig

logger = logging.getLogger(__name__)

# Default configurations to seed if the database is empty
DEFAULTS = [
    {
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "max_tokens": 8192,
        "temperature": 0.1,
        "timeout_seconds": 60,
        "sequence_order": 1,
        "is_enabled": True,
    },
    {
        "provider": "openrouter",
        "model": "google/gemini-3.6-flash",
        "max_tokens": 512,
        "temperature": 0.3,
        "timeout_seconds": 60,
        "sequence_order": 2,
        "is_enabled": True,
    },
    {
        "provider": "cerebras",
        "model": "gpt-oss-120b",
        "max_tokens": 512,
        "temperature": 0.3,
        "timeout_seconds": 60,
        "sequence_order": 3,
        "is_enabled": True,
    },
    {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "max_tokens": 512,
        "temperature": 0.3,
        "timeout_seconds": 60,
        "sequence_order": 4,
        "is_enabled": True,
    },
]

# In-memory cache
_cached_configs: Optional[List[Dict[str, Any]]] = None
_config_lock = asyncio.Lock()


def _to_dict(config: LLMConfig) -> Dict[str, Any]:
    """Helper to serialize model instance to a dictionary."""
    return {
        "id": config.id,
        "provider": config.provider,
        "model": config.model,
        "api_url": config.api_url,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "timeout_seconds": config.timeout_seconds,
        "sequence_order": config.sequence_order,
        "is_enabled": config.is_enabled,
        "api_key": config.api_key,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


async def get_active_llm_configs() -> List[Dict[str, Any]]:
    """Return all active configurations sorted by sequence_order.

    Loads from cache if populated, otherwise queries DB (and seeds if empty).
    """
    global _cached_configs
    async with _config_lock:
        if _cached_configs is not None:
            return _cached_configs

        # Load from database
        configs = await asyncio.to_thread(_load_and_seed_configs)
        _cached_configs = configs
        return _cached_configs


def _load_and_seed_configs() -> List[Dict[str, Any]]:
    """Synchronous database loader & seeder run in a thread pool."""
    db = AppDBSession()
    try:
        results = db.query(LLMConfig).order_by(LLMConfig.sequence_order).all()
        if not results:
            logger.info("llm_configs table is empty. Seeding defaults...")
            for d in DEFAULTS:
                cfg = LLMConfig(**d)
                db.add(cfg)
            db.commit()
            # Query again to get full autoincremented database records
            results = db.query(LLMConfig).order_by(LLMConfig.sequence_order).all()

        return [_to_dict(r) for r in results if r.is_enabled]
    except Exception as e:
        logger.error(f"Error loading LLM configs from database: {e}")
        # Return fallback static defaults so the app doesn't crash on DB issues
        return [d for d in DEFAULTS if d["is_enabled"]]
    finally:
        db.close()


async def get_all_llm_configs() -> List[Dict[str, Any]]:
    """Return all configurations (including disabled ones) sorted by sequence_order.

    Queries database directly to get the current administrative state.
    """
    return await asyncio.to_thread(_load_all_configs)


def _load_all_configs() -> List[Dict[str, Any]]:
    db = AppDBSession()
    try:
        results = db.query(LLMConfig).order_by(LLMConfig.sequence_order).all()
        if not results:
            logger.info("llm_configs table is empty. Seeding defaults...")
            for d in DEFAULTS:
                cfg = LLMConfig(**d)
                db.add(cfg)
            db.commit()
            results = db.query(LLMConfig).order_by(LLMConfig.sequence_order).all()
        return [_to_dict(r) for r in results]
    finally:
        db.close()


async def update_llm_configs(configs_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Update configurations in the database and clear the in-memory cache."""
    global _cached_configs
    async with _config_lock:
        updated = await asyncio.to_thread(_save_configs, configs_list)
        # Clear in-memory cache to force reload on the next query
        _cached_configs = None
        return updated


def _save_configs(configs_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    db = AppDBSession()
    try:
        for item in configs_list:
            provider = item.get("provider")
            if not provider:
                continue

            cfg = db.query(LLMConfig).filter(LLMConfig.provider == provider).first()
            if not cfg:
                cfg = LLMConfig(provider=provider)
                db.add(cfg)

            cfg.model = item.get("model", cfg.model)
            cfg.max_tokens = int(item.get("max_tokens", cfg.max_tokens))
            cfg.temperature = float(item.get("temperature", cfg.temperature))
            cfg.timeout_seconds = int(item.get("timeout_seconds", cfg.timeout_seconds))
            cfg.sequence_order = int(item.get("sequence_order", cfg.sequence_order))
            cfg.is_enabled = bool(item.get("is_enabled", cfg.is_enabled))
            
            # API Key can be modified
            if "api_key" in item:
                cfg.api_key = item["api_key"]

        db.commit()
        results = db.query(LLMConfig).order_by(LLMConfig.sequence_order).all()
        return [_to_dict(r) for r in results]
    except Exception as e:
        logger.error(f"Error saving LLM configs to database: {e}")
        db.rollback()
        raise
    finally:
        db.close()

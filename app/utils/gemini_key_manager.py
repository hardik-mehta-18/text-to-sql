"""Gemini API key rotation manager.

Supports up to 13 API keys. When a key hits its quota (RPD/RPM limit),
automatically rotates to the next key and retries the call transparently.

Keys are loaded from environment variables:
  GEMINI_API_KEY_1, GEMINI_API_KEY_2, ... GEMINI_API_KEY_13
  (Falls back to GEMINI_API_KEY for single-key setups)

Usage:
    from app.utils.gemini_key_manager import get_key_manager

    km = get_key_manager()
    response = await km.generate_content(prompt, generation_config=...)
    embedding = await km.embed_content(model, text, task_type=...)
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Quota-related error indicators (caught to trigger key rotation)
_QUOTA_SIGNALS = (
    "quota",
    "429",
    "resource_exhausted",
    "resourceexhausted",
    "rate limit",
    "ratelimit",
    "too many requests",
    "daily limit",
    "per day",
)


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception looks like a quota / rate-limit error."""
    msg = str(exc).lower()
    return any(signal in msg for signal in _QUOTA_SIGNALS)


class GeminiKeyManager:
    """Round-robin API key manager with automatic quota-triggered rotation.

    Thread-safe via asyncio.Lock — safe to use from concurrent coroutines.
    """

    MAX_KEYS = 13

    def __init__(self, keys: List[str], model_name: str, embedding_model: str):
        if not keys:
            raise ValueError("At least one Gemini API key is required.")
        self._keys = keys
        self._model_name = model_name
        self._embedding_model = embedding_model
        self._index = 0                          # current active key index
        self._exhausted: set[int] = set()        # indices that hit quota today
        self._lock = asyncio.Lock()
        
        # Initialize Client wrappers for each key with attempts=1 (0 retries) to enable immediate failover
        http_opts = types.HttpOptions(
            timeout=30_000,  # 30 seconds
            retry_options=types.HttpRetryOptions(
                attempts=1
            )
        )
        self._clients = [
            genai.Client(api_key=k, http_options=http_opts)
            for k in keys
        ]
        
        logger.info(
            f"GeminiKeyManager initialised with {len(keys)} key(s). "
            f"Model: {model_name}"
        )

    # ── Public API ────────────────────────────────────────────────────────

    async def generate_content(
        self,
        prompt: str,
        generation_config: Optional[Any] = None,
        model_name: Optional[str] = None,
    ) -> Any:
        """Call Gemini generate_content, rotating keys on quota errors.

        Args:
            prompt: The prompt string
            generation_config: Optional genai.GenerationConfig
            model_name: Optional model override string

        Returns:
            Gemini response object

        Raises:
            RuntimeError: If all keys are exhausted
        """
        return await self._call_with_rotation(
            self._do_generate, prompt, generation_config, model_name
        )

    async def embed_content(
        self,
        text: str,
        task_type: str = "retrieval_document",
    ) -> List[float]:
        """Call Gemini embed_content, rotating keys on quota errors.

        Args:
            text: Text to embed
            task_type: Gemini task type string

        Returns:
            Embedding vector (list of floats)

        Raises:
            RuntimeError: If all keys are exhausted
        """
        return await self._call_with_rotation(
            self._do_embed, text, task_type
        )

    @property
    def active_key_index(self) -> int:
        return self._index

    @property
    def keys_remaining(self) -> int:
        return len(self._keys) - len(self._exhausted)

    # ── Internal rotation logic ───────────────────────────────────────────

    async def _call_with_rotation(self, fn, *args) -> Any:
        """Try fn(*args) with each key, rotating on quota errors."""
        # Reset exhausted set at the start of a new full cycle
        async with self._lock:
            available = [i for i in range(len(self._keys)) if i not in self._exhausted]
            if not available:
                logger.warning("All keys were exhausted — resetting for a new attempt.")
                self._exhausted.clear()
                available = list(range(len(self._keys)))

        # Try each available key once
        last_exc: Optional[Exception] = None
        tried: set[int] = set()

        while True:
            async with self._lock:
                available = [i for i in range(len(self._keys)) if i not in self._exhausted and i not in tried]
                if not available:
                    break
                key_index = self._index if self._index in available else available[0]
                self._index = key_index

            key = self._keys[key_index]
            tried.add(key_index)

            try:
                self._configure(key)
                result = await asyncio.to_thread(fn, *args)
                # Success — keep using this key
                return result

            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc):
                    async with self._lock:
                        self._exhausted.add(key_index)
                        # Advance to next key
                        remaining = [i for i in range(len(self._keys)) if i not in self._exhausted]
                        if remaining:
                            self._index = remaining[0]
                            logger.warning(
                                f"Key #{key_index + 1} quota exceeded. "
                                f"Rotating to key #{self._index + 1}. "
                                f"{len(remaining)} key(s) remaining."
                            )
                        else:
                            logger.error("All Gemini API keys have hit their quota.")
                else:
                    # Non-quota error — don't rotate, just re-raise
                    raise

        raise RuntimeError(
            f"All {len(self._keys)} Gemini API key(s) have hit their quota. "
            f"Last error: {last_exc}"
        )

    def _configure(self, key: str) -> None:
        """No-op for backward compatibility (using pre-initialized Clients instead)."""
        pass

    def _do_generate(self, prompt: str, generation_config: Optional[Any], model_name: Optional[str] = None) -> Any:
        """Synchronous Gemini generate_content call (runs in thread pool)."""
        client = self._clients[self._index]
        
        config = types.GenerateContentConfig()
        if generation_config is not None:
            if hasattr(generation_config, "temperature") and generation_config.temperature is not None:
                config.temperature = generation_config.temperature
            if hasattr(generation_config, "max_output_tokens") and generation_config.max_output_tokens is not None:
                config.max_output_tokens = generation_config.max_output_tokens
                
        target_model = model_name if model_name is not None else self._model_name
        return client.models.generate_content(
            model=target_model,
            contents=prompt,
            config=config,
        )

    def _do_embed(self, text: str, task_type: str) -> List[float]:
        """Synchronous Gemini embed_content call (runs in thread pool)."""
        client = self._clients[self._index]
        result = client.models.embed_content(
            model=self._embedding_model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type
            )
        )
        if hasattr(result, "embeddings") and result.embeddings:
            return result.embeddings[0].values
        raise ValueError(f"Unexpected embedding response structure: {result}")


# ── Singleton factory ─────────────────────────────────────────────────────

_manager: Optional[GeminiKeyManager] = None


def _load_keys() -> List[str]:
    """Load all GEMINI_API_KEY_1 … GEMINI_API_KEY_13 from environment.

    Also accepts the legacy GEMINI_API_KEY as a fallback.
    Duplicate keys are deduplicated.
    """
    keys: List[str] = []
    seen: set[str] = set()

    # Try numbered keys first
    for i in range(1, GeminiKeyManager.MAX_KEYS + 1):
        val = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if val and val not in seen:
            keys.append(val)
            seen.add(val)

    # Fallback to plain GEMINI_API_KEY
    fallback = os.getenv("GEMINI_API_KEY", "").strip()
    if fallback and fallback not in seen:
        keys.append(fallback)

    return keys


def get_key_manager() -> GeminiKeyManager:
    """Return the global GeminiKeyManager singleton (lazy-init)."""
    global _manager
    if _manager is None:
        from app.config import get_settings
        settings = get_settings()
        keys = _load_keys()
        if not keys:
            raise RuntimeError(
                "No Gemini API keys found. "
                "Set GEMINI_API_KEY or GEMINI_API_KEY_1 … GEMINI_API_KEY_13 in .env"
            )
        _manager = GeminiKeyManager(
            keys=keys,
            model_name=settings.gemini.model,
            embedding_model=settings.gemini.embedding_model,
        )
    return _manager

"""Multi-provider LLM fallback module.

Fallback order:  OpenRouter → Gemini (multi-key) → Groq (multi-key) → Cerebras

Usage (drop-in replacement for get_key_manager().generate_content()):
    from app.utils.llm_provider import generate_with_fallback

    result = await generate_with_fallback(prompt, temperature=0.7, max_output_tokens=300)
    # result is already a string (no .text.strip() needed)
"""

import asyncio
import json
import logging
import os
import time as _time
from typing import Any, Dict, List, Optional

import requests

from app.utils.gemini_key_manager import get_key_manager

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration — loaded from environment
# ═══════════════════════════════════════════════════════════════════════════

# OpenRouter (single key)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")

# Groq (multi-key rotation)
_GROQ_KEYS: List[str] = []
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Cerebras (single key)
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = "gpt-oss-120b"


def _load_groq_keys() -> List[str]:
    """Load GROQ_API_KEY_1, GROQ_API_KEY_2, ... from environment.
    Falls back to GROQ_API_KEY for single-key setups.
    """
    keys: List[str] = []
    seen: set = set()
    for i in range(1, 14):
        val = os.getenv(f"GROQ_API_KEY_{i}", "").strip()
        if val and val not in seen:
            keys.append(val)
            seen.add(val)
    # Fallback to plain GROQ_API_KEY
    fallback = os.getenv("GROQ_API_KEY", "").strip()
    if fallback and fallback not in seen:
        keys.append(fallback)
    return keys


def _init_groq_keys():
    """Lazy-init Groq keys on first use."""
    global _GROQ_KEYS
    if not _GROQ_KEYS:
        _GROQ_KEYS = _load_groq_keys()
    return _GROQ_KEYS


# ═══════════════════════════════════════════════════════════════════════════
#  Groq key rotation state
# ═══════════════════════════════════════════════════════════════════════════

_groq_key_index = 0
_groq_exhausted: set = set()
_groq_lock = asyncio.Lock()

_QUOTA_SIGNALS = (
    "rate_limit", "rate limit", "429", "quota", "resource_exhausted",
    "too many requests", "limit exceeded", "tokens per minute",
)


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(signal in msg for signal in _QUOTA_SIGNALS)


# ═══════════════════════════════════════════════════════════════════════════
#  Token estimation (for logging only)
# ═══════════════════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def _estimate_message_tokens(messages: List[Dict]) -> int:
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


# ═══════════════════════════════════════════════════════════════════════════
#  LLM call tracking (logging only)
# ═══════════════════════════════════════════════════════════════════════════

def track_llm_call(
    label: str,
    provider: str,
    model: str = "",
    *,
    is_fallback: bool = False,
    latency_ms: float = 0,
    prompt_tokens: int = 0,
    response_tokens: int = 0,
    total_tokens: int = 0,
    error: str = "",
):
    """Log LLM call details for observability."""
    extra = {
        "label": label,
        "provider": provider,
        "model": model,
        "is_fallback": is_fallback,
        "latency_ms": round(latency_ms, 1),
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": total_tokens,
    }
    if error:
        extra["error"] = error
        logger.warning("llm_call_tracked", extra=extra)
    else:
        logger.info("llm_call_tracked", extra=extra)


# ═══════════════════════════════════════════════════════════════════════════
#  Provider: OpenRouter (single key)
# ═══════════════════════════════════════════════════════════════════════════

def openrouter_chat_completion(
    messages: List[Dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """Send a chat completion to OpenRouter. Raises RuntimeError on failure."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured")
    try:
        resp = requests.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        return data["choices"][0]["message"]["content"].strip()
    except requests.Timeout:
        raise RuntimeError("OpenRouter request timed out")
    except requests.RequestException as e:
        raise RuntimeError(f"OpenRouter request failed: {e}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"OpenRouter returned invalid response: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  Provider: Gemini (multi-key via existing GeminiKeyManager)
# ═══════════════════════════════════════════════════════════════════════════

async def gemini_chat_completion(
    messages: List[Dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """Send a chat completion to Gemini via the existing GeminiKeyManager.

    Converts OpenAI-style messages to a single prompt string since the key
    manager's generate_content expects a prompt string.
    """
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig

    # Build a single prompt from messages
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.insert(0, content)  # system goes first
        else:
            parts.append(content)
    prompt = "\n\n".join(parts)

    try:
        km = get_key_manager()
        response = await km.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        if response.text is None:
            raise RuntimeError("Gemini returned empty response (content blocked)")
        return response.text.strip()
    except Exception as e:
        raise RuntimeError(f"Gemini failed: {e}") from e


# ═══════════════════════════════════════════════════════════════════════════
#  Provider: Groq (multi-key rotation)
# ═══════════════════════════════════════════════════════════════════════════

def _groq_single_call(
    api_key: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
) -> str:
    """Make a single Groq API call with a specific key."""
    resp = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Groq error: {data['error']}")
    return data["choices"][0]["message"]["content"].strip()


async def groq_chat_completion(
    messages: List[Dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """Send a chat completion to Groq with multi-key rotation.
    Rotates to the next key on rate-limit / quota errors.
    """
    global _groq_key_index, _groq_exhausted

    keys = _init_groq_keys()
    if not keys:
        raise RuntimeError("No Groq API keys configured")

    async with _groq_lock:
        available = [i for i in range(len(keys)) if i not in _groq_exhausted]
        if not available:
            logger.warning("All Groq keys exhausted — resetting")
            _groq_exhausted.clear()
            available = list(range(len(keys)))

    last_exc: Optional[Exception] = None
    tried: set = set()

    while True:
        async with _groq_lock:
            available = [i for i in range(len(keys)) if i not in _groq_exhausted and i not in tried]
            if not available:
                break
            key_index = _groq_key_index if _groq_key_index in available else available[0]
            _groq_key_index = key_index

        key = keys[key_index]
        tried.add(key_index)

        try:
            result = await asyncio.to_thread(
                _groq_single_call, key, messages, temperature, max_tokens
            )
            return result
        except Exception as exc:
            last_exc = exc
            if _is_quota_error(exc):
                async with _groq_lock:
                    _groq_exhausted.add(key_index)
                    remaining = [i for i in range(len(keys)) if i not in _groq_exhausted]
                    if remaining:
                        _groq_key_index = remaining[0]
                        logger.warning(
                            f"Groq key #{key_index + 1} quota exceeded. "
                            f"Rotating to key #{_groq_key_index + 1}. "
                            f"{len(remaining)} key(s) remaining."
                        )
                    else:
                        logger.error("All Groq API keys have hit their quota.")
            else:
                raise

    raise RuntimeError(
        f"All {len(keys)} Groq API key(s) have hit their quota. Last error: {last_exc}"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Provider: Cerebras (single key)
# ═══════════════════════════════════════════════════════════════════════════

def cerebras_chat_completion(
    messages: List[Dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """Send a chat completion to Cerebras. Raises RuntimeError on failure."""
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not configured")
    try:
        resp = requests.post(
            CEREBRAS_API_URL,
            headers={
                "Authorization": f"Bearer {CEREBRAS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": CEREBRAS_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Cerebras error: {data['error']}")
        return data["choices"][0]["message"]["content"].strip()
    except requests.Timeout:
        raise RuntimeError("Cerebras request timed out")
    except requests.RequestException as e:
        raise RuntimeError(f"Cerebras request failed: {e}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Cerebras returned invalid response: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  Unified 4-tier fallback: OpenRouter → Gemini → Groq → Cerebras
# ═══════════════════════════════════════════════════════════════════════════

async def llm_chat_completion(
    messages: List[Dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 512,
    label: str = "llm",
) -> str:
    """Reusable 4-tier fallback: OpenRouter → Gemini → Groq → Cerebras.

    Args:
        messages:    OpenAI-style message list [{"role": ..., "content": ...}].
        temperature: Sampling temperature.
        max_tokens:  Max response tokens.
        label:       Log label to identify the caller (e.g. "planner", "generator").

    Returns:
        The LLM response as a stripped string.

    Raises:
        RuntimeError: If all providers fail.
    """
    prompt_tokens = _estimate_message_tokens(messages)
    or_err = gemini_err = groq_err = cerebras_err = None

    #--- 1) Primary: OpenRouter ---
    if OPENROUTER_API_KEY:
        try:
            logger.info(f"{label}_attempt_openrouter", extra={"prompt_tokens_est": prompt_tokens})
            t0 = _time.time()
            result = await asyncio.to_thread(
                openrouter_chat_completion, messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            ms = (_time.time() - t0) * 1000
            response_tokens = _estimate_tokens(result)
            logger.info(f"{label}_method", extra={"method": "openrouter", "latency_ms": round(ms, 1)})
            track_llm_call(label, "openrouter", model=OPENROUTER_MODEL, latency_ms=ms,
                           prompt_tokens=prompt_tokens, response_tokens=response_tokens,
                           total_tokens=prompt_tokens + response_tokens)
            return result
        except Exception as e:
            or_err = e
            logger.warning(f"{label}_openrouter_failed", extra={"error": str(e), "fallback": "gemini"})
            track_llm_call(label, "openrouter", model=OPENROUTER_MODEL, error=str(e), prompt_tokens=prompt_tokens)
    else:
        logger.debug(f"{label}_openrouter_skipped (no key)")

    # --- 2) Fallback: Gemini (multi-key rotation) ---
    try:
        logger.info(f"{label}_attempt_gemini", extra={"prompt_tokens_est": prompt_tokens})
        t0 = _time.time()
        result = await gemini_chat_completion(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
        ms = (_time.time() - t0) * 1000
        response_tokens = _estimate_tokens(result)
        logger.info(f"{label}_method", extra={"method": "gemini", "latency_ms": round(ms, 1)})
        track_llm_call(label, "gemini", model="gemini-key-manager", is_fallback=bool(or_err),
                       latency_ms=ms, prompt_tokens=prompt_tokens, response_tokens=response_tokens,
                       total_tokens=prompt_tokens + response_tokens)
        return result
    except Exception as e:
        gemini_err = e
        logger.warning(f"{label}_gemini_failed", extra={"error": str(e), "fallback": "groq"})
        track_llm_call(label, "gemini", model="gemini-key-manager", is_fallback=True,
                       error=str(e), prompt_tokens=prompt_tokens)

    # --- 3) Fallback: Groq (multi-key rotation) ---
    try:
        logger.info(f"{label}_attempt_groq", extra={"prompt_tokens_est": prompt_tokens})
        t0 = _time.time()
        result = await groq_chat_completion(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
        ms = (_time.time() - t0) * 1000
        response_tokens = _estimate_tokens(result)
        logger.info(f"{label}_method", extra={"method": "groq", "latency_ms": round(ms, 1)})
        track_llm_call(label, "groq", model=GROQ_MODEL, is_fallback=True, latency_ms=ms,
                       prompt_tokens=prompt_tokens, response_tokens=response_tokens,
                       total_tokens=prompt_tokens + response_tokens)
        return result
    except Exception as e:
        groq_err = e
        logger.warning(f"{label}_groq_failed", extra={"error": str(e), "fallback": "cerebras"})
        track_llm_call(label, "groq", model=GROQ_MODEL, is_fallback=True, error=str(e),
                       prompt_tokens=prompt_tokens)

    # --- 4) Last resort: Cerebras ---
    if CEREBRAS_API_KEY:
        try:
            logger.info(f"{label}_attempt_cerebras", extra={"prompt_tokens_est": prompt_tokens})
            t0 = _time.time()
            result = await asyncio.to_thread(
                cerebras_chat_completion, messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            ms = (_time.time() - t0) * 1000
            response_tokens = _estimate_tokens(result)
            logger.info(f"{label}_method", extra={"method": "cerebras", "latency_ms": round(ms, 1)})
            track_llm_call(label, "cerebras", model=CEREBRAS_MODEL, is_fallback=True,
                           latency_ms=ms, prompt_tokens=prompt_tokens, response_tokens=response_tokens,
                           total_tokens=prompt_tokens + response_tokens)
            return result
        except Exception as e:
            cerebras_err = e
            logger.error(f"{label}_all_providers_failed", extra={
                "openrouter_err": str(or_err), "gemini_err": str(gemini_err),
                "groq_err": str(groq_err), "cerebras_err": str(cerebras_err),
            })
            track_llm_call(label, "cerebras", model=CEREBRAS_MODEL, is_fallback=True,
                           error=str(e), prompt_tokens=prompt_tokens)

    raise RuntimeError(
        f"All LLM providers failed — "
        f"openrouter: {or_err}, gemini: {gemini_err}, groq: {groq_err}, cerebras: {cerebras_err}"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Drop-in replacement for get_key_manager().generate_content()
# ═══════════════════════════════════════════════════════════════════════════

async def generate_with_fallback(
    prompt: str,
    *,
    temperature: float = 0.3,
    max_output_tokens: int = 512,
    label: str = "llm",
) -> str:
    """Async drop-in replacement for get_key_manager().generate_content().

    Unlike the old API which returned a response object requiring .text.strip(),
    this returns a plain string directly.

    Args:
        prompt:            The prompt string (same as before).
        temperature:       Sampling temperature.
        max_output_tokens: Max response tokens.
        label:             Log label for tracking (e.g. "planner", "formatter").

    Returns:
        The LLM response as a stripped string.
    """
    messages = [{"role": "user", "content": prompt}]
    return await llm_chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_output_tokens,
        label=label,
    )

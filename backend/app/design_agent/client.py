"""Anthropic client factory for the Design Agent module.

Per AD16, all Design Agent LLM calls go through this factory so the
DESIGN_AGENT_ANTHROPIC_API_KEY environment variable can attribute spend +
support per-key rate-limit/rotation at handoff. Fallback to the shared
ANTHROPIC_API_KEY is allowed (local dev only) and emits a one-shot startup
warning the first time it's used.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from threading import Lock

from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

_lock = Lock()
_fallback_warned = False


@lru_cache(maxsize=16)
def _client_for_key(api_key: str) -> Anthropic:
    """Cached Design Agent client keyed by the API key (no explicit timeout —
    long tool loops rely on the SDK's default)."""
    return Anthropic(api_key=api_key)


def _platform_key() -> str | None:
    """The Design Agent's platform key: DESIGN_AGENT_ANTHROPIC_API_KEY, else the
    shared ANTHROPIC_API_KEY (with a one-shot fallback warning)."""
    global _fallback_warned
    key = (settings.design_agent_anthropic_api_key or "").strip()
    if key:
        return key
    fallback = (settings.anthropic_api_key or "").strip()
    if fallback:
        with _lock:
            if not _fallback_warned:
                logger.warning(
                    "DESIGN_AGENT_ANTHROPIC_API_KEY not set; falling back to "
                    "ANTHROPIC_API_KEY. Set the Design Agent key for cost "
                    "attribution + per-key rotation."
                )
                _fallback_warned = True
        return fallback
    return None


def get_design_agent_client() -> Anthropic:
    """Return a cached Anthropic client for Design Agent calls.

    Routes through app.llm_keys.resolve_llm_api_key: when the acting company has
    its own Claude key, ALL Design Agent calls use THAT key (overriding both
    DESIGN_AGENT_ANTHROPIC_API_KEY and ANTHROPIC_API_KEY); when a bound company
    has no key and platform fallback isn't allowed, it raises. Raises
    HTTPException(500) at request time when no key is available at all.
    """
    from app.llm_keys import resolve_llm_api_key

    key = resolve_llm_api_key(_platform_key())
    if not key:
        raise HTTPException(
            status_code=500,
            detail=(
                "Design Agent is not configured: set "
                "DESIGN_AGENT_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY as "
                "fallback) in the backend env, or add a workspace Claude key "
                "in Settings → Admin."
            ),
        )
    return _client_for_key(key)


def reset_design_agent_client() -> None:
    """Test-only: clear the cached clients + warning state."""
    global _fallback_warned
    with _lock:
        _fallback_warned = False
    _client_for_key.cache_clear()

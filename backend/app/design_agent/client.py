"""Anthropic client factory for the Design Agent module.

Per AD16, all Design Agent LLM calls go through this factory so the
DESIGN_AGENT_ANTHROPIC_API_KEY environment variable can attribute spend +
support per-key rate-limit/rotation at handoff. Fallback to the shared
ANTHROPIC_API_KEY is allowed (local dev only) and emits a one-shot startup
warning the first time it's used.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Optional

from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[Anthropic] = None
_lock = Lock()
_fallback_warned = False


def get_design_agent_client() -> Anthropic:
    """Return a cached Anthropic client for Design Agent calls.

    Reads DESIGN_AGENT_ANTHROPIC_API_KEY first; falls back to ANTHROPIC_API_KEY
    with a startup warning. Raises HTTPException(500) at request time if
    neither is set (matches llm.py's lazy-init pattern — no import-time
    failure).
    """
    global _client, _fallback_warned
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        key = (settings.design_agent_anthropic_api_key or "").strip()
        if not key:
            fallback = (settings.anthropic_api_key or "").strip()
            if not fallback:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Design Agent is not configured: set "
                        "DESIGN_AGENT_ANTHROPIC_API_KEY (or "
                        "ANTHROPIC_API_KEY as fallback) in the backend env."
                    ),
                )
            if not _fallback_warned:
                logger.warning(
                    "DESIGN_AGENT_ANTHROPIC_API_KEY not set; falling back to "
                    "ANTHROPIC_API_KEY. Set the Design Agent key for cost "
                    "attribution + per-key rotation."
                )
                _fallback_warned = True
            key = fallback
        _client = Anthropic(api_key=key)
        return _client


def reset_design_agent_client() -> None:
    """Test-only: clear the cached client + warning state."""
    global _client, _fallback_warned
    with _lock:
        _client = None
        _fallback_warned = False

"""LLM client factory for the Design Agent module.

Per AD16, all Design Agent LLM calls go through this factory so the
DESIGN_AGENT_ANTHROPIC_API_KEY environment variable can attribute spend +
support per-key rate-limit/rotation at handoff. Fallback to the shared
ANTHROPIC_API_KEY is allowed (local dev only) and emits a one-shot startup
warning the first time it's used.

That dedicated key is an ANTHROPIC platform key, so it applies only when the
acting company runs on Anthropic. A company on OpenAI gets an
`OpenAIMessagesClient` here — its own key when it has one, `OPENAI_API_KEY`
otherwise — and the design-agent key is simply not part of that path. The rest
of the module (agent_loop, the tool dispatch, the vite build) is unchanged:
both clients present the same `messages` surface.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from threading import Lock

from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings
from app.llm_metering import install_metering
from app.llm_providers import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from app.openai_client import OpenAIMessagesClient

logger = logging.getLogger(__name__)

_lock = Lock()
_fallback_warned = False


@lru_cache(maxsize=16)
def _client_for_key(api_key: str, key_mode: str = "platform") -> Anthropic:
    """Cached Design Agent client keyed by the API key (no explicit timeout —
    long tool loops rely on the SDK's default). `max_retries=0`: mirrors
    `app.llm._client_for_key`'s identical precedent — `agent_loop`'s own
    retry loop (runner.py) is the single source of truth for retry-on-
    transient-failure, so the SDK's opaque default (`max_retries=2`, no
    callback hook) must not silently double-retry underneath it.

    Instrumented for usage metering before caching (see app.llm_metering), so
    prototype generation/iteration lands in `llm_usage_events` on the same
    footing as every other surface. `key_mode` records whose key is billed."""
    client = Anthropic(api_key=api_key, max_retries=0)
    return install_metering(client, key_mode, provider=PROVIDER_ANTHROPIC)


@lru_cache(maxsize=16)
def _openai_client_for_key(
    api_key: str, key_mode: str = "platform"
) -> OpenAIMessagesClient:
    """The OpenAI counterpart of `_client_for_key`, cached and metered the same
    way. No explicit timeout, for the same reason: the design agent's tool loops
    are long and the per-request default is what the Anthropic path uses too."""
    client = OpenAIMessagesClient(api_key=api_key)
    return install_metering(client, key_mode, provider=PROVIDER_OPENAI)


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


def get_design_agent_client() -> Anthropic | OpenAIMessagesClient:
    """Return a cached client for Design Agent calls, for the acting company's
    provider.

    Routes through app.llm_keys.resolve_llm_client_config: when the acting
    company has its own key, ALL Design Agent calls use THAT key (on Anthropic
    it overrides both DESIGN_AGENT_ANTHROPIC_API_KEY and ANTHROPIC_API_KEY);
    otherwise the platform key for whichever provider the company chose. Raises
    HTTPException(500) at request time when no key is available at all.
    """
    from app.llm_keys import resolve_llm_client_config

    provider, key, key_mode = resolve_llm_client_config(
        anthropic_platform_key=_platform_key(),
        openai_platform_key=(settings.openai_api_key or "").strip() or None,
    )
    if not key:
        raise HTTPException(
            status_code=500,
            detail=(
                "Design Agent is not configured: set OPENAI_API_KEY in the "
                "backend env, or add a workspace OpenAI key in Settings → Admin."
                if provider == PROVIDER_OPENAI
                else "Design Agent is not configured: set "
                "DESIGN_AGENT_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY as "
                "fallback) in the backend env, or add a workspace Claude key "
                "in Settings → Admin."
            ),
        )
    if provider == PROVIDER_OPENAI:
        return _openai_client_for_key(key, key_mode)
    return _client_for_key(key, key_mode)


def reset_design_agent_client() -> None:
    """Test-only: clear the cached clients + warning state."""
    global _fallback_warned
    with _lock:
        _fallback_warned = False
    _client_for_key.cache_clear()
    _openai_client_for_key.cache_clear()

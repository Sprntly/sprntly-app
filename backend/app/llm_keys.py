"""Per-company Claude API key — resolution and ambient binding.

Policy (product):
  * A company MAY bring its own Anthropic (Claude) API key — collected during
    onboarding (before the connectors step) or later in Settings → Admin. When
    set, ALL of that company's Claude calls use it.
  * If a company has no key configured, Claude calls fall back to the platform
    (default account) key. BYOK is optional; nothing ever hard-fails on a
    missing company key.
  * OpenAI embeddings (`app/graph/embeddings.py`) are unaffected: they read
    `settings.openai_api_key` directly and never touch this module.

Mechanism
---------
A `ContextVar` holds the acting company id for the current call stack. Two
binders populate it:

  * `CompanyLLMKeyMiddleware` (app/main.py) binds it for the whole of every
    authenticated HTTP request — so EVERY request-scoped Claude call resolves
    the company key without each call site opting in. Request-spawned tasks
    (`create_task`, BackgroundTasks) inherit the binding via the contextvars
    snapshot taken at task creation.
  * `company_llm_key(company_id)` binds it explicitly for NON-request contexts
    that carry a company id — the KG gateway, the weekly-brief scheduler, warm
    Ask jobs, and the design-agent worker process (which runs outside any HTTP
    request).

The three Anthropic client factories (app.llm, app.design_agent.client,
app.routes.agent_chat) call `resolve_llm_api_key(platform_key)` to pick the
company key when one is configured, the platform key otherwise. Truly-unbound
calls (CLI, system startup, anything with no company in scope) get the
platform key unchanged.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time

from app.connectors.tokens import decrypt_token_json

logger = logging.getLogger(__name__)

# The acting company id for the current call stack, or None (unbound → platform).
_current_company_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "company_llm_company_id", default=None
)


# Small TTL cache of company_id → decrypted company key (or None when the
# company hasn't configured one). Keeps request-path binding from hitting the
# DB on every call. The Admin routes call `invalidate()` on writes for an
# immediate flush; the short TTL bounds staleness otherwise.
_CACHE_TTL_S = 30.0
_cache: dict[str, tuple[float, str | None]] = {}


def invalidate(company_id: str) -> None:
    """Drop the cached key for a company (call after a key save/remove)."""
    _cache.pop(company_id, None)


def _resolve(company_id: str) -> str | None:
    now = time.monotonic()
    hit = _cache.get(company_id)
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]

    company_key: str | None = None
    try:
        from app.db.companies import get_llm_api_key_encrypted

        cipher = get_llm_api_key_encrypted(company_id)
        if cipher:
            company_key = decrypt_token_json(cipher).strip() or None
    except Exception:  # noqa: BLE001 — never break an LLM call on a resolution error
        logger.exception("Failed to resolve company LLM key for %s", company_id)
        # Fall back to the platform key for THIS call, but don't cache the
        # error result — a company with a BYOK key shouldn't keep running on
        # the platform key for the TTL because of one transient DB error.
        return None

    _cache[company_id] = (now, company_key)
    return company_key


def resolve_llm_api_key(platform_key: str | None) -> str | None:
    """Pick the API key an Anthropic client factory should use.

    * Company has its own key → that key (never the platform key).
    * Otherwise (no key configured, or no company bound at all) → the platform
      key.
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return platform_key
    return _resolve(company_id) or platform_key


@contextlib.contextmanager
def company_llm_key(company_id: str | None):
    """Bind `company_id` as the acting tenant for the enclosed calls.

    Used by non-request contexts (KG gateway, scheduler, warm jobs, design-agent
    worker). A falsy `company_id` is a no-op passthrough (leaves the surrounding
    binding intact — supports nesting under the request middleware)."""
    if not company_id:
        yield
        return
    token = _current_company_id.set(company_id)
    try:
        yield
    finally:
        _current_company_id.reset(token)


def current_company_id() -> str | None:
    """The company id bound for the current call stack (test/introspection)."""
    return _current_company_id.get()

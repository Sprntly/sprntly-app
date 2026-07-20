"""Per-company Claude API key — resolution, enforcement, and ambient binding.

Policy (product):
  * A company SHOULD use its own Anthropic (Claude) API key. The key is
    collected during onboarding (before the connectors step).
  * If a company has no key configured, Claude calls fall back to the PLATFORM
    key rather than failing. `companies.use_platform_key` and onboarding state
    no longer gate this — they remain as billing/reporting signals only (the
    staff admin UI still shows the key mode).
  * OpenAI embeddings (`app/graph/embeddings.py`) are unaffected: they read
    `settings.openai_api_key` directly and never touch this module.

Mechanism
---------
A `ContextVar` holds the acting company id for the current call stack. Two
binders populate it:

  * `CompanyLLMKeyMiddleware` (app/main.py) binds it for the whole of every
    authenticated HTTP request — so EVERY request-scoped Claude call is enforced
    without each call site opting in. Request-spawned tasks (`create_task`,
    BackgroundTasks) inherit the binding via the contextvars snapshot taken at
    task creation.
  * `company_llm_key(company_id)` binds it explicitly for NON-request contexts
    that carry a company id — the KG gateway, the weekly-brief scheduler, warm
    Ask jobs, and the design-agent worker process (which runs outside any HTTP
    request).

The three Anthropic client factories (app.llm, app.design_agent.client,
app.routes.agent_chat) call `resolve_llm_api_key(platform_key)` to pick the key.
Truly-unbound calls (CLI, system startup, anything with no company in scope) get
the platform key unchanged.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app.connectors.tokens import decrypt_token_json

logger = logging.getLogger(__name__)

# The acting company id for the current call stack, or None (unbound → platform).
_current_company_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "company_llm_company_id", default=None
)


@dataclass(frozen=True)
class _Resolution:
    """A company's resolved LLM-key posture."""

    company_key: str | None


class KeyResolutionUnavailableError(HTTPException):
    """Raised when the company's key posture could not be READ (DB error, decrypt
    error) — distinct from a resolved "no key", which now falls back to the
    platform key. The caller's request failed on our side, so the message says
    "try again". Never cached: the next call re-reads the DB."""

    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail=(
                "Sprntly couldn't verify this workspace's API key configuration "
                "due to a temporary problem. Please try again."
            ),
        )


# Small TTL cache of company_id → _Resolution. Keeps request-path binding from
# hitting the DB on every call. The Admin routes call `invalidate()` on writes
# for an immediate flush; the short TTL bounds staleness otherwise.
_CACHE_TTL_S = 30.0
_cache: dict[str, tuple[float, _Resolution]] = {}


def invalidate(company_id: str) -> None:
    """Drop the cached resolution for a company (call after a key save/remove)."""
    _cache.pop(company_id, None)


def _resolve(company_id: str) -> _Resolution:
    now = time.monotonic()
    hit = _cache.get(company_id)
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]

    company_key: str | None = None
    try:
        from app.db.companies import get_company_llm_config

        cipher, _use_platform_key, _onboarding_complete = get_company_llm_config(company_id)
        if cipher:
            company_key = decrypt_token_json(cipher).strip() or None
    except Exception as exc:  # noqa: BLE001 — a read failure is not a key posture
        logger.exception("Failed to resolve company LLM config for %s", company_id)
        # A read failure is NOT "no key": we cannot tell whether this company has
        # its own key, and silently billing the platform for a company that has
        # one would be wrong. Surface a retryable 503 instead, and never cache it
        # — a transient DB blip must not poison this company for a TTL window.
        raise KeyResolutionUnavailableError() from exc

    res = _Resolution(company_key=company_key)
    _cache[company_id] = (now, res)
    return res


def resolve_llm_api_key(platform_key: str | None) -> str | None:
    """Pick the API key an Anthropic client factory should use.

    * No company bound (CLI / system / unauthenticated) → the platform key.
    * Company has its own key → that key (never the platform key).
    * Company has no key → the platform key, whatever the `use_platform_key`
      flag or onboarding state says. A missing key is a billing question, not a
      reason to fail the user's request: keyless workspaces used to hit a hard
      400 (CompanyKeyRequiredError) that surfaced in the product as "failed to
      generate answer".
    * Key posture could not be read (DB/decrypt failure) → raise
      KeyResolutionUnavailableError (503, retryable, never cached).
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return platform_key
    res = _resolve(company_id)
    if res.company_key:
        return res.company_key
    return platform_key


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

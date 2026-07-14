"""Per-company Claude API key — resolution, enforcement, and ambient binding.

Policy (product):
  * A company MUST use its own Anthropic (Claude) API key. The key is collected
    during onboarding (before the connectors step).
  * If a company has no key configured, Claude calls FAIL — UNLESS platform
    fallback is allowed for that company, which happens in exactly two cases:
      1. `companies.use_platform_key` is true (a DB-only flag Sprntly sets for
         specific contracted customers — there is no UI toggle), or
      2. the company has not finished onboarding yet (pre-key onboarding LLM
         work runs on the platform key).
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
app.routes.agent_chat) call `resolve_llm_api_key(platform_key)` to pick the key
or raise. Truly-unbound calls (CLI, system startup, anything with no company in
scope) get the platform key unchanged.
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
    allow_platform: bool


class CompanyKeyRequiredError(HTTPException):
    """Raised when a bound company has no Claude key and platform fallback is not
    allowed (no `use_platform_key` flag, onboarding complete). Surfaces as a 400
    with an actionable message; not retried by the LLM retry layer."""

    def __init__(self) -> None:
        super().__init__(
            status_code=400,
            detail=(
                "This workspace has no Claude API key configured. Add your "
                "Anthropic API key in Settings → Admin to use Sprntly."
            ),
        )


class KeyResolutionUnavailableError(HTTPException):
    """Raised when the company's key posture could not be READ (DB error, decrypt
    error) — distinct from a resolved "no key" (CompanyKeyRequiredError). The
    caller's request failed on our side, not on their configuration, so the
    message says "try again" rather than "add your key". Never cached: the next
    call re-reads the DB."""

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

        cipher, use_platform_key, onboarding_complete = get_company_llm_config(company_id)
        if cipher:
            company_key = decrypt_token_json(cipher).strip() or None
        # Platform fallback is allowed for contracted customers (the DB flag), or
        # while the company is still onboarding (pre-key setup work).
        allow_platform = bool(use_platform_key) or not bool(onboarding_complete)
    except Exception as exc:  # noqa: BLE001 — a read failure is not a key posture
        logger.exception("Failed to resolve company LLM config for %s", company_id)
        # Still fail safe toward NOT leaking the platform key — but as an
        # explicit "couldn't read your config, try again" (503), never the
        # misleading "add your API key" (400), and never cached: a transient
        # DB blip must not poison this company's calls for a TTL window.
        raise KeyResolutionUnavailableError() from exc

    res = _Resolution(company_key=company_key, allow_platform=allow_platform)
    _cache[company_id] = (now, res)
    return res


def resolve_llm_api_key(platform_key: str | None) -> str | None:
    """Pick the API key an Anthropic client factory should use.

    * No company bound (CLI / system / unauthenticated) → the platform key.
    * Company has its own key → that key (never the platform key).
    * Company has no key but platform fallback is allowed (DB flag or still
      onboarding) → the platform key.
    * Company has no key and fallback is not allowed → raise
      CompanyKeyRequiredError.
    * Key posture could not be read (DB/decrypt failure) → raise
      KeyResolutionUnavailableError (503, retryable, never cached).
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return platform_key
    res = _resolve(company_id)
    if res.company_key:
        return res.company_key
    if res.allow_platform:
        return platform_key
    raise CompanyKeyRequiredError()


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

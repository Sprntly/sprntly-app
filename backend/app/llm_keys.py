"""Per-company LLM provider + API key — resolution, enforcement, ambient binding.

Policy (product):
  * A company picks ONE provider — Anthropic (Claude) or OpenAI — and SHOULD
    supply its own API key for it. Both are collected the same way: the
    onboarding api-key step (before connectors) or Settings → Admin.
  * A company may hold BOTH keys at once. `companies.llm_provider` decides which
    one is live; switching provider is a single field change and does not
    require re-entering anything.
  * If a company has no key for its chosen provider, calls fall back to the
    PLATFORM key for THAT provider (`settings.anthropic_api_key` /
    `settings.openai_api_key`) rather than failing. `companies.use_platform_key`
    and onboarding state no longer gate this — they remain as billing/reporting
    signals only (the staff admin UI still shows the key mode).
  * OpenAI embeddings (`app/graph/embeddings.py`) are unaffected either way:
    they read `settings.openai_api_key` directly and never touch this module.
    Embeddings stay on Sprntly's own account for every workspace, which is what
    both the Claude-key and OpenAI-key copy has always promised.

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
    that carry a company id — the KG gateway, the top-insights scheduler, warm
    Ask jobs, and the design-agent worker process (which runs outside any HTTP
    request).

The three client factories (app.llm, app.design_agent.client,
app.routes.agent_chat) call `resolve_llm_client_config(...)` to pick both the
provider and the key in one step, then build an Anthropic client or an
`OpenAIMessagesClient` accordingly. Truly-unbound calls (CLI, system startup,
anything with no company in scope) get Anthropic + the platform key unchanged.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app.connectors.tokens import decrypt_token_json
from app.llm_providers import PROVIDER_ANTHROPIC, PROVIDER_OPENAI

logger = logging.getLogger(__name__)

# The acting company id for the current call stack, or None (unbound → platform).
_current_company_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "company_llm_company_id", default=None
)


@dataclass(frozen=True)
class _Resolution:
    """A company's resolved LLM posture: which provider, and its own key for it.

    `company_key` is the key for `provider` specifically. A company holding a
    Claude key while pointed at OpenAI resolves to `(provider='openai',
    company_key=None)` — i.e. the OpenAI PLATFORM key — never to the Claude key
    it happens to also have stored. Mixing them would send an `sk-ant-` secret
    to api.openai.com.
    """

    company_key: str | None
    provider: str = PROVIDER_ANTHROPIC


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
    provider = PROVIDER_ANTHROPIC
    try:
        from app.db.companies import get_company_llm_config

        config = get_company_llm_config(company_id)
        provider = config.provider
        # Only the ACTIVE provider's key is decrypted. Reading the other one
        # would be a secret handled for no reason.
        cipher = config.cipher_for(provider)
        if cipher:
            company_key = decrypt_token_json(cipher).strip() or None
    except Exception as exc:  # noqa: BLE001 — a read failure is not a key posture
        logger.exception("Failed to resolve company LLM config for %s", company_id)
        # A read failure is NOT "no key": we cannot tell whether this company has
        # its own key, and silently billing the platform for a company that has
        # one would be wrong. Surface a retryable 503 instead, and never cache it
        # — a transient DB blip must not poison this company for a TTL window.
        raise KeyResolutionUnavailableError() from exc

    res = _Resolution(company_key=company_key, provider=provider)
    _cache[company_id] = (now, res)
    return res


def resolve_llm_api_key(platform_key: str | None) -> str | None:
    """Pick the ANTHROPIC API key a Claude client factory should use.

    * No company bound (CLI / system / unauthenticated) → the platform key.
    * Company on Anthropic with its own key → that key (never the platform key).
    * Company on Anthropic with no key → the platform key, whatever the
      `use_platform_key` flag or onboarding state says. A missing key is a
      billing question, not a reason to fail the user's request: keyless
      workspaces used to hit a hard 400 (CompanyKeyRequiredError) that surfaced
      in the product as "failed to generate answer".
    * Company on another provider → the Anthropic platform key (see
      `resolve_llm_api_key_with_mode` for why, and use
      `resolve_llm_client_config` instead if you can build either client).
    * Key posture could not be read (DB/decrypt failure) → raise
      KeyResolutionUnavailableError (503, retryable, never cached).
    """
    return resolve_llm_api_key_with_mode(platform_key)[0]


# Which key paid for a call — the billing-responsibility dimension on every
# usage row. "customer" = the workspace's own provider key (billed to them),
# "platform" = ours (billed to us). Provider-independent: which PROVIDER was
# billed is the separate `provider` column on the same row.
KEY_MODE_CUSTOMER = "customer"
KEY_MODE_PLATFORM = "platform"


def resolve_llm_api_key_with_mode(platform_key: str | None) -> tuple[str | None, str]:
    """`resolve_llm_api_key`, plus WHICH key was chosen.

    Returns `(key, key_mode)` where `key_mode` is `KEY_MODE_CUSTOMER` when the
    acting company's own key was selected and `KEY_MODE_PLATFORM` otherwise
    (including the unbound and no-company-key fallbacks).

    The mode is derived here — at the one place the choice is actually made —
    rather than inferred later by comparing key strings at the call site, so the
    two can never disagree. Usage metering reads it to attribute spend to the
    party whose key was billed; see `app.llm_metering`.

    Anthropic-only, and kept that way: `platform_key` is the caller's Anthropic
    fallback, so a company on OpenAI would get an `sk-ant-` key back with no way
    to tell. Provider-aware callers use `resolve_llm_client_config`, which is
    what all three client factories now do; this stays for the paths that are
    Anthropic by construction (the design-agent key probe, tests).
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return platform_key, KEY_MODE_PLATFORM
    res = _resolve(company_id)
    if res.provider != PROVIDER_ANTHROPIC:
        # The company is not on Anthropic at all. Returning its OpenAI key here
        # would send an OpenAI secret to api.anthropic.com; returning the
        # Anthropic platform key at least fails safe on the right host.
        return platform_key, KEY_MODE_PLATFORM
    if res.company_key:
        return res.company_key, KEY_MODE_CUSTOMER
    return platform_key, KEY_MODE_PLATFORM


def current_provider() -> str:
    """The provider the acting company runs on ('anthropic' when unbound).

    Read by the client factories and by anything that needs to describe the
    active provider without also resolving a secret.
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return PROVIDER_ANTHROPIC
    return _resolve(company_id).provider


def resolve_llm_client_config(
    *,
    anthropic_platform_key: str | None,
    openai_platform_key: str | None = None,
) -> tuple[str, str | None, str]:
    """The one call a client factory makes: `(provider, api_key, key_mode)`.

    Provider and key are resolved TOGETHER because they are one decision — the
    key is only meaningful against the host it was issued for. Splitting them
    across two calls is how an `sk-ant-` key ends up in an `Authorization`
    header pointed at OpenAI.

    * Unbound (CLI / startup / unauthenticated) → Anthropic + its platform key.
    * Company on provider P with its own key    → P + that key, mode 'customer'.
    * Company on provider P with no key         → P + P's platform key, mode
      'platform'. A missing key is a billing question, not a reason to fail the
      user's request — the same rule keyless Claude workspaces have run under
      since `CompanyKeyRequiredError` was removed.
    * Key posture unreadable (DB/decrypt failure) → raises
      KeyResolutionUnavailableError (503, retryable, never cached).

    `openai_platform_key` defaults to `settings.openai_api_key` when omitted, so
    every factory gets the same fallback without restating it.
    """
    company_id = _current_company_id.get()
    if company_id is None:
        return PROVIDER_ANTHROPIC, anthropic_platform_key, KEY_MODE_PLATFORM

    res = _resolve(company_id)
    if res.company_key:
        return res.provider, res.company_key, KEY_MODE_CUSTOMER

    if res.provider == PROVIDER_OPENAI:
        if openai_platform_key is None:
            from app.config import settings

            openai_platform_key = settings.openai_api_key or None
        return PROVIDER_OPENAI, openai_platform_key, KEY_MODE_PLATFORM
    return PROVIDER_ANTHROPIC, anthropic_platform_key, KEY_MODE_PLATFORM


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

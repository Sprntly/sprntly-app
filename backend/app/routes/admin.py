"""Admin settings — which LLM provider this company runs on, and its API keys.

Owners/admins choose Claude (Anthropic) or OpenAI and can store a key for
either. When a key is configured for the ACTIVE provider, every LLM call for
that company uses THEIR key instead of the platform key (see app.llm_keys);
OpenAI embeddings are unaffected and stay on Sprntly's own account either way.
Keys are Fernet-encrypted at rest (same TOKEN_ENCRYPTION_KEY as connector
tokens) and are NEVER returned in full — reads return only a masked preview.

A company may hold BOTH keys at once. Which one is live is `llm_provider`, set
separately, so switching provider never means re-entering a key and removing one
key never silently moves the workspace to the other provider.

Routes (all gated on require_company + owner/admin role):
  GET    /v1/admin/llm-config     → {provider, providers: {<id>: {configured, masked}}}
  PUT    /v1/admin/llm-config     → set the active provider
  GET    /v1/admin/llm-key        → {configured, masked}          ?provider=…
  PUT    /v1/admin/llm-key        → store/replace the key         ?provider=…
  DELETE /v1/admin/llm-key        → remove the key                ?provider=…
  POST   /v1/admin/llm-key/test   → one live check of the stored key ?provider=…

`provider` defaults to 'anthropic' on every key route, so the pre-OpenAI
requests (which is what the onboarding step and older clients send) behave
byte-identically.
"""
from __future__ import annotations

import logging

import anthropic
from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.db.companies import (
    clear_llm_api_key,
    get_company_llm_config,
    get_llm_api_key_encrypted,
    set_llm_api_key_encrypted,
    set_llm_provider,
)
from app.llm_keys import invalidate
from app.llm_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDERS,
    key_error_message,
    mask_key,
    validate_key,
)
from app.openai_client import (
    OpenAIAuthenticationError,
    OpenAIConnectionError,
    OpenAIStatusError,
    verify_api_key as verify_openai_api_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

# Cheapest model with universal key access — used only by the explicit Test button.
_TEST_MODEL = "claude-haiku-4-5"


def _require_admin(company: CompanyContext) -> None:
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Admin settings are restricted to owners and admins")


def _require_known_provider(provider: str) -> str:
    """Reject an unknown provider loudly HERE, unlike the hot-path readers which
    coerce to Anthropic. A write is a user's explicit choice: silently storing a
    key under a provider they did not name is worse than a 400."""
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider {provider!r}.")
    return provider


class LlmKeyIn(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=500)


class LlmKeyStatus(BaseModel):
    configured: bool
    masked: str | None = None


class LlmProviderIn(BaseModel):
    provider: str


class LlmConfig(BaseModel):
    """Everything the Admin pane needs in one request: which provider is live,
    and the key status of BOTH — so the UI can show "Claude key saved" next to an
    inactive Claude card without a second round trip."""

    provider: str
    providers: dict[str, LlmKeyStatus]


def _status_for(company_id: str, provider: str) -> LlmKeyStatus:
    cipher = get_llm_api_key_encrypted(company_id, provider)
    if not cipher:
        return LlmKeyStatus(configured=False, masked=None)
    try:
        key = decrypt_token_json(cipher)
    except TokenEncryptionError:
        # A stored-but-undecryptable key (e.g. TOKEN_ENCRYPTION_KEY rotated):
        # report configured so the UI still offers "remove", but no preview.
        logger.warning("Company %s %s key present but undecryptable", company_id, provider)
        return LlmKeyStatus(configured=True, masked=None)
    return LlmKeyStatus(configured=True, masked=mask_key(provider, key))


@router.get("/llm-config", response_model=LlmConfig)
def get_llm_config(company: CompanyContext = Depends(require_company)) -> LlmConfig:
    _require_admin(company)
    config = get_company_llm_config(company.company_id)
    return LlmConfig(
        provider=config.provider,
        providers={p: _status_for(company.company_id, p) for p in PROVIDERS},
    )


@router.put("/llm-config", response_model=LlmConfig)
def put_llm_config(
    body: LlmProviderIn, company: CompanyContext = Depends(require_company)
) -> LlmConfig:
    """Switch which provider this workspace's LLM calls run on.

    Allowed with NO key stored for the target: the workspace then runs on
    Sprntly's platform key for that provider, which is the same posture a
    keyless Claude workspace has always had. Blocking the switch would leave an
    admin unable to try OpenAI before committing a key to us.
    """
    _require_admin(company)
    provider = _require_known_provider(body.provider.strip().lower())
    set_llm_provider(company.company_id, provider)
    invalidate(company.company_id)
    return get_llm_config(company)


@router.get("/llm-key", response_model=LlmKeyStatus)
def get_llm_key(
    company: CompanyContext = Depends(require_company),
    provider: str = Query(PROVIDER_ANTHROPIC),
) -> LlmKeyStatus:
    _require_admin(company)
    return _status_for(company.company_id, _require_known_provider(provider))


@router.put("/llm-key", response_model=LlmKeyStatus)
def set_llm_key(
    body: LlmKeyIn,
    company: CompanyContext = Depends(require_company),
    provider: str = Query(PROVIDER_ANTHROPIC),
) -> LlmKeyStatus:
    _require_admin(company)
    provider = _require_known_provider(provider)
    api_key = body.api_key.strip()
    if not validate_key(provider, api_key):
        raise HTTPException(400, key_error_message(provider))
    try:
        cipher = encrypt_token_json(api_key)
    except TokenEncryptionError:
        # Server misconfig (no/invalid TOKEN_ENCRYPTION_KEY) — never store plaintext.
        raise HTTPException(500, "Server key storage is not configured; contact support.")
    set_llm_api_key_encrypted(company.company_id, cipher, provider)
    invalidate(company.company_id)
    return LlmKeyStatus(configured=True, masked=mask_key(provider, api_key))


@router.delete("/llm-key", response_model=LlmKeyStatus)
def delete_llm_key(
    company: CompanyContext = Depends(require_company),
    provider: str = Query(PROVIDER_ANTHROPIC),
) -> LlmKeyStatus:
    _require_admin(company)
    clear_llm_api_key(company.company_id, _require_known_provider(provider))
    invalidate(company.company_id)
    return LlmKeyStatus(configured=False, masked=None)


def _test_anthropic_key(key: str) -> None:
    client = Anthropic(api_key=key, max_retries=0, timeout=30.0)
    try:
        client.messages.create(
            model=_TEST_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(400, "Anthropic rejected this key (authentication failed).")
    except anthropic.PermissionDeniedError:
        raise HTTPException(400, "This key lacks permission or has no available credit.")
    except anthropic.APIStatusError as e:
        raise HTTPException(400, f"Anthropic returned an error ({e.status_code}); the key may be invalid.")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Couldn't reach Anthropic to test the key. Try again.")


def _test_openai_key(key: str) -> None:
    """Validate via `GET /v1/models` rather than a completion.

    It authenticates identically and costs the customer nothing — there is no
    reason to bill someone a token to find out whether their key works. (The
    Anthropic path above has no equivalent free authenticated endpoint, hence
    the 1-token ping there.)
    """
    try:
        verify_openai_api_key(key)
    except OpenAIAuthenticationError:
        raise HTTPException(400, "OpenAI rejected this key (authentication failed).")
    except OpenAIConnectionError:
        raise HTTPException(502, "Couldn't reach OpenAI to test the key. Try again.")
    except OpenAIStatusError as e:
        raise HTTPException(400, f"OpenAI returned an error ({e.status_code}); the key may be invalid.")


@router.post("/llm-key/test")
def test_llm_key(
    company: CompanyContext = Depends(require_company),
    provider: str = Query(PROVIDER_ANTHROPIC),
) -> dict:
    """Explicit, opt-in validation: one minimal live check against the STORED key
    so the user chooses when to spend against it (not auto-run on save)."""
    _require_admin(company)
    provider = _require_known_provider(provider)
    cipher = get_llm_api_key_encrypted(company.company_id, provider)
    if not cipher:
        label = "OpenAI" if provider == PROVIDER_OPENAI else "Claude"
        raise HTTPException(400, f"No {label} API key is configured.")
    try:
        key = decrypt_token_json(cipher)
    except TokenEncryptionError:
        raise HTTPException(400, "The stored key could not be read; re-enter it.")

    if provider == PROVIDER_OPENAI:
        _test_openai_key(key)
    else:
        _test_anthropic_key(key)
    return {"ok": True}

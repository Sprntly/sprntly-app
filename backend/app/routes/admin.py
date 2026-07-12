"""Admin settings — company Claude (Anthropic) API key.

Owners/admins can set a per-company Claude API key. When configured, EVERY
Claude LLM call for that company uses THEIR key instead of the platform key
(see app.llm_keys); OpenAI embeddings are unaffected. The key is Fernet-encrypted
at rest (same TOKEN_ENCRYPTION_KEY as connector tokens) and is NEVER returned in
full — reads return only a masked preview.

Routes (all gated on require_company + owner/admin role):
  GET    /v1/admin/llm-key        → {configured, masked}
  PUT    /v1/admin/llm-key        → store/replace the key (no live test)
  DELETE /v1/admin/llm-key        → remove the key (revert to platform key)
  POST   /v1/admin/llm-key/test   → one cheap live call to validate the stored key
"""
from __future__ import annotations

import logging

import anthropic
from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.db.companies import (
    clear_llm_api_key,
    get_llm_api_key_encrypted,
    set_llm_api_key_encrypted,
)
from app.llm_keys import invalidate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

# Cheapest model with universal key access — used only by the explicit Test button.
_TEST_MODEL = "claude-haiku-4-5"


def _require_admin(company: CompanyContext) -> None:
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Admin settings are restricted to owners and admins")


def _mask(key: str) -> str:
    """A safe preview: keep the `sk-ant-` prefix + last 4 chars, hide the rest."""
    key = key.strip()
    if len(key) <= 14:
        return "sk-ant-…"
    return f"{key[:7]}…{key[-4:]}"


class LlmKeyIn(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=500)


class LlmKeyStatus(BaseModel):
    configured: bool
    masked: str | None = None


@router.get("/llm-key", response_model=LlmKeyStatus)
def get_llm_key(company: CompanyContext = Depends(require_company)) -> LlmKeyStatus:
    _require_admin(company)
    cipher = get_llm_api_key_encrypted(company.company_id)
    if not cipher:
        return LlmKeyStatus(configured=False, masked=None)
    try:
        key = decrypt_token_json(cipher)
    except TokenEncryptionError:
        # A stored-but-undecryptable key (e.g. TOKEN_ENCRYPTION_KEY rotated):
        # report configured so the UI still offers "remove", but no preview.
        logger.warning("Company %s LLM key present but undecryptable", company.company_id)
        return LlmKeyStatus(configured=True, masked=None)
    return LlmKeyStatus(configured=True, masked=_mask(key))


@router.put("/llm-key", response_model=LlmKeyStatus)
def set_llm_key(
    body: LlmKeyIn, company: CompanyContext = Depends(require_company)
) -> LlmKeyStatus:
    _require_admin(company)
    api_key = body.api_key.strip()
    if not api_key.startswith("sk-ant-"):
        raise HTTPException(
            400,
            "That doesn't look like an Anthropic API key — it should start with 'sk-ant-'.",
        )
    try:
        cipher = encrypt_token_json(api_key)
    except TokenEncryptionError:
        # Server misconfig (no/invalid TOKEN_ENCRYPTION_KEY) — never store plaintext.
        raise HTTPException(500, "Server key storage is not configured; contact support.")
    set_llm_api_key_encrypted(company.company_id, cipher)
    invalidate(company.company_id)
    return LlmKeyStatus(configured=True, masked=_mask(api_key))


@router.delete("/llm-key", response_model=LlmKeyStatus)
def delete_llm_key(company: CompanyContext = Depends(require_company)) -> LlmKeyStatus:
    _require_admin(company)
    clear_llm_api_key(company.company_id)
    invalidate(company.company_id)
    return LlmKeyStatus(configured=False, masked=None)


@router.post("/llm-key/test")
def test_llm_key(company: CompanyContext = Depends(require_company)) -> dict:
    """Explicit, opt-in validation: one minimal live call against the STORED key
    so the user chooses when to spend against it (not auto-run on save)."""
    _require_admin(company)
    cipher = get_llm_api_key_encrypted(company.company_id)
    if not cipher:
        raise HTTPException(400, "No Claude API key is configured.")
    try:
        key = decrypt_token_json(cipher)
    except TokenEncryptionError:
        raise HTTPException(400, "The stored key could not be read; re-enter it.")

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
    return {"ok": True}

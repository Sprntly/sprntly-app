"""Customer-issued MCP (Model Context Protocol) API token persistence.

A customer generates a token from Settings to connect their own AI client
(Claude Desktop, Claude Code, claude.ai custom connectors) to their Sprntly
workspace via the `mcp/` service. The raw token is shown once at creation
time and never stored — only its SHA-256 hash (compare-by-hash, the standard
API-key pattern), unlike the Fernet-reversible encryption used for outbound
OAuth connector tokens (app/connectors/tokens.py), which this app must be
able to read back to call the third party.

All access is via require_client() (service-role; runs server-side only).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "sprn_mcp_"


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@retry_on_disconnect
def create_mcp_token(*, company_id: str, user_id: str, name: str) -> dict:
    """Mint a token and return the row PLUS the one-time raw token under 'token'.

    The raw token is never persisted or logged; callers must hand it to the
    user immediately and never surface it again after this call returns.
    """
    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "user_id": user_id,
        "name": (name or "MCP token").strip() or "MCP token",
        "token_hash": _hash(raw_token),
        "token_prefix": raw_token[:20],
        "created_at": utc_now(),
    }
    client = require_client()
    client.table("mcp_tokens").insert(row).execute()
    logger.info("mcp token created: id=%s company=%s", row["id"], company_id)
    return {**row, "token": raw_token}


@retry_on_disconnect
def list_mcp_tokens(company_id: str) -> list[dict]:
    """List a company's tokens (never includes the hash or raw token)."""
    client = require_client()
    resp = (
        client.table("mcp_tokens")
        .select("id, name, token_prefix, created_at, last_used_at, revoked_at")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


@retry_on_disconnect
def revoke_mcp_token(company_id: str, token_id: str) -> bool:
    """Revoke a token. company_id filter is IN the query (not just app-layer),
    so a cross-tenant revoke attempt matches zero rows rather than relying on
    the caller to have checked ownership first."""
    client = require_client()
    resp = (
        client.table("mcp_tokens")
        .update({"revoked_at": utc_now()})
        .eq("id", token_id)
        .eq("company_id", company_id)
        .execute()
    )
    return bool(resp.data)


@retry_on_disconnect
def resolve_mcp_token(raw_token: str) -> dict | None:
    """Resolve a bearer token to {company_id, user_id, role, token_id}, or
    None if the token is unknown, revoked, or the user no longer belongs to
    that company.

    Re-checks LIVE company membership on every resolve (not just
    `revoked_at`) — mirrors app.auth.require_company's live lookup. This is a
    deliberate security property: removing a user from a company must kill
    their MCP access immediately, even if nobody explicitly revoked the
    token.
    """
    client = require_client()
    resp = (
        client.table("mcp_tokens")
        .select("*")
        .eq("token_hash", _hash(raw_token))
        .is_("revoked_at", "null")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]

    from app.db.companies import memberships_for_user

    match = next(
        (
            m
            for m in memberships_for_user(row["user_id"])
            if m["company_id"] == row["company_id"]
        ),
        None,
    )
    if not match:
        return None

    client.table("mcp_tokens").update({"last_used_at": utc_now()}).eq(
        "id", row["id"]
    ).execute()
    return {
        "company_id": row["company_id"],
        "user_id": row["user_id"],
        "role": match["role"],
        "token_id": row["id"],
    }

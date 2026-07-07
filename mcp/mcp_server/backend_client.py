"""Async HTTP client to the Sprntly backend's internal (X-Internal-Key) API.

Mirrors ds-agent/ds_agent/server/backend_client.py's shared-secret pattern,
but async (MCP tool handlers run in an async context) and generic — one
`get_json` helper for every data route under /internal/mcp/*, plus a
dedicated `resolve_token` for the token->company_id exchange.

This module holds the ONLY network calls mcp/ ever makes. It never touches
Supabase, never sees a Supabase key, never sees supabase_jwt_secret.
"""
from __future__ import annotations

import os

import httpx

_BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
_INTERNAL_KEY = os.environ.get("BACKEND_INTERNAL_KEY", "")
_TIMEOUT = 30.0


class BackendError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"backend returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _headers() -> dict[str, str]:
    return {"X-Internal-Key": _INTERNAL_KEY}


async def resolve_token(token: str) -> dict:
    """POST /internal/mcp-tokens/resolve -> {company_id, user_id, role, token_id}.

    Raises BackendError on any non-200 (including 401 for an invalid/revoked
    token) — the caller (middleware) turns that into a 401 to the MCP client.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_BACKEND_URL}/internal/mcp-tokens/resolve",
            json={"token": token},
            headers=_headers(),
        )
    if resp.status_code != 200:
        raise BackendError(resp.status_code, resp.text)
    return resp.json()


async def get_json(path: str, **params: str) -> dict | None:
    """GET /internal/mcp{path} -> parsed JSON, or None on 404.

    Raises BackendError on any other non-200 status.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{_BACKEND_URL}/internal/mcp{path}",
            params=params,
            headers=_headers(),
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise BackendError(resp.status_code, resp.text)
    return resp.json()

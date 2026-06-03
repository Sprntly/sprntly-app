"""Shared helpers for connector route tests in the multitenancy slice
(commit 4).

The connector routes now sit behind `require_workspace_membership`,
which:
  - rejects legacy demo cookies (they have no user identity)
  - requires `workspace_id` as a query param
  - looks up `company_members` to verify the bearer-token user is on
    the workspace's roster

Helpers below give each connector test the minimum it needs to pass
that gate: a real Supabase JWT, a seeded company, a member row, and a
TestClient configured to send the Bearer header by default.
"""
from __future__ import annotations

import importlib
import sys
import time
import uuid
from types import SimpleNamespace

import jwt
from fastapi.testclient import TestClient


SUPABASE_JWT_SECRET = "test-supabase-jwt-secret"


def setup_supabase_auth(monkeypatch) -> None:
    """Set SUPABASE_JWT_SECRET and reload app.config + app.auth so
    require_session accepts our minted bearer JWTs as Supabase sessions."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SUPABASE_JWT_SECRET)
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.auth"])


def supabase_bearer(user_id: str) -> dict[str, str]:
    """Authorization header dict for a Supabase-issued JWT for `user_id`."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        SUPABASE_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def seed_workspace(*, user_id: str = "test-user", slug: str = "acme") -> str:
    """Seed a `companies` row and a `company_members` row linking
    `user_id` as owner. Returns the workspace_id (uuid hex)."""
    from app.db.client import require_client

    wsid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": wsid, "slug": slug, "display_name": slug.title()}
    ).execute()
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": wsid,
            "user_id": user_id,
            "role": "owner",
        }
    ).execute()
    return wsid


def seed_connection(
    *,
    workspace_id: str,
    provider: str,
    token_blob: dict,
    label: str = "alice@co.com",
) -> None:
    """Insert an already-encrypted connection row for the workspace."""
    import json

    from app import db
    from app.connectors.tokens import encrypt_token_json

    enc = encrypt_token_json(json.dumps(token_blob))
    db.upsert_connection(
        workspace_id=workspace_id,
        provider=provider,
        token_encrypted=enc,
        scopes="",
        account_label=label,
        config_json="{}",
    )


def workspace_client(monkeypatch) -> SimpleNamespace:
    """One-shot setup for a connector test: returns
    `(client, workspace_id, user_id, bearer_headers)` where the client
    already carries the Bearer header by default.

    Equivalent to `_client(env)` in the pre-multitenancy tests, but
    swaps the legacy demo cookie for a real Supabase session and seeds
    a workspace the user is a member of.
    """
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])

    user_id = "test-user-" + uuid.uuid4().hex[:8]
    workspace_id = seed_workspace(user_id=user_id)
    headers = supabase_bearer(user_id)
    client = TestClient(main_mod.app, headers=headers)
    return SimpleNamespace(
        client=client,
        workspace_id=workspace_id,
        user_id=user_id,
        headers=headers,
    )

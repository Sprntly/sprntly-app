"""Shared helpers for connector route tests post-require_company refactor.

The connector routes now sit behind `require_company`, which:
  - rejects legacy demo cookies (no user identity)
  - resolves the active company purely from the JWT (no client-side
    workspace_id; one-user-one-company invariant)
  - 403 if the user has no membership; 500 if the user has > 1 row
    (data anomaly per the schema invariant)

Helpers below give each connector test the minimum it needs to pass
that gate: a Supabase JWT for a test user, a seeded company, a
company_members row, and a TestClient that sends the Bearer header
by default.

Naming note: this module replaces tests/_workspace_helpers.py from the
pre-rebase shape of the slice. Same idea, new field names + no client
workspace_id param.
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


def seed_company(*, user_id: str = "test-user", slug: str = "acme") -> str:
    """Seed a `companies` row and a `company_members` row linking
    `user_id` as owner. Returns the company_id (uuid hex). Matches the
    one-user-one-company invariant — call once per user."""
    from app.db.client import require_client

    cid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": cid, "slug": slug, "display_name": slug.title()}
    ).execute()
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": cid,
            "user_id": user_id,
            "role": "owner",
        }
    ).execute()
    return cid


def seed_connection(
    *,
    company_id: str,
    provider: str,
    token_blob: dict,
    label: str = "alice@co.com",
) -> None:
    """Insert an already-encrypted connection row for the company."""
    import json

    from app import db
    from app.connectors.tokens import encrypt_token_json

    enc = encrypt_token_json(json.dumps(token_blob))
    db.upsert_connection(
        company_id=company_id,
        provider=provider,
        token_encrypted=enc,
        scopes="",
        account_label=label,
        config_json="{}",
    )


def company_client(monkeypatch) -> SimpleNamespace:
    """One-shot setup for a connector test: returns
    `(client, company_id, user_id, headers)` where the client already
    carries the Bearer header by default. Routes no longer take a
    workspace_id query param — the dep resolves company from the JWT."""
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])

    user_id = "test-user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id)
    headers = supabase_bearer(user_id)
    client = TestClient(main_mod.app, headers=headers)
    return SimpleNamespace(
        client=client,
        company_id=company_id,
        user_id=user_id,
        headers=headers,
    )

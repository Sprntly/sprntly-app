"""Tests for the workspace_membership helper + the route-level dep
that gates connector access (commit 3).
"""
from __future__ import annotations

import importlib
import time
import uuid

import jwt
import pytest
from fastapi import HTTPException

from app import auth
from app.db.client import require_client
from app.db.workspace_membership import is_member


def _seed_company(slug: str = "acme") -> str:
    cid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": cid, "slug": slug, "display_name": slug.title()}
    ).execute()
    return cid


def _add_member(workspace_id: str, user_id: str, role: str = "member") -> None:
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": workspace_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


# ─────────────────────────── is_member helper ───────────────────────────


def test_is_member_returns_true_when_row_exists(isolated_settings):
    ws = _seed_company()
    _add_member(ws, "user-a")
    assert is_member(user_id="user-a", workspace_id=ws) is True


def test_is_member_returns_false_for_non_member(isolated_settings):
    ws = _seed_company()
    _add_member(ws, "user-a")
    assert is_member(user_id="user-b", workspace_id=ws) is False


def test_is_member_returns_false_for_unknown_workspace(isolated_settings):
    _seed_company()  # create one valid workspace so the table exists with rows
    assert is_member(user_id="user-a", workspace_id=uuid.uuid4().hex) is False


def test_is_member_accepts_any_role(isolated_settings):
    """owner / admin / member all count as members for connector access."""
    ws = _seed_company()
    _add_member(ws, "owner-user", role="owner")
    _add_member(ws, "admin-user", role="admin")
    _add_member(ws, "member-user", role="member")
    assert is_member(user_id="owner-user", workspace_id=ws)
    assert is_member(user_id="admin-user", workspace_id=ws)
    assert is_member(user_id="member-user", workspace_id=ws)


# ─────────────────────── require_workspace_membership dep ───────────────────────


def _supabase_jwt(user_id: str, monkeypatch) -> str:
    """Mint a JWT that auth.require_session accepts as a Supabase session."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-supabase-jwt-secret")
    from app import config as config_mod

    importlib.reload(config_mod)
    importlib.reload(auth)
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        "test-supabase-jwt-secret",
        algorithm=auth.JWT_ALG,
    )


def test_dep_returns_workspace_id_when_member(isolated_settings, monkeypatch):
    ws = _seed_company()
    _add_member(ws, "alice")
    tok = _supabase_jwt("alice", monkeypatch)
    session = auth.require_session(f"Bearer {tok}", None, None)
    assert auth.require_workspace_membership(ws, session) == ws


def test_dep_rejects_non_member_with_403(isolated_settings, monkeypatch):
    ws = _seed_company()
    _add_member(ws, "alice")
    # Bob has a valid Supabase session but isn't on this workspace's roster.
    tok = _supabase_jwt("bob", monkeypatch)
    session = auth.require_session(f"Bearer {tok}", None, None)
    with pytest.raises(HTTPException) as exc:
        auth.require_workspace_membership(ws, session)
    assert exc.value.status_code == 403


def test_dep_rejects_legacy_demo_cookie_with_403(isolated_settings):
    """Demo cookies have no user identity — they cannot claim membership."""
    ws = _seed_company()
    demo_session = {"aud": "demo", "scope": "demo"}
    with pytest.raises(HTTPException) as exc:
        auth.require_workspace_membership(ws, demo_session)
    assert exc.value.status_code == 403


def test_dep_rejects_unknown_workspace_with_403(isolated_settings, monkeypatch):
    """Even a real signed-in user gets 403 for a workspace they don't belong to —
    the 403 message intentionally doesn't disclose whether the workspace exists."""
    tok = _supabase_jwt("alice", monkeypatch)
    session = auth.require_session(f"Bearer {tok}", None, None)
    with pytest.raises(HTTPException) as exc:
        auth.require_workspace_membership(uuid.uuid4().hex, session)
    assert exc.value.status_code == 403

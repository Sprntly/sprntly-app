"""Tests for `app.auth.require_company` — JWT → company_id tenant resolution.

Tenancy model: one user ↔ one company (product decision 2026-06-04). The
dependency is a pure lookup — Supabase Bearer JWT → sub → company_members →
CompanyContext. The client never passes a company id; multiple membership
rows are a data-integrity anomaly (fail closed, 500).
"""
from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi import HTTPException


SECRET = "shared-hs256-test-secret"


def _mint_token(sub: str) -> str:
    return pyjwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 300},
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def auth_env(isolated_settings, monkeypatch):
    """HS256 Supabase JWT env + reloaded auth module + the fake DB."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    import importlib
    import app.config, app.auth
    importlib.reload(app.config)
    importlib.reload(app.auth)
    return isolated_settings


def _seed_membership(db, company_id: str, user_id: str, role: str = "member",
                     row_id: str | None = None):
    db.table("company_members").insert({
        "id": row_id or f"cm-{company_id}-{user_id}",
        "company_id": company_id,
        "user_id": user_id,
        "role": role,
    }).execute()


def _call(authorization: str | None = None):
    from app.auth import require_company
    return require_company(
        authorization=authorization,
        sprntly_app_session=None,
        sprntly_demo_session=None,
    )


# ---------- happy path ----------

def test_membership_resolves(auth_env):
    _seed_membership(auth_env["supabase"], "co-acme", "user-1", role="owner")
    ctx = _call(authorization=f"Bearer {_mint_token('user-1')}")
    assert ctx.company_id == "co-acme"
    assert ctx.role == "owner"
    assert ctx.user_id == "user-1"


def test_tenant_comes_from_membership_not_client(auth_env):
    """The company is derived purely from the DB; another user's membership
    is invisible (the anti-spoof property under the 1:1 model — there is no
    client-supplied company id at all)."""
    _seed_membership(auth_env["supabase"], "co-acme", "user-1")
    _seed_membership(auth_env["supabase"], "co-globex", "user-2")  # someone else's
    ctx = _call(authorization=f"Bearer {_mint_token('user-1')}")
    assert ctx.company_id == "co-acme"        # never co-globex


# ---------- rejection paths ----------

def test_multiple_memberships_is_500_integrity_error(auth_env):
    """One-company-per-user is the invariant; >1 row = corrupted data →
    fail closed rather than guess a tenant."""
    _seed_membership(auth_env["supabase"], "co-acme", "user-1")
    _seed_membership(auth_env["supabase"], "co-globex", "user-1")
    with pytest.raises(HTTPException) as e:
        _call(authorization=f"Bearer {_mint_token('user-1')}")
    assert e.value.status_code == 500
    assert "integrity" in e.value.detail.lower()


def test_no_membership_is_403(auth_env):
    with pytest.raises(HTTPException) as e:
        _call(authorization=f"Bearer {_mint_token('user-orphan')}")
    assert e.value.status_code == 403
    assert "onboarding" in e.value.detail.lower()


def test_unauthenticated_is_401(auth_env):
    with pytest.raises(HTTPException) as e:
        _call(authorization=None)
    assert e.value.status_code == 401


def test_invalid_token_is_401(auth_env):
    with pytest.raises(HTTPException) as e:
        _call(authorization="Bearer not-a-real-jwt")
    assert e.value.status_code == 401


def test_legacy_cookie_session_is_403(auth_env):
    """Demo/app cookie sessions carry no user id → cannot resolve a company."""
    from app.auth import _make_token, require_company
    cookie = _make_token("demo")
    with pytest.raises(HTTPException) as e:
        require_company(
            authorization=None,
            sprntly_app_session=None,
            sprntly_demo_session=cookie,
        )
    assert e.value.status_code == 403
    assert "signed-in user" in e.value.detail

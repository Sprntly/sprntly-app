"""Tests for `app.auth.require_company` — JWT → company_id tenant resolution.

The dependency layers on require_session: Supabase Bearer JWT → sub →
company_members lookup → CompanyContext. The client can only *select among*
its memberships (X-Company-Id), never assert an arbitrary company.
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


def _call(authorization: str | None = None, x_company_id: str | None = None):
    from app.auth import require_company
    return require_company(
        authorization=authorization,
        sprntly_app_session=None,
        sprntly_demo_session=None,
        x_company_id=x_company_id,
    )


# ---------- happy paths ----------

def test_single_membership_resolves_without_header(auth_env):
    _seed_membership(auth_env["supabase"], "co-acme", "user-1", role="owner")
    ctx = _call(authorization=f"Bearer {_mint_token('user-1')}")
    assert ctx.company_id == "co-acme"
    assert ctx.role == "owner"
    assert ctx.user_id == "user-1"


def test_multi_membership_selects_via_header(auth_env):
    _seed_membership(auth_env["supabase"], "co-acme", "user-1", role="member")
    _seed_membership(auth_env["supabase"], "co-globex", "user-1", role="admin")
    ctx = _call(authorization=f"Bearer {_mint_token('user-1')}",
                x_company_id="co-globex")
    assert ctx.company_id == "co-globex"
    assert ctx.role == "admin"


def test_header_also_works_with_single_membership(auth_env):
    _seed_membership(auth_env["supabase"], "co-acme", "user-1")
    ctx = _call(authorization=f"Bearer {_mint_token('user-1')}",
                x_company_id="co-acme")
    assert ctx.company_id == "co-acme"


# ---------- rejection paths ----------

def test_multi_membership_without_header_is_400(auth_env):
    _seed_membership(auth_env["supabase"], "co-acme", "user-1")
    _seed_membership(auth_env["supabase"], "co-globex", "user-1")
    with pytest.raises(HTTPException) as e:
        _call(authorization=f"Bearer {_mint_token('user-1')}")
    assert e.value.status_code == 400
    assert "X-Company-Id" in e.value.detail


def test_spoofed_company_header_is_403(auth_env):
    """Member of A asserting B via header → 403 (the anti-spoof property)."""
    _seed_membership(auth_env["supabase"], "co-acme", "user-1")
    _seed_membership(auth_env["supabase"], "co-globex", "user-2")  # someone else's
    with pytest.raises(HTTPException) as e:
        _call(authorization=f"Bearer {_mint_token('user-1')}",
              x_company_id="co-globex")
    assert e.value.status_code == 403


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
            x_company_id=None,
        )
    assert e.value.status_code == 403
    assert "signed-in user" in e.value.detail

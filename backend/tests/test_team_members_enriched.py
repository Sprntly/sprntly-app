"""Tests for the enriched GET /v1/team/members response (SC2).

Mockup needs name + email + avatar per member row. The backend now
LEFT-JOINs profiles into the response so each member row carries:
  - user_id, role (existing)
  - display_name (full_name; falls back to "first last"; null if profile missing)
  - email (null if profile missing)
  - avatar_url (null if profile missing)

Profiles row may not exist (test fixtures often skip it, brand-new
auth.users without a profile, etc.). The endpoint must tolerate that
gracefully — those rows return user_id only, with the three enriched
fields as null.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401

from tests._company_helpers import company_client


def _add_member(*, company_id: str, user_id: str, role: str = "member") -> None:
    from app.db.client import require_client

    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


def _seed_profile(
    *,
    user_id: str,
    email: str | None = None,
    full_name: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    avatar_url: str | None = None,
) -> None:
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {
            "id": user_id,
            "email": email,
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "avatar_url": avatar_url,
        }
    ).execute()


def test_members_response_includes_enriched_fields(isolated_settings, monkeypatch):
    """Owner has a profile with full_name + email + avatar; row should
    return all three fields populated."""
    ctx = company_client(monkeypatch)
    _seed_profile(
        user_id=ctx.user_id,
        email="boss@co.com",
        full_name="Boss Person",
        avatar_url="https://cdn.example/avatar.png",
    )

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    rows = r.json()["members"]
    me = next(m for m in rows if m["user_id"] == ctx.user_id)
    assert me["display_name"] == "Boss Person"
    assert me["email"] == "boss@co.com"
    assert me["avatar_url"] == "https://cdn.example/avatar.png"
    assert me["role"] == "owner"


def test_members_display_name_falls_back_to_first_last(isolated_settings, monkeypatch):
    """When full_name is missing but first/last exist, use the concat."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice")
    _seed_profile(
        user_id="alice",
        email="alice@co.com",
        first_name="Alice",
        last_name="Liddell",
    )

    r = ctx.client.get("/v1/team/members")
    alice = next(m for m in r.json()["members"] if m["user_id"] == "alice")
    assert alice["display_name"] == "Alice Liddell"
    assert alice["email"] == "alice@co.com"


def test_members_missing_profile_returns_nulls(isolated_settings, monkeypatch):
    """A member without a profile row gets display_name/email/avatar = null.
    Endpoint must NOT 500 just because the profile is missing."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="ghost")
    # Intentionally do NOT seed a profile for ghost.

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    ghost = next(m for m in r.json()["members"] if m["user_id"] == "ghost")
    assert ghost["display_name"] is None
    assert ghost["email"] is None
    assert ghost["avatar_url"] is None
    assert ghost["role"] == "member"


def test_members_existing_shape_preserved(isolated_settings, monkeypatch):
    """user_id + role still appear (SC2 must be additive — no breaking changes)."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/team/members")
    rows = r.json()["members"]
    for m in rows:
        assert "user_id" in m
        assert "role" in m
        # New enriched fields are present (null-safe).
        assert "display_name" in m
        assert "email" in m
        assert "avatar_url" in m


def test_members_does_not_leak_profile_of_other_companies(
    isolated_settings, monkeypatch
):
    """Seed a profile for a user in a different company; should not appear
    in our team-members response. (Membership-filter still bounds this.)"""
    ctx = company_client(monkeypatch)
    _seed_profile(user_id="stranger", email="leak@x.com", full_name="Should Not Show")
    # Stranger is NOT in ctx.company_id.

    r = ctx.client.get("/v1/team/members")
    assert "stranger" not in {m["user_id"] for m in r.json()["members"]}
    assert all(m.get("email") != "leak@x.com" for m in r.json()["members"])

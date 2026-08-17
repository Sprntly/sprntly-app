"""Tests for the `profiles.role` job-designation surface on the team roster.

`list_company_members` reads `profiles.role` and exposes it as `job_role`
(keyed separately from the existing permission `role`). A member can PATCH
their own `profiles.role` from Settings; they can never target a teammate's
row — the write target is always resolved from the authenticated session.
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


def _seed_profile(*, user_id: str, role: str | None = None, email: str | None = None) -> None:
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": user_id, "email": email, "role": role}
    ).execute()


# ── Retrieval ────────────────────────────────────────────────────────────


def test_list_company_members_includes_job_role(isolated_settings, monkeypatch):
    """Member dict carries `job_role` from `profiles.role`, alongside the
    unchanged permission `role` (company_members.role)."""
    ctx = company_client(monkeypatch)
    _seed_profile(user_id=ctx.user_id, role="Designer", email="owner@co.com")

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    me = next(m for m in r.json()["members"] if m["user_id"] == ctx.user_id)
    assert me["job_role"] == "Designer"
    # Permission role unchanged/untouched by the job-role addition.
    assert me["role"] == "owner"


def test_list_company_members_null_role(isolated_settings, monkeypatch):
    """A member with null `profiles.role` returns `job_role: None`, no crash."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice")
    _seed_profile(user_id="alice", role=None, email="alice@co.com")

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    alice = next(m for m in r.json()["members"] if m["user_id"] == "alice")
    assert alice["job_role"] is None


# ── Serialization ────────────────────────────────────────────────────────


def test_member_dict_keys_superset_unchanged(isolated_settings, monkeypatch):
    """Every pre-existing key still present on the response row; only
    `job_role` was added."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/team/members")
    rows = r.json()["members"]
    assert rows
    for m in rows:
        for key in (
            "id",
            "user_id",
            "role",
            "created_at",
            "display_name",
            "email",
            "avatar_url",
            "workspace_ids",
            "job_role",
        ):
            assert key in m


# ── Creation / update ────────────────────────────────────────────────────


def test_self_edit_role_persists(isolated_settings, monkeypatch):
    """Self PATCH updates `profiles.role`; the next read reflects it."""
    ctx = company_client(monkeypatch)
    _seed_profile(user_id=ctx.user_id, role=None, email="owner@co.com")

    r = ctx.client.patch(
        f"/v1/team/members/{ctx.user_id}/job-role", json={"role": "Engineer"}
    )
    assert r.status_code == 200
    assert r.json()["job_role"] == "Engineer"

    r2 = ctx.client.get("/v1/team/members")
    me = next(m for m in r2.json()["members"] if m["user_id"] == ctx.user_id)
    assert me["job_role"] == "Engineer"


# ── Error handling ───────────────────────────────────────────────────────


def test_edit_other_user_role_forbidden(isolated_settings, monkeypatch):
    """PATCH targeting another user_id → 403, and that other row is
    never mutated."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="teammate")
    _seed_profile(user_id="teammate", role="PM", email="mate@co.com")

    r = ctx.client.patch(
        "/v1/team/members/teammate/job-role", json={"role": "Hacked"}
    )
    assert r.status_code == 403

    r2 = ctx.client.get("/v1/team/members")
    mate = next(m for m in r2.json()["members"] if m["user_id"] == "teammate")
    assert mate["job_role"] == "PM"


def test_edit_role_target_from_session_not_body(isolated_settings, monkeypatch):
    """A body-supplied `user_id` is ignored entirely — the write always
    targets the authenticated session's own user, from the path AND the
    write call, never anything the client puts in the JSON body."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="teammate")
    _seed_profile(user_id=ctx.user_id, role=None, email="owner@co.com")
    _seed_profile(user_id="teammate", role="PM", email="mate@co.com")

    r = ctx.client.patch(
        f"/v1/team/members/{ctx.user_id}/job-role",
        json={"role": "Founder", "user_id": "teammate"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == ctx.user_id

    r2 = ctx.client.get("/v1/team/members")
    rows = {m["user_id"]: m for m in r2.json()["members"]}
    assert rows[ctx.user_id]["job_role"] == "Founder"
    # The smuggled body user_id never got touched.
    assert rows["teammate"]["job_role"] == "PM"


# ── Edge cases ───────────────────────────────────────────────────────────


def test_edit_role_free_text_no_check(isolated_settings, monkeypatch):
    """An "Other" free-text value persists (no DB CHECK rejection) — the
    ROLE_OPTIONS taxonomy is client-validated only."""
    ctx = company_client(monkeypatch)
    _seed_profile(user_id=ctx.user_id, role=None, email="owner@co.com")

    r = ctx.client.patch(
        f"/v1/team/members/{ctx.user_id}/job-role",
        json={"role": "Chief Vibes Officer"},
    )
    assert r.status_code == 200
    assert r.json()["job_role"] == "Chief Vibes Officer"

"""The `create_invite`/`accept_invite_for_user` project-association extension
(AD-TNM3, Extension B): `project_id` round-trips through the invite, and
accept auto-adds a `project_members` row on BOTH accept paths — while a
project-less invite (every existing WJ caller) adds nothing and behaves
exactly as before.

Fast lane over FakeSupabaseClient (real SQLite behind it), plus an env-gated
real-DB accept (`RUN_INVITE_PROJECT_ASSOCIATION_LIVE=1`).
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db import projects as projects_db
from app.db import team as team_db
from tests._company_helpers import seed_company


def _project_member_rows(project_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("project_members")
        .select("*")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )


def _new_project(company_id: str, created_by: str) -> dict:
    return projects_db.create_project(
        company_id=company_id,
        workspace_id="ws-" + uuid.uuid4().hex[:8],
        name="Assoc " + uuid.uuid4().hex[:6],
        created_by=created_by,
    )


# ── AC-10: project_id round-trips through the invite ─────────────────────


def test_create_invite_persists_project_id(isolated_settings):
    company_id = seed_company(user_id="owner-" + uuid.uuid4().hex[:6])
    project = _new_project(company_id, "owner-x")
    invite = team_db.create_invite(
        company_id=company_id,
        email="peer@acme.example",
        role="member",
        invited_by="owner-x",
        project_id=project["id"],
    )
    assert invite["project_id"] == project["id"]
    # Re-read via get_invite confirms it persisted (select widened).
    fetched = team_db.get_invite(invite["id"])
    assert fetched["project_id"] == project["id"]


def test_accept_adds_project_member_new_company_path(isolated_settings):
    owner = "owner-" + uuid.uuid4().hex[:6]
    company_id = seed_company(user_id=owner)
    project = _new_project(company_id, owner)

    newbie = "newbie-" + uuid.uuid4().hex[:6]
    email = f"{newbie}@acme.example"
    team_db.create_invite(
        company_id=company_id,
        email=email,
        role="member",
        invited_by=owner,
        project_id=project["id"],
    )
    # newbie has NO prior membership → the new-company accept path.
    result = team_db.accept_invite_for_user(user_id=newbie, email=email)
    assert result is not None and result["company_id"] == company_id
    assert newbie in {m["user_id"] for m in _project_member_rows(project["id"])}


def test_accept_adds_project_member_same_company_path(isolated_settings):
    owner = "owner-" + uuid.uuid4().hex[:6]
    company_id = seed_company(user_id=owner)
    project = _new_project(company_id, owner)  # owner is the only project member

    # A second existing member of the SAME company, NOT on the project.
    from app.db.client import require_client

    member_y = "member-y-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company_id, "user_id": member_y, "role": "member"}
    ).execute()
    email = f"{member_y}@acme.example"
    team_db.create_invite(
        company_id=company_id,
        email=email,
        role="member",
        invited_by=owner,
        project_id=project["id"],
    )
    # member_y already belongs to company_id → the same-company idempotent path.
    result = team_db.accept_invite_for_user(user_id=member_y, email=email)
    assert result is not None
    assert member_y in {m["user_id"] for m in _project_member_rows(project["id"])}


def test_projectless_invite_adds_no_project_member(isolated_settings):
    """AC-10 non-breakage: an invite with NO project_id (every existing WJ
    caller) adds no project_members row and accepts exactly as before."""
    owner = "owner-" + uuid.uuid4().hex[:6]
    company_id = seed_company(user_id=owner)
    project = _new_project(company_id, owner)  # exists, but the invite ignores it

    newbie = "plain-" + uuid.uuid4().hex[:6]
    email = f"{newbie}@acme.example"
    invite = team_db.create_invite(
        company_id=company_id, email=email, role="member", invited_by=owner
    )
    assert invite.get("project_id") is None
    result = team_db.accept_invite_for_user(user_id=newbie, email=email)
    # Membership landed (existing behaviour intact) …
    assert result is not None and result["company_id"] == company_id
    # … but NO project_members row was added.
    assert newbie not in {m["user_id"] for m in _project_member_rows(project["id"])}


# ── AC-10 non-breakage: send_invite_email copy ───────────────────────────


def test_send_invite_email_projectless_copy_unchanged_and_projectscoped(
    isolated_settings, monkeypatch
):
    """No project_name → byte-identical existing subject (non-breakage for
    every WJ caller); with project_name → the project-scoped copy. Reuses the
    invite-email test's Resend/generate_link harness."""
    from tests._company_helpers import company_client
    from tests.test_team_invite_email import _install_fake_admin, _set_resend
    from app.team_email import send_invite_email

    company_client(monkeypatch)  # reloads app.config; wires the fake db client
    _install_fake_admin(monkeypatch)
    post_mock = _set_resend(monkeypatch)

    # project_name=None → the exact pre-existing subject (matches
    # test_team_invite_email.py's assertion).
    send_invite_email("fresh@co.com", inviter_first_name="Ada", workspace_name="Acme")
    plain = post_mock.call_args.kwargs["json"]
    assert plain["subject"] == "Ada has invited you to Sprntly to collaborate"
    assert "collaborate on" not in plain["subject"]

    # project_name set → project-scoped copy carrying the NAME only.
    send_invite_email(
        "fresh2@co.com", inviter_first_name="Ada", workspace_name="Acme",
        project_name="Falcon Launch",
    )
    proj = post_mock.call_args.kwargs["json"]
    assert proj["subject"] == "Ada has invited you to collaborate on Falcon Launch"
    assert "Falcon Launch" in proj["html"]
    assert "Falcon Launch" in proj["text"]


# ── env-gated real-DB accept ─────────────────────────────────────────────

_RUN_LIVE = os.getenv("RUN_INVITE_PROJECT_ASSOCIATION_LIVE") == "1"


@pytest.mark.integration
@pytest.mark.skipif(
    not _RUN_LIVE,
    reason=(
        "needs a real local Supabase — set RUN_INVITE_PROJECT_ASSOCIATION_LIVE=1 "
        "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
        "and the projects/team migrations applied (incl. "
        "20260813140200_workspace_invites_project.sql)"
    ),
)
def test_accept_invite_lands_project_member_live():
    """A real accept of a project-carrying invite inserts the real
    project_members row (new-company path) end to end through the app db
    layer against real Postgres — the fake-DB tests above prove the logic;
    this proves it round-trips through PostgREST + real FKs."""
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live accept against a non-loopback SUPABASE_URL ({url!r})"
    )
    sb = create_client(url, settings.supabase_service_role_key)

    # Borrow a real auth user (a profile) not currently in any company — the
    # new-company accept path needs a real auth.users id for the FK.
    member_user_ids = {
        r["user_id"] for r in (sb.table("company_members").select("user_id").execute().data or [])
    }
    borrowed = (
        sb.table("profiles")
        .select("id, email")
        .not_.in_("id", list(member_user_ids) or ["_none_"])
        .limit(1)
        .execute()
        .data
    )
    assert borrowed and borrowed[0].get("email"), (
        "need >=1 real profile not already in any company, with an email"
    )
    accepter_id = borrowed[0]["id"]
    accepter_email = borrowed[0]["email"]

    company_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    project = None
    try:
        sb.table("companies").insert(
            {
                "id": company_id,
                "slug": f"invite-assoc-live-{uuid.uuid4().hex[:8]}",
                "display_name": "Invite assoc live",
            }
        ).execute()
        sb.table("workspaces").insert(
            {
                "id": workspace_id,
                "company_id": company_id,
                "name": "WS",
                "slug": "ws",
                "is_default": True,
            }
        ).execute()
        project = (
            sb.table("projects")
            .insert(
                {
                    "company_id": company_id,
                    "workspace_id": workspace_id,
                    "name": f"Invite assoc live {uuid.uuid4().hex[:6]}",
                    "created_by": accepter_id,
                }
            )
            .execute()
            .data[0]
        )

        team_db.create_invite(
            company_id=company_id,
            email=accepter_email,
            role="member",
            invited_by=accepter_id,
            workspace_ids=[workspace_id],
            project_id=project["id"],
        )
        result = team_db.accept_invite_for_user(user_id=accepter_id, email=accepter_email)
        assert result is not None and result["company_id"] == company_id

        landed = (
            sb.table("project_members")
            .select("user_id")
            .eq("project_id", project["id"])
            .eq("user_id", accepter_id)
            .execute()
            .data
        )
        assert landed, "live accept did not land a real project_members row"
    finally:
        if project is not None:
            sb.table("project_members").delete().eq("project_id", project["id"]).execute()
            sb.table("workspace_invites").delete().eq("project_id", project["id"]).execute()
            sb.table("projects").delete().eq("id", project["id"]).execute()
        sb.table("workspace_members").delete().eq("workspace_id", workspace_id).execute()
        sb.table("company_members").delete().eq("company_id", company_id).execute()
        sb.table("workspaces").delete().eq("id", workspace_id).execute()
        sb.table("companies").delete().eq("id", company_id).execute()

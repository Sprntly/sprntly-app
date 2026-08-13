"""Real local-Supabase round-trip for `app.db.projects.resolve_candidate`.

Mirrors `test_projects_crud_live.py`'s fixture shape (reuse a real
company/workspace/user tuple already in the rig, fabricate a SECOND real
company/workspace pair) for the identical reason: `FakeSupabaseClient` is an
in-memory store with no real SQL engine behind it, so the fast-lane suite
(`test_resolve_candidate.py`) can only prove the tenancy re-assertion was
CALLED with the right arguments over monkeypatched stand-ins — not that it
holds against a genuine second `company_members`/`workspace_members` row
reached through the real supabase-py client over real HTTP via PostgREST.

Run it with:

    RUN_RESOLVE_CANDIDATE_LIVE=1 \\
        pytest tests/test_resolve_candidate_live.py -m integration

Skips cleanly (same posture as `test_projects_crud_live.py`) unless the
local rig is up and the env var is set. Everything this file creates
(two projects, a fabricated foreign company/workspace, a borrowed real
profile's membership in that foreign company) is deleted in fixture
teardown; every reused company/workspace/user/profile row is read, never
mutated.
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_RESOLVE_CANDIDATE_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_RESOLVE_CANDIDATE_LIVE=1 with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and "
            "the projects/team/workspaces migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live resolve_candidate round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    """A real (company, workspace) pair already in the rig, plus a
    fabricated SECOND (company, workspace) — real FK-backed rows, unlike
    the fake-DB tests — used to prove a real cross-tenant account/email
    genuinely refuses through the LIVE membership checks."""
    from app.db.artifact_shares import owning_company_domain

    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces")
        .select("id")
        .eq("company_id", company_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
        .data
    )
    assert workspaces, f"no default workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owning_domain = owning_company_domain(company_id)
    assert owning_domain, (
        f"company {company_id} has no resolvable owner/admin domain — "
        "seed an owner or admin company_members row with a profile email"
    )

    owner_rows = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .eq("role", "owner")
        .limit(1)
        .execute()
        .data
    )
    assert owner_rows, f"no owner company_members row for company {company_id}"
    creator_user_id = owner_rows[0]["user_id"]

    # A real workspace member (this project's workspace) who is NOT on the
    # project — the t_workspace fixture.
    ws_member_rows = (
        sb.table("workspace_members")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .neq("user_id", creator_user_id)
        .limit(1)
        .execute()
        .data
    )
    assert ws_member_rows, f"need >=1 non-owner workspace_members row for workspace {workspace_id}"
    workspace_member_user_id = ws_member_rows[0]["user_id"]

    # A real company member of the SAME company who is NOT a member of this
    # workspace — the t_company fixture.
    ws_member_user_ids = {
        row["user_id"]
        for row in (
            sb.table("workspace_members")
            .select("user_id")
            .eq("workspace_id", workspace_id)
            .execute()
            .data
            or []
        )
    }
    company_member_rows = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    company_only_candidates = [
        r["user_id"] for r in company_member_rows if r["user_id"] not in ws_member_user_ids
    ]
    assert company_only_candidates, (
        f"need a company_members row for company {company_id} that is NOT also a "
        f"workspace_members row of workspace {workspace_id}"
    )
    company_only_user_id = company_only_candidates[0]

    def _email_for(user_id: str) -> str:
        rows = sb.table("profiles").select("email").eq("id", user_id).limit(1).execute().data
        assert rows and rows[0].get("email"), f"profile {user_id} has no email"
        return rows[0]["email"]

    workspace_member_email = _email_for(workspace_member_user_id)
    company_only_email = _email_for(company_only_user_id)

    newuser_email = f"resolve-candidate-live-{uuid.uuid4().hex[:10]}@{owning_domain}"
    cross_company_email = f"resolve-candidate-live-{uuid.uuid4().hex[:10]}@totally-different-domain.test"

    # A real profile, currently unaffiliated with any company, borrowed to
    # prove the "real account in a DIFFERENT company" refuse case.
    borrowed_rows = (
        sb.table("profiles")
        .select("id, email")
        .not_.in_("id", [r["user_id"] for r in company_member_rows])
        .limit(1)
        .execute()
        .data
    )
    assert borrowed_rows, "need >=1 profile row not already a member of the primary company"
    other_company_user_id = borrowed_rows[0]["id"]
    other_company_email = borrowed_rows[0]["email"]

    # Fabricate a SECOND real company/workspace — the foreign tenant.
    foreign_company_id = str(uuid.uuid4())
    foreign_workspace_id = str(uuid.uuid4())
    sb.table("companies").insert(
        {
            "id": foreign_company_id,
            "slug": f"resolve-candidate-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "resolve_candidate live foreign tenant",
        }
    ).execute()
    sb.table("workspaces").insert(
        {
            "id": foreign_workspace_id,
            "company_id": foreign_company_id,
            "name": "Foreign workspace",
            "slug": "foreign",
            "is_default": True,
        }
    ).execute()
    sb.table("company_members").insert(
        {
            "id": str(uuid.uuid4()),
            "company_id": foreign_company_id,
            "user_id": other_company_user_id,
            "role": "member",
        }
    ).execute()

    # Projects: one in the primary tenant (what most tiers resolve against),
    # one in the foreign tenant (proves the SAME real primary-tenant account
    # refuses symmetrically when viewed from the other side).
    primary_project = (
        sb.table("projects")
        .insert(
            {
                "company_id": company_id,
                "workspace_id": workspace_id,
                "name": f"resolve_candidate live {uuid.uuid4().hex[:8]}",
                "created_by": creator_user_id,
            }
        )
        .execute()
        .data[0]
    )
    sb.table("project_members").insert(
        {"project_id": primary_project["id"], "user_id": creator_user_id}
    ).execute()

    foreign_project = (
        sb.table("projects")
        .insert(
            {
                "company_id": foreign_company_id,
                "workspace_id": foreign_workspace_id,
                "name": f"resolve_candidate live foreign {uuid.uuid4().hex[:8]}",
                "created_by": other_company_user_id,
            }
        )
        .execute()
        .data[0]
    )

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "owning_domain": owning_domain,
        "project_id": primary_project["id"],
        "workspace_member_email": workspace_member_email,
        "company_only_email": company_only_email,
        "newuser_email": newuser_email,
        "cross_company_email": cross_company_email,
        "other_company_email": other_company_email,
        "other_company_user_id": other_company_user_id,
        "foreign_company_id": foreign_company_id,
        "foreign_workspace_id": foreign_workspace_id,
        "foreign_project_id": foreign_project["id"],
    }

    sb.table("projects").delete().eq("id", primary_project["id"]).execute()
    sb.table("projects").delete().eq("id", foreign_project["id"]).execute()
    sb.table("company_members").delete().eq(
        "company_id", foreign_company_id
    ).eq("user_id", other_company_user_id).execute()
    sb.table("workspaces").delete().eq("id", foreign_workspace_id).execute()
    sb.table("companies").delete().eq("id", foreign_company_id).execute()


def test_tiers_classify_against_real_membership_tables(sb, fixture_ids):
    """AC2/3/4/5: the SAME resolver, driven with NO patched dependency,
    classifies against genuine `workspace_members`/`company_members`/
    `profiles` rows in two real tenants."""
    from app.db.projects import resolve_candidate

    # AC2 — a real workspace member not on the project -> t_workspace.
    ws_out = resolve_candidate(fixture_ids["project_id"], fixture_ids["workspace_member_email"])
    assert ws_out["tier"] == "t_workspace", ws_out
    assert ws_out["email"] == fixture_ids["workspace_member_email"].strip().lower()

    # AC3 — a real company member not in this workspace -> t_company.
    co_out = resolve_candidate(fixture_ids["project_id"], fixture_ids["company_only_email"])
    assert co_out["tier"] == "t_company", co_out
    assert co_out["email"] == fixture_ids["company_only_email"].strip().lower()

    # AC4 — no account, domain matches the company's real owning domain -> t_newuser.
    new_out = resolve_candidate(fixture_ids["project_id"], fixture_ids["newuser_email"])
    assert new_out == {"tier": "t_newuser", "email": fixture_ids["newuser_email"].strip().lower()}

    # AC5a — no account, foreign domain -> t_refuse cross_company.
    cross_out = resolve_candidate(fixture_ids["project_id"], fixture_ids["cross_company_email"])
    assert cross_out == {"tier": "t_refuse", "reason": "cross_company"}

    # AC5b/AC5d — a REAL account that is a member of a DIFFERENT company ->
    # t_refuse other_company, from THIS (primary) project.
    other_out = resolve_candidate(fixture_ids["project_id"], fixture_ids["other_company_email"])
    assert other_out == {"tier": "t_refuse", "reason": "other_company"}

    # Symmetric proof from the FOREIGN project: a real account that belongs
    # to the PRIMARY company/workspace also refuses from the foreign side —
    # the tenancy anchor is always the CALLER'S project, never the account's
    # own company.
    reverse_out = resolve_candidate(
        fixture_ids["foreign_project_id"], fixture_ids["workspace_member_email"]
    )
    assert reverse_out == {"tier": "t_refuse", "reason": "other_company"}

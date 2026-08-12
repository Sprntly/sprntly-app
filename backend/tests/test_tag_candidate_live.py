"""Real local-Supabase round-trip for the tag-action surface
(`POST /v1/projects/{id}/tag`) — drives a real `TestClient(app)` over real
HTTP through PostgREST against real Postgres (127.0.0.1:54322), NOT the
in-memory FakeSupabaseClient. Mirrors `test_projects_crud_live.py`'s harness
(reuse a real company/workspace/owner tuple; fabricate a SECOND real tenant).

Proves what the fake-DB blast-radius suite cannot: the cross-tenant refuse
(AD-TNM1) holds through the REAL route across TWO real tenants — a member of
a project in tenant A tags an identity belonging to tenant B and is refused
with zero writes in either tenant; a real t_workspace add lands a real
`project_members` row; a real t_newuser tag creates a real `workspace_invites`
row carrying `project_id`.

Run it with:

    RUN_TAG_CANDIDATE_LIVE=1 \\
        pytest tests/test_tag_candidate_live.py -m integration

Skips cleanly unless the local rig is up and the env var is set.
"""
from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_TAG_CANDIDATE_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_TAG_CANDIDATE_LIVE=1 with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET pointed "
            "at the local rig and the projects/team/workspaces migrations "
            "applied (incl. 20260812120200_workspace_invites_project.sql)"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live tag round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
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
    ) or sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owning_domain = owning_company_domain(company_id)
    assert owning_domain, f"company {company_id} has no resolvable owner/admin domain"

    owners = (
        sb.table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    creator_user_id = owners[0]["user_id"]

    # A real workspace member of THIS project's workspace who will NOT be on
    # the project — the t_workspace add fixture.
    ws_member = (
        sb.table("workspace_members")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .neq("user_id", creator_user_id)
        .limit(1)
        .execute()
        .data
    )
    assert ws_member, f"need >=1 non-creator workspace_members row for workspace {workspace_id}"
    ws_member_user_id = ws_member[0]["user_id"]
    ws_member_email = (
        sb.table("profiles").select("email").eq("id", ws_member_user_id).limit(1).execute().data
    )
    assert ws_member_email and ws_member_email[0].get("email"), "workspace member has no email"
    ws_member_email = ws_member_email[0]["email"]

    # A borrowed real profile not in company A, made a member of a fabricated
    # SECOND company — the cross-tenant refuse target (an identity in tenant B).
    company_a_members = {
        r["user_id"]
        for r in (
            sb.table("company_members").select("user_id").eq("company_id", company_id).execute().data
            or []
        )
    }
    borrowed = (
        sb.table("profiles")
        .select("id, email")
        .not_.in_("id", list(company_a_members))
        .limit(1)
        .execute()
        .data
    )
    assert borrowed and borrowed[0].get("email"), "need >=1 profile not in company A, with an email"
    foreign_user_id = borrowed[0]["id"]
    foreign_email = borrowed[0]["email"]

    foreign_company_id = str(uuid.uuid4())
    foreign_workspace_id = str(uuid.uuid4())
    sb.table("companies").insert(
        {
            "id": foreign_company_id,
            "slug": f"tag-live-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "Tag live foreign tenant",
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
            "user_id": foreign_user_id,
            "role": "member",
        }
    ).execute()

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "creator_user_id": creator_user_id,
        "ws_member_user_id": ws_member_user_id,
        "ws_member_email": ws_member_email,
        "owning_domain": owning_domain,
        "foreign_company_id": foreign_company_id,
        "foreign_email": foreign_email,
    }

    sb.table("company_members").delete().eq("company_id", foreign_company_id).execute()
    sb.table("companies").delete().eq("id", foreign_company_id).execute()


@pytest.fixture
def project_ids(sb):
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("workspace_invites").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()


def _bearer(user_id: str) -> dict[str, str]:
    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client(fixture_ids):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(fixture_ids["creator_user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


def _new_project(client, project_ids, name: str) -> dict:
    r = client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    project = r.json()
    project_ids.append(project["id"])
    return project


def test_cross_tenant_refuse_through_real_route_two_tenants(
    client, sb, fixture_ids, project_ids
):
    project = _new_project(client, project_ids, f"Tag live A {uuid.uuid4().hex[:8]}")

    invites_before = len(
        sb.table("workspace_invites").select("id").eq("project_id", project["id"]).execute().data
        or []
    )
    members_before = len(
        sb.table("project_members").select("user_id").eq("project_id", project["id"]).execute().data
        or []
    )

    # Tag an identity belonging to tenant B (a real account in the foreign
    # company) → opaque 403, zero writes in BOTH tenants.
    r = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["foreign_email"]}
    )
    assert r.status_code == 403, r.text

    invites_after = len(
        sb.table("workspace_invites").select("id").eq("project_id", project["id"]).execute().data
        or []
    )
    members_after = len(
        sb.table("project_members").select("user_id").eq("project_id", project["id"]).execute().data
        or []
    )
    assert invites_after == invites_before
    assert members_after == members_before
    # Nothing landed in tenant B either.
    assert (
        sb.table("workspace_invites")
        .select("id")
        .eq("company_id", fixture_ids["foreign_company_id"])
        .execute()
        .data
        or []
    ) == []


def test_workspace_add_and_newuser_invite_live(
    client, sb, fixture_ids, project_ids, monkeypatch
):
    # Avoid real email / auth-user side effects — the DB mutation is the proof.
    from app.routes import projects as projects_routes

    monkeypatch.setattr(projects_routes, "send_invite_email", lambda email, **kw: "sent")

    project = _new_project(client, project_ids, f"Tag live B {uuid.uuid4().hex[:8]}")

    # t_workspace: a real workspace member not on the project → real add.
    r = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["ws_member_email"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "t_workspace"
    landed = (
        sb.table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .eq("user_id", fixture_ids["ws_member_user_id"])
        .execute()
        .data
    )
    assert landed, "t_workspace add did not land a real project_members row"

    # t_newuser: a fresh email at company A's owning domain → real invite row
    # carrying project_id.
    new_email = f"tag-live-new-{uuid.uuid4().hex[:8]}@{fixture_ids['owning_domain']}"
    r2 = client.post(f"/v1/projects/{project['id']}/tag", json={"needle": new_email})
    assert r2.status_code == 200, r2.text
    assert r2.json()["tier"] == "t_newuser"
    invite = (
        sb.table("workspace_invites")
        .select("id, project_id, workspace_ids")
        .eq("company_id", fixture_ids["company_id"])
        .eq("email", new_email)
        .execute()
        .data
    )
    assert invite and invite[0]["project_id"] == project["id"], (
        "t_newuser tag did not create an invite row carrying project_id"
    )

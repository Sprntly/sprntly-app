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
            "applied (incl. 20260813140200_workspace_invites_project.sql)"
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
    # company) → the clear cross-company message (B5b), 409, zero writes in
    # BOTH tenants. (Was an opaque 403 before B5b — `other_company` is now
    # the one t_refuse reason that discloses.)
    r = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["foreign_email"]}
    )
    assert r.status_code == 409, r.text
    from app.db.team import CROSS_COMPANY_INVITE_MESSAGE

    assert r.json()["detail"] == CROSS_COMPANY_INVITE_MESSAGE

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

    monkeypatch.setattr(projects_routes, "dispatch_invite_email", lambda email, **kw: "sent")

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


def test_tag_other_company_domain_nonuser_email_mints_scoped_invite_live(
    client, sb, fixture_ids, project_ids
):
    """AC16a (policy match — the load-bearing proof, broadest allowed case):
    a brand-new email whose domain belongs to ANOTHER registered company
    (the fabricated foreign tenant) but who has no account anywhere still
    resolves t_newuser through the real route and mints exactly ONE pending
    invite carrying THIS project's company_id/workspace_id/project_id — the
    project-only same-domain gate this ticket removes was the only thing
    that used to refuse this case."""
    project = _new_project(client, project_ids, f"Tag live D {uuid.uuid4().hex[:8]}")
    other_company_domain = fixture_ids["foreign_email"].split("@", 1)[-1]
    new_email = f"tag-live-other-domain-{uuid.uuid4().hex[:8]}@{other_company_domain}"

    r = client.post(f"/v1/projects/{project['id']}/tag", json={"needle": new_email})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "t_newuser"

    invites = (
        sb.table("workspace_invites")
        .select("id, company_id, project_id, workspace_ids, email")
        .eq("company_id", fixture_ids["company_id"])
        .eq("email", new_email)
        .execute()
        .data
        or []
    )
    assert len(invites) == 1, invites
    invite = invites[0]
    assert invite["project_id"] == project["id"]
    assert fixture_ids["workspace_id"] in (invite.get("workspace_ids") or [])


def test_accepted_invite_scoped_to_inviting_tenant_live(client, sb, fixture_ids, project_ids):
    """AC16b: accepting the invite from AC16a grants `company_members`/
    `workspace_members` rows ONLY for the inviting company + that workspace,
    and `project_members` ONLY for the invited project — a real DB read
    confirms NO membership in any other tenant (incl. the fabricated
    foreign company this fixture also created)."""
    from app.db import team as team_db

    project = _new_project(client, project_ids, f"Tag live E {uuid.uuid4().hex[:8]}")
    other_company_domain = fixture_ids["foreign_email"].split("@", 1)[-1]
    new_email = f"tag-live-accept-{uuid.uuid4().hex[:8]}@{other_company_domain}"

    r = client.post(f"/v1/projects/{project['id']}/tag", json={"needle": new_email})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "t_newuser"

    # Borrow a real profile currently unaffiliated with ANY company to accept
    # as — the new-company accept path needs a real id for the FK (same
    # pattern `test_invite_project_association.py`'s live accept test uses).
    member_user_ids = {
        row["user_id"] for row in (sb.table("company_members").select("user_id").execute().data or [])
    }
    borrowed = (
        sb.table("profiles")
        .select("id, email")
        .not_.in_("id", list(member_user_ids) or ["_none_"])
        .neq("email", new_email)
        .limit(1)
        .execute()
        .data
    )
    assert borrowed and borrowed[0].get("email"), "need >=1 unaffiliated real profile to accept as"
    accepter_id = borrowed[0]["id"]

    # `accept_invite_for_user` resolves the invite by EMAIL — update the
    # invite's email to the borrowed accepter's real (verified) email so the
    # accept path can be driven for real, mirroring how a real sign-up would
    # match its own verified email against the pending invite.
    accepter_email = borrowed[0]["email"]
    sb.table("workspace_invites").update({"email": accepter_email}).eq(
        "company_id", fixture_ids["company_id"]
    ).eq("email", new_email).execute()

    try:
        result = team_db.accept_invite_for_user(user_id=accepter_id, email=accepter_email)
        assert result is not None and result["company_id"] == fixture_ids["company_id"]

        company_rows = (
            sb.table("company_members").select("company_id").eq("user_id", accepter_id).execute().data
            or []
        )
        assert {row["company_id"] for row in company_rows} == {fixture_ids["company_id"]}, (
            "accepted invitee landed in an unexpected company"
        )

        workspace_rows = (
            sb.table("workspace_members").select("workspace_id").eq("user_id", accepter_id).execute().data
            or []
        )
        assert {row["workspace_id"] for row in workspace_rows} == {fixture_ids["workspace_id"]}, (
            "accepted invitee landed in an unexpected workspace"
        )

        project_rows = (
            sb.table("project_members").select("project_id").eq("user_id", accepter_id).execute().data
            or []
        )
        assert {row["project_id"] for row in project_rows} == {project["id"]}, (
            "accepted invitee landed in an unexpected project"
        )

        # No membership in the fabricated FOREIGN tenant either.
        foreign_membership = (
            sb.table("company_members")
            .select("user_id")
            .eq("company_id", fixture_ids["foreign_company_id"])
            .eq("user_id", accepter_id)
            .execute()
            .data
            or []
        )
        assert foreign_membership == []
    finally:
        sb.table("project_members").delete().eq("project_id", project["id"]).eq(
            "user_id", accepter_id
        ).execute()
        sb.table("workspace_members").delete().eq("user_id", accepter_id).eq(
            "workspace_id", fixture_ids["workspace_id"]
        ).execute()
        sb.table("company_members").delete().eq("user_id", accepter_id).eq(
            "company_id", fixture_ids["company_id"]
        ).execute()


def test_accepted_invitee_has_no_cross_project_access_live(client, sb, fixture_ids, project_ids):
    """AC16c: post-accept, the invitee's real bearer token cannot reach a
    project in a DIFFERENT company through the real route.

    Landmark drift (flagged for the planner): the ticket's AC text says
    `_require_project_member` "still 403s" that user for a foreign-company
    project. The actual code (`routes/projects.py::_require_project`) 404s a
    cross-tenant project id ON PURPOSE (existence-non-disclosure, AD-TNM1 —
    docstring: "404 (never 403) on any mismatch or absence, so a foreign
    project id's existence is never disclosed"); `_require_project_member`
    only 403s a SAME-tenant non-member. This test asserts the REAL code's
    404, not the AC's stated 403 — the code is the authority per the
    engagement's landmark-drift rule."""
    from app.config import settings
    from fastapi.testclient import TestClient
    import app.main as main_mod
    from app.db import team as team_db

    project = _new_project(client, project_ids, f"Tag live F {uuid.uuid4().hex[:8]}")
    other_company_domain = fixture_ids["foreign_email"].split("@", 1)[-1]
    new_email = f"tag-live-crossproj-{uuid.uuid4().hex[:8]}@{other_company_domain}"

    r = client.post(f"/v1/projects/{project['id']}/tag", json={"needle": new_email})
    assert r.status_code == 200, r.text

    member_user_ids = {
        row["user_id"] for row in (sb.table("company_members").select("user_id").execute().data or [])
    }
    borrowed = (
        sb.table("profiles")
        .select("id, email")
        .not_.in_("id", list(member_user_ids) or ["_none_"])
        .neq("email", new_email)
        .limit(1)
        .execute()
        .data
    )
    assert borrowed and borrowed[0].get("email"), "need >=1 unaffiliated real profile to accept as"
    accepter_id = borrowed[0]["id"]
    accepter_email = borrowed[0]["email"]
    sb.table("workspace_invites").update({"email": accepter_email}).eq(
        "company_id", fixture_ids["company_id"]
    ).eq("email", new_email).execute()

    # A real project that lives in the FABRICATED FOREIGN company/workspace —
    # what the accepted invitee must never reach.
    foreign_workspace_id = (
        sb.table("workspaces")
        .select("id")
        .eq("company_id", fixture_ids["foreign_company_id"])
        .limit(1)
        .execute()
        .data
    )
    assert foreign_workspace_id, "fixture's foreign company has no workspace"
    foreign_project = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["foreign_company_id"],
                "workspace_id": foreign_workspace_id[0]["id"],
                "name": f"Tag live foreign project {uuid.uuid4().hex[:6]}",
                "created_by": accepter_id,
            }
        )
        .execute()
        .data[0]
    )

    try:
        team_db.accept_invite_for_user(user_id=accepter_id, email=accepter_email)

        accepter_headers = _bearer(accepter_id)
        accepter_headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
        accepter_headers["Origin"] = settings.origins_list[0]
        accepter_client = TestClient(main_mod.app, headers=accepter_headers)

        r2 = accepter_client.get(f"/v1/projects/{foreign_project['id']}")
        assert r2.status_code == 404, r2.text
    finally:
        sb.table("projects").delete().eq("id", foreign_project["id"]).execute()
        sb.table("project_members").delete().eq("project_id", project["id"]).eq(
            "user_id", accepter_id
        ).execute()
        sb.table("workspace_members").delete().eq("user_id", accepter_id).eq(
            "workspace_id", fixture_ids["workspace_id"]
        ).execute()
        sb.table("company_members").delete().eq("user_id", accepter_id).eq(
            "company_id", fixture_ids["company_id"]
        ).execute()


def test_seat_and_membership_gates_hold_live(client, sb, fixture_ids, project_ids, monkeypatch):
    """AC18: unrelated guards this ticket must NOT weaken still fire —
    seat-limit 409 (full company), the already-member idempotent response,
    `_require_project_member` GATE 1 (a same-tenant non-member can't even
    reach `/tag`), and the existing-user `other_company` carve-out (AC15)."""
    from app.config import settings
    from app.routes import projects as projects_routes
    from fastapi.testclient import TestClient
    import app.main as main_mod

    project = _new_project(client, project_ids, f"Tag live G {uuid.uuid4().hex[:8]}")

    # GATE 1 — a same-tenant WORKSPACE member who is NOT yet a project member
    # cannot reach `/tag` at all (403, before `resolve_candidate` runs).
    ws_member_headers = _bearer(fixture_ids["ws_member_user_id"])
    ws_member_headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    ws_member_headers["Origin"] = settings.origins_list[0]
    ws_member_client = TestClient(main_mod.app, headers=ws_member_headers)
    r_gate1 = ws_member_client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["ws_member_email"]}
    )
    assert r_gate1.status_code == 403, r_gate1.text

    # Already-member idempotent response — tag the SAME workspace member
    # twice; the second call notifies (t_member), no duplicate write.
    r1 = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["ws_member_email"]}
    )
    assert r1.status_code == 200 and r1.json()["tier"] == "t_workspace", r1.text
    r2 = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["ws_member_email"]}
    )
    assert r2.status_code == 200 and r2.json()["tier"] == "t_member", r2.text
    landed = (
        sb.table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .eq("user_id", fixture_ids["ws_member_user_id"])
        .execute()
        .data
        or []
    )
    assert len(landed) == 1, "idempotent re-tag must not duplicate the project_members row"

    # Existing-user `other_company` carve-out (AC15) — still refuses through
    # the real route; now the clear cross-company message + 409 (B5b) rather
    # than the opaque 403 it returned before this ticket.
    r_other = client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": fixture_ids["foreign_email"]}
    )
    assert r_other.status_code == 409, r_other.text

    # Seat-limit 409 — stub the seat check itself (real DB stays real for
    # every other assertion in this test); mirrors this file's own
    # `dispatch_invite_email` stub pattern above.
    monkeypatch.setattr(projects_routes, "get_seat_limit", lambda company_id: 0)
    new_email = f"tag-live-seatlimit-{uuid.uuid4().hex[:8]}@{fixture_ids['owning_domain']}"
    r_seat = client.post(f"/v1/projects/{project['id']}/tag", json={"needle": new_email})
    assert r_seat.status_code == 409, r_seat.text

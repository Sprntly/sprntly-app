"""Real local-Supabase round-trip for the project artifacts fan-out:
`db/artifacts.py::list_artifacts_for_project` + `GET`/`POST
/v1/projects/{id}/artifacts` — against a real local Postgres (127.0.0.1:54322),
not `FakeSupabaseClient`. Same posture as `test_projects_crud_live.py`: no
`supabase_client` patching, a real `TestClient(app)` over real HTTP through
PostgREST.

Proves what the blast-radius (fake-DB) suite in
`test_project_artifacts_fanout.py` cannot: the fan-out reuse, the
write-time ownership gate (`require_owned_prd` against a REAL brief→prd
chain), and the membership gate all round-trip through real Postgres via
the real supabase-py client.

Run it with:

    RUN_PROJECT_ARTIFACTS_LIVE=1 \\
        pytest tests/test_project_artifacts_fanout_live.py -m integration

Skips cleanly unless the local rig is up and the env var is set.
"""
from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_ARTIFACTS_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECT_ARTIFACTS_LIVE=1 with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET pointed "
            "at the local rig and the projects/chat/memory migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    """A real (company, workspace, dataset, user) tuple already in the rig,
    a SECOND same-tenant user never added to any project (membership-gate
    probe), a real brief+prd owned by that company (the ownership-gate
    happy path), and a fabricated foreign (company, workspace, brief, prd)
    pair (the cross-tenant IDOR probe)."""
    companies = sb.table("companies").select("id, slug").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]
    dataset = companies[0]["slug"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owners = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    other_members = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .neq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    assert other_members, (
        f"need a SECOND company_members row (any role) for company {company_id} "
        "to prove the same-tenant non-member gate"
    )
    non_member_user_id = other_members[0]["user_id"]
    existing_ws_member = (
        sb.table("workspace_members")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("user_id", non_member_user_id)
        .limit(1)
        .execute()
        .data
    )
    created_ws_member = False
    if not existing_ws_member:
        sb.table("workspace_members").insert(
            {
                "id": str(uuid.uuid4()),
                "workspace_id": workspace_id,
                "user_id": non_member_user_id,
                "role": "member",
            }
        ).execute()
        created_ws_member = True

    # A real, owned brief + prd (the write-time ownership-gate happy path).
    own_brief = sb.table("briefs").insert(
        {
            "dataset": dataset,
            "week_label": f"Live fan-out {uuid.uuid4().hex[:8]}",
            "payload": {},
            "is_current": False,
        }
    ).execute().data[0]
    own_prd = sb.table("prds").insert(
        {"brief_id": own_brief["id"], "insight_index": 0, "title": "Live fan-out PRD", "status": "ready"}
    ).execute().data[0]

    # A fully foreign tenant + its own brief + prd (the cross-tenant IDOR probe).
    foreign_company_id = str(uuid.uuid4())
    foreign_workspace_id = str(uuid.uuid4())
    sb.table("companies").insert(
        {
            "id": foreign_company_id,
            "slug": f"live-fanout-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "Live fan-out foreign tenant",
        }
    ).execute()
    sb.table("workspaces").insert(
        {
            "id": foreign_workspace_id,
            "company_id": foreign_company_id,
            "name": "Foreign workspace",
            "slug": "foreign",
        }
    ).execute()
    foreign_brief = sb.table("briefs").insert(
        {
            "dataset": f"live-fanout-foreign-{uuid.uuid4().hex[:8]}",
            "week_label": "Foreign wk",
            "payload": {},
            "is_current": False,
        }
    ).execute().data[0]
    foreign_prd = sb.table("prds").insert(
        {"brief_id": foreign_brief["id"], "insight_index": 0, "title": "Foreign PRD", "status": "ready"}
    ).execute().data[0]

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "dataset": dataset,
        "user_id": user_id,
        "non_member_user_id": non_member_user_id,
        "own_prd_id": own_prd["id"],
        "foreign_company_id": foreign_company_id,
        "foreign_workspace_id": foreign_workspace_id,
        "foreign_prd_id": foreign_prd["id"],
    }

    sb.table("prds").delete().eq("id", own_prd["id"]).execute()
    sb.table("briefs").delete().eq("id", own_brief["id"]).execute()
    sb.table("prds").delete().eq("id", foreign_prd["id"]).execute()
    sb.table("briefs").delete().eq("id", foreign_brief["id"]).execute()
    sb.table("companies").delete().eq("id", foreign_company_id).execute()
    if created_ws_member:
        sb.table("workspace_members").delete().eq(
            "workspace_id", workspace_id
        ).eq("user_id", non_member_user_id).execute()


@pytest.fixture
def project_ids():
    created: list[int] = []
    yield created


@pytest.fixture(autouse=True)
def _cleanup_projects(sb, project_ids):
    yield
    for pid in project_ids:
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

    headers = _bearer(fixture_ids["user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture(scope="module")
def non_member_client(fixture_ids):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(fixture_ids["non_member_user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


def test_add_and_list_owned_prd_roundtrip(client, fixture_ids, project_ids):
    project = client.post(
        "/v1/projects", json={"name": f"Live fan-out {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    added = client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": fixture_ids["own_prd_id"]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["artifact_id"] == fixture_ids["own_prd_id"]

    listed = client.get(f"/v1/projects/{project['id']}/artifacts")
    assert listed.status_code == 200, listed.text
    ids = [a["id"] for a in listed.json()["artifacts"] if a["type"] == "prd"]
    assert fixture_ids["own_prd_id"] in ids

    # Repeat add dedupes (PK) rather than erroring.
    again = client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": fixture_ids["own_prd_id"]},
    )
    assert again.status_code == 200, again.text


def test_add_foreign_prd_404_no_ref_written(client, sb, fixture_ids, project_ids):
    project = client.post(
        "/v1/projects", json={"name": f"Live fan-out IDOR {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    r = client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": fixture_ids["foreign_prd_id"]},
    )
    assert r.status_code == 404

    refs = sb.table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_list_foreign_tenant_project_404(client, sb, fixture_ids, project_ids):
    foreign_project = sb.table("projects").insert(
        {
            "company_id": fixture_ids["foreign_company_id"],
            "workspace_id": fixture_ids["foreign_workspace_id"],
            "name": "Foreign tenant project",
            "created_by": fixture_ids["user_id"],
        }
    ).execute().data[0]
    project_ids.append(foreign_project["id"])

    r = client.get(f"/v1/projects/{foreign_project['id']}/artifacts")
    assert r.status_code == 404


def test_same_tenant_non_member_403_on_artifacts(client, non_member_client, fixture_ids, project_ids):
    """The exact membership gap the WAVE invariant requires: a real second
    account in the SAME company/workspace, never added to the project, is
    403'd on both GET and POST — distinct from the cross-tenant 404 above."""
    project = client.post(
        "/v1/projects", json={"name": f"Live fan-out membership gate {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    r_get = non_member_client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r_get.status_code == 403

    r_post = non_member_client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": fixture_ids["own_prd_id"]},
    )
    assert r_post.status_code == 403

    # The real owner (an actual member) is unaffected.
    r_owner = client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r_owner.status_code == 200

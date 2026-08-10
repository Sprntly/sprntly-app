"""Real local-Supabase round-trip for the `db/projects.py` +
`db/project_memory_entries.py` helpers AND the `/v1/projects*` routes
themselves — not just the schema (that's `test_projects_schema_roundtrip.py`,
which proved the migration set's constraints).

Every other backend test substitutes `FakeSupabaseClient` for the Supabase
client. This file deliberately does NOT patch `supabase_client` — it drives
a real `TestClient(app)` over real HTTP through PostgREST against a real
local Postgres (127.0.0.1:54322), so `require_client()` throughout
`app.db.projects`/`app.db.project_memory_entries` talks to the real thing.
It proves what the blast-radius (fake-DB) suite cannot: the tenant-gate 404
parity, `project_belongs_to_company`, and the memory CRUD actually round-trip
through real Postgres via the real supabase-py client, not an in-memory
SQLite stand-in.

Run it with:

    RUN_PROJECTS_CRUD_LIVE=1 \\
        pytest tests/test_projects_crud_live.py -m integration

Skips cleanly (same posture as `test_projects_schema_roundtrip.py`) unless
the local rig is up and the env var is set.
"""
from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECTS_CRUD_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECTS_CRUD_LIVE=1 with "
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
        "refusing to run the live CRUD round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    """Reuse a real (company, workspace, user) tuple already in the rig,
    plus a fabricated SECOND (company, workspace) pair — real FK-backed
    rows, unlike the fake-DB tenant-gate tests — used only to prove
    `project_belongs_to_company` 404s on a genuine cross-tenant project."""
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

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
    user_id = owners[0]["user_id"]

    profile = (
        sb.table("profiles").select("email").eq("id", user_id).limit(1).execute().data
    )
    email = (profile[0]["email"] if profile else None) or f"{user_id}@example.invalid"
    if not profile or not profile[0].get("email"):
        sb.table("profiles").update({"email": email}).eq("id", user_id).execute()

    # A SECOND real, same-tenant/same-workspace account — resolves
    # require_workspace successfully but is never added to any project.
    # This is the exact shape that proves membership-gating (AD-P11): a
    # same-tenant non-member must be distinguishable from the fully
    # foreign-tenant probe below (403, not 404).
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

    foreign_company_id = str(uuid.uuid4())
    foreign_workspace_id = str(uuid.uuid4())
    sb.table("companies").insert(
        {
            "id": foreign_company_id,
            "slug": f"live-crud-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "Live CRUD foreign tenant",
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

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "email": email,
        "non_member_user_id": non_member_user_id,
        "foreign_company_id": foreign_company_id,
        "foreign_workspace_id": foreign_workspace_id,
    }

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
    """A TestClient over the real app with NO Supabase patching — every
    request this drives hits the real local Postgres via PostgREST."""
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(fixture_ids["user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture(scope="module")
def non_member_client(fixture_ids):
    """The SAME app/DB, driven as the real second same-tenant account that
    is never added to any project."""
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(fixture_ids["non_member_user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


def test_create_list_get_project_roundtrip(client, fixture_ids, project_ids):
    r = client.post("/v1/projects", json={"name": f"Live CRUD {uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    project = r.json()
    project_ids.append(project["id"])
    assert project["company_id"] == fixture_ids["company_id"]
    assert project["workspace_id"] == fixture_ids["workspace_id"]
    assert project["origin"] == "manual"

    r_list = client.get("/v1/projects")
    assert r_list.status_code == 200, r_list.text
    ids = [p["id"] for p in r_list.json()["projects"]]
    assert project["id"] in ids

    r_get = client.get(f"/v1/projects/{project['id']}")
    assert r_get.status_code == 200, r_get.text
    body = r_get.json()
    assert body["members"][0]["kind"] == "agent"
    assert body["members"][0]["name"] == "Sprntly"
    assert any(m.get("user_id") == fixture_ids["user_id"] for m in body["members"][1:])
    assert body["group_chat_id"] is None


def test_foreign_tenant_project_404(client, sb, fixture_ids, project_ids):
    foreign = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["foreign_company_id"],
                "workspace_id": fixture_ids["foreign_workspace_id"],
                "name": "Foreign tenant project",
                "created_by": fixture_ids["user_id"],
            }
        )
        .execute()
        .data[0]
    )
    project_ids.append(foreign["id"])

    r = client.get(f"/v1/projects/{foreign['id']}")
    assert r.status_code == 404

    # The `dataset` param this router never declares has no effect either.
    r2 = client.get("/v1/projects", params={"dataset": "whatever-slug"})
    assert foreign["id"] not in [p["id"] for p in r2.json()["projects"]]


def test_memory_crud_and_summary_roundtrip(client, fixture_ids, project_ids):
    project = client.post(
        "/v1/projects", json={"name": f"Live memory {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    added = client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Real-DB guardrail."}
    )
    assert added.status_code == 200, added.text
    entry = added.json()
    assert entry["author_user_id"] == fixture_ids["user_id"]
    assert entry["promoted_by"] is None

    listed = client.get(f"/v1/projects/{project['id']}/memory")
    assert listed.status_code == 200
    assert entry["id"] in [e["id"] for e in listed.json()["entries"]]

    edited = client.patch(
        f"/v1/projects/{project['id']}/memory/{entry['id']}", json={"body": "Edited guardrail."}
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "Edited guardrail."

    summary = client.get(f"/v1/projects/{project['id']}/memory/summary")
    assert summary.status_code == 200
    assert summary.json()["summary_md"] is None
    assert summary.json()["entry_count"] == 1

    deleted = client.delete(f"/v1/projects/{project['id']}/memory/{entry['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    other_project = client.post(
        "/v1/projects", json={"name": f"Live memory sibling {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(other_project["id"])
    cross = client.patch(
        f"/v1/projects/{other_project['id']}/memory/{entry['id']}", json={"body": "hijack"}
    )
    assert cross.status_code == 404


def test_add_member_roundtrip(client, fixture_ids, project_ids, sb):
    project = client.post(
        "/v1/projects", json={"name": f"Live members {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    r = client.post(
        f"/v1/projects/{project['id']}/members", json={"email": fixture_ids["email"]}
    )
    # The creator is already a member — adding them again is an idempotent
    # no-op via upsert, not a duplicate-key error.
    assert r.status_code == 200, r.text

    members = (
        sb.table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert [m["user_id"] for m in members] == [fixture_ids["user_id"]]


def test_same_tenant_non_member_is_blocked(client, non_member_client, fixture_ids, project_ids):
    """The membership gap this proves shut: a real second account in the
    SAME company/workspace, never added to the project, must be blocked
    from detail + every memory route (403) and must not see the project
    in their own list — even though `require_workspace` resolves them
    successfully (they are NOT a foreign tenant, AC6 already covers
    that case)."""
    project = client.post(
        "/v1/projects", json={"name": f"Live membership gate {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    owned_entry = client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Owner's real guardrail"}
    ).json()

    r_detail = non_member_client.get(f"/v1/projects/{project['id']}")
    assert r_detail.status_code == 403

    r_list = non_member_client.get(f"/v1/projects/{project['id']}/memory")
    assert r_list.status_code == 403

    r_add = non_member_client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Injected by a non-member"}
    )
    assert r_add.status_code == 403

    r_summary = non_member_client.get(f"/v1/projects/{project['id']}/memory/summary")
    assert r_summary.status_code == 403

    r_edit = non_member_client.patch(
        f"/v1/projects/{project['id']}/memory/{owned_entry['id']}",
        json={"body": "Hijacked by a non-member"},
    )
    assert r_edit.status_code == 403

    r_delete = non_member_client.delete(f"/v1/projects/{project['id']}/memory/{owned_entry['id']}")
    assert r_delete.status_code == 403

    # The list scoped to the non-member excludes this project entirely.
    r_their_list = non_member_client.get("/v1/projects")
    assert project["id"] not in [p["id"] for p in r_their_list.json()["projects"]]

    # Nothing the blocked calls attempted actually landed: the owner's
    # entry is untouched and reads back exactly as written.
    r_reread = client.get(f"/v1/projects/{project['id']}/memory")
    bodies = [e["body"] for e in r_reread.json()["entries"]]
    assert bodies == ["Owner's real guardrail"]

    # The real owner (an actual member) is unaffected by any of this.
    r_owner_detail = client.get(f"/v1/projects/{project['id']}")
    assert r_owner_detail.status_code == 200

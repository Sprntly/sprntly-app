"""Real local-Supabase round-trip for the group-chat surface: `db/conversations.py`'s
`create_group_chat`/`get_group_chat`/`list_group_turns`/`post_group_turn` helpers
AND the `/v1/projects/{id}/group*` routes, driven over real HTTP through
PostgREST against a real local Postgres (127.0.0.1:54322) — not the fake-DB
substitute the rest of the suite uses.

Proves what the blast-radius (fake-DB) suite in `test_group_chat_turns.py`
cannot: the `uq_one_group_chat_per_project` partial unique index, the
`project_chat_members` roster seed, and the membership gate actually
round-trip through real Postgres via the real supabase-py client.

The `@Sprntly`-triggered LLM call is STUBBED here too (`app.routes.projects.call_md`
monkeypatched) — the group turn/DB wiring is what this round-trip proves;
the memory-synthesis and smart-interjection surfaces are where the build
spec calls for a real-LLM live run (a later phase, not this surface).
Stubbing it here proves the DB write path (assistant turn persisted,
author_user_id NULL) without spending real Anthropic credits against the
shared key.

Run it with:

    RUN_GROUP_CHAT_LIVE=1 \\
        pytest tests/test_group_chat_turns_live.py -m integration

Skips cleanly unless the local rig is up and the env var is set.
"""
from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_GROUP_CHAT_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_GROUP_CHAT_LIVE=1 with "
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
        "refusing to run the live group-chat round-trip against a non-loopback "
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
    plus a second real same-tenant user (membership-gate probe) and a
    fabricated foreign (company, workspace) pair — mirrors
    test_projects_crud_live.py's fixture exactly."""
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

    profile = sb.table("profiles").select("id, full_name, role").eq("id", user_id).limit(1).execute().data
    if not profile or not profile[0].get("full_name"):
        sb.table("profiles").upsert(
            {"id": user_id, "email": f"{user_id}@example.invalid", "full_name": "Live Owner", "role": "Engineer"}
        ).execute()
        owner_name, owner_role = "Live Owner", "Engineer"
    else:
        owner_name, owner_role = profile[0]["full_name"], profile[0].get("role")

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
            "slug": f"live-group-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "Live group-chat foreign tenant",
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
        "owner_name": owner_name,
        "owner_role": owner_role,
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


@pytest.fixture
def stub_group_llm(monkeypatch):
    """Stub the ONE LLM call site the mention-reply path uses — see module
    docstring for why this live test still stubs it (cost avoidance; not
    one of the build spec's real-LLM-gated tickets)."""
    calls: list[dict] = []

    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):
        calls.append({"system": system, "user": user, "model": model})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 87,
                    "output_tokens": 33,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return "Live-stubbed reply from Sprntly."

    import app.routes.projects as projects_route

    monkeypatch.setattr(projects_route, "call_md", _fake_call_md)
    return calls


def test_group_chat_full_roundtrip(client, fixture_ids, project_ids, sb, stub_group_llm):
    project = client.post(
        "/v1/projects", json={"name": f"Live group chat {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    # Create-if-absent, idempotent.
    r_create1 = client.post(f"/v1/projects/{project['id']}/group")
    assert r_create1.status_code == 200, r_create1.text
    conversation = r_create1.json()
    assert conversation["kind"] == "group"
    r_create2 = client.post(f"/v1/projects/{project['id']}/group")
    assert r_create2.json()["id"] == conversation["id"]

    roster = (
        sb.table("project_chat_members")
        .select("user_id")
        .eq("conversation_id", conversation["id"])
        .execute()
        .data
    )
    assert fixture_ids["user_id"] in {r["user_id"] for r in roster}

    # Human turn — no LLM call.
    r_human = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "kicking off the real-DB round trip"},
    )
    assert r_human.status_code == 200, r_human.text
    assert stub_group_llm == []
    assert r_human.json()["author_user_id"] == fixture_ids["user_id"]
    assert r_human.json()["role"] == "user"

    # @Sprntly turn — one agent reply.
    r_mention = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly can you confirm this round-trips?"},
    )
    assert r_mention.status_code == 200, r_mention.text
    assert len(stub_group_llm) == 1

    r_list = client.get(f"/v1/projects/{project['id']}/group/turns")
    assert r_list.status_code == 200, r_list.text
    turns = r_list.json()["turns"]
    assert [t["role"] for t in turns] == ["user", "user", "assistant"]
    assert turns[0]["author_user_id"] == fixture_ids["user_id"]
    assert turns[0]["author_name"] == fixture_ids["owner_name"]
    assert turns[0]["author_job_role"] == fixture_ids["owner_role"]
    assert turns[-1]["author_user_id"] is None
    assert turns[-1]["author_name"] == "Sprntly"
    assert turns[-1]["content"] == "Live-stubbed reply from Sprntly."

    # Poll cursor: since the first turn's id excludes it.
    r_since = client.get(
        f"/v1/projects/{project['id']}/group/turns", params={"since": turns[0]["id"]}
    )
    assert [t["id"] for t in r_since.json()["turns"]] == [t["id"] for t in turns[1:]]


def test_foreign_tenant_group_404(client, sb, fixture_ids, project_ids):
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

    assert client.post(f"/v1/projects/{foreign['id']}/group").status_code == 404
    assert client.get(f"/v1/projects/{foreign['id']}/group/turns").status_code == 404
    assert (
        client.post(
            f"/v1/projects/{foreign['id']}/group/turns", json={"content": "x"}
        ).status_code
        == 404
    )


def test_same_tenant_non_member_403(client, non_member_client, fixture_ids, project_ids):
    project = client.post(
        "/v1/projects", json={"name": f"Live gate {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    client.post(f"/v1/projects/{project['id']}/group")

    assert (
        non_member_client.post(f"/v1/projects/{project['id']}/group").status_code == 403
    )
    assert (
        non_member_client.get(
            f"/v1/projects/{project['id']}/group/turns"
        ).status_code
        == 403
    )
    assert (
        non_member_client.post(
            f"/v1/projects/{project['id']}/group/turns", json={"content": "hi"}
        ).status_code
        == 403
    )

    # The real owner (a real member) is unaffected.
    assert client.get(f"/v1/projects/{project['id']}/group/turns").status_code == 200


def test_individual_conversation_id_not_reachable_via_group_routes(
    client, sb, fixture_ids, project_ids
):
    """AD-P2 isolation, against the real DB: an individual conversation
    scoped to this project cannot be surfaced by the group routes — the
    group turns list stays empty even after a private turn exists."""
    project = client.post(
        "/v1/projects", json={"name": f"Live isolation {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    individual = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "user_id": fixture_ids["user_id"],
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    sb.table("conversation_turns").insert(
        {
            "conversation_id": individual["id"],
            "role": "user",
            "content": "private individual-chat turn",
        }
    ).execute()

    r_list = client.get(f"/v1/projects/{project['id']}/group/turns")
    assert r_list.status_code == 200
    # No group chat has been created for this project — the individual
    # conversation never counts as one, regardless of its project_id.
    assert r_list.json()["turns"] == []

    sb.table("conversations").delete().eq("id", individual["id"]).execute()

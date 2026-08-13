"""Real local-Supabase / real-HTTP round-trip proof for the delegation-
ledger emit endpoint — the one piece `test_delegation_events_api.py`'s
fast lane cannot prove: that `current_status`/`list_status_for_assignee`/
`list_status_for_assigner` correctly evaluate the ACTUAL Postgres
`v_delegation_status` view (a `left join lateral`) through the REAL route,
not a data-driven stand-in ([[feedback_stubbed-e2e-masks-loop-behaviour]]).

No `ANTHROPIC_API_KEY` needed — this endpoint makes no LLM call at all
(AC11); gating is purely on a real local Supabase.

Mirrors `test_delegation_events.py`'s / `test_project_delegation.py`'s
live-tier fixture shape: reuses an existing (company, workspace) already
in the local rig plus two of its `company_members` rows (assigner,
assignee) rather than minting new `auth.users` rows.

Run it with:

    RUN_PROJECT_LEDGER_LIVE=1 \\
        pytest tests/test_project_ledger_live.py -m integration
"""
from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_LEDGER_LIVE") == "1"

_LIVE_SKIP_REASON = (
    "needs a real local Supabase — set RUN_PROJECT_LEDGER_LIVE=1 with "
    "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET pointed at "
    "the local rig and the project_delegations/delegation_events migrations "
    "applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live ledger round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
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
        .limit(2)
        .execute()
        .data
    )
    assert len(owners) >= 2, (
        f"need >=2 owner/admin company_members rows for company {company_id} "
        "(one assigner, one assignee)"
    )
    assigner_id, assignee_id = owners[0]["user_id"], owners[1]["user_id"]

    return {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "assigner_id": assigner_id,
        "assignee_id": assignee_id,
    }


def _bearer(user_id: str) -> dict[str, str]:
    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_client(user_id: str, workspace_id: str):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(user_id)
    headers["X-Workspace-Id"] = workspace_id
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture(scope="module")
def assigner_client(fixture_ids):
    return _make_client(fixture_ids["assigner_id"], fixture_ids["workspace_id"])


@pytest.fixture(scope="module")
def assignee_client(fixture_ids):
    return _make_client(fixture_ids["assignee_id"], fixture_ids["workspace_id"])


@pytest.fixture
def project_ids(sb):
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("delegation_events").delete().in_(
            "delegation_id",
            [
                d["id"]
                for d in sb.table("project_delegations")
                .select("id")
                .eq("project_id", pid)
                .execute()
                .data
            ],
        ).execute()
        sb.table("project_delegations").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()


def _seed_delegation(sb, fixture_ids, project_id: int) -> dict:
    from app.db.project_delegations import record_delegation

    return record_delegation(
        project_id=project_id,
        assigner_user_id=fixture_ids["assigner_id"],
        assignee_user_id=fixture_ids["assignee_id"],
        task_summary=f"live ledger round-trip {uuid.uuid4().hex[:8]}",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=None,
        delivered_turn_id=None,
    )


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_accept_complete_round_trip_through_route(
    sb, fixture_ids, assigner_client, assignee_client, project_ids
):
    """assigned -> accepted -> completed through the REAL route +
    `v_delegation_status`: both parties' reads reflect the final status
    (AC1, AC7)."""
    project = assigner_client.post(
        "/v1/projects", json={"name": f"Live ledger {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    sb.table("project_members").upsert(
        {"project_id": project["id"], "user_id": fixture_ids["assignee_id"]}
    ).execute()

    deleg = _seed_delegation(sb, fixture_ids, project["id"])

    r1 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "accepted"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json() == {"delegation_id": deleg["id"], "status": "accepted"}

    r2 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "completed"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"delegation_id": deleg["id"], "status": "completed"}

    view_row = (
        sb.table("v_delegation_status")
        .select("status")
        .eq("delegation_id", deleg["id"])
        .execute()
        .data[0]
    )
    assert view_row["status"] == "completed"

    waiting_on = assigner_client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "waiting_on"}
    ).json()
    mine = next(row for row in waiting_on if row["delegation_id"] == deleg["id"])
    assert mine["status"] == "completed"
    assert mine["bucket"] == "done"

    assigned_to_me = assignee_client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "assigned_to_me"}
    ).json()
    mine2 = next(row for row in assigned_to_me if row["delegation_id"] == deleg["id"])
    assert mine2["status"] == "completed"
    assert mine2["bucket"] == "done"

    events = (
        sb.table("delegation_events")
        .select("event")
        .eq("delegation_id", deleg["id"])
        .order("id")
        .execute()
        .data
    )
    assert [e["event"] for e in events] == ["accepted", "completed"]


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_illegal_edge_rejected_live(sb, fixture_ids, assigner_client, assignee_client, project_ids):
    """A real `completed -> accepted` is rejected (409) and writes NOTHING
    to the live table (AC5)."""
    project = assigner_client.post(
        "/v1/projects", json={"name": f"Live ledger illegal {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    sb.table("project_members").upsert(
        {"project_id": project["id"], "user_id": fixture_ids["assignee_id"]}
    ).execute()

    deleg = _seed_delegation(sb, fixture_ids, project["id"])

    r1 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "completed"},
    )
    assert r1.status_code == 409, r1.text  # assigned -> completed is illegal

    events_before = (
        sb.table("delegation_events").select("id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert events_before == []

    r2 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "accepted"},
    )
    assert r2.status_code == 200, r2.text

    r3 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "completed"},
    )
    assert r3.status_code == 200, r3.text

    r4 = assignee_client.post(
        f"/v1/projects/{project['id']}/delegations/{deleg['id']}/events",
        json={"event": "accepted"},
    )
    assert r4.status_code == 409, r4.text  # completed -> accepted is illegal

    events_after = (
        sb.table("delegation_events")
        .select("event")
        .eq("delegation_id", deleg["id"])
        .order("id")
        .execute()
        .data
    )
    assert [e["event"] for e in events_after] == ["accepted", "completed"]

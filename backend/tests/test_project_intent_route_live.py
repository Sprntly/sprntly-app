"""Real local-Supabase + real-LLM round-trip for
`POST /v1/projects/{project_id}/chat/intent` — proving the private-chat
classify fix against a REAL model, not a mocked envelope: for a project with
exactly one attached PRD, an edit-phrased message classifies `edit_prd`
carrying the server-resolved `prd_id`, so `resolve_chat_intent`'s
`_NEEDS_PRD` downgrade (`chat_intent.py:431`) does not fire on real model
output the way it always did when the private chat classified with an empty
target.

Every other backend test for this route substitutes a monkeypatched
`resolve_chat_intent` (`test_project_intent_route.py`, the deterministic
fast-lane backstop). This file deliberately does not: it is the only test
that proves the SUCCESS path holds against a real Anthropic call, mirroring
the sibling write-route live test's real-DB, real-route (not function-direct)
discipline.

Gated on BOTH a real LLM and the run flag; skips cleanly otherwise.
Registered in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_INTENT_LIVE` and `ANTHROPIC_API_KEY`. Restores the DB to its
pre-test state.

    RUN_PROJECT_INTENT_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_project_intent_route_live.py -m integration
"""
from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_INTENT_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase + a real LLM — set "
            "RUN_PROJECT_INTENT_LIVE=1 and ANTHROPIC_API_KEY, with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and "
            "the projects/prds/prd_versions migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live intent round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with one project carrying one PRD —
    the single-PRD auto-select case `_resolve_prd_id` server-resolves.
    Cleans up every row it created."""
    from app.db import projects as projects_db
    from app.db.client import require_client

    c = require_client()

    members = sb.table("company_members").select("company_id, user_id").limit(50).execute().data
    company_id = workspace_id = user_id = slug = None
    for m in members:
        comp = sb.table("companies").select("slug").eq("id", m["company_id"]).limit(1).execute().data
        ws = sb.table("workspaces").select("id").eq("company_id", m["company_id"]).limit(1).execute().data
        if comp and comp[0].get("slug") and ws:
            company_id, workspace_id, user_id, slug = (
                m["company_id"], ws[0]["id"], m["user_id"], comp[0]["slug"]
            )
            break
    assert company_id, "no (company w/ slug, workspace, member) in the local rig"

    created = {"projects": [], "briefs": [], "prds": []}

    def _brief(label):
        row = c.table("briefs").insert({
            "dataset": slug, "week_label": label, "is_current": False,
            "payload": {"insights": []},
        }).execute().data[0]
        created["briefs"].append(row["id"])
        return row["id"]

    def _prd(brief_id, title):
        row = c.table("prds").insert({
            "brief_id": brief_id, "insight_index": 0, "title": title,
            "payload_md": f"# {title}\n\nOriginal problem statement.", "status": "ready",
        }).execute().data[0]
        created["prds"].append(row["id"])
        return row["id"]

    def _project(name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id
        )
        created["projects"].append(p["id"])
        return p["id"]

    project_id = _project("intent route live")
    prd_id = _prd(_brief("intent route live"), "Onboarding PRD")
    projects_db.add_artifact(project_id, "prd", prd_id)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "user_id": user_id,
        "project_id": project_id, "prd_id": prd_id,
    }

    for pid in created["projects"]:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for prd_id in created["prds"]:
        sb.table("prd_versions").delete().eq("prd_id", prd_id).execute()
        sb.table("prds").delete().eq("id", prd_id).execute()
    for bid in created["briefs"]:
        sb.table("briefs").delete().eq("id", bid).execute()


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


def test_project_edit_message_classifies_edit_prd_with_target_live(scene):
    client = _make_client(scene["user_id"], scene["workspace_id"])

    resp = client.post(
        f"/v1/projects/{scene['project_id']}/chat/intent",
        json={"message": "Please tighten the problem statement in the onboarding PRD."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The proof: a real model call classified `edit_prd` AND the server-
    # resolved `prd_id` survived — i.e. the `_NEEDS_PRD` downgrade in
    # `resolve_chat_intent` (chat_intent.py:431) did not fire, because this
    # route (unlike the pre-fix client call) never sends an empty target.
    assert body["intent"] == "edit_prd", body
    assert body["prd_id"] == scene["prd_id"]


@pytest.fixture
def scene_two_prds(sb):
    """A real (company, workspace, user) with one project carrying TWO
    PRDs — the genuine-ambiguity case the private route must now surface
    as a `clarify` instead of the silent no-op it returned pre-fix."""
    from app.db import projects as projects_db
    from app.db.client import require_client

    c = require_client()

    members = sb.table("company_members").select("company_id, user_id").limit(50).execute().data
    company_id = workspace_id = user_id = slug = None
    for m in members:
        comp = sb.table("companies").select("slug").eq("id", m["company_id"]).limit(1).execute().data
        ws = sb.table("workspaces").select("id").eq("company_id", m["company_id"]).limit(1).execute().data
        if comp and comp[0].get("slug") and ws:
            company_id, workspace_id, user_id, slug = (
                m["company_id"], ws[0]["id"], m["user_id"], comp[0]["slug"]
            )
            break
    assert company_id, "no (company w/ slug, workspace, member) in the local rig"

    created = {"projects": [], "briefs": [], "prds": []}

    def _brief(label):
        row = c.table("briefs").insert({
            "dataset": slug, "week_label": label, "is_current": False,
            "payload": {"insights": []},
        }).execute().data[0]
        created["briefs"].append(row["id"])
        return row["id"]

    def _prd(brief_id, title):
        row = c.table("prds").insert({
            "brief_id": brief_id, "insight_index": 0, "title": title,
            "payload_md": f"# {title}\n\nOriginal problem statement.", "status": "ready",
        }).execute().data[0]
        created["prds"].append(row["id"])
        return row["id"]

    def _project(name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id
        )
        created["projects"].append(p["id"])
        return p["id"]

    project_id = _project("intent route two-prd live")
    brief_id = _brief("intent route two-prd live")
    prd_a = _prd(brief_id, "Onboarding PRD")
    prd_b = _prd(brief_id, "Billing PRD")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "user_id": user_id,
        "project_id": project_id, "prd_ids": {prd_a, prd_b},
    }

    for pid in created["projects"]:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for prd_id in created["prds"]:
        sb.table("prd_versions").delete().eq("prd_id", prd_id).execute()
        sb.table("prds").delete().eq("id", prd_id).execute()
    for bid in created["briefs"]:
        sb.table("briefs").delete().eq("id", bid).execute()


def test_two_prd_edit_returns_clarify_with_both_options_live(scene_two_prds):
    """AC1/AC2 (real model): a 2-PRD project's edit-phrased message → the
    private route returns a real `clarify` listing BOTH PRDs — the "which
    PRD?" disambiguation `_resolve_prd_id` already computes, surfaced
    instead of discarded.

    KNOWN GAP, flagged for ship-gate/planner — NOT fixed by this ticket:
    AC2's second half ("supplying an id applies the edit in place") is
    UNVERIFIABLE against the current write path. `POST /{project_id}/prd/
    chat-edit` (`routes/projects.py::project_chat_edit`) resolves its edit
    target via `_resolve_prd_id({}, project_id, dataset, company_id)` with
    a HARD-CODED empty `tool_input` — there is no `prd_id` field on
    `ProjectChatEditIn` for a client to supply an id through, and this
    ticket's Deliverables do not touch that route (only `_resolve_prd_id`'s
    CALLERS may not change its signature; the write route itself is out of
    scope here). On a 2+-PRD project the write route will keep refusing
    EVERY follow-up message, disambiguated or not, until a follow-up
    ticket threads a client-resolved id through it. This test proves the
    achievable half (the clarify itself, real-LLM) and stops there."""
    client = _make_client(scene_two_prds["user_id"], scene_two_prds["workspace_id"])

    resp = client.post(
        f"/v1/projects/{scene_two_prds['project_id']}/chat/intent",
        json={"message": "Please tighten the problem statement in the PRD."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "clarify", body
    assert body["prd_id"] is None
    assert isinstance(body.get("clarification"), str) and body["clarification"]
    listed_ids = {o["id"] for o in body.get("prd_options", [])}
    assert listed_ids == scene_two_prds["prd_ids"]

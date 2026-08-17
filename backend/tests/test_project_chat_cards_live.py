"""Real local-Supabase proof that a PROJECT chat's artifact cards are the
PROJECT's set — the regression's exact shape, which the fake-Supabase tier
(`test_chat_envelope_shared.py`) proves in wiring but not against the real
five-table fan-out through PostgREST.

Every other envelope test substitutes `FakeSupabaseClient`. This file
deliberately does NOT patch `supabase_client` — `enrich_chat_envelope` →
`list_artifacts_for_project` runs against a real local Postgres
(127.0.0.1:54322), so the ref fan-out, the tenant intersection and the
recency sort are the production code paths, not an in-memory stand-in.
No LLM is involved: the classifier's envelope is a fixed dict — the unit
under proof is the DATA leg, which is pure DB.

Run it with:

    RUN_PROJECT_CARDS_LIVE=1 \\
        pytest tests/test_project_chat_cards_live.py -m integration

Skips cleanly (same posture as `test_projects_crud_live.py`) unless the
local rig is up and the env var is set.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_CARDS_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECT_CARDS_LIVE=1 with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the projects migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live cards round-trip against a non-loopback "
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
    companies = sb.table("companies").select("id, slug").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id, slug = companies[0]["id"], companies[0]["slug"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"

    members = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
    )
    assert members, f"need >=1 company_members row for company {company_id}"

    yield {
        "company_id": company_id,
        "slug": slug,
        "workspace_id": workspaces[0]["id"],
        "user_id": members[0]["user_id"],
    }


@pytest.fixture
def created_rows(sb):
    """Teardown by CREATED ids only — the rig is shared; never sweep by
    slug/company."""
    created: dict[str, list[int]] = {"projects": [], "ticket_sets": []}
    yield created
    for pid in created["projects"]:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for sid in created["ticket_sets"]:
        sb.table("ticket_sets").delete().eq("id", sid).execute()


def test_project_cards_match_prose_real_db(sb, fixture_ids, created_rows):
    """A project holding FEWER artifacts than its workspace returns exactly
    the project's set in `artifact_list` (not the workspace's most-recent
    page) and counts exactly the project's total — while the default
    (no-`project_id`) path still lists workspace-wide, proven side by side
    on the same seeds."""
    from app.chat_envelope import enrich_chat_envelope

    company_id = fixture_ids["company_id"]
    set_ids: list[int] = []
    for i, title in enumerate(["Alpha tickets", "Beta tickets", "Gamma tickets"]):
        row = sb.table("ticket_sets").insert({
            "company_id": company_id,
            "title": title,
            "stories": [{"title": f"S{i}", "body": "b"}],
            "status": "ready",
        }).execute().data[0]
        set_ids.append(row["id"])
        created_rows["ticket_sets"].append(row["id"])

    project = sb.table("projects").insert({
        "company_id": company_id,
        "workspace_id": fixture_ids["workspace_id"],
        "name": "Cards live check",
        "origin": "manual",
        "created_by": fixture_ids["user_id"],
    }).execute().data[0]
    created_rows["projects"].append(project["id"])
    pinned = set_ids[0]
    sb.table("project_artifacts").insert({
        "project_id": project["id"],
        "artifact_type": "ticket_set",
        "artifact_id": pinned,
    }).execute()

    ctx = SimpleNamespace(company_id=company_id)

    def _envelope() -> dict:
        return {
            "intent": "list_artifacts", "list_kind": "ticket_set",
            "list_mode": "count",
        }

    scoped = enrich_chat_envelope(
        _envelope(), ctx, fixture_ids["slug"], project_id=project["id"]
    )
    assert {r["id"] for r in scoped["artifact_list"]} == {pinned}
    assert scoped["artifact_counts"]["total"] == 1

    workspace_wide = enrich_chat_envelope(_envelope(), ctx, fixture_ids["slug"])
    ws_ids = {r["id"] for r in workspace_wide["artifact_list"]}
    # The three just-seeded sets are the newest rows, so the recency-sorted
    # workspace page carries all of them — strictly wider than the project's
    # single pin, which is the regression's exact disagreement shape.
    assert set(set_ids) <= ws_ids
    assert workspace_wide["artifact_counts"]["total"] >= len(set_ids)

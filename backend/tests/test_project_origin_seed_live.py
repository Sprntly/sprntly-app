"""Real local-Supabase + real-Anthropic round trip for
`app/project_origin_seed.py::seed_project_origin_memory`
(`[[feedback_stubbed-e2e-masks-loop-behaviour]]`,
`[[reference_local-supabase-real-db-verification]]`) — a fully-stubbed LLM
(`test_project_origin_seed.py`) can prove the writer's wiring, never that a
real model actually produces a grounded brief or that the entry lands
through real PostgREST and the real `project_memory_summary` regen loop.

Gated behind `RUN_PROJECT_ORIGIN_SEED_LIVE=1` PLUS a real `ANTHROPIC_API_KEY`
— mirrors `test_project_memory_promotion.py`'s live-tier shape exactly (same
loopback-only guard, same fixture-id resolution against a real seeded
company/workspace/user in the local rig). Run with:

    RUN_PROJECT_ORIGIN_SEED_LIVE=1 \\
        pytest tests/test_project_origin_seed_live.py -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_ORIGIN_SEED_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_ORIGIN_SEED_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY "
    "pointed at the local rig and the projects/chat/memory migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live origin-seed round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
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
        .select("user_id")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    yield {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id}


@pytest.fixture
def live_seed(sb, fixture_ids):
    """A real project + a real originating conversation (with real
    `conversation_turns`) + a real PRD — the minimum a real
    `seed_project_origin_memory` call needs. Restores the DB afterward
    (deletes the project, its memory rows, the conversation, and the PRD)."""
    company_id = fixture_ids["company_id"]
    workspace_id = fixture_ids["workspace_id"]
    user_id = fixture_ids["user_id"]

    project = sb.table("projects").insert(
        {
            "company_id": company_id, "workspace_id": workspace_id,
            "name": f"Live origin seed {uuid.uuid4().hex[:8]}", "origin": "prd_auto",
            "created_by": user_id,
        }
    ).execute().data[0]

    conversation = sb.table("conversations").insert(
        {
            "company_id": company_id, "user_id": user_id,
            "title": "generate prd", "query": "generate prd",
        }
    ).execute().data[0]
    sb.table("conversation_turns").insert(
        [
            {
                "conversation_id": conversation["id"], "role": "user",
                "content": "We need a way for support reps to bulk-close stale tickets.",
            },
            {
                "conversation_id": conversation["id"], "role": "assistant",
                "content": (
                    "Drafting a PRD — we'll cap the bulk-close action to tickets "
                    "untouched for 30+ days and require a manager approval step "
                    "before the close is final."
                ),
            },
        ]
    ).execute()

    brief = sb.table("briefs").insert(
        {
            "dataset": f"live-origin-seed-{uuid.uuid4().hex[:8]}",
            "week_label": "Live origin seed", "payload": {}, "is_current": False,
        }
    ).execute().data[0]
    prd = sb.table("prds").insert(
        {
            "brief_id": brief["id"], "insight_index": 0,
            "title": f"Bulk-close stale tickets {uuid.uuid4().hex[:8]}",
            "status": "ready",
            "payload_md": (
                "# Bulk-close stale tickets\n\n"
                "Support reps need a bulk action to close tickets that have been "
                "untouched for 30+ days, gated behind a manager approval step."
            ),
        }
    ).execute().data[0]

    ids = {
        "project_id": project["id"], "conversation_id": conversation["id"],
        "prd_id": prd["id"], "prd_title": prd["title"],
    }
    yield ids

    sb.table("project_memory_summary").delete().eq("project_id", project["id"]).execute()
    sb.table("project_memory_entries").delete().eq("project_id", project["id"]).execute()
    sb.table("projects").delete().eq("id", project["id"]).execute()
    sb.table("conversation_turns").delete().eq("conversation_id", conversation["id"]).execute()
    sb.table("conversations").delete().eq("id", conversation["id"]).execute()
    sb.table("prds").delete().eq("id", prd["id"]).execute()
    sb.table("briefs").delete().eq("id", brief["id"]).execute()


@pytest.mark.integration
@pytest.mark.real_origin_seed_synthesis  # opt OUT of conftest's autouse call_json
# stub for the seed's own summarizer call — this test needs the real thing.
@pytest.mark.real_memory_synthesis  # opt OUT of conftest's autouse call_md stub —
# the seed's `schedule_regen` triggers the REAL `regenerate_summary` loop too.
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_seed_lands_memory_and_summary_regenerates_live(sb, live_seed):
    """AC-12: a real seed inserts >=1 real `project_memory_entries` row
    tagged `source_conversation_id`, and the scheduled regen (inline under
    pytest, same seam `schedule_regen` uses everywhere else) leaves
    `project_memory_summary` non-blank with `stale` cleared."""
    from app.project_origin_seed import seed_project_origin_memory

    seed_project_origin_memory(
        project_id=live_seed["project_id"], prd_id=live_seed["prd_id"],
        prd_title=live_seed["prd_title"], conversation_id=live_seed["conversation_id"],
    )

    entries = (
        sb.table("project_memory_entries")
        .select("*")
        .eq("project_id", live_seed["project_id"])
        .execute()
        .data
    )
    assert len(entries) >= 1, "the real seed must write at least the brief entry"
    for entry in entries:
        assert entry["promoted_by"] == "agent"
        assert entry["source_conversation_id"] == live_seed["conversation_id"]
        assert entry["body"].strip() != ""

    summary = (
        sb.table("project_memory_summary")
        .select("*")
        .eq("project_id", live_seed["project_id"])
        .execute()
        .data
    )
    assert summary, "the scheduled regen must have produced a summary row"
    assert summary[0]["stale"] is False
    assert summary[0]["summary_md"], "regen must produce a non-blank summary_md"

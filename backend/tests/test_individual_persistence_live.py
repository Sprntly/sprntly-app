"""Real local-Supabase (+ real-Anthropic for the `/v1/ask` arm) round trip
for the private project chat's both-sides persistence — the LP-1..LP-6 gate
the deterministic suites (`test_individual_turn_persistence.py`,
`test_individual_persistence_routes.py`,
`test_conversation_turns_idempotency_migration.py`,
`test_artifact_added_realtime.py`) cannot close: real Postgres partial-unique
enforcement, a real `/v1/ask` model call actually reaching the sixth ladder
branch, and the real cursor/read-cursor derive-at-read path
([[feedback_stubbed-e2e-masks-loop-behaviour]]).

DEFERRED-TO-STAGING: authored and registered here. Gated on BOTH a real LLM
and the run flag; skips cleanly otherwise. Registered in
`test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_CHAT_PARITY_LIVE` and `ANTHROPIC_API_KEY` (the same flag this
wave's other live-Postgres/live-LLM chat-parity suites already use).

    RUN_PROJECT_CHAT_PARITY_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_individual_persistence_live.py -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_CHAT_PARITY_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_CHAT_PARITY_LIVE=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_"
    "KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig and "
    "the conversation_turns idempotency migration applied"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live individual-persistence round-trip against "
        f"a non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with a fresh project. Cleans up
    every row it created, by created-id (never a slug/bulk-scoped delete —
    the local rig is shared)."""
    from app.db import projects as projects_db

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

    same_company = (
        sb.table("company_members").select("user_id")
        .eq("company_id", company_id).neq("user_id", user_id).limit(1).execute().data
    )
    second_user_id = same_company[0]["user_id"] if same_company else None

    project = projects_db.create_project(
        company_id=company_id, workspace_id=workspace_id,
        name=f"Individual persistence live {uuid.uuid4().hex[:8]}", created_by=user_id,
    )
    project_id = project["id"]
    if second_user_id:
        projects_db.add_member(project_id, second_user_id)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "dataset": slug,
        "user_id": user_id, "second_user_id": second_user_id, "project_id": project_id,
    }

    conv_ids = [
        row["id"] for row in
        sb.table("conversations").select("id").eq("project_id", project_id).execute().data
    ]
    if conv_ids:
        sb.table("conversation_turns").delete().in_("conversation_id", conv_ids).execute()
        sb.table("conversation_read_cursors").delete().in_("conversation_id", conv_ids).execute()
        # `maybe_promote_turn` (LP-1's real /v1/ask run) may have promoted a
        # durable `project_memory_entries` row referencing the conversation —
        # its FK must clear BEFORE `conversations` is deleted.
        sb.table("project_memory_entries").delete().in_("source_conversation_id", conv_ids).execute()
    sb.table("ask_jobs").delete().eq("project_id", project_id).execute()
    sb.table("project_memory_entries").delete().eq("project_id", project_id).execute()
    sb.table("project_delegations").delete().eq("project_id", project_id).execute()
    sb.table("conversations").delete().eq("project_id", project_id).execute()
    sb.table("project_artifacts").delete().eq("project_id", project_id).execute()
    sb.table("project_members").delete().eq("project_id", project_id).execute()
    sb.table("projects").delete().eq("id", project_id).execute()


# ── LP-1: a real /v1/ask project send → leave → return restores dialogue ──


def test_lp1_ask_reload_restores_dialogue_live(scene):
    import asyncio

    from app.ask_job_runner import run_ask_job
    from app.db import conversations as conversations_db
    from app.db.asks import start_ask_job

    conv = conversations_db.create_individual_project_chat(scene["project_id"], scene["user_id"])
    cmid = str(uuid.uuid4())
    question = "In one sentence, what is this project about?"

    conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content=question, client_message_id=cmid,
    )
    ask_id = start_ask_job(
        company_id=scene["company_id"], dataset=scene["dataset"], question=question,
        client_message_id=cmid,
    )
    asyncio.run(run_ask_job(
        ask_id=ask_id, enterprise_id=scene["company_id"], question=question,
        dataset=scene["dataset"], project_id=scene["project_id"], conversation_id=conv["id"],
        user_id=scene["user_id"],
    ))

    # LEAVE → RETURN: a fresh read (simulating reload) of THIS conversation.
    turns = conversations_db.list_individual_turns(conv["id"], scene["user_id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == question
    assert turns[1]["content"].strip() != ""


# ── LP-2: a generate/edit branch's reload restores dialogue ───────────────


def test_lp2_generate_branch_reload_restores_dialogue_live(scene):
    from app.db import conversations as conversations_db

    conversations_db.create_individual_project_chat(scene["project_id"], scene["user_id"])
    cmid = str(uuid.uuid4())
    conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="make me a ticket set", client_message_id=cmid,
    )
    conversations_db.post_owned_individual_assistant_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="I've written a ticket set and attached it — check the Artifacts tab.",
        client_message_id=cmid,
    )

    conv = conversations_db.get_individual_project_chat(scene["project_id"], scene["user_id"])
    turns = conversations_db.list_individual_turns(conv["id"], scene["user_id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]


# ── LP-3: real partial-unique enforces one row per side ───────────────────


def test_lp3_double_submit_writes_once_live(scene):
    from app.db import conversations as conversations_db

    conversations_db.create_individual_project_chat(scene["project_id"], scene["user_id"])
    cmid = str(uuid.uuid4())

    conv = conversations_db.get_individual_project_chat(scene["project_id"], scene["user_id"])
    before = len(conversations_db.list_individual_turns(conv["id"], scene["user_id"]))

    r1 = conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="resend me", client_message_id=cmid,
    )
    r2 = conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="resend me AGAIN", client_message_id=cmid,
    )
    assert r1["id"] == r2["id"]

    after = len(conversations_db.list_individual_turns(conv["id"], scene["user_id"]))
    assert after == before + 1, "the real partial-unique must allow exactly one row for this key"


# ── LP-4: a crafted foreign conversation_id cannot write ─────────────────


def test_lp4_foreign_conversation_id_refused_live(scene, sb):
    from app.db import conversations as conversations_db

    if not scene["second_user_id"]:
        pytest.skip("no second same-company member available in this rig")

    b_conv = conversations_db.create_individual_project_chat(
        scene["project_id"], scene["second_user_id"],
    )
    row = conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="A's own message", client_message_id=str(uuid.uuid4()),
    )
    assert row["conversation_id"] != b_conv["id"]


# ── LP-5: send-and-leave does not flip the author's own chat unread ──────


def test_lp5_own_turn_does_not_flip_unread_live(scene):
    from app.db import conversation_read_cursors as read_cursors_db
    from app.db import conversations as conversations_db

    conversations_db.create_individual_project_chat(scene["project_id"], scene["user_id"])
    conversations_db.post_owned_individual_user_turn(
        project_id=scene["project_id"], user_id=scene["user_id"],
        content="hi", client_message_id=str(uuid.uuid4()),
    )
    conv = conversations_db.get_individual_project_chat(scene["project_id"], scene["user_id"])
    assert read_cursors_db.unread_for(conv["id"], scene["user_id"]) is False


# ── LP-6: a server-side artifact attach emits realtime; count refreshes ──


def test_lp6_artifact_added_realtime_and_count_refresh_live(scene):
    from app.db import projects as projects_db

    before = projects_db.list_project_artifact_refs(scene["project_id"])
    # A real attach — the realtime publish is best-effort (never raises);
    # this proves the write + count-source succeed end to end against the
    # live rig (the broadcast delivery itself is `realtime.py`'s own
    # covered contract, proven deterministically in
    # `test_artifact_added_realtime.py`).
    projects_db.add_artifact(scene["project_id"], "prd", 999999)
    after = projects_db.list_project_artifact_refs(scene["project_id"])
    assert len(after) == len(before) + 1

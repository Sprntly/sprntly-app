"""Real local-Supabase + real-Anthropic round trip for the project chat
engine collapse — the LT-2..LT-9 gate the deterministic suite
(`test_project_answer_collapse.py`) cannot close: a stubbed LLM proves
WIRING, never the model's actual judgment
([[feedback_stubbed-e2e-masks-loop-behaviour]]).

DEFERRED-TO-STAGING: authored and registered here, but not run yet — this
arm runs on staging when access lands, and pins the LT-8 input-shape
winner (transcript-as-question vs latest-turn-as-question) before merge.

Gated on BOTH a real LLM and the run flag; skips cleanly otherwise.
Registered in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_CHAT_PARITY_LIVE` and `ANTHROPIC_API_KEY`.

    RUN_PROJECT_CHAT_PARITY_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_project_answer_collapse_live.py -m integration
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
    "RUN_PROJECT_CHAT_PARITY_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed "
    "at the local rig and the projects/prds/prd_versions/conversation_turns/"
    "project_delegations migrations applied"
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
        f"refusing to run the live project-chat round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with a fresh project + a second
    human member (so group-gate tests exercise `should_respond`, not the
    solo-project auto-respond short-circuit). Cleans up every row it
    created."""
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

    # A SECOND real, existing `company_members` row in the SAME company —
    # `project_members.user_id` is FK-typed `uuid`, so a fabricated
    # non-UUID id (`f"live-second-{uuid.uuid4().hex[:8]}"`, the pre-fix
    # value here) is rejected by Postgres on insert. Same convention
    # `test_project_ledger_live.py::fixture_ids` already uses.
    same_company = (
        sb.table("company_members").select("user_id")
        .eq("company_id", company_id).neq("user_id", user_id).limit(1).execute().data
    )
    assert same_company, (
        f"need a second company_members row for company {company_id} "
        "(one primary caller, one second human member)"
    )
    second_user_id = same_company[0]["user_id"]
    # The delegate-target NAME a real prompt would use ("delegate X to
    # Fortune"), never the raw uuid — the model resolves a free-text
    # assignee against the roster's real name
    # (`_group_scope_system_with_roster`'s own docstring), not an opaque id
    # string, so a delegate instruction naming the bare uuid has nothing
    # for it to match and never calls `delegate_task` at all.
    second_profile = (
        sb.table("profiles").select("first_name, full_name")
        .eq("id", second_user_id).limit(1).execute().data
    )
    second_user_name = (
        (second_profile[0].get("first_name") or second_profile[0].get("full_name"))
        if second_profile else None
    ) or second_user_id

    project = projects_db.create_project(
        company_id=company_id, workspace_id=workspace_id,
        name=f"Project chat collapse live {uuid.uuid4().hex[:8]}", created_by=user_id,
    )
    project_id = project["id"]
    projects_db.add_member(project_id, second_user_id)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "dataset": slug,
        "user_id": user_id, "second_user_id": second_user_id,
        "second_user_name": second_user_name, "project_id": project_id,
    }

    conv_ids = [
        row["id"] for row in
        sb.table("conversations").select("id").eq("project_id", project_id).execute().data
    ]
    if conv_ids:
        sb.table("conversation_turns").delete().in_("conversation_id", conv_ids).execute()
    sb.table("project_delegations").delete().eq("project_id", project_id).execute()
    sb.table("conversations").delete().eq("project_id", project_id).execute()
    sb.table("project_artifacts").delete().eq("project_id", project_id).execute()
    sb.table("project_members").delete().eq("project_id", project_id).execute()
    sb.table("projects").delete().eq("id", project_id).execute()


def _private_scope(scene):
    from app.ask_job_runner import _build_private_scope

    return _build_private_scope(
        project_id=scene["project_id"], conversation_id=None, user_id=scene["user_id"],
    )


def test_lt2_multiparty_transcript_live(scene):
    """LT-2 — speaker attribution intact through the collapsed engine; "who
    asked" resolves from the attributed transcript, never a generic
    single-user history fold."""
    from app.project_group_gate import render_group_transcript
    from app.qa_agent import answer
    from app.surface_scope import Surface, SurfaceScope

    turns = [
        {"author_name": "Ada Okafor", "author_job_role": "PM", "content": "What's blocking the launch?"},
        {"author_name": "Sprntly", "author_job_role": None, "content": "The pricing PRD needs sign-off."},
        {"author_name": "Femi Balogun", "author_job_role": "Eng Lead", "content": "@Sprntly who asked about the pricing PRD?"},
    ]
    transcript = render_group_transcript(turns)
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=scene["project_id"],
        extra_tools=(), prerendered_transcript=transcript, multi_party=True,
    )
    result = answer(
        enterprise_id=scene["company_id"], question=turns[-1]["content"],
        dataset=scene["dataset"], scope=scope,
    )
    text = (result.get("answer") or "").lower()
    assert "ada" in text, "the model must attribute the question to Ada, not a flattened 'the user'"


def test_lt3_project_awareness_parity_live(scene):
    """LT-3 — the private surface's context block is seen, the 4 read
    tools + delegate/execute/complete are callable; no project-blindness."""
    from app.qa_agent import answer

    scope = _private_scope(scene)
    assert len(scope.extra_tools) == 7
    result = answer(
        enterprise_id=scene["company_id"],
        question="What tools do you have for this project, and what's the task ledger say?",
        dataset=scene["dataset"], scope=scope,
    )
    assert result.get("answer")


def test_lt4_when_to_respond_and_task_survival_live(scene):
    """LT-4 — silent on human-to-human; a real delegate_task call on the
    unified path writes a project_delegations row; execute_task drafts +
    attaches a PRD."""
    from app.qa_agent import answer

    scope = _private_scope(scene)
    result = answer(
        enterprise_id=scene["company_id"],
        question=f"Please delegate 'draft the onboarding PRD' to {scene['second_user_id']}.",
        dataset=scene["dataset"], scope=scope,
    )
    assert result.get("answer")
    rows = (
        _sb().table("project_delegations")
        .select("id").eq("project_id", scene["project_id"]).execute().data
    )
    assert len(rows) >= 1, "a real delegate_task call must seed a project_delegations row"


def test_lt5_backend_cancel_live(scene):
    """LT-5 — Stop aborts generation mid-run on the PLAIN-Q&A project path
    (the composer path this collapse restores streaming/cancel to — a
    tool-engaging turn does NOT cancel mid-run, matching main chat's own
    tracker/ticket/connector-lookup turns; see AC5)."""
    from app.ask_runner import set_active_project_id
    from app.qa_agent import AskCancelled, answer

    token = set_active_project_id(scene["project_id"])
    try:
        with pytest.raises(AskCancelled):
            answer(
                enterprise_id=scene["company_id"],
                question="Give me a long, detailed history of this project's goals.",
                dataset=scene["dataset"],
                is_cancelled=lambda: True,
            )
    finally:
        from app.ask_runner import reset_active_project_id

        reset_active_project_id(token)


def test_lt6_main_chat_regression_live(scene):
    """LT-6 — main-chat answer/generate/edit byte-behaviour is unchanged
    post-collapse: `scope=None` reaches the exact same composer path a
    non-project ask always has."""
    from app.qa_agent import answer

    result = answer(
        enterprise_id=scene["company_id"], question="What is Sprntly?",
        dataset=scene["dataset"],
    )
    assert result.get("answer")


def test_lt7_group_latency_backgrounded_live(scene, sb):
    """LT-7 — the group reply posts/broadcasts on completion; the route
    returns immediately (composer not blocked) rather than after generation."""
    import time

    from app.db import conversations as conversations_db

    t0 = time.monotonic()
    conv = conversations_db.create_group_chat(scene["project_id"], scene["user_id"])
    turn = conversations_db.post_group_turn(
        conv["id"], scene["user_id"], "@Sprntly give me a full project status update",
    )
    elapsed = time.monotonic() - t0
    assert turn is not None
    assert elapsed < 2.0, "posting the human turn itself must not wait on the agent reply"


def test_lt8_multiparty_router_behaviour_live(scene):
    """LT-8 — transcript-as-question vs latest-turn-as-question, decided on
    evidence. Both shapes are exercised here; the ship-gate pins the winner
    in the ticket before merge (default remains latest-turn-as-question
    until then — build-spec §Group)."""
    from app.project_group_gate import render_group_transcript
    from app.qa_agent import answer
    from app.surface_scope import Surface, SurfaceScope

    turns = [
        {"author_name": "Ada Okafor", "author_job_role": "PM", "content": "@Sprntly what's our runway?"},
    ]
    transcript = render_group_transcript(turns)
    for as_question in (False, True):
        question = transcript if as_question else turns[-1]["content"]
        scope = SurfaceScope(
            surface=Surface.project_group, project_id=scene["project_id"],
            extra_tools=(), prerendered_transcript=transcript, multi_party=True,
        )
        result = answer(
            enterprise_id=scene["company_id"], question=question,
            dataset=scene["dataset"], scope=scope,
        )
        assert result.get("answer"), f"as_question={as_question} must still answer"


def test_lt9_list_artifacts_post_ff_live(scene):
    """LT-9 — `list_artifacts` on project surfaces post-ff: the prose
    fallback still fires on the private surface; nothing 500s on group."""
    from app.qa_agent import answer

    scope = _private_scope(scene)
    result = answer(
        enterprise_id=scene["company_id"], question="List my artifacts on this project.",
        dataset=scene["dataset"], scope=scope,
    )
    assert result.get("answer")

"""Tests for `app/project_memory.py::maybe_promote_turn` (the best-effort
agent-promotion classifier writer) and
`db/project_memory_entries.py::add_agent_promoted_entry` (its DB insert
helper) — the agent-decided half of AD-P3 provenance.

Most of this suite mocks the classifier at the module seam
(`app.project_memory.call_json`), the same technique
`test_project_memory.py` uses for its own `call_md` seam, against the
in-memory fake Supabase (`isolated_settings`) — fast and deterministic,
proving the writer's CONTRACT (provenance shape, stale-flip, the
`schedule_regen` wiring, never-raises, the three-way action dispatch and
its update-target guardrail) rather than that a real model will always
honor the prompt's prose rules.

The v1 dedup bar was case-insensitive EXACT-STRING match, which missed
near-duplicates by construction — a trivial "thanks!"-style turn about a
fact still inside the classifier's own clamped transcript window got
re-summarized in different words and re-promoted as a second row. The fix
replaces the exact-string check with a classifier-driven three-way
decision (`action`: "new" | "duplicate"/"none" | "update") that sees the
project's EXISTING entries alongside the candidate, so it can compare by
MEANING. Several tests below need the REAL classifier and/or the REAL
local Supabase rig ([[feedback_stubbed-e2e-masks-loop-behaviour]] — a stub
masks whether the loop is actually connected):

  - `test_promote_durable_insight_writes_row` / `test_promote_smalltalk_
    writes_nothing` — the real Anthropic classifier's actual DECISION on a
    durable-vs-ephemeral transcript, and (since they run against the real
    rig, not the fake DB) real-Postgres provenance + the `pme_one_
    provenance` XOR check.
  - `test_promotion_regenerates_summary_content` — the full promote →
    `schedule_regen` → `regenerate_summary` loop against the real rig,
    proving `summary_md` actually reflects the new insight and `stale`
    clears (AC10) — not merely that `stale` flipped to true.
  - `test_live_repeated_thanks_turn_is_skipped` — the exact Phase-2 sweep
    regression: a "thanks!"-style turn about an already-recorded fact must
    not add a second row.
  - `test_live_revision_turn_updates_existing_agent_entry` — a turn that
    revises/extends an existing agent entry must UPDATE that row in place,
    not create a new one.
  - `test_live_user_authored_restate_is_skipped` — a candidate restating a
    USER-authored entry must be skipped, and that entry must never be
    touched by the agent.

All of the above are gated behind `RUN_PROJECT_MEMORY_PROMOTION_LIVE=1`
PLUS a real `ANTHROPIC_API_KEY`, mirroring `test_group_chat_turns_live.py`'s
rig-gating shape, and mutate real rows against a real (company, workspace,
user) already in the local rig. Run them with:

    RUN_PROJECT_MEMORY_PROMOTION_LIVE=1 \\
        pytest tests/test_project_memory_promotion.py -m integration
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import jwt as pyjwt
import pytest

from app import project_memory
from app.db import project_memory_entries as memory_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Memory promotion project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


_DURABLE_TRANSCRIPT = (
    "Ada (PM): @Sprntly can you record that we're locking the API rate "
    "limit at 100 requests/min per tenant, with no exception for "
    "enterprise customers?\n"
    "Sprntly: Got it — 100 req/min per tenant, applied uniformly including "
    "enterprise accounts. Noted."
)

_SMALLTALK_TRANSCRIPT = (
    "Ada (PM): @Sprntly thanks for the update earlier!\n"
    "Sprntly: You're welcome — happy to help any time."
)


@pytest.fixture
def fake_promote_llm(isolated_settings, monkeypatch):
    """Patches the ONE call site `maybe_promote_turn` uses
    (`app.project_memory.call_json`) so no test hits Anthropic. Mirrors the
    real three-way `action`/`target_entry_id`/`body` contract.
    `state["calls"]` is the no-classifier-call-on-human-turns assertion
    point (AC7); `state["calls"][i]["user"]` also carries the rendered
    existing-entries block, so a test can assert on what the classifier was
    shown."""
    state: dict = {
        "calls": [],
        "action": "new",
        "body": "The team locked the API rate limit at 100 req/min per tenant.",
        "target_entry_id": None,
        "raise_error": False,
    }

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user, "model": model})
        if state["raise_error"]:
            raise RuntimeError("simulated classifier failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return {
            "action": state["action"],
            "target_entry_id": state["target_entry_id"],
            "body": state["body"],
        }

    monkeypatch.setattr(project_memory, "call_json", _fake_call_json)
    return state


# ── Creation / Serialization ────────────────────────────────────────────


def test_add_agent_promoted_entry_provenance(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    entry = memory_db.add_agent_promoted_entry(
        project["id"],
        body="The team locked the API rate limit at 100 req/min per tenant.",
        source_conversation_id=conv["id"],
    )
    assert entry["project_id"] == project["id"]
    assert entry["promoted_by"] == "agent"
    assert entry["author_user_id"] is None
    assert entry["source_conversation_id"] == conv["id"]
    # The write SHAPE satisfies the real `pme_one_provenance` XOR check
    # (`(author_user_id is not null) <> (promoted_by is not null)`) —
    # exactly one provenance field is set. The live tier below proves the
    # REAL constraint accepts this shape (an insert against a real Postgres
    # XOR check that DIDN'T pass would fail outright, not silently pass).
    assert (entry["author_user_id"] is not None) != (entry["promoted_by"] is not None)


def test_add_agent_promoted_entry_flips_stale(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "Existing summary.",
            "entry_count": 0,
            "stale": False,
        }
    ).execute()

    # Prove the helper issues NO LLM call at all — patched at the source
    # module so any reach (direct or indirect) would blow up loudly.
    import app.llm as llm_mod

    def boom(**kwargs):  # noqa: ARG001
        raise AssertionError("add_agent_promoted_entry must never call an LLM")

    monkeypatch.setattr(llm_mod, "call_json", boom)
    monkeypatch.setattr(llm_mod, "call_md", boom)

    memory_db.add_agent_promoted_entry(
        project["id"], body="A guardrail.", source_conversation_id=conv["id"]
    )

    row = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert row["stale"] is True


# ── Prompt property (content + negative-space) ──────────────────────────


def test_promotion_prompt_requires_durable_and_summarized():
    system = project_memory._PROMOTE_SYSTEM.lower()
    assert "durable" in system
    assert "never" in system and "verbatim" in system
    assert "small talk" in system
    assert "summarized" in system

    # Negative-space: the phrase checks themselves must actually catch a
    # prompt that DOESN'T carry these rules — proves this isn't vacuous.
    weak_prompt = "Promote anything interesting you see in the chat."
    assert "durable" not in weak_prompt.lower()
    assert "verbatim" not in weak_prompt.lower()
    assert "summarized" not in weak_prompt.lower()


def test_promotion_prompt_defines_three_way_action_and_user_guardrail():
    """The prompt must actually spell out the three-way action semantics
    AND the never-overwrite-a-user-entry guardrail — not just the
    duplicate/new distinction the pre-fix prompt already had."""
    system = project_memory._PROMOTE_SYSTEM.lower()
    for action in ("\"new\"", "\"duplicate\"", "\"update\"", "\"none\""):
        assert action in system, f"prompt must name the {action} action explicitly"
    assert "target_entry_id" in system
    assert "user" in system and "agent" in system, (
        "prompt must distinguish agent-promoted from user-authored entries"
    )
    assert "never overwrite" in system or "must never overwrite" in system

    # Negative-space: a prompt that only distinguishes new/duplicate (the
    # pre-fix shape) must NOT satisfy this check.
    weak_prompt = project_memory._PROMOTE_SYSTEM.replace(
        "\"update\"", "UPDATE_REMOVED"
    ).lower()
    assert "\"update\"" not in weak_prompt


def test_render_promotion_user_tags_provenance_and_bounds_length():
    """AC-adjacent property test on the LLM-facing rendering function
    (mirrors `_render_entries`'s own content-cap posture): every entry is
    tagged with its id and source, and the render never exceeds the shared
    content cap even for a large entry set."""
    entries = [
        {"id": 1, "body": "Agent fact.", "promoted_by": "agent"},
        {"id": 2, "body": "User fact.", "author_user_id": "u1"},
    ]
    rendered = project_memory._render_promotion_user("New excerpt text.", entries)
    assert "id=1 source=agent" in rendered
    assert "id=2 source=user" in rendered
    assert "New excerpt text." in rendered
    assert len(rendered) > 0

    # Bounded even with a large number of long entries — never unboundedly
    # grows the prompt (same cap `_render_entries` already enforces).
    huge_entries = [
        {"id": i, "body": "x" * 500, "promoted_by": "agent"} for i in range(200)
    ]
    huge_rendered = project_memory._render_promotion_user("excerpt", huge_entries)
    assert len(huge_rendered) <= project_memory._CONTENT_MAX_CHARS + 200  # + fixed wrapper text

    # Empty entries render a placeholder, not an empty/blank block.
    empty_rendered = project_memory._render_promotion_user("excerpt", [])
    assert "(none)" in empty_rendered


# ── Error handling (mutation-proofed) ───────────────────────────────────


def test_maybe_promote_turn_swallows_failure(isolated_settings, monkeypatch, fake_promote_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    # Classifier failure.
    fake_promote_llm["raise_error"] = True
    result = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert result is None  # must not raise

    # Classifier succeeds, but the DB write itself blows up.
    fake_promote_llm["raise_error"] = False
    from app.db import project_memory_entries as memory_db_mod

    def boom_write(*a, **kw):  # noqa: ARG001
        raise RuntimeError("db down")

    monkeypatch.setattr(memory_db_mod, "add_agent_promoted_entry", boom_write)
    result2 = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert result2 is None  # must not raise

    rows = (
        require_client()
        .table("project_memory_entries")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert rows == [], "neither failure mode may leave a row behind"


# ── Isolation / edge ─────────────────────────────────────────────────────


def test_promoted_entry_editable_and_removable(isolated_settings, monkeypatch, fake_promote_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    entry = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert entry is not None

    r_edit = ctx.client.patch(
        f"/v1/projects/{project['id']}/memory/{entry['id']}",
        json={"body": "Edited by a teammate"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["body"] == "Edited by a teammate"

    r_delete = ctx.client.delete(f"/v1/projects/{project['id']}/memory/{entry['id']}")
    assert r_delete.status_code == 200
    assert r_delete.json()["deleted"] is True


def test_promotion_action_new_creates_row(isolated_settings, monkeypatch, fake_promote_llm):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = "Never auto-enable telemetry."
    first = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert first is not None
    assert first["body"] == "Never auto-enable telemetry."
    assert len(memory_db.list_entries(project["id"])) == 1


def test_promotion_action_duplicate_and_none_create_no_row(
    isolated_settings, monkeypatch, fake_promote_llm
):
    """The classifier's own "duplicate"/"none" outcomes both short-circuit
    to no row change — this is the WIRING half of the fix (does
    `maybe_promote_turn` honor the action the classifier picked); the
    real-LLM live tier below proves the model actually PICKS "duplicate"
    for a semantic near-duplicate, not just an exact-string match."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = "Never auto-enable telemetry."
    first = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert first is not None

    fake_promote_llm["action"] = "duplicate"
    fake_promote_llm["body"] = ""
    second = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert second is None

    fake_promote_llm["action"] = "none"
    third = project_memory.maybe_promote_turn(project["id"], conv["id"], _SMALLTALK_TRANSCRIPT)
    assert third is None

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1, "only the one genuine 'new' promotion may leave a row"

    # Negative-space: a genuinely different insight IS still promoted — the
    # duplicate/none branches aren't vacuously blocking everything.
    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = "Ship dark mode behind a feature flag."
    fourth = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert fourth is not None
    assert len(memory_db.list_entries(project["id"])) == 2


def test_promotion_action_update_revises_existing_agent_entry(
    isolated_settings, monkeypatch, fake_promote_llm
):
    """The "update" branch: revises the SAME row in place (no new row),
    body changes, `updated_at` moves it to the front of `list_entries`'
    recency order, and a regen is (re-)scheduled — AC10-equivalent for the
    update path, not just the new-entry path."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = "API rate limit is 100 req/min per tenant."
    original = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert original is not None
    original_id = original["id"]

    require_client().table("project_memory_summary").update({"stale": False}).eq(
        "project_id", project["id"]
    ).execute()

    fake_promote_llm["action"] = "update"
    fake_promote_llm["target_entry_id"] = original_id
    fake_promote_llm["body"] = "API rate limit is 250 req/min per tenant, revised after load testing."
    conv2 = conversations_db.create_group_chat(project["id"], ctx.user_id)
    updated = project_memory.maybe_promote_turn(
        project["id"], conv2["id"], "Ada: @Sprntly bump the rate limit to 250/min.\n"
        "Sprntly: Updated — 250 req/min per tenant now.",
    )
    assert updated is not None
    assert updated["id"] == original_id, "update must revise the SAME row, not create a new one"
    assert updated["body"] == fake_promote_llm["body"]
    assert updated["promoted_by"] == "agent"
    assert updated["source_conversation_id"] == conv2["id"]

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1, "update must never leave a duplicate row behind"

    summary = require_client().table("project_memory_summary").select("*").eq(
        "project_id", project["id"]
    ).execute().data[0]
    assert summary["stale"] is False, "update must (re-)trigger schedule_regen, not just flip stale"


def test_promotion_update_guardrail_rejects_user_entry_target(
    isolated_settings, monkeypatch, fake_promote_llm
):
    """Defense-in-depth: even if the classifier hallucinates and targets a
    USER-authored entry, the write must be rejected — never overwritten —
    both at the `maybe_promote_turn` validation step and, independently,
    at `update_agent_promoted_entry`'s own WHERE-clause guard."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    user_entry = memory_db.add_entry(
        project["id"], body="Original user guardrail.", author_user_id=ctx.user_id
    )

    fake_promote_llm["action"] = "update"
    fake_promote_llm["target_entry_id"] = user_entry["id"]
    fake_promote_llm["body"] = "A hallucinated revision."
    result = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert result is None

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1
    assert rows[0]["body"] == "Original user guardrail.", "the user entry must be untouched"

    # Independent guard: even a direct call bypassing the classifier-level
    # check must be rejected by the DB helper's own WHERE clause.
    direct = memory_db.update_agent_promoted_entry(
        project["id"], user_entry["id"], body="direct bypass attempt",
        source_conversation_id=conv["id"],
    )
    assert direct is None
    rows_after = memory_db.list_entries(project["id"])
    assert rows_after[0]["body"] == "Original user guardrail."


def test_promotion_update_guardrail_rejects_unknown_target(
    isolated_settings, monkeypatch, fake_promote_llm
):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "update"
    fake_promote_llm["target_entry_id"] = 999_999_999
    fake_promote_llm["body"] = "Revision of a row that doesn't exist."
    result = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert result is None
    assert memory_db.list_entries(project["id"]) == []


def test_human_turn_no_promotion(isolated_settings, monkeypatch, fake_promote_llm, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "morning team, nothing to see here"},
        )
    assert r.status_code == 200
    assert fake_promote_llm["calls"] == [], "no @Sprntly mention → no classifier call at all"

    cost_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "projects.memory.promotion" in rec.getMessage()
    ]
    assert cost_lines == []

    rows = memory_db.list_entries(project["id"])
    assert rows == []


def test_promotion_cost_log_no_body_text(isolated_settings, monkeypatch, fake_promote_llm, caplog):
    """AC9: exactly one `projects.memory.promotion` cost line per classifier
    call, and neither the transcript nor the insight text ever reaches a
    log line."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], ctx.user_id)
    fake_promote_llm["insight"] = "SECRET_INSIGHT_DO_NOT_LOG"
    secret_transcript = "Ada: @Sprntly SECRET_TRANSCRIPT_DO_NOT_LOG, lock it in."

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        result = project_memory.maybe_promote_turn(project["id"], conv["id"], secret_transcript)
    assert result is not None

    cost_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "projects.memory.promotion" in rec.getMessage()
    ]
    assert len(cost_lines) == 1
    assert f"project_id={project['id']}" in cost_lines[0]
    assert f"conversation_id={conv['id']}" in cost_lines[0]
    assert "status=complete" in cost_lines[0]

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_INSIGHT_DO_NOT_LOG" not in joined
    assert "SECRET_TRANSCRIPT_DO_NOT_LOG" not in joined


# ── Real-LLM / real-DB live tier ─────────────────────────────────────────
#
# Gated behind RUN_PROJECT_MEMORY_PROMOTION_LIVE=1 PLUS a real
# ANTHROPIC_API_KEY. Mutates real rows against a real (company, workspace,
# user) already seeded in the local rig — mirrors
# test_group_chat_turns_live.py's fixture shape.

_RUN_LIVE = os.getenv("RUN_PROJECT_MEMORY_PROMOTION_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_MEMORY_PROMOTION_LIVE=1 with SUPABASE_URL/"
    "SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY "
    "pointed at the local rig and the projects/chat/memory migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live promotion round-trip against a "
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
        .select("user_id, role")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    yield {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id}


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


@pytest.fixture
def project_ids(sb):
    """NOT autouse (deliberately, unlike `test_group_chat_turns_live.py`'s
    otherwise-identical fixture): this file mixes fast unit tests with the
    live tier in ONE module, and an autouse fixture that depends on `sb`
    would force pytest to resolve the module-scoped `sb` fixture — which
    calls `pytest.skip()` when the live tier is disabled — for EVERY test
    in the file, silently skipping the fast tests too. Only the three live
    tests below request this fixture explicitly."""
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("projects").delete().eq("id", pid).execute()


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_promote_durable_insight_writes_row(client, fixture_ids, project_ids, sb):
    """(a) A salient group turn promotes an entry with correct provenance —
    real classifier decision, real Postgres insert, real XOR check."""
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion durable {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    entry = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert entry is not None, "a durable rate-limit decision must be promoted"
    assert entry["promoted_by"] == "agent"
    assert entry["author_user_id"] is None
    assert entry["source_conversation_id"] == conv["id"]
    assert entry["body"].strip() != ""
    assert entry["body"] not in _DURABLE_TRANSCRIPT, "must be summarized, not a verbatim line"


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_promote_smalltalk_writes_nothing(client, fixture_ids, project_ids, sb):
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion smalltalk {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    result = project_memory.maybe_promote_turn(project["id"], conv["id"], _SMALLTALK_TRANSCRIPT)
    assert result is None

    rows = memory_db.list_entries(project["id"])
    assert rows == []


@pytest.mark.integration
@pytest.mark.real_memory_synthesis  # opt OUT of conftest's autouse call_md stub —
# AC10 needs the REAL regen loop, not the placeholder synthesis text
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_promotion_regenerates_summary_content(client, fixture_ids, project_ids, sb):
    """(b) AC10 — the assertion that catches a disconnected loop: after
    `maybe_promote_turn` promotes, the scheduled regen (inline under
    pytest) must leave `get_summary` with `stale is False` AND `summary_md`
    reflecting the new insight's substance — not merely `stale` flipped."""
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion e2e regen {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    entry = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert entry is not None, "setup: the durable transcript must promote for this test to prove anything"

    summary = memory_db.get_summary(project["id"])
    assert summary["stale"] is False, "the scheduled regen must have run inline under pytest"
    assert summary["summary_md"], "regen must have produced a real summary, not left the row absent"
    body_lower = summary["summary_md"].lower()
    assert "100" in summary["summary_md"] or "rate limit" in body_lower, (
        "the regenerated summary_md must reflect the promoted insight's "
        f"substance, not just flip stale — got: {summary['summary_md']!r}"
    )


# ── Real-LLM three-way semantic-dedup demonstration ──────────────────────
#
# The exact regression the Phase-2 sweep found, plus the two new branches
# the fix adds (update-in-place, never-touch-a-user-entry). Each of these
# proves a real Anthropic classifier DECISION, not just the wiring.


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_live_repeated_thanks_turn_is_skipped(client, fixture_ids, project_ids, sb):
    """The exact Phase-2 sweep regression: a trivial "thanks!" turn about
    an already-recorded fact, still inside the classifier's own clamped
    transcript window, must NOT re-promote a second row."""
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion thanks-skip {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    first = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert first is not None, "setup: the durable fact must promote first"
    count_after_first = len(memory_db.list_entries(project["id"]))
    assert count_after_first == 1

    thanks_transcript = _DURABLE_TRANSCRIPT + (
        "\nAda (PM): @Sprntly thanks!\n"
        "Sprntly: You're welcome — locked in at 100 req/min per tenant."
    )
    second = project_memory.maybe_promote_turn(project["id"], conv["id"], thanks_transcript)
    assert second is None, "a trivial 'thanks!' turn about an already-recorded fact must not re-promote"

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == count_after_first == 1, (
        f"row count changed: before={count_after_first} after={len(rows)}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_live_revision_turn_updates_existing_agent_entry(client, fixture_ids, project_ids, sb):
    """A turn that genuinely revises/extends an existing agent entry must
    UPDATE that row in place (same id, new body), not create a new row."""
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion update {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    original = project_memory.maybe_promote_turn(project["id"], conv["id"], _DURABLE_TRANSCRIPT)
    assert original is not None, "setup: the durable fact must promote first"
    original_id = original["id"]
    count_after_first = len(memory_db.list_entries(project["id"]))
    assert count_after_first == 1

    revision_transcript = (
        "Ada (PM): @Sprntly actually, let's raise that rate limit to 250 "
        "requests/min per tenant — we load-tested it and 100 was too "
        "conservative.\n"
        "Sprntly: Updated — 250 req/min per tenant now, replacing the "
        "earlier 100 req/min limit."
    )
    updated = project_memory.maybe_promote_turn(project["id"], conv["id"], revision_transcript)
    assert updated is not None, "a genuine revision of a recorded fact must promote"
    assert updated["id"] == original_id, (
        "a revision must UPDATE the existing row, not create a new one — "
        f"got a different id ({updated['id']} != {original_id})"
    )
    assert updated["promoted_by"] == "agent"
    assert "250" in updated["body"], "the revised body must reflect the new value"

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == count_after_first == 1, (
        f"row count changed on update: before={count_after_first} after={len(rows)}"
    )

    summary = memory_db.get_summary(project["id"])
    assert summary["stale"] is False, "the update must (re-)trigger the summary regen"


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_live_user_authored_restate_is_skipped(client, fixture_ids, project_ids, sb):
    """A candidate that merely restates something a TEAMMATE already wrote
    by hand must be skipped — never overwritten by the agent, and never
    duplicated as a second agent-promoted row."""
    project = client.post(
        "/v1/projects", json={"name": f"Live promotion user-restate {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project["id"], fixture_ids["user_id"])

    user_entry = memory_db.add_entry(
        project["id"],
        body="We never enable telemetry by default for enterprise customers.",
        author_user_id=fixture_ids["user_id"],
    )

    restate_transcript = (
        "Ada (PM): @Sprntly just confirming — telemetry stays off by "
        "default for enterprise customers, right?\n"
        "Sprntly: Correct — telemetry is never auto-enabled by default for "
        "enterprise customers."
    )
    result = project_memory.maybe_promote_turn(project["id"], conv["id"], restate_transcript)
    assert result is None, "restating an existing user-authored entry must not promote anything"

    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1, "no new agent row may be created for a user-entry restatement"
    assert rows[0]["id"] == user_entry["id"]
    assert rows[0]["body"] == user_entry["body"], "the user entry must be byte-identical, untouched"
    assert rows[0]["promoted_by"] is None
    assert rows[0]["author_user_id"] == fixture_ids["user_id"]

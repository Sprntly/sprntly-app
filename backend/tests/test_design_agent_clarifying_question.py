"""Tests for the clarifying_question exit-sentinel (P3-08, F12).

Sentinel #1 of AD17's ≤4. Coverage matches the ticket's Unit Tests section:

- TOOL REGISTRATION — clarifying_question is in SENTINEL_TOOLS (category sentinel),
  the cap holds, it appears in all three tool-partition modes, and its description
  carries negative space.
- LOOP BREAK — a clarifying_question tool_use makes `agent_loop` return with
  status='awaiting_clarification' + pending_question, the loop does NOT make a
  second messages.create call, and a clarifying_question batched with a `write`
  WINS (the write is not applied; vfs unchanged). Plus the AC7a sentinel-distinction
  assertion: a DIFFERENT-named sentinel does NOT trigger the pause path.
- PERSISTENCE — set_pending_question / clear_pending_question round-trip the jsonb,
  are workspace-isolated, and iterate_prototype persists on a pause without staging
  a checkpoint.
- MIGRATION / OBSERVABILITY — the migration is idempotent by construction; the
  set helper logs no question text (Rule #24).

The recording-fake-Anthropic-client shape is reused from test_design_agent_runner.py;
the fake-Supabase `proto` fixture mirrors test_db_prototypes.py (adds the new
pending_question column + registers it as jsonb).
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import py_compile
import re
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import runner
from app.design_agent.runner import RunResult, agent_loop
from app.design_agent.tools import (
    SENTINEL_TOOLS,
    ToolContext,
    all_tools,
    dispatch,
    tools_for_mode,
)

from tests.conftest import _TEST_COMPANY_ID
from tests._fake_anthropic import _FakeStream

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260601000180_design_agent_clarifying_question.sql"
)


# ═══════════════════════════════════════════════════════════════════════════
# Recording fake Anthropic client (reused shape from test_design_agent_runner.py)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeBlock:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return copy.deepcopy(self._data)


class _FakeMessage:
    def __init__(self, stop_reason, blocks, usage):
        self.stop_reason = stop_reason
        self.content = [_FakeBlock(b) for b in blocks]
        self.usage = usage


class _RecordingClient:
    """Sync messages.create replaying a list of responses; last entry replays."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({
            "messages": copy.deepcopy(kwargs.get("messages")),
            "tools": kwargs.get("tools"),
        })
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def _stream(self, **kwargs):
        return _FakeStream(self._create(**kwargs))


def _usage(cache_creation=0, cache_read=0, inp=0, out=0):
    return types.SimpleNamespace(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=inp,
        output_tokens=out,
    )


def _msg(stop_reason, blocks=None, usage=None):
    return _FakeMessage(stop_reason, blocks or [], usage or _usage())


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _tool_use(id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": inp}


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent."},
        {
            "type": "text",
            "text": "<stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Build a landing page.") -> dict:
    return {"role": "user", "content": [_text(text)]}


def _ctx(**overrides) -> ToolContext:
    base = dict(prototype_id=1, workspace_id=_TEST_COMPANY_ID, virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# Tool registration
# ═══════════════════════════════════════════════════════════════════════════


def test_clarifying_question_is_sentinel():
    # AC1: clarifying_question is in SENTINEL_TOOLS with category="sentinel".
    names = [t.name for t in SENTINEL_TOOLS]
    assert "clarifying_question" in names
    tool = next(t for t in SENTINEL_TOOLS if t.name == "clarifying_question")
    assert tool.category == "sentinel"
    # It is NOT an action tool — the AD17 action cap (6) is unchanged.
    assert sum(1 for t in all_tools() if t.category == "action") == 6


def test_sentinel_count_within_cap():
    # AC1: the module-level assert len(SENTINEL_TOOLS) <= 4 holds (1 <= 4).
    assert len(SENTINEL_TOOLS) <= 4


def test_clarifying_question_in_all_modes():
    # AC2: clarifying_question is plan-safe — present in plan, scaffold, execute.
    for mode in ("plan", "scaffold", "execute"):
        names = {t.name for t in tools_for_mode(mode)}
        assert "clarifying_question" in names, f"missing in {mode}"
        # AD17 per-mode split still holds.
        registry = tools_for_mode(mode)
        assert sum(1 for t in registry if t.category == "action") <= 6
        assert sum(1 for t in registry if t.category == "sentinel") <= 4
    # propose_prd_patch (sentinel #2) landed in P3-09 as EXECUTE-ONLY — present in
    # execute mode, absent from plan/scaffold (no PRD-edit step there).
    assert "propose_prd_patch" in {t.name for t in tools_for_mode("execute")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("plan")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("scaffold")}


def test_description_has_negative_space():
    # AC3: description is >=4 sentences and includes a "Do NOT" negative-space
    # clause (per [[property-tests-on-llm-facing-description-quality]]).
    tool = next(t for t in SENTINEL_TOOLS if t.name == "clarifying_question")
    desc = tool.description
    assert "Do NOT" in desc
    assert len(desc) >= 200
    # >=4 sentences (terminal punctuation count is a cheap proxy).
    assert desc.count(". ") + desc.count(".") >= 4
    # The two specific negative-space cases the ticket calls out.
    assert "design" in desc and "courtesy" in desc


# ═══════════════════════════════════════════════════════════════════════════
# Loop break
# ═══════════════════════════════════════════════════════════════════════════


def test_agent_loop_breaks_on_clarifying_question(monkeypatch):
    # AC4: a clarifying_question tool_use ends the loop as awaiting_clarification.
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "clarifying_question", {
            "question": "Submit the form or open a modal?",
        })]),
        # A second response exists but must NEVER be consumed (loop must break).
        _msg("end_turn", [_text("should not reach")]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "awaiting_clarification"
    assert isinstance(result, RunResult)


def test_pending_question_payload_captured(monkeypatch):
    # AC4: pending_question carries {question, choices, context}.
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "clarifying_question", {
            "question": "Submit or modal?",
            "choices": ["Submit", "Open modal"],
            "context": "The PRD does not say what the CTA does.",
        })]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.pending_question == {
        "question": "Submit or modal?",
        "choices": ["Submit", "Open modal"],
        "context": "The PRD does not say what the CTA does.",
    }


def test_pending_question_optional_fields_default_none(monkeypatch):
    # question-only call: choices/context come through as None, not missing keys.
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "clarifying_question", {"question": "Q?"})]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.pending_question == {"question": "Q?", "choices": None, "context": None}


def test_clarifying_question_wins_over_batched_write(monkeypatch):
    # AC5: terminal precedence. clarifying_question batched with a write → the
    # loop breaks, the write is NOT applied (vfs unchanged), status is the pause.
    ctx = _ctx(virtual_fs={"src/App.tsx": "ORIGINAL"})
    _install_client(monkeypatch, [
        _msg("tool_use", [
            _tool_use("t1", "write", {"path": "src/App.tsx", "content": "MUTATED"}),
            _tool_use("t2", "clarifying_question", {"question": "Ambiguous?"}),
        ]),
    ])
    result = _run(agent_loop(_system(), _user(), ctx))
    assert result.status == "awaiting_clarification"
    # The batched write must NOT have run — virtual_fs is untouched.
    assert ctx.virtual_fs == {"src/App.tsx": "ORIGINAL"}


def test_loop_does_not_continue_after_clarifying_question(monkeypatch):
    # AC4: exactly ONE messages.create call — the loop does not continue.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "clarifying_question", {"question": "Q?"})]),
        _msg("end_turn", [_text("never reached")]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    assert len(client.calls) == 1


def test_other_sentinel_name_does_not_trigger_pause(monkeypatch):
    # AC7a: the branch keys on the SPECIFIC tool name, NOT "any sentinel". A
    # stub sentinel with a DIFFERENT name does NOT route to awaiting_clarification
    # — it falls through to normal dispatch (here, an "Unknown tool" is_error
    # tool_result, then the loop continues to the next turn / end_turn).
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "propose_prd_patch", {"foo": "bar"})]),
        _msg("end_turn", [_text("continued past the non-clarifying sentinel")]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.pending_question is None
    # The loop continued (2 calls), proving the pause branch did NOT fire.
    assert len(client.calls) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch-level non-breakage (AC10) — the executor resolves + echoes payload
# ═══════════════════════════════════════════════════════════════════════════


def test_dispatch_clarifying_question_routes_to_executor():
    # AC10: dispatch('clarifying_question', …) resolves to the executor and
    # returns the structured _sentinel payload.
    res = _run(dispatch("clarifying_question", {
        "question": "Q?", "choices": ["a", "b"], "context": "ctx",
    }, _ctx()))
    assert res["_sentinel"] == "clarifying_question"
    assert res["question"] == "Q?"
    assert res["choices"] == ["a", "b"]
    assert res["context"] == "ctx"


# ═══════════════════════════════════════════════════════════════════════════
# Persistence — set / clear / workspace isolation (fake Supabase)
# ═══════════════════════════════════════════════════════════════════════════


_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    pending_question       TEXT,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
);
"""


@pytest.fixture
def proto(isolated_settings, monkeypatch):
    """Reloaded app.db.prototypes wired to the fake Supabase, with a prototypes
    table that carries the new pending_question column registered as jsonb."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    # Register pending_question so it round-trips as a real dict, not a JSON str.
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototypes", {"pending_question"},
    )
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    return proto_mod


def _seed_prototype(proto_mod, workspace_id: str = _TEST_COMPANY_ID) -> int:
    return proto_mod.start_prototype(
        prd_id=1, workspace_id=workspace_id, template_version=1,
    )


def test_set_pending_question_writes_jsonb(proto):
    # AC6: set_pending_question writes the dict to the prototype row.
    pid = _seed_prototype(proto)
    q = {"question": "Submit or modal?", "choices": ["Submit", "Modal"], "context": "ambiguous"}
    proto.set_pending_question(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, question=q)
    row = proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["pending_question"] == q


def test_clear_pending_question_nulls_it(proto):
    # AC6: clear_pending_question nulls the column.
    pid = _seed_prototype(proto)
    proto.set_pending_question(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, question={"question": "Q?"},
    )
    proto.clear_pending_question(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    row = proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["pending_question"] is None


def test_pending_question_workspace_isolated(proto):
    # AC6: a 'demo' call does NOT touch an 'app' row.
    app_pid = _seed_prototype(proto, workspace_id=_TEST_COMPANY_ID)
    proto.set_pending_question(
        prototype_id=app_pid, workspace_id=_TEST_COMPANY_ID, question={"question": "APP_Q"},
    )
    # A write scoped to a different workspace must NOT mutate the app row.
    proto.set_pending_question(
        prototype_id=app_pid, workspace_id="demo", question={"question": "DEMO_Q"},
    )
    row = proto.get_prototype(prototype_id=app_pid, workspace_id=_TEST_COMPANY_ID)
    assert row["pending_question"] == {"question": "APP_Q"}


def test_iterate_persists_question_no_checkpoint(monkeypatch):
    # AC7: on an awaiting_clarification result, iterate_prototype persists the
    # question via set_pending_question and creates NO checkpoint (no bundle was
    # built). We capture the set_pending_question call and assert the runner never
    # references a checkpoint-staging helper on this path.
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        r = RunResult(status="awaiting_clarification", iters=1,
                      usage=runner.RunUsage(), duration_ms=1, final_content=[])
        r.pending_question = {"question": "Q?", "choices": None, "context": None}
        return r

    def fake_set(*, prototype_id, workspace_id, question):
        captured["prototype_id"] = prototype_id
        captured["workspace_id"] = workspace_id
        captured["question"] = question

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "set_pending_question", fake_set)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)

    result, vfs = _run(runner.iterate_prototype(
        prototype_id=7, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(),
        user_message=_user(), current_source={"src/App.tsx": "x"}, figma_file_key=None,
    ))
    assert result.status == "awaiting_clarification"
    assert captured["prototype_id"] == 7
    assert captured["workspace_id"] == _TEST_COMPANY_ID
    assert captured["question"] == {"question": "Q?", "choices": None, "context": None}
    # iterate_prototype never stages a checkpoint — the helper is not even imported
    # into the runner module (the route's _stage_iterate_run owns staging, and only
    # on status=='complete').
    assert not hasattr(runner, "create_checkpoint")


def test_generate_persists_question_on_pause(monkeypatch):
    # The scaffold entrypoint persists too (clarifying_question is in scaffold mode).
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        assert mode == "scaffold"
        r = RunResult(status="awaiting_clarification", iters=1,
                      usage=runner.RunUsage(), duration_ms=1, final_content=[])
        r.pending_question = {"question": "Q?", "choices": None, "context": None}
        return r

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "set_pending_question",
                        lambda **kw: captured.update(kw))
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)

    _run(runner.generate_prototype(
        prototype_id=9, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(),
        user_message=_user(), figma_file_key=None,
    ))
    assert captured["prototype_id"] == 9
    assert captured["question"] == {"question": "Q?", "choices": None, "context": None}


def test_complete_result_does_not_persist_question(monkeypatch):
    # Negative: a normal complete run never calls set_pending_question.
    called = {"n": 0}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        return RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                         duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "set_pending_question",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)
    _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(),
        user_message=_user(), current_source={}, figma_file_key=None,
    ))
    assert called["n"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Migration / observability
# ═══════════════════════════════════════════════════════════════════════════


def test_migration_file_exists_and_named_correctly():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260601000180_design_agent_clarifying_question.sql"


def test_migration_applies_idempotently():
    # AC8: idempotent by construction — additive `add column if not exists`,
    # no ALTER that would fail on re-apply, targets the prototypes table.
    sql = "\n".join(
        line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()
    ).lower()
    assert "alter table prototypes" in sql
    assert "add column if not exists pending_question" in sql
    assert "jsonb" in sql
    # No ALTER on the sibling/base tables we must never touch.
    for forbidden in ("alter table prds", "alter table briefs", "alter table evidences"):
        assert forbidden not in sql


def test_set_pending_question_logs_no_question_text(proto, caplog):
    # AC9: the log line is identifiers-only — the question TEXT never appears.
    pid = _seed_prototype(proto)
    secret = "SECRET_PRODUCT_DETAIL_XYZ"
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        proto.set_pending_question(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
            question={"question": secret, "context": "also " + secret},
        )
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "prototype_question_set" in log_text
    assert f"prototype_id={pid}" in log_text
    assert secret not in log_text


def test_clear_pending_question_logs_cleared(proto, caplog):
    pid = _seed_prototype(proto)
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        proto.clear_pending_question(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "prototype_question_cleared" in log_text


# ═══════════════════════════════════════════════════════════════════════════
# P4-08 — F12 clarifying-question PAUSE on the iterate/resume path
#
# Regression: the route bg layer (`_run_iterate_bg`) used to route an
# `awaiting_clarification` runner result to `fail_prototype`, flipping the row to
# 'failed' and 409-blocking the P3-16 answer-resume. The fix special-cases the
# pause → `mark_awaiting_clarification` (leave the row PAUSED-ready) above the
# genuine-failure `else`. These tests are the durable home for the assertion the
# P3-13 capstone's optional AC9 described (clarifying-question-on-iterate leaves
# the row resumable, not failed). The generate-time pause is SCOPED OUT (AC7).
# ═══════════════════════════════════════════════════════════════════════════

# Full route-stack DDL (mirrors test_design_agent_iterate.py's _DDL) plus the
# P3-08 `pending_question` column, so the pause-persistence round-trips. Carries
# share_token / is_complete / complete_checkpoint_id so the helper-preservation
# AC (AC3) can seed + assert them.
_ROUTE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    pending_question       TEXT,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE prototype_comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    anchor_id     TEXT NOT NULL,
    body          TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT 'demo',
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'resolved', 'orphaned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT,
    user_id        TEXT
);
CREATE TABLE prototype_pending_iterations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id       INTEGER NOT NULL,
    workspace_id       TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    applied_comment_id INTEGER,
    mode               TEXT NOT NULL DEFAULT 'execute',
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'failed')),
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    started_at         TEXT,
    finished_at        TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Full route stack reloaded in dependency order (proto → comments → routes →
    main) against the fake Supabase, feature flag ON, with pending_question
    registered as jsonb. Deliberately does NOT reload app.design_agent.runner —
    reloading runner mints a fresh RunResult class that breaks isinstance in the
    runner tests under the full suite (see the runner-reload learning)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_ROUTE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototypes", {"pending_question"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


def _seed_ready_route(env, *, workspace_id: str = _TEST_COMPANY_ID, current_checkpoint_id=None) -> int:
    """Insert a ready, unlocked prototype (status='ready', is_complete=0) with a
    prior bundle — the pre-iterate state of the iterate-time pause path."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="https://bundle/original", current_checkpoint_id=current_checkpoint_id,
    )
    return pid


def _stub_iterate_status(monkeypatch, routes_mod, status: str, **extra):
    """Patch routes.iterate_prototype to return a given RunResult-shaped status
    (no real LLM/storage), and stub the source read. virtual_fs is empty so no
    staging runs on any path."""
    async def _fake(**kwargs):
        return SimpleNamespace(
            status=status, iters=extra.get("iters", 1),
            error_message=extra.get("error_message"),
            error_class=extra.get("error_class"),
        ), {}

    async def _fake_read(prototype_id, checkpoint_id):
        return {}

    monkeypatch.setattr(routes_mod, "iterate_prototype", _fake)
    monkeypatch.setattr(routes_mod, "read_source_files_for_checkpoint", _fake_read)


# ─── Regression (each FAILS on the unfixed code) ─────────────────────────────


@pytest.mark.asyncio
async def test_iterate_bg_pause_leaves_row_ready_not_failed(env, monkeypatch):
    # AC1: an awaiting_clarification iterate result leaves the row PAUSED-ready —
    # status='ready', error IS NULL, pending_question still set, completed_at
    # unchanged. FAILS on the unfixed code (the else→fail_prototype flips it to
    # 'failed'). Durable home for the dropped P3-13 AC9.
    _stub_iterate_status(monkeypatch, env.routes, "awaiting_clarification")
    # Stub clear_pending_question so the pre-seeded question survives the bg run.
    # (In production the bg run clears the old question at start, then
    # iterate_prototype re-sets a new one; here iterate_prototype is stubbed so
    # we block the clear to keep the assertion meaningful.)
    monkeypatch.setattr(
        "app.routes.design_agent.clear_pending_question", lambda **kw: None
    )
    pid = _seed_ready_route(env, current_checkpoint_id=42)
    # The runner (P3-08) persists the question during iterate_prototype; we stub
    # that call, so seed the sidecar here to mirror the real paused state.
    env.proto.set_pending_question(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        question={"question": "Submit or open a modal?"},
    )
    before = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="make the CTA blue"),
    )

    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"            # NOT 'failed'
    assert row["error"] is None
    assert row["pending_question"] == {"question": "Submit or open a modal?"}
    assert row["completed_at"] == before["completed_at"]   # not re-stamped
    assert row["bundle_url"] == "https://bundle/original"  # prior bundle preserved


def test_iterate_bg_pause_resume_not_409(env, client, monkeypatch):
    # AC2: given the paused row left by AC1, the P3-16 answer-resume
    # (POST /{id}/iterate) passes the `status != 'ready'` guard — NOT a 409.
    # FAILS on the unfixed code (row is 'failed' → 409).
    # SYNC test: asyncio.run(_run_iterate_bg) completes fully, THEN the sync
    # TestClient runs — avoids a nested running event loop.
    _stub_iterate_status(monkeypatch, env.routes, "awaiting_clarification")
    pid = _seed_ready_route(env, current_checkpoint_id=42)
    env.proto.set_pending_question(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, question={"question": "Q?"},
    )
    # Drive the pause through the bg layer (asyncio.run completes fully before the
    # sync TestClient call — no nested event loop).
    _run(env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="x"),
    ))
    assert env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)["status"] == "ready"

    # The answer submits as a new iterate; it must NOT be 409-blocked.
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "Submit the form"})
    assert resp.status_code != 409, resp.text
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"


# ─── Helper correctness (AC3, AC4) ───────────────────────────────────────────


def test_mark_awaiting_clarification_sets_ready_clears_error(env):
    # AC3: sets status='ready' + error=None; does NOT touch bundle_url /
    # completed_at / current_checkpoint_id / share_token.
    pid = _seed_ready_route(env, current_checkpoint_id=99)
    env.proto.set_share_config(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, share_mode="public")
    # Simulate the bug's intermediate state: the bg layer had flipped it failed.
    env.proto.fail_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, error="boom")
    before = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert before["status"] == "failed" and before["share_token"]

    env.proto.mark_awaiting_clarification(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)

    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"
    assert row["error"] is None
    assert row["bundle_url"] == before["bundle_url"]
    assert row["completed_at"] == before["completed_at"]
    assert row["current_checkpoint_id"] == before["current_checkpoint_id"]
    assert row["share_token"] == before["share_token"]


def test_mark_awaiting_clarification_workspace_isolated(env):
    # AC4: a 'demo'-scoped call does NOT mutate an 'app' row.
    app_pid = _seed_ready_route(env, workspace_id=_TEST_COMPANY_ID)
    env.proto.fail_prototype(prototype_id=app_pid, workspace_id=_TEST_COMPANY_ID, error="boom")
    # Cross-workspace call: must be a no-op against the app row.
    env.proto.mark_awaiting_clarification(prototype_id=app_pid, workspace_id="demo")
    row = env.proto.get_prototype(prototype_id=app_pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "failed"   # unchanged — the demo-scoped call missed it
    assert row["error"] == "boom"


# ─── Scope guard (AC5, AC7) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iterate_bg_genuine_failure_still_fails(env, monkeypatch):
    # AC5: a non-complete, non-pause result (max_iters) still routes to
    # fail_prototype — the pause branch does NOT swallow genuine failures.
    _stub_iterate_status(
        monkeypatch, env.routes, "max_iters", iters=12,
        error_message="loop hit cap", error_class="MaxIters",
    )
    pid = _seed_ready_route(env)
    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="x"),
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "failed"
    assert "status=max_iters" in row["error"]
    assert "error_class=MaxIters" in row["error"]
    # The prior bundle is preserved (failure does not erase it).
    assert row["bundle_url"] == "https://bundle/original"


@pytest.mark.asyncio
async def test_generate_bg_pause_unchanged(env, monkeypatch):
    # AC7: the generate path is SCOPED OUT — a scaffold-mode awaiting_clarification
    # result still hits _run_generation_bg's else→fail_prototype. This documents
    # the deliberate scope boundary so a future generate-resume ticket flips it
    # intentionally (the generate path needs answer-as-generate-continuation, not
    # answer-as-iterate over an empty source).
    async def fake_generate(**kwargs):
        return SimpleNamespace(
            status="awaiting_clarification", iters=1,
            error_message=None, error_class=None,
        ), {}

    monkeypatch.setattr(env.routes, "generate_prototype", fake_generate)
    monkeypatch.setattr(env.routes, "_load_prd_body", lambda prd_id: "PRD body")
    monkeypatch.setattr(env.routes, "_figma_context_block", lambda key: "")
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=1,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "failed"   # generate-time pause still fails (loud), by design


# ─── Observability (AC6) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iterate_bg_pause_logs_identifier_only(env, monkeypatch, caplog):
    # AC6: the pause branch logs prototype_iterate_paused_awaiting_clarification
    # with the prototype_id and NO question text / source content.
    _stub_iterate_status(monkeypatch, env.routes, "awaiting_clarification")
    pid = _seed_ready_route(env, current_checkpoint_id=42)
    secret = "SECRET_QUESTION_PRODUCT_DETAIL"
    env.proto.set_pending_question(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        question={"question": secret, "context": "also " + secret},
    )
    with caplog.at_level(logging.INFO):
        await env.routes._run_iterate_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
            body=env.routes.IterateRequest(prompt="x"),
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"prototype_iterate_paused_awaiting_clarification prototype_id={pid}" in blob
    # The helper's own state-transition line is also identifier-only.
    assert f"prototype_awaiting_clarification prototype_id={pid}" in blob
    assert secret not in blob   # never the question text


# ─── Non-breakage (AC8, AC9) ─────────────────────────────────────────────────

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ROUTES_FILE = _BACKEND_ROOT / "app" / "routes" / "design_agent.py"
_PROTOTYPES_FILE = _BACKEND_ROOT / "app" / "db" / "prototypes.py"


def test_routes_design_agent_still_compiles_callsites_unchanged():
    # AC8: the module py_compiles and the new symbol is consumed by exactly ONE
    # call site (in _run_iterate_bg) — existing routes/handlers untouched.
    py_compile.compile(str(_ROUTES_FILE), doraise=True)
    src = _ROUTES_FILE.read_text()
    # Imported once (in the from app.db.prototypes import block).
    assert "mark_awaiting_clarification" in src
    # Exactly one CALL site (the trailing "(") — the _run_iterate_bg pause branch.
    assert src.count("mark_awaiting_clarification(") == 1
    # The genuine-failure else branch + the generate path still fail via fail_prototype.
    assert "fail_prototype" in src


def test_prototypes_callsites_unchanged_plus_new_helper():
    # AC9: db/prototypes.py py_compiles and defines the single new helper; prior
    # helpers are untouched (their defs still present).
    py_compile.compile(str(_PROTOTYPES_FILE), doraise=True)
    src = _PROTOTYPES_FILE.read_text()
    assert "def mark_awaiting_clarification(" in src
    for prior in ("def complete_prototype(", "def fail_prototype(",
                  "def set_pending_question(", "def advance_current_checkpoint("):
        assert prior in src

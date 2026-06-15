"""Tests for the Plan/Discuss → Execute structural wiring (P3-07):

    DESIGN_AGENT_PLAN_SYSTEM                              (prompts.py)
    tools_for_mode / tool_definitions_for_mode + dispatch (tools.py)
    agent_loop mode-registry + prepend_plan_addendum     (runner.py)
    POST /iterate (mode='plan') + POST /iterate/confirm-plan (routes/design_agent.py)
    supabase/migrations/20260601000150_design_agent_iteration_plan.sql

Five layers, matching the ticket's Unit Tests section + the AD17 reconciliation:

- MIGRATION   — static + ordering assertions on the additive `plan` column.
- PARTITION   — tools_for_mode per-mode registry + the AD17 per-mode invariant
                (action ≤6 AND sentinel ≤4, NOT a flat ≤7), incl. stub-sentinel
                filtering (clarifying_question in plan/execute; propose_prd_patch
                execute-only).
- PROMPT      — DESIGN_AGENT_PLAN_SYSTEM distinct + no-write instruction.
- RUNNER      — agent_loop uses the mode registry (cache-frozen), dispatch rejects
                an out-of-mode write (plan-mode-cannot-mutate), confirm-plan
                prepends the approved plan to the system blocks.
- ROUTE / BG  — plan-mode enqueue + the confirm-plan transition + the plan-run
                staging behaviour (persists the plan, builds NO checkpoint).

Load-bearing: AC3 (per-mode AD17 invariant for every mode), AC5 (plan mode
cannot mutate — dispatch rejects a hallucinated write, vfs unchanged), AC7 (the
approved plan rides in the system blocks), AC9 (registry frozen at run start).
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import pathlib
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import runner, tools
from app.design_agent.prompts import (
    DESIGN_AGENT_ITERATE_SYSTEM,
    DESIGN_AGENT_PLAN_SYSTEM,
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
)
from app.design_agent.tools import (
    ACTION_TOOLS,
    EXECUTE_ACTION_TOOLS,
    PLAN_ACTION_TOOLS,
    SENTINEL_TOOLS,
    ToolContext,
    ToolDef,
    all_tools,
    dispatch,
    tool_definitions_for_api,
    tool_definitions_for_mode,
    tools_for_mode,
)

from tests.conftest import _TEST_COMPANY_ID
from tests._fake_anthropic import _FakeStream

ACTION_NAMES = {"view", "write", "line_replace", "search", "fetch_figma", "read_console"}
PLAN_NAMES = {"view", "search", "fetch_figma", "read_console"}

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260601000150_design_agent_iteration_plan.sql"
)


def _run(coro):
    return asyncio.run(coro)


def _stub_sentinel(name: str) -> ToolDef:
    """A throwaway sentinel ToolDef (never dispatched) for filtering tests."""
    return ToolDef(
        name=name,
        description="stub sentinel " * 30,  # ≥200 chars, never asserted on
        input_schema={"type": "object", "properties": {}},
        execute=lambda inp, ctx: None,  # never called
        category="sentinel",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Layer 0 — Migration (AC6a)
# ═══════════════════════════════════════════════════════════════════════════


def test_migration_adds_plan_column_idempotently():
    # AC6a: additive `plan text` on prototype_pending_iterations, idempotent marker.
    sql = _MIGRATION.read_text().lower()
    assert "alter table prototype_pending_iterations" in sql
    assert "add column if not exists plan text" in sql


def test_migration_suffix_sorts_between_p306_and_p308():
    # AC6a: 000100 (P3-06 table) < 000150 (this) < 000180 (P3-08 clarifying_question).
    name = _MIGRATION.name
    assert name == "20260601000150_design_agent_iteration_plan.sql"
    assert "20260601000100" < "20260601000150" < "20260601000180"
    # The predecessor migration the column extends actually exists on disk.
    p306 = _MIGRATION.parent / "20260601000100_design_agent_pending_iterations.sql"
    assert p306.exists()


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — Partition + AD17 per-mode invariant (AC2, AC3)
# ═══════════════════════════════════════════════════════════════════════════


def test_plan_tools_omit_write_and_line_replace():
    # AC2: plan registry = explore-only action tools; NO write / line_replace.
    names = {t.name for t in tools_for_mode("plan")}
    assert PLAN_NAMES <= names
    assert "write" not in names
    assert "line_replace" not in names
    # PLAN_ACTION_TOOLS constant matches.
    assert {t.name for t in PLAN_ACTION_TOOLS} == PLAN_NAMES


def test_execute_tools_include_all_six_actions():
    # AC2: execute registry has all 6 action tools.
    names = {t.name for t in tools_for_mode("execute")}
    assert ACTION_NAMES <= names
    assert {t.name for t in EXECUTE_ACTION_TOOLS} == ACTION_NAMES


def test_scaffold_tools_include_all_six_actions():
    # AC4-adjacent: scaffold runs all 6 action tools.
    names = {t.name for t in tools_for_mode("scaffold")}
    assert ACTION_NAMES <= names


def test_iterate_label_falls_back_to_execute_registry():
    # The legacy 'iterate' telemetry label is NOT a partition mode — it resolves to
    # the execute registry via the else branch (callers must pass canonical modes).
    assert {t.name for t in tools_for_mode("iterate")} == {t.name for t in tools_for_mode("execute")}


def test_per_mode_ad17_invariant_holds_for_all_modes():
    # AC3: for EVERY mode, action-count ≤6 AND sentinel-count ≤4 (NOT a flat ≤7).
    for mode in ("plan", "execute", "scaffold", "iterate"):
        reg = tools_for_mode(mode)
        actions = [t for t in reg if t.category == "action"]
        sentinels = [t for t in reg if t.category == "sentinel"]
        assert len(actions) <= 6, f"{mode}: {len(actions)} action tools > 6"
        assert len(sentinels) <= 4, f"{mode}: {len(sentinels)} sentinel tools > 4"


def test_clarifying_question_in_plan_and_execute(monkeypatch):
    # AC3: with a stub clarifying_question sentinel, it appears in plan + execute +
    # scaffold registries (it is plan-safe). Asserts the per-mode filter logic.
    monkeypatch.setattr(tools, "SENTINEL_TOOLS", [_stub_sentinel("clarifying_question")])
    for mode in ("plan", "execute", "scaffold"):
        names = {t.name for t in tools_for_mode(mode)}
        assert "clarifying_question" in names, f"missing in {mode}"


def test_propose_prd_patch_execute_only(monkeypatch):
    # AC3: with both sentinels stubbed, propose_prd_patch appears ONLY in execute —
    # never in plan or scaffold (there is no PRD-edit step there).
    monkeypatch.setattr(
        tools, "SENTINEL_TOOLS",
        [_stub_sentinel("clarifying_question"), _stub_sentinel("propose_prd_patch")],
    )
    assert "propose_prd_patch" in {t.name for t in tools_for_mode("execute")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("plan")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("scaffold")}
    # Both are still within the AD17 split (1 sentinel in plan/scaffold, 2 in execute).
    assert sum(1 for t in tools_for_mode("execute") if t.category == "sentinel") == 2
    assert sum(1 for t in tools_for_mode("plan") if t.category == "sentinel") == 1


def test_tool_definitions_for_mode_shape():
    # Serialised shape carries only {name, description, input_schema}.
    for d in tool_definitions_for_mode("plan"):
        assert set(d.keys()) == {"name", "description", "input_schema"}


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1b — Back-compat (AC8, AC10)
# ═══════════════════════════════════════════════════════════════════════════


def test_tool_definitions_for_api_equals_execute_mode():
    # AC8: the deprecated alias == the execute-mode registry serialisation.
    assert tool_definitions_for_api() == tool_definitions_for_mode("execute")


def test_module_level_asserts_still_pass():
    # AC10: the frozen module-level caps still hold; all_tools resolves. P3-08
    # landed sentinel #1 (clarifying_question), so all_tools() is now the 6 actions
    # FIRST (stable order) then the sentinel(s) — the ACTION cap stays at 6.
    assert len(ACTION_TOOLS) == 6
    assert len(SENTINEL_TOOLS) <= 4
    assert [t.name for t in all_tools()][:6] == [
        "view", "write", "line_replace", "search", "fetch_figma", "read_console",
    ]
    assert sum(1 for t in all_tools() if t.category == "action") == 6
    assert "clarifying_question" in {t.name for t in all_tools()}


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — Prompt (AC1)
# ═══════════════════════════════════════════════════════════════════════════


def test_plan_system_distinct_and_no_write_instruction():
    # AC1: distinct from BOTH scaffold and iterate; instructs emit-a-plan / no-write.
    assert DESIGN_AGENT_PLAN_SYSTEM != DESIGN_AGENT_SCAFFOLD_SYSTEM
    assert DESIGN_AGENT_PLAN_SYSTEM != DESIGN_AGENT_ITERATE_SYSTEM
    assert "PLAN" in DESIGN_AGENT_PLAN_SYSTEM
    # The WORKFLOW must tell the agent it has no write/line_replace + to emit a plan.
    assert "NO `write`" in DESIGN_AGENT_PLAN_SYSTEM or "no `write`" in DESIGN_AGENT_PLAN_SYSTEM.lower()
    assert "plan" in DESIGN_AGENT_PLAN_SYSTEM.lower()
    # Rendered, not a literal placeholder; shares the shadcn vocabulary.
    assert "{shadcn_inventory}" not in DESIGN_AGENT_PLAN_SYSTEM
    assert "shadcn/ui components" in DESIGN_AGENT_PLAN_SYSTEM


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3 — Runner / transition (recording fake client)  (AC4, AC5, AC7, AC9)
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
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({
            "messages": copy.deepcopy(kwargs.get("messages")),
            "system": kwargs.get("system"),
            "tools": kwargs.get("tools"),
        })
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def _stream(self, **kwargs):
        return _FakeStream(self._create(**kwargs))


def _usage():
    return types.SimpleNamespace(
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        input_tokens=0, output_tokens=0,
    )


def _msg(stop_reason, blocks=None):
    return _FakeMessage(stop_reason, blocks or [], _usage())


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _tool_use(id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": inp}


def _system():
    return [{
        "type": "text",
        "text": "system + tool defs",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }]


def _user():
    return {"role": "user", "content": [_text("Make the CTA blue.")]}


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _ctx(**overrides) -> ToolContext:
    base = dict(prototype_id=1, workspace_id=_TEST_COMPANY_ID, virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def test_agent_loop_uses_mode_registry_plan(monkeypatch):
    # AC4: agent_loop passes the PLAN-mode registry (no write/line_replace) to
    # client.messages.create. AC9: the tools payload is identical across iterations
    # (computed once, never reassigned mid-loop).
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/App.tsx"})]),
        _msg("end_turn", [_text("- change the CTA colour in src/App.tsx")]),
    ])
    ctx = _ctx(virtual_fs={"src/App.tsx": "x"})
    _run(runner.agent_loop(_system(), _user(), ctx, mode="plan"))
    names = {t["name"] for t in client.calls[0]["tools"]}
    # P3-08: plan mode now also carries the clarifying_question sentinel (plan-safe).
    assert names == PLAN_NAMES | {"clarifying_question"}
    assert "write" not in names and "line_replace" not in names
    # Frozen: same tools object content on every create call.
    assert client.calls[0]["tools"] == client.calls[1]["tools"]


def test_agent_loop_uses_mode_registry_execute(monkeypatch):
    # AC4: execute mode gets all 6 action tools.
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    _run(runner.agent_loop(_system(), _user(), _ctx(), mode="execute"))
    names = {t["name"] for t in client.calls[0]["tools"]}
    assert ACTION_NAMES <= names


def test_dispatch_rejects_out_of_mode_write_name():
    # AC5: dispatch with the PLAN allowed-set rejects a write WITHOUT executing.
    ctx = _ctx(virtual_fs={"a.tsx": "ORIGINAL"})
    res = _run(dispatch("write", {"path": "a.tsx", "content": "MUTATED"}, ctx, PLAN_NAMES))
    assert res["is_error"] is True
    assert "Unknown tool" in res["content"]
    assert res["tool_name"] == "write"
    assert ctx.virtual_fs == {"a.tsx": "ORIGINAL"}  # vfs UNCHANGED


def test_plan_run_rejects_write_tool_call(monkeypatch):
    # AC5 (end-to-end): a mocked model that emits a write in PLAN mode gets an
    # is_error "Unknown tool" tool_result and the virtual_fs is unchanged.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "write", {"path": "a.tsx", "content": "MUTATED"})]),
        _msg("end_turn", [_text("plan only")]),
    ])
    ctx = _ctx(virtual_fs={"a.tsx": "ORIGINAL"})
    result = _run(runner.agent_loop(_system(), _user(), ctx, mode="plan"))
    assert result.status == "complete"
    # The tool_result for the rejected write rode on the follow-up user turn.
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is True
    assert "Unknown tool" in tr["content"]
    assert ctx.virtual_fs == {"a.tsx": "ORIGINAL"}  # mutation blocked


def test_confirm_plan_prepends_plan_to_system_blocks(monkeypatch):
    # AC7: iterate_prototype(mode='execute', approved_plan=...) prepends the plan to
    # the system blocks as an addendum; the cache breakpoint stays on the LAST block.
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["system_blocks"] = system_blocks
        captured["mode"] = mode
        return runner.RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                                duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)
    base_blocks = _system()
    _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=base_blocks, user_message=_user(),
        current_source={}, figma_file_key=None, mode="execute",
        approved_plan="- recolour the CTA to blue\n- keep the layout",
    ))
    sb = captured["system_blocks"]
    assert captured["mode"] == "execute"
    # Addendum prepended; cache_control still on the LAST (original) block (AD2).
    assert "recolour the CTA to blue" in sb[0]["text"]
    assert "APPROVED PLAN" in sb[0]["text"]
    assert "cache_control" not in sb[0]
    assert sb[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Caller's list was not mutated (a fresh list is returned).
    assert base_blocks == _system()


def test_no_approved_plan_leaves_system_blocks_untouched(monkeypatch):
    # A plain iterate (no approved plan) does NOT prepend an addendum.
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["system_blocks"] = system_blocks
        return runner.RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                                duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key, ws: None)
    _run(runner.iterate_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, system_blocks=_system(), user_message=_user(),
        current_source={}, figma_file_key=None, mode="execute",
    ))
    assert len(captured["system_blocks"]) == 1
    assert "APPROVED PLAN" not in captured["system_blocks"][0]["text"]


def test_prepend_plan_addendum_is_pure():
    blocks = _system()
    out = runner.prepend_plan_addendum(blocks, "  do the thing  ")
    assert out is not blocks
    assert blocks == _system()                       # input untouched
    assert out[0]["text"].endswith("do the thing")   # stripped
    assert out[1:] == blocks                          # original blocks preserved in order


# ═══════════════════════════════════════════════════════════════════════════
# Layer 4 — Route + background task (FakeSupabaseClient)  (AC6)
# ═══════════════════════════════════════════════════════════════════════════

# Mirrors test_design_agent_iterate.py's _DDL + the P3-07 `plan` column.
_DDL = """
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
    plan               TEXT,
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
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.db.prototype_pending_iterations as queue_mod
    importlib.reload(queue_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, queue=queue_mod,
                           routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    return TestClient(env.main.app)


def _seed_ready(env, *, workspace_id: str = _TEST_COMPANY_ID, current_checkpoint_id=None) -> int:
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="https://bundle/original", current_checkpoint_id=current_checkpoint_id,
    )
    return pid


def _seed_locked(env, *, workspace_id: str = _TEST_COMPANY_ID) -> int:
    pid = _seed_ready(env, workspace_id=workspace_id, current_checkpoint_id=7)
    env.proto.mark_complete(prototype_id=pid, workspace_id=workspace_id)
    return pid


def _all_rows(pid: int) -> list[dict]:
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    cur = db.execute(
        "SELECT * FROM prototype_pending_iterations WHERE prototype_id = ? ORDER BY id ASC",
        (pid,),
    )
    return [dict(r) for r in cur.fetchall()]


def _no_drain(monkeypatch, env):
    """Stub the drain so a route call only enqueues (no real LLM/staging run)."""
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(env.routes, "drain_iteration_queue", _noop)


# ─── POST /iterate mode='plan' (AC6) ───────────────────────────────────────


def test_post_iterate_plan_mode_enqueues_plan_row(env, client, monkeypatch):
    _no_drain(monkeypatch, env)
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate",
                       json={"prompt": "rethink the header", "mode": "plan"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"
    rows = _all_rows(pid)
    assert len(rows) == 1
    assert rows[0]["mode"] == "plan"
    assert rows[0]["plan"] is None  # not written until the plan run completes


# ─── POST /iterate/confirm-plan transition (AC7 at the route level) ─────────


def test_post_confirm_plan_enqueues_execute_with_plan(env, client, monkeypatch):
    _no_drain(monkeypatch, env)
    pid = _seed_ready(env)
    resp = client.post(
        f"/v1/design-agent/{pid}/iterate/confirm-plan",
        json={"prompt": "rethink the header", "plan": "- shrink the logo\n- add a nav link"},
    )
    assert resp.status_code == 200, resp.text
    rows = _all_rows(pid)
    assert len(rows) == 1
    assert rows[0]["mode"] == "execute"
    assert rows[0]["plan"] == "- shrink the logo\n- add a nav link"


def test_confirm_plan_locked_returns_409(env, client):
    pid = _seed_locked(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate/confirm-plan",
                       json={"prompt": "x", "plan": "do x"})
    assert resp.status_code == 409


def test_confirm_plan_wrong_workspace_returns_404(env, client):
    pid = _seed_ready(env, workspace_id="other")
    resp = client.post(f"/v1/design-agent/{pid}/iterate/confirm-plan",
                       json={"prompt": "x", "plan": "do x"})
    assert resp.status_code == 404


def test_confirm_plan_requires_session(env, unauth):
    resp = unauth.post("/v1/design-agent/1/iterate/confirm-plan",
                       json={"prompt": "x", "plan": "do x"})
    assert resp.status_code == 401


def test_confirm_plan_feature_flag_off_returns_404(env, client, monkeypatch):
    pid = _seed_ready(env)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.post(f"/v1/design-agent/{pid}/iterate/confirm-plan",
                       json={"prompt": "x", "plan": "do x"})
    assert resp.status_code == 404


def test_confirm_plan_missing_plan_returns_422(env, client):
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate/confirm-plan", json={"prompt": "x"})
    assert resp.status_code == 422


# ─── Background plan run: persists plan, builds NO checkpoint (AC6) ──────────


@pytest.mark.asyncio
async def test_plan_run_persists_plan_and_creates_no_checkpoint(env, monkeypatch):
    # AC6: a mode='plan' bg run persists the emitted plan onto the queue row and
    # stages NOTHING — no vite_build, no create_checkpoint, no stage_bundle.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    pid = _seed_ready(env)  # no current_checkpoint → source read skipped
    iteration = env.queue.enqueue_iteration(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="rethink header", mode="plan",
    )

    async def fake_iterate(**kwargs):
        return (
            SimpleNamespace(
                status="complete", iters=1,
                final_content=[{"type": "text", "text": "- shrink logo\n- add nav link"}],
                error_message=None, error_class=None,
            ),
            {},
        )

    build_calls, ckpt_calls = [], []
    monkeypatch.setattr(env.routes, "iterate_prototype", fake_iterate)
    monkeypatch.setattr(env.routes, "vite_build", lambda *a, **k: build_calls.append(1))
    monkeypatch.setattr(env.routes, "create_checkpoint", lambda **k: ckpt_calls.append(1))

    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="rethink header", mode="plan"),
        iteration_id=iteration["id"],
    )

    # Plan persisted on the row; NO checkpoint / build.
    row = next(r for r in _all_rows(pid) if r["id"] == iteration["id"])
    assert row["plan"] == "- shrink logo\n- add nav link"
    assert build_calls == []
    assert ckpt_calls == []
    # Prototype row untouched (still ready, original bundle).
    proto = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert proto["status"] == "ready"
    assert proto["bundle_url"] == "https://bundle/original"


@pytest.mark.asyncio
async def test_run_one_iteration_threads_approved_plan_for_execute(env, monkeypatch):
    # AC7 (queue path): a dequeued EXECUTE row with a stored plan threads it to
    # iterate_prototype as approved_plan; the canonical mode='execute' is passed.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    captured: dict = {}

    async def fake_iterate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="error", iters=1, error_message=None,
                               error_class=None, final_content=[]), {}

    monkeypatch.setattr(env.routes, "iterate_prototype", fake_iterate)

    async def fake_read(prototype_id, checkpoint_id):
        return {}
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", fake_read)

    pid = _seed_ready(env)
    await env.routes._run_one_iteration({
        "id": 99, "prototype_id": pid, "workspace_id": _TEST_COMPANY_ID,
        "prompt": "do the approved thing", "applied_comment_id": None,
        "mode": "execute", "plan": "- the approved plan",
    })
    assert captured["mode"] == "execute"
    assert captured["approved_plan"] == "- the approved plan"

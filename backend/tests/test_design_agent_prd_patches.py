"""Tests for the `prd_patches` model + `propose_prd_patch` exit-sentinel (P3-09, F11).

Sentinel #2 of AD17's ≤4. Coverage matches the ticket's Unit Tests section:

- MIGRATION — the new `prd_patches` migration is idempotent by construction, the
  status CHECK rejects illegal values, and the `prds` table is NEVER altered (F11).
- TOOL REGISTRATION + AD17 — propose_prd_patch is in SENTINEL_TOOLS (category
  sentinel), the cap holds at 2 ≤ 4, the 6-action cap is unchanged, execute mode is
  the 8-tool (6 action + 2 sentinel) reconciliation case, and propose_prd_patch is
  EXECUTE-ONLY (absent from plan + scaffold) while clarifying_question stays in all
  three modes.
- HELPERS — insert/list/mark round-trip pending→applied/rejected, are workspace-
  isolated, and apply_patches_to_prd_md folds applied patches in created_at order
  (applied-only, pure + byte-identical).
- TOOL EXEC — dispatching propose_prd_patch persists a pending row AND ends the loop
  as terminal-COMPLETE (no further messages.create; runner never touches
  complete_prototype); a missing prototype returns is_error.
- OBSERVABILITY — insert_patch logs identifiers only (no rationale / patch_md body).

The recording-fake-Anthropic-client + fake-Supabase fixtures mirror
test_design_agent_clarifying_question.py / test_db_prototype_comments.py — the fake
exercises SQL semantics (not Postgres DDL); db modules are imported INSIDE fixtures
(never at collection time) per the sibling-test convention.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import re
import sqlite3
import types
from pathlib import Path

import pytest

from app.design_agent import runner
from app.design_agent.runner import agent_loop
from app.design_agent.tools import (
    ACTION_TOOLS,
    SENTINEL_TOOLS,
    ToolContext,
    dispatch,
    tools_for_mode,
)
from tests._fake_anthropic import _FakeStream

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260601000200_design_agent_prd_patches.sql"
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


def _user(text: str = "Tighten the checkout flow.") -> dict:
    return {"role": "user", "content": [_text(text)]}


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# Fake-Supabase fixtures
# ═══════════════════════════════════════════════════════════════════════════

# SQLite-compatible end-state of `prd_patches` after the P3-09 migration. Postgres-
# only constructs (bigint identity, timestamptz, RLS, FK references, the separate
# ALTER ... ADD CONSTRAINT) are translated/omitted exactly as the sibling test DDLs
# do. The status CHECK is inlined so the fake rejects illegal values like Postgres.
_PRD_PATCHES_DDL = """
CREATE TABLE IF NOT EXISTS prd_patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id        INTEGER NOT NULL,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    patch_md      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'applied', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""

# Mirrors test_design_agent_clarifying_question.py's prototypes DDL — the columns
# `start_prototype` inserts + the id/created_at the fake auto-fills.
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
def patches(isolated_settings, monkeypatch):
    """Reloaded app.db.prd_patches wired to the fake Supabase, with the prd_patches
    table present in the in-memory DB."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PRD_PATCHES_DDL)
    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)  # rebind require_client/utc_now from the reloaded client
    return patches_mod


@pytest.fixture
def exec_env(isolated_settings, monkeypatch):
    """Both prototypes + prd_patches tables present and both helper modules reloaded
    — for the tool-exec path (the executor reads prd_id via get_prototype, then
    insert_patch)."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.executescript(_PROTOTYPE_DDL)
    db.executescript(_PRD_PATCHES_DDL)
    import app.db.prototypes as proto_mod
    import app.db.prd_patches as patches_mod
    importlib.reload(proto_mod)
    importlib.reload(patches_mod)
    return proto_mod, patches_mod


# ═══════════════════════════════════════════════════════════════════════════
# Migration (string-level — isolation-friendly, no live Postgres)
# ═══════════════════════════════════════════════════════════════════════════


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = [line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()]
    return "\n".join(lines).lower()


def test_migration_file_exists_and_named_correctly():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260601000200_design_agent_prd_patches.sql"


def test_migration_declares_all_columns():
    # AC1 — the columns + their key attributes are present in the migration.
    sql = _migration_sql_only()
    for col in (
        "id", "prd_id", "prototype_id", "workspace_id", "rationale",
        "patch_md", "status", "created_at", "resolved_at",
    ):
        assert col in sql, f"migration missing column {col}"
    assert "prd_id        bigint not null references prds(id) on delete cascade" in sql
    assert "prototype_id  bigint not null references prototypes(id) on delete cascade" in sql
    assert "status        text   not null default 'pending'" in sql


def test_migration_applies_idempotently():
    # AC1 — structural idempotency (apply-twice at the SQL-string level; a live-
    # Postgres apply-twice is deferred to a phase smoke, same convention as siblings).
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    name = "prd_patches_status_check"
    assert f"drop constraint if exists {name}" in sql, f"{name} not dropped-before-add"
    assert f"add constraint {name}" in sql
    assert sql.index(f"drop constraint if exists {name}") < sql.index(f"add constraint {name}")


def test_migration_workspace_id_no_default():
    # AC1 / Rule #20 — workspace_id TEXT NOT NULL with NO DEFAULT.
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql), "workspace_id column missing"
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT (Rule #20)"


def test_migration_uses_rls_no_policies():
    sql = _migration_sql_only()
    assert sql.count("enable row level security") == 1
    assert "create policy" not in sql


def test_migration_does_not_alter_prds():
    # AC1 / AC9 / F11 — the prds table is NEVER altered by this migration.
    sql = _migration_sql_only()
    assert "alter table prds" not in sql
    assert "update prds" not in sql


def test_status_check_rejects_invalid(patches):
    # AC1 — inserting status='broken' raises a DB integrity error.
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO prd_patches "
            "(prd_id, prototype_id, workspace_id, rationale, patch_md, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [1, 2, "app", "r", "m", "broken"],
        )


# ═══════════════════════════════════════════════════════════════════════════
# Tool registration + AD17
# ═══════════════════════════════════════════════════════════════════════════


def test_propose_prd_patch_is_sentinel():
    # AC2 — propose_prd_patch is in SENTINEL_TOOLS with category="sentinel".
    names = [t.name for t in SENTINEL_TOOLS]
    assert "propose_prd_patch" in names
    tool = next(t for t in SENTINEL_TOOLS if t.name == "propose_prd_patch")
    assert tool.category == "sentinel"


def test_sentinel_count_is_two_within_cap():
    # AC2 — len(SENTINEL_TOOLS) == 2; the module-level assert <= 4 holds; the
    # 6-action cap is unchanged.
    assert len(SENTINEL_TOOLS) == 2
    assert len(SENTINEL_TOOLS) <= 4
    assert len(ACTION_TOOLS) == 6


def test_execute_mode_has_six_action_two_sentinel():
    # AC3 — the 8-tool execute registry; AD17 split: action ≤6 ∧ sentinel ≤4
    # (NOT a flat ≤7). This is the reconciliation case the dispatch brief flagged.
    registry = tools_for_mode("execute")
    actions = [t for t in registry if t.category == "action"]
    sentinels = [t for t in registry if t.category == "sentinel"]
    assert len(actions) == 6
    assert len(sentinels) == 2
    assert len(registry) == 8
    assert len(actions) <= 6 and len(sentinels) <= 4


def test_propose_prd_patch_execute_only():
    # AC3 — propose_prd_patch appears ONLY in execute mode (no PRD-edit step at
    # plan/scaffold).
    assert "propose_prd_patch" in {t.name for t in tools_for_mode("execute")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("plan")}
    assert "propose_prd_patch" not in {t.name for t in tools_for_mode("scaffold")}


def test_clarifying_question_still_in_all_modes():
    # AC3 — sentinel #1 stays plan-safe in all three modes; the per-mode AD17 split
    # still holds with both sentinels declared.
    for mode in ("plan", "scaffold", "execute"):
        registry = tools_for_mode(mode)
        assert "clarifying_question" in {t.name for t in registry}, f"missing in {mode}"
        assert sum(1 for t in registry if t.category == "action") <= 6
        assert sum(1 for t in registry if t.category == "sentinel") <= 4


def test_description_has_negative_space():
    # AC4 — description is >=4 sentences and includes the two specific negative-space
    # clauses the ticket calls out ("visual tweaks", "one patch per run").
    tool = next(t for t in SENTINEL_TOOLS if t.name == "propose_prd_patch")
    desc = tool.description
    assert "Do NOT" in desc
    assert len(desc) >= 200
    assert desc.count(". ") + desc.count(".") >= 4
    assert "visual" in desc
    assert "one patch per run" in desc


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — insert / list / mark / apply
# ═══════════════════════════════════════════════════════════════════════════


def test_insert_patch_persists_pending(patches):
    # AC5 — insert returns a pending row; list_pending_patches round-trips it.
    row = patches.insert_patch(
        prd_id=1, prototype_id=2, workspace_id="app",
        rationale="added a confirm step", patch_md="## Confirm\nNew step.",
    )
    assert row["status"] == "pending"
    assert row["rationale"] == "added a confirm step"
    assert row["patch_md"] == "## Confirm\nNew step."
    assert isinstance(row["id"], int) and row["id"] > 0
    pend = patches.list_pending_patches(prd_id=1, workspace_id="app")
    assert len(pend) == 1 and pend[0]["id"] == row["id"]


def test_insert_patch_empty_rationale_raises(patches):
    # AC5 — validation: empty rationale / whitespace patch_md raise ValueError.
    with pytest.raises(ValueError):
        patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                             rationale="   ", patch_md="m")
    with pytest.raises(ValueError):
        patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                             rationale="r", patch_md="   ")


def test_list_pending_patches_excludes_resolved(patches):
    # AC5 — only pending rows; applied/rejected excluded.
    p1 = patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                              rationale="r1", patch_md="m1")
    p2 = patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                              rationale="r2", patch_md="m2")
    patches.mark_patch_applied(patch_id=p1["id"], workspace_id="app")
    pend = patches.list_pending_patches(prd_id=1, workspace_id="app")
    assert [p["id"] for p in pend] == [p2["id"]]


def test_mark_patch_applied_sets_resolved_at(patches):
    # AC6 — applied flip sets status + resolved_at.
    p = patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                             rationale="r", patch_md="m")
    updated = patches.mark_patch_applied(patch_id=p["id"], workspace_id="app")
    assert updated is not None
    assert updated["status"] == "applied"
    assert updated["resolved_at"] is not None


def test_mark_patch_rejected(patches):
    # AC6 — rejected flip sets status + resolved_at and drops it from pending.
    p = patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                             rationale="r", patch_md="m")
    updated = patches.mark_patch_rejected(patch_id=p["id"], workspace_id="app")
    assert updated is not None
    assert updated["status"] == "rejected"
    assert updated["resolved_at"] is not None
    assert patches.list_pending_patches(prd_id=1, workspace_id="app") == []


def test_patch_helpers_workspace_isolated(patches):
    # AC6 / AC10 — list under the wrong workspace is empty; a 'demo' mark does not
    # touch an 'app' patch.
    app_patch = patches.insert_patch(prd_id=1, prototype_id=2, workspace_id="app",
                                     rationale="r", patch_md="m")
    assert patches.list_pending_patches(prd_id=1, workspace_id="demo") == []
    assert len(patches.list_pending_patches(prd_id=1, workspace_id="app")) == 1
    assert patches.mark_patch_applied(patch_id=app_patch["id"], workspace_id="demo") is None
    still = patches.list_pending_patches(prd_id=1, workspace_id="app")
    assert len(still) == 1 and still[0]["status"] == "pending"


def test_apply_patches_appends_applied_only_in_order(patches):
    # AC7 — applied patches appended under "## Design Agent updates", created_at
    # order; pending/rejected ignored.
    prd = "# My PRD\n\nOriginal body."
    rows = [
        {"id": 1, "status": "applied", "patch_md": "First update.", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "status": "pending", "patch_md": "Ignored pending.", "created_at": "2026-01-02T00:00:00Z"},
        {"id": 3, "status": "applied", "patch_md": "Second update.", "created_at": "2026-01-03T00:00:00Z"},
        {"id": 4, "status": "rejected", "patch_md": "Ignored rejected.", "created_at": "2026-01-04T00:00:00Z"},
    ]
    out = patches.apply_patches_to_prd_md(prd, rows)
    assert "## Design Agent updates" in out
    assert "First update." in out and "Second update." in out
    assert "Ignored pending." not in out
    assert "Ignored rejected." not in out
    assert out.index("First update.") < out.index("Second update.")
    assert out.startswith("# My PRD")


def test_apply_patches_no_applied_returns_unchanged(patches):
    # AC7 — with no applied patch, the PRD is returned unchanged (no section added).
    prd = "# PRD\n\nbody"
    rows = [{"id": 1, "status": "pending", "patch_md": "x", "created_at": "2026-01-01"}]
    assert patches.apply_patches_to_prd_md(prd, rows) == prd


def test_apply_patches_pure_byte_identical(patches):
    # AC7 — pure + deterministic: same inputs → byte-identical output; created_at
    # ascending regardless of list order; input list not mutated.
    prd = "# PRD\n\nbody"
    rows = [
        {"id": 2, "status": "applied", "patch_md": "BBB", "created_at": "2026-01-02"},
        {"id": 1, "status": "applied", "patch_md": "AAA", "created_at": "2026-01-01"},
    ]
    out1 = patches.apply_patches_to_prd_md(prd, copy.deepcopy(rows))
    out2 = patches.apply_patches_to_prd_md(prd, copy.deepcopy(rows))
    assert out1 == out2
    assert out1.index("AAA") < out1.index("BBB")
    assert [r["id"] for r in rows] == [2, 1], "input list must not be mutated"


# ═══════════════════════════════════════════════════════════════════════════
# Tool exec — terminal-COMPLETE + persistence
# ═══════════════════════════════════════════════════════════════════════════


def test_propose_prd_patch_exec_inserts_pending_and_ends_loop(exec_env, monkeypatch):
    # AC8 — dispatching the sentinel inserts a pending row AND ends the loop
    # (terminal-COMPLETE): no further messages.create; runner never references
    # complete_prototype (the iterate path stages via the route's _stage_iterate_run,
    # which does NOT re-stamp completed_at).
    proto_mod, patches_mod = exec_env
    pid = proto_mod.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "propose_prd_patch", {
            "rationale": "added a confirmation modal",
            "patch_md": "## Confirmation\nThe CTA now opens a modal.",
        })]),
        # A second response exists but must NEVER be consumed (loop must break).
        _msg("end_turn", [_text("should not reach")]),
    ])
    ctx = ToolContext(prototype_id=pid, workspace_id="app", virtual_fs={})
    result = _run(agent_loop(_system(), _user(), ctx, mode="execute"))
    assert result.status == "complete"
    assert len(client.calls) == 1
    pend = patches_mod.list_pending_patches(prd_id=1, workspace_id="app")
    assert len(pend) == 1
    assert pend[0]["rationale"] == "added a confirmation modal"
    assert pend[0]["patch_md"] == "## Confirmation\nThe CTA now opens a modal."
    assert pend[0]["status"] == "pending"
    # Terminal-COMPLETE is NOT a complete_prototype re-stamp — the runner module does
    # not even import it (mirrors P3-08's "no create_checkpoint in runner" assertion).
    assert not hasattr(runner, "complete_prototype")
    assert not hasattr(runner, "_stage_complete_run")


def test_propose_prd_patch_wins_over_batched_write(exec_env, monkeypatch):
    # AC8 (terminal precedence, consistent with clarifying_question's AC5): a
    # propose_prd_patch batched with a write WINS — the batched write is NOT applied.
    proto_mod, patches_mod = exec_env
    pid = proto_mod.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    ctx = ToolContext(prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "ORIGINAL"})
    _install_client(monkeypatch, [
        _msg("tool_use", [
            _tool_use("t1", "write", {"path": "src/App.tsx", "content": "MUTATED"}),
            _tool_use("t2", "propose_prd_patch", {"rationale": "r", "patch_md": "m"}),
        ]),
    ])
    result = _run(agent_loop(_system(), _user(), ctx, mode="execute"))
    assert result.status == "complete"
    # The batched write must NOT have run — virtual_fs is untouched.
    assert ctx.virtual_fs == {"src/App.tsx": "ORIGINAL"}
    # The patch was still persisted.
    assert len(patches_mod.list_pending_patches(prd_id=1, workspace_id="app")) == 1


def test_propose_prd_patch_scaffold_does_not_terminate(exec_env, monkeypatch):
    # AC3 + AD10 — in scaffold mode propose_prd_patch is NOT registered, so an
    # emission is rejected out-of-mode and the loop CONTINUES (no pending row, no
    # early terminal-COMPLETE). Guards the gate that keeps P3-08's
    # other-sentinel-name test green.
    proto_mod, patches_mod = exec_env
    pid = proto_mod.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "propose_prd_patch", {"rationale": "r", "patch_md": "m"})]),
        _msg("end_turn", [_text("continued past the out-of-mode sentinel")]),
    ])
    ctx = ToolContext(prototype_id=pid, workspace_id="app", virtual_fs={})
    result = _run(agent_loop(_system(), _user(), ctx, mode="scaffold"))
    assert result.status == "complete"
    assert len(client.calls) == 2  # the loop continued — terminal arm did NOT fire
    assert patches_mod.list_pending_patches(prd_id=1, workspace_id="app") == []


def test_propose_prd_patch_exec_missing_prototype_returns_is_error(exec_env):
    # AC8 — the executor returns is_error when the prototype row is absent.
    ctx = ToolContext(prototype_id=99999, workspace_id="app", virtual_fs={})
    res = _run(dispatch("propose_prd_patch", {"rationale": "r", "patch_md": "m"}, ctx))
    assert res.get("is_error") is True
    assert res["tool_name"] == "propose_prd_patch"


def test_dispatch_propose_prd_patch_routes_to_executor(exec_env):
    # AC12 — dispatch('propose_prd_patch', …) resolves to the executor and persists
    # + returns the _sentinel payload.
    proto_mod, patches_mod = exec_env
    pid = proto_mod.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    ctx = ToolContext(prototype_id=pid, workspace_id="app", virtual_fs={})
    res = _run(dispatch("propose_prd_patch", {"rationale": "r", "patch_md": "m"}, ctx))
    assert res["_sentinel"] == "propose_prd_patch"
    assert isinstance(res["patch_id"], int)
    pend = patches_mod.list_pending_patches(prd_id=1, workspace_id="app")
    assert len(pend) == 1 and pend[0]["id"] == res["patch_id"]


# ═══════════════════════════════════════════════════════════════════════════
# Never-ALTER-prds (AC9) + observability (AC11)
# ═══════════════════════════════════════════════════════════════════════════


def test_prd_patches_module_never_writes_prds():
    # AC9 / F11 — no code path issues a write/read against the `prds` table; the
    # agent's PRD edit lives only in `prd_patches`.
    src = (Path(__file__).resolve().parents[1] / "app" / "db" / "prd_patches.py").read_text()
    assert 'table("prds")' not in src
    assert "table('prds')" not in src


def test_insert_patch_logs_no_patch_content(patches, caplog):
    # AC11 — the INFO line carries identifiers only; rationale + patch_md never appear.
    secret_rationale = "SECRET_RATIONALE_XYZ"
    secret_patch = "SECRET_PATCH_BODY_ABC"
    with caplog.at_level(logging.INFO, logger="app.db.prd_patches"):
        row = patches.insert_patch(
            prd_id=7, prototype_id=8, workspace_id="app",
            rationale=secret_rationale, patch_md=secret_patch,
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"prd_patch_proposed prototype_id=8 prd_id=7 patch_id={row['id']}" in blob
    assert secret_rationale not in blob
    assert secret_patch not in blob


# ═══════════════════════════════════════════════════════════════════════════
# P7-03 — lock apply_patches_to_prd_md against the ux-explore S3 dev-hack
# ═══════════════════════════════════════════════════════════════════════════
# The ux-explore session ran a local DO-NOT-COMMIT hack: an UNCONDITIONAL early
# `return prd_md` at the top of apply_patches_to_prd_md (before the `applied`
# filter), which short-circuited ALL patch application on read — a correctness
# regression to the P3/F11 PRD-writeback loop, not just a heading-hide. That hack
# was intentionally EXCLUDED from the committed snapshot (a10c2df). These tests
# LOCK the clean P3 behaviour so the local patch cannot silently re-enter: they
# assert applied patches still fold in, and would FAIL against the dev-hack's
# unconditional `return prd_md`. They call apply_patches_to_prd_md directly with
# in-test fixture inputs and assert on the returned string — no git-rev / historical
# object reads (CI uses fetch-depth=1 shallow clones).


def test_unconditional_early_return_absent(patches):
    # P7-03 AC1/AC2 (regression) — a single status='applied' NON-heading patch must
    # appear in the output. Against the S3 dev-hack (unconditional `return prd_md`
    # at function top) this FAILS, because the function would return prd_md verbatim
    # before ever reaching the `applied` fold.
    prd = "# Checkout PRD\n\nOriginal flow description."
    rows = [
        {"id": 1, "status": "applied", "patch_md": "Add a confirm-before-pay step.",
         "created_at": "2026-02-01T00:00:00Z"},
    ]
    out = patches.apply_patches_to_prd_md(prd, rows)
    assert out != prd, "output must differ from input — the applied patch was folded"
    assert "Add a confirm-before-pay step." in out
    assert "## Design Agent updates" in out


def test_applied_non_heading_patch_renders(patches):
    # P7-03 AC2 — patch_md that is a plain (non-heading) line renders under the
    # "## Design Agent updates" section.
    prd = "# Onboarding PRD\n\nBody."
    rows = [
        {"id": 5, "status": "applied", "patch_md": "New bullet about onboarding.",
         "created_at": "2026-02-02T00:00:00Z"},
    ]
    out = patches.apply_patches_to_prd_md(prd, rows)
    assert "## Design Agent updates" in out
    assert "New bullet about onboarding." in out
    # The patch text appears AFTER the section heading.
    assert out.index("## Design Agent updates") < out.index("New bullet about onboarding.")


def test_no_applied_patches_returns_input_unchanged(patches):
    # P7-03 AC3 (edge) — the LEGITIMATE `if not applied:` guard: an all-pending list
    # returns prd_md unchanged (no "## Design Agent updates" section emitted). This
    # is the guard the hack masqueraded as; the lock keeps the two distinct.
    prd = "# PRD\n\nbody"
    rows = [
        {"id": 1, "status": "pending", "patch_md": "pending one",
         "created_at": "2026-02-01T00:00:00Z"},
        {"id": 2, "status": "pending", "patch_md": "pending two",
         "created_at": "2026-02-02T00:00:00Z"},
    ]
    assert patches.apply_patches_to_prd_md(prd, rows) == prd
    assert "## Design Agent updates" not in patches.apply_patches_to_prd_md(prd, rows)


def test_apply_patches_is_deterministic(patches):
    # P7-03 AC4 (edge) — same (prd_md, patches) inputs → byte-identical output on two
    # successive calls.
    prd = "# PRD\n\nbody"
    rows = [
        {"id": 2, "status": "applied", "patch_md": "second", "created_at": "2026-02-02T00:00:00Z"},
        {"id": 1, "status": "applied", "patch_md": "first", "created_at": "2026-02-01T00:00:00Z"},
    ]
    out1 = patches.apply_patches_to_prd_md(prd, copy.deepcopy(rows))
    out2 = patches.apply_patches_to_prd_md(prd, copy.deepcopy(rows))
    assert out1 == out2


def test_rejected_patches_excluded(patches):
    # P7-03 (edge) — a status='rejected' patch is NOT folded into the output, even
    # when an applied patch is present alongside it.
    prd = "# PRD\n\nbody"
    rows = [
        {"id": 1, "status": "rejected", "patch_md": "REJECTED_CONTENT",
         "created_at": "2026-02-01T00:00:00Z"},
        {"id": 2, "status": "applied", "patch_md": "APPLIED_CONTENT",
         "created_at": "2026-02-02T00:00:00Z"},
    ]
    out = patches.apply_patches_to_prd_md(prd, rows)
    assert "APPLIED_CONTENT" in out
    assert "REJECTED_CONTENT" not in out

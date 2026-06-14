"""Tests for the post-build typecheck-repair loop (Design Agent reliability).

When a freshly built prototype fails its runtime-breaking type check (e.g. an
agent imported a screen it was cut off before writing, TS2304), the route no
longer fails outright. With agent context (`system_blocks`) in hand it re-enters
the agent up to a bounded number of times to write the missing file(s),
rebuilding after each pass. On exhaustion it deterministically strips/stubs the
dangling imports and rebuilds once so the prototype still renders; only if THAT
still fails does it fail the row with a distinct `TypeCheckRepairExhausted`.

Conventions (env fixture, fake Supabase, _PROTOTYPE_DDL, _async_return,
_checkpoints_for) are duplicated from test_design_agent_build_repair.py — each
test file in this repo is self-contained. The route's `vite_build_with_repair`
is REAL; tests stub the underlying `storage.vite_build` so the repair loop runs,
and monkeypatch `env.routes.repair_typecheck_run` (the agent re-entry seam).
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import types
from types import SimpleNamespace

import pytest

import app.design_agent.storage as storage
from app.design_agent import runner
from app.design_agent.progress import FINISHING_LABEL, FINISHING_STEP
from app.design_agent.storage import TypeCheckError, ViteBuildError

# ─── Fixtures from the spec ──────────────────────────────────────────────────

_ORPHAN_APP_TSX = (
    'import HomeScreen from "./screens/HomeScreen";\n'
    'export default function App() { return <HomeScreen />; }\n'
)
_TS2304_MSG = (
    "runtime-breaking type errors: src/App.tsx(1,8): error TS2304: "
    "Cannot find name 'HomeScreen'"
)
_SYS = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]


def _stateful_typecheck_build(*, required_key, dist=None):
    """vite_build replacement: raise TypeCheckError UNTIL `required_key` appears in
    the vfs, then return a dist. Records calls so the rebuild count is assertable."""
    state = {"calls": 0}

    async def _build(virtual_fs):
        state["calls"] += 1
        if required_key not in virtual_fs:
            raise TypeCheckError(_TS2304_MSG)
        return dict(dist or {"index.html": "<html>built</html>"})

    return _build, state


def _fake_repair_run(*, writes=None, cost_usd=0.02):
    """A fake repair_typecheck_run: records calls, optionally writes `writes` into the
    returned vfs (simulating the agent fixing the build), returns (result, vfs) where
    result.usage.est_cost_usd(model) == cost_usd."""
    state = {"calls": 0, "diagnostics": []}

    async def _run(*, prototype_id, workspace_id, system_blocks, virtual_fs, diagnostics,
                   figma_file_key=None, figma_node_id=None, scenario="A"):
        state["calls"] += 1
        state["diagnostics"].append(diagnostics)
        new_vfs = dict(virtual_fs)
        if writes:
            new_vfs.update(writes)
        result = SimpleNamespace(usage=SimpleNamespace(est_cost_usd=lambda m: cost_usd))
        return result, new_vfs

    return _run, state


# ─── Route hook — fake Supabase DB (duplicated per-file convention) ───────────

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
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
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
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Fake-Supabase DB + design-agent route module reloaded in dependency order.

    The route's `vite_build_with_repair` is REAL; tests stub the underlying
    `storage.vite_build` so the repair loop runs."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, db=db_mod)


def _checkpoints_for(prototype_id: int):
    from tests import _fake_supabase

    return _fake_supabase.get_fake_db().execute(
        f"SELECT id, bundle_url FROM prototype_checkpoints WHERE prototype_id = {prototype_id}"
    ).fetchall()


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _wire_staging(env, monkeypatch):
    """Common staging stubs so the route can reach 'ready' once a build is green."""
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)


# ─── Tests ───────────────────────────────────────────────────────────────────


async def test_repair_writes_missing_screen_then_completes(env, monkeypatch):
    """AC3 happy path: a TypeCheckError triggers an agent re-entry that writes the
    missing screen; the next rebuild is green and the prototype reaches 'ready'."""
    build, _ = _stateful_typecheck_build(required_key="src/screens/HomeScreen.tsx")
    repair, repair_state = _fake_repair_run(
        writes={"src/screens/HomeScreen.tsx": "export default function HomeScreen(){return <div/>;}"},
    )
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert _checkpoints_for(pid)               # a checkpoint was created
    assert repair_state["calls"] >= 1          # the agent was re-entered


async def test_repair_feeds_diagnostics_to_agent(env, monkeypatch):
    """AC3/PIN: the build's compiler diagnostics are threaded into the repair
    re-entry so the agent knows exactly what to fix."""
    build, _ = _stateful_typecheck_build(required_key="src/screens/HomeScreen.tsx")
    repair, repair_state = _fake_repair_run(
        writes={"src/screens/HomeScreen.tsx": "export default function HomeScreen(){return <div/>;}"},
    )
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    assert repair_state["diagnostics"]                       # at least one re-entry
    assert "TS2304" in repair_state["diagnostics"][0]        # the real build message


async def test_exhaustion_strips_to_green(env, monkeypatch):
    """AC3 fallback: when the agent never fixes the build (exhausting the re-tries),
    the deterministic strip/stub pass writes the orphan screen stub, and the final
    rebuild is green → 'ready'. Exercises the REAL repair_unresolved_relative_imports."""
    # required_key is the stub path the real strip writes for a `./screens/*` orphan.
    build, _ = _stateful_typecheck_build(required_key="src/screens/HomeScreen.tsx")
    repair, repair_state = _fake_repair_run(writes=None)     # agent never fixes it
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert repair_state["calls"] == env.routes._TYPECHECK_REPAIR_MAX_ITERS


async def test_exhaustion_strip_cannot_fix_fails_with_distinct_class(env, monkeypatch, caplog):
    """AC3 fail edge: when even the strip-to-green rebuild keeps raising
    TypeCheckError, the row fails with the distinct TypeCheckRepairExhausted class
    and a WARNING log line names that class — without leaking the raw diagnostics."""
    # vite_build NEVER returns a dist — required_key can never appear.
    build, _ = _stateful_typecheck_build(required_key="__never__.tsx")
    repair, _repair_state = _fake_repair_run(writes=None)
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(
            prototype_id=pid, workspace_id="app",
            virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
        )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "TypeCheckRepairExhausted" in (row["error"] or "")
    warned = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("typecheck_repair_failed")
    ]
    assert warned and "error_class=TypeCheckRepairExhausted" in warned[0]


async def test_repair_cost_cap_stops_early(env, monkeypatch):
    """Separate-cap behaviour: repair spend is its own budget. Two re-entries at
    $0.06 each hit the $0.10 cap, so the loop breaks BEFORE a third re-entry; the
    strip-to-green pass then stubs the orphan → 'ready'. Exactly 2 repair calls."""
    build, _ = _stateful_typecheck_build(required_key="src/screens/HomeScreen.tsx")
    repair, repair_state = _fake_repair_run(writes=None, cost_usd=0.06)
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert repair_state["calls"] == 2          # third re-entry skipped by the cap
    assert row["status"] == "ready"


async def test_clean_build_runs_zero_repairs(env, monkeypatch):
    """AC6 regression: a build that is green on the first pass never enters the
    repair loop — zero agent re-entries, status 'ready'."""
    async def _clean_build(virtual_fs):
        return {"index.html": "<html>built</html>"}

    repair, repair_state = _fake_repair_run()
    monkeypatch.setattr(storage, "vite_build", _clean_build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert repair_state["calls"] == 0


async def test_non_typecheck_failure_still_fails_without_repair(env, monkeypatch):
    """AC6 regression: a non-typecheck build failure (e.g. a syntax error vite's own
    repair can't fix) still fails the row immediately — the typecheck-repair loop is
    never entered."""
    async def _syntax_error(virtual_fs):
        raise ViteBuildError("vite build exit=1: Unexpected token")

    repair, repair_state = _fake_repair_run()
    monkeypatch.setattr(storage, "vite_build", _syntax_error)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert repair_state["calls"] == 0
    assert "ViteBuildError" in (row["error"] or "")


async def test_typecheck_error_without_system_blocks_fails_precisely(env, monkeypatch):
    """AC6 guard: with NO agent context (system_blocks omitted), a TypeCheckError
    fails the row precisely as before — there is nothing to re-enter the agent with,
    so the repair loop is never invoked."""
    async def _typecheck_fail(virtual_fs):
        raise TypeCheckError(_TS2304_MSG)

    repair, repair_state = _fake_repair_run()
    monkeypatch.setattr(storage, "vite_build", _typecheck_fail)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX},   # NO system_blocks
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "TypeCheckError" in (row["error"] or "")
    assert repair_state["calls"] == 0


async def test_repair_progress_is_generic_label_only(env, monkeypatch):
    """AC5 egress seam (route): repair publishes the calm 'Finishing up…' step, and
    NO step event ever carries the raw compiler diagnostics (TS2304 / 'Cannot find
    name'). The diagnostics live in the agent's user turn, never in user-facing copy."""
    events: list[dict] = []
    monkeypatch.setattr(env.routes, "publish_step", lambda pid, step: events.append(step))

    build, _ = _stateful_typecheck_build(required_key="src/screens/HomeScreen.tsx")
    repair, _state = _fake_repair_run(
        writes={"src/screens/HomeScreen.tsx": "export default function HomeScreen(){return <div/>;}"},
    )
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "repair_typecheck_run", repair)
    _wire_staging(env, monkeypatch)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX}, system_blocks=_SYS,
    )
    texts = [e.get("text", "") for e in events]
    assert FINISHING_LABEL in texts                          # the calm step was shown
    for text in texts:
        assert "TS2304" not in text
        assert "Cannot find name" not in text


# ─── Runner-level egress seam: fake Anthropic client (copied for self-containment) ──


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
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append({
            "system": kwargs.get("system"),
            "messages": copy.deepcopy(kwargs.get("messages")),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "tools": kwargs.get("tools"),
        })
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp


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


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent. Build prototypes."},
        {
            "type": "text",
            "text": "<design system + tool defs — the stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Build a landing page."):
    return {"role": "user", "content": [_text(text)]}


def _ctx(**overrides):
    from app.design_agent.tools import ToolContext

    base = dict(prototype_id=1, workspace_id="app", virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def test_agent_loop_progress_label_pins_every_step(monkeypatch):
    """AC5 egress seam (runner): when agent_loop is given a `progress_label`, EVERY
    per-iteration step event emits that fixed label — never a per-build label that
    could leak the diagnostics handed to the repair re-entry."""
    events: list[dict] = []
    monkeypatch.setattr(runner, "publish_step", lambda pid, step: events.append(step))
    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])

    asyncio.run(runner.agent_loop(
        _system(), _user(), _ctx(), max_iters=2, progress_label="Finishing up…",
    ))
    assert events                                            # at least one step fired
    for e in events:
        assert e.get("text") == "Finishing up…"


def test_render_repair_user_embeds_diagnostics():
    """Supporting: the diagnostics ride in the agent's USER turn, not in the
    user-facing step copy. The rendered user message contains the diagnostics; the
    progress label does not."""
    from app.design_agent.runner import _render_typecheck_repair_user

    diagnostics = "src/App.tsx(1,8): error TS2304: Cannot find name 'HomeScreen'"
    rendered = _render_typecheck_repair_user(diagnostics)
    assert diagnostics in rendered
    assert diagnostics not in FINISHING_LABEL


def test_nudge_both_branches_lead_with_cleanup():
    """AC2: both wrap-up branches put the remove-orphan-import imperative FIRST,
    ahead of the stop/converge guidance — so a cut-off agent drops dangling imports
    before anything else, the cheapest way to keep a build green."""
    from app.design_agent.runner import _wrap_up_nudge

    hard = _wrap_up_nudge(0)
    soft = _wrap_up_nudge(10)
    assert "remove that import" in hard
    assert "remove that import" in soft
    # Cleanup imperative leads in both branches.
    assert hard.index("remove that import") < hard.index("STOP now")
    assert soft.index("remove that import") < soft.index("Start converging")

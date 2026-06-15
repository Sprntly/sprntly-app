"""End-to-end capstone for Phase 4 — manual edit commit-back + export-feedback
enrichment (P4-06).

The Phase-4 CI capstone (BUILD-PHASES.md §Phase 4 acceptance). It exercises the
*real* P4 backend code paths end-to-end through the live FastAPI stack, with ONLY
the Anthropic client mocked at the runner boundary and `vite_build` stubbed (the
toolchain is not under test — P0-04 owns the real build + anchor-id coverage). It
proves three closures, mirroring the predecessor capstones
`test_design_agent_e2e_p2.py` (lifecycle/share/export) and
`test_design_agent_p3_smoke.py` (comment → iterate → checkpoint):

  1. MANUAL EDIT (P4-01 triples → P4-02 commit-back): a manual edit advances the
     checkpoint, the change reaches the staged `_source/`, and the share URL is
     stable (F7). The agent's `line_replace` swap is committed into source, NOT
     recomputed by the LLM (AD23).

  2. EXPORT FEEDBACK (P3-02 comment → resolve + P4-07): after a comment is created
     and resolved and the prototype is marked complete, the export markdown carries
     a `## Resolved Feedback` section naming the resolved comment — closing F16's
     "comments, and how they were resolved" clause end-to-end. An OPEN comment's
     body never reaches the export (P4-07 AC3).

  3. STALE-ANCHOR (fail-closed, AD23): a manual edit whose target the agent cannot
     resolve fails LOUDLY (status='failed', a `manual_edit: anchor … not found`
     error visible on poll) and does NOT advance the checkpoint — never a silent
     no-op `ready`.

WHY THIS MIRRORS P2-11/P3-13 BUT DIVERGES — all verified against the release HEAD:

- Mock at `app.design_agent.runner.get_design_agent_client` (the runner binds the
  name locally and calls it inside `agent_loop` — "patch where it's used"), exactly
  as the predecessors. The mocked `messages.create` is SYNC (the runner wraps it in
  `asyncio.to_thread`); a MagicMock `.side_effect` list scripts each leg.

- Async harness, not sync TestClient. POST /generate + POST /manual-edit fire
  background work via `asyncio.create_task`; a bare TestClient runs each request on
  a fresh per-request portal and orphans that task. Running the app through
  `httpx.ASGITransport` inside `async def` keeps the task on the test's own loop,
  where it completes deterministically across `await`s.

- `vite_build` is STUBBED in the routes module (toolchain-independent + CI-fast).
  But `stage_bundle` + `read_source_files_for_checkpoint` are LEFT REAL on the
  filesystem fallback (SUPABASE_STORAGE_BUCKET unset, `storage_dir` pointed at a
  tmp dir): the manual-edit "source reflects the change" assertion (AC1) needs the
  raw `_source/` to genuinely round-trip stage → read, so it cannot be stubbed.

- Auth is INJECTED via `app.dependency_overrides[require_company]` (the P4-02
  mitigation) — the e2e does NOT rely on the local auth.py path. The public
  `/by-token` resolver carries no auth dependency and is unaffected.

- This ticket adds NO production code. If a leg could not be exercised because a
  P4-01/02/07 contract were underspecified, that would be a defect in the relevant
  ticket — surfaced, not worked around inline.
"""
from __future__ import annotations

import asyncio
import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.auth import CompanyContext
from tests._fake_anthropic import _FakeStream
from tests.conftest import _TEST_COMPANY_ID, _TEST_USER_ID

# ─── Generated source under test ─────────────────────────────────────────────
#
# A small, self-contained, autofixer-clean component (React-only import, valid
# JSX, real Tailwind palette classes — `bg-blue-600`/`bg-slate-50` pass the P1-10
# Tailwind fixer, which flags ONLY shadcn semantic tokens). The `bg-blue-600`
# button is the manual-edit target. The agent NEVER emits `data-anchor-id` (AD4);
# the Vite plugin would apply it at build time (stubbed here).
_GEN_APP_TSX = (
    'import { useState } from "react";\n'
    "\n"
    "export default function App() {\n"
    "  const [count, setCount] = useState(0);\n"
    "  return (\n"
    '    <div className="flex min-h-screen items-center justify-center bg-slate-50">\n'
    "      <button\n"
    '        type="button"\n'
    '        className="bg-blue-600 text-white rounded-md px-4 py-2"\n'
    "        onClick={() => setCount((c) => c + 1)}\n"
    "      >\n"
    "        Count: {count}\n"
    "      </button>\n"
    "    </div>\n"
    "  );\n"
    "}\n"
)
# Derive the line_replace pre-image from the constant itself (robust to edits of
# the source above — NOT a hardcoded line number). `_exec_line_replace` joins
# `splitlines()[first-1:last]` and requires it to equal `search` byte-for-byte.
_GEN_LINES = _GEN_APP_TSX.splitlines()
_BLUE_IDX = next(i for i, ln in enumerate(_GEN_LINES) if "bg-blue-600" in ln)
_BLUE_LINE_NO = _BLUE_IDX + 1                       # 1-indexed, inclusive
_SEARCH_LINE = _GEN_LINES[_BLUE_IDX]
_REPLACE_LINE = _SEARCH_LINE.replace("bg-blue-600", "bg-red-600")


# Combined DDL: P1-06 prototypes + P2-06 share/lock + P3-08 pending_question +
# prototype_checkpoints (P1-06) + prototype_comments (P3-01, resolve enum +
# resolved_at) + prototype_exports (P2-09). prds + prd_patches come from conftest's
# base _FAKE_SCHEMA (do NOT recreate them). The SQLite fake exercises SQL
# semantics, not PG-specific DDL (booleans/jsonb translated by the fake's layer).
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
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER,
    pending_question       TEXT
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
CREATE TABLE prototype_exports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    checkpoint_id     INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    markdown_content  TEXT NOT NULL,
    generated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    is_stale          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (prototype_id, checkpoint_id)
);
"""


# ─── Mock Anthropic client (mirrors test_design_agent_e2e_p2.py) ─────────────


def _block(data: dict):
    """A stand-in content block — the runner only ever calls `.model_dump()`."""
    return SimpleNamespace(model_dump=lambda: data)


def _usage(cache_creation=0, cache_read=0, inp=0, out=0):
    return SimpleNamespace(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=inp,
        output_tokens=out,
    )


def _write_msg(content: str):
    """Generate iter-1: a `write` tool_use for src/App.tsx (the scaffold path)."""
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_block({
            "type": "tool_use", "id": "tu_write", "name": "write",
            "input": {"path": "src/App.tsx", "content": content},
        })],
        usage=_usage(cache_creation=2000, inp=500, out=300),
    )


def _line_replace_msg():
    """Manual-edit iter-1: a `line_replace` tool_use swapping bg-blue-600 →
    bg-red-600 on the known line. This IS the AD23 commit step — the LLM commits
    the already-applied visual change into source; it does not recompute it."""
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_block({
            "type": "tool_use", "id": "tu_lr", "name": "line_replace",
            "input": {
                "path": "src/App.tsx",
                "first_replaced_line": _BLUE_LINE_NO,
                "last_replaced_line": _BLUE_LINE_NO,
                "search": _SEARCH_LINE,
                "replace": _REPLACE_LINE,
            },
        })],
        usage=_usage(cache_read=2000, inp=300, out=120),
    )


def _end_msg(text: str = "Done."):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[_block({"type": "text", "text": text})],
        usage=_usage(cache_read=2000, inp=200, out=100),
    )


def _mock(side_effect: list) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = side_effect
    client.messages.stream.side_effect = lambda **kw: _FakeStream(client.messages.create(**kw))
    return client


async def _fake_vite_build(virtual_fs):  # noqa: ARG001
    """Stub the Vite build (no toolchain in CI). Returns a minimal dist carrying a
    single data-anchor-id so the AD12 reconcile pass has something to extract; the
    raw `_source/` (the load-bearing artefact for AC1) is staged for REAL."""
    return {"index.html": '<!doctype html><div data-anchor-id="aaaa1111"></div>'}


async def _fake_vite_build_with_repair(virtual_fs):
    """P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs).
    A clean build returns the source map unchanged."""
    return await _fake_vite_build(virtual_fs), virtual_fs


# ─── Fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch, tmp_path):
    """isolated_settings + the full P4 prototype/comment/export schema + the design
    agent module stack reloaded in dependency order, with the session dependency
    INJECTED and filesystem storage pointed at an isolated tmp dir.

    NOT reloaded: app.db / app.db.prds / app.design_agent.runner. Reloading app.db*
    desyncs the in-process llm_telemetry registry under full-suite ordering; reloading
    the runner forks the RunResult class so runner-test isinstance checks break (both
    are established hazards in the sibling capstones). prds + prd_patches resolve the
    fake via require_client() at call time, so they need no reload.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    # jsonb columns: checkpoints' prompt_history/comment_state (create_checkpoint
    # passes lists) + prototypes.pending_question (set_pending_question passes a dict).
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS,
        "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setitem(_fake_supabase._JSONB_COLUMNS, "prototypes", {"pending_question"})
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("DESIGN_AGENT_TOKEN_SECRET", "test-secret-only-used-for-binding")
    # AC6: no Supabase Storage bucket → the filesystem fallback is used. Point it at
    # an isolated tmp dir; empty public_url → bundle_url is a file:// URI (test-only).
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.db.prototype_exports as exp_mod
    importlib.reload(exp_mod)
    import app.design_agent.export as ser_mod
    importlib.reload(ser_mod)

    import app.design_agent.storage as storage_mod
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    monkeypatch.setattr(storage_mod.settings, "storage_dir", str(storage_dir), raising=False)
    monkeypatch.setattr(storage_mod.settings, "storage_public_url", "", raising=False)

    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    # Inject the company context (P4-02 mitigation): do NOT rely on the live auth.py
    # require_company path. workspace_id resolves to _TEST_COMPANY_ID. The public
    # /by-token routes carry no auth dependency, so they are unaffected by this override.
    main_mod.app.dependency_overrides[routes_mod.require_company] = lambda: CompanyContext(
        company_id=_TEST_COMPANY_ID, role="owner", user_id=_TEST_USER_ID
    )

    import app.db as db_mod
    yield SimpleNamespace(
        proto=proto_mod, comments=comments_mod, exp=exp_mod, ser=ser_mod,
        storage=storage_mod, routes=routes_mod, main=main_mod, db=db_mod,
    )
    main_mod.app.dependency_overrides.clear()


# ─── helpers ──────────────────────────────────────────────────────────────────


def _seed_prd(db_mod, *, body: str) -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="P4 Capstone", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="P4 Capstone PRD", md=body)
    return prd_id


def _fake_db():
    from tests import _fake_supabase
    return _fake_supabase.get_fake_db()


def _completed_at(prototype_id: int):
    return _fake_db().execute(
        "SELECT completed_at FROM prototypes WHERE id = ?", [prototype_id]
    ).fetchone()[0]


def _checkpoint_count(prototype_id: int) -> int:
    return _fake_db().execute(
        "SELECT COUNT(*) FROM prototype_checkpoints WHERE prototype_id = ?", [prototype_id]
    ).fetchone()[0]


async def _wait_ready(ac, prototype_id, *, budget=120):
    for _ in range(budget):
        r = await ac.get(f"/v1/design-agent/{prototype_id}")
        assert r.status_code == 200, r.text
        row = r.json()
        assert row["status"] != "failed", f"generation failed: {row.get('error')!r}"
        if row["status"] == "ready":
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("prototype never reached 'ready'")


async def _poll(ac, prototype_id, predicate, *, what, budget=120):
    row = None
    for _ in range(budget):
        r = await ac.get(f"/v1/design-agent/{prototype_id}")
        assert r.status_code == 200, r.text
        row = r.json()
        if predicate(row):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {what}; last row = {row!r}")


def _client(env):
    transport = httpx.ASGITransport(app=env.main.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _generate_ready(ac, env, *, prd_body="# P4 Capstone\n\nA counter button.\n"):
    """Drive POST /generate → ready through the (already-monkeypatched) mock client.
    Returns (prototype_id, ready_row, prd_id)."""
    prd_id = _seed_prd(env.db, body=prd_body)
    gen = await ac.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert gen.status_code == 200, gen.text
    assert gen.json()["status"] == "generating"
    pid = gen.json()["prototype_id"]
    row = await _wait_ready(ac, pid)
    assert row["current_checkpoint_id"] is not None
    assert row["bundle_url"], "ready prototype has no bundle_url"
    return pid, row, prd_id


def _install_generate_mock(monkeypatch, env, side_effect):
    # ONE shared client instance across every get_design_agent_client() call — the
    # generate run AND the later manual-edit run draw from the SAME ordered
    # side_effect list (generate consumes the head, manual-edit the tail). A fresh
    # mock per call would replay the list from the top and mis-route the manual edit.
    client = _mock(side_effect)
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client", lambda: client
    )
    monkeypatch.setattr(
        "app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None
    )
    monkeypatch.setattr(env.routes, "vite_build", _fake_vite_build)
    monkeypatch.setattr(env.routes, "vite_build_with_repair", _fake_vite_build_with_repair)
    return client


_MANUAL_EDIT_BODY = {"edits": [
    {"anchor_id": "a1b2c3d4", "property": "background",
     "old_value": "bg-blue-600", "new_value": "bg-red-600"},
]}


# ─── Manual edit: AC1 (checkpoint advance + source reflects change) ───────────


async def test_manual_edit_advances_checkpoint_source_reflects_change(env, monkeypatch):
    # generate (write → end_turn) then manual edit (line_replace → end_turn): 4 calls.
    _install_generate_mock(monkeypatch, env, [
        _write_msg(_GEN_APP_TSX), _end_msg("Built the counter."),
        _line_replace_msg(), _end_msg("Committed the colour change."),
    ])
    async with _client(env) as ac:
        pid, ready, _prd = await _generate_ready(ac, env)
        checkpoint_a = ready["current_checkpoint_id"]
        completed_at_before = _completed_at(pid)
        assert _checkpoint_count(pid) == 1

        # The staged generate source carries the OLD value (round-trips through the
        # real filesystem _source/ stage — vite_build is stubbed, staging is not).
        src_a = await env.storage.read_source_files_for_checkpoint(pid, checkpoint_a)
        assert "bg-blue-600" in src_a["src/App.tsx"]

        me = await ac.post(f"/v1/design-agent/{pid}/manual-edit", json=_MANUAL_EDIT_BODY)
        assert me.status_code == 200, me.text
        assert me.json() == {"prototype_id": pid, "status": "generating", "queue_position": 0}

        advanced = await _poll(
            ac, pid,
            lambda r: r["current_checkpoint_id"] not in (None, checkpoint_a),
            what="manual-edit checkpoint advance (B != A)",
        )
        checkpoint_b = advanced["current_checkpoint_id"]

        # AC1: a NEW checkpoint row; current advanced; completed_at NOT re-stamped
        # (advance_current_checkpoint, not complete_prototype).
        assert checkpoint_b != checkpoint_a
        assert _checkpoint_count(pid) == 2
        assert advanced["status"] == "ready"
        assert _completed_at(pid) == completed_at_before

        # AC1: the new checkpoint's staged _source/ carries the NEW value, not the old
        # — the manual edit reached source (genuine stage → read round-trip).
        src_b = await env.storage.read_source_files_for_checkpoint(pid, checkpoint_b)
        assert "bg-red-600" in src_b["src/App.tsx"]
        assert "bg-blue-600" not in src_b["src/App.tsx"]


# ─── Manual edit: AC2 (stable share URL, F7 — resolver serves the new bundle) ──


async def test_manual_edit_keeps_share_url_stable_resolver_serves_new_bundle(env, monkeypatch):
    _install_generate_mock(monkeypatch, env, [
        _write_msg(_GEN_APP_TSX), _end_msg("Built the counter."),
        _line_replace_msg(), _end_msg("Committed the colour change."),
    ])
    async with _client(env) as ac:
        pid, ready, _prd = await _generate_ready(ac, env)
        checkpoint_a = ready["current_checkpoint_id"]
        bundle_url_a = ready["bundle_url"]

        # Share public BEFORE the edit so /by-token resolves; capture the token.
        share = await ac.post(f"/v1/design-agent/{pid}/share", json={"mode": "public"})
        assert share.status_code == 200, share.text
        token = share.json()["share_token"]
        uuid.UUID(str(token))  # opaque UUID (F6)

        me = await ac.post(f"/v1/design-agent/{pid}/manual-edit", json=_MANUAL_EDIT_BODY)
        assert me.status_code == 200, me.text

        advanced = await _poll(
            ac, pid,
            lambda r: r["current_checkpoint_id"] not in (None, checkpoint_a),
            what="manual-edit checkpoint advance",
        )
        bundle_url_b = advanced["bundle_url"]

        # F7: the manual edit advanced the bundle but did NOT rotate the share config.
        assert advanced["share_token"] == token
        assert advanced["share_mode"] == "public"
        # No-bypass migration: the authed row's bundle_url is a STABLE proxy URL
        # keyed by the prototype id (the proxy serves the latest checkpoint
        # server-side), so it does NOT change across the manual edit — only the
        # bytes it resolves to do.
        assert f"/_da-bundle/v1/design-agent/{pid}/bundle/index.html" in bundle_url_b
        assert bundle_url_b == bundle_url_a  # stable URL, content changed server-side

        # AC2: the public resolver serves a by-token-keyed proxy URL under the SAME
        # token — stable across the edit; the reload shows the change server-side.
        res = await ac.get(f"/v1/design-agent/by-token/{token}")
        assert res.status_code == 200, res.text
        public_url = res.json()["bundle_url"]
        assert "/_da-bundle/v1/design-agent/by-token/" in public_url
        assert str(token) in public_url


# ─── Manual edit: AC5 (stale anchor fails clearly, not silently) ──────────────


async def test_manual_edit_stale_anchor_fails_clearly_not_silent(env, monkeypatch):
    # generate (2 calls) + a manual-edit run that commits NO change (the agent could
    # not resolve the triple → ends its turn without editing): 1 call (end_turn).
    _install_generate_mock(monkeypatch, env, [
        _write_msg(_GEN_APP_TSX), _end_msg("Built the counter."),
        _end_msg("Could not locate the target element; no edit made."),
    ])
    async with _client(env) as ac:
        pid, ready, _prd = await _generate_ready(ac, env)
        checkpoint_a = ready["current_checkpoint_id"]

        stale_body = {"edits": [
            {"anchor_id": "deadbeef", "property": "background",
             "old_value": "bg-blue-600", "new_value": "bg-red-600"},
        ]}
        me = await ac.post(f"/v1/design-agent/{pid}/manual-edit", json=stale_body)
        assert me.status_code == 200, me.text  # the FAILURE surfaces on poll, not the kickoff

        failed = await _poll(
            ac, pid, lambda r: r["status"] == "failed",
            what="manual-edit stale-anchor failure (status=failed)",
        )
        # AC5: a LOUD, specific error naming the unresolved anchor — not a silent no-op.
        err = failed.get("error") or ""
        assert "manual_edit" in err
        assert "not found" in err
        assert "deadbeef" in err
        # AC5: NO checkpoint advance — current_checkpoint_id unchanged, no new row.
        assert failed["current_checkpoint_id"] == checkpoint_a
        assert _checkpoint_count(pid) == 1


# ─── Export feedback: AC3 (resolved comment → export carries Resolved Feedback) ─


async def test_comment_resolved_then_export_carries_resolved_feedback(env, monkeypatch):
    _install_generate_mock(monkeypatch, env, [
        _write_msg(_GEN_APP_TSX), _end_msg("Built the counter."),
    ])
    async with _client(env) as ac:
        pid, _ready, _prd = await _generate_ready(ac, env)

        # Create + resolve a comment with a recognisable body on a known anchor.
        created = await ac.post(
            f"/v1/design-agent/{pid}/comments",
            json={"anchor_id": "aaaa1111", "body": "make the CTA larger"},
        )
        assert created.status_code == 200, created.text
        cid = created.json()["id"]
        assert created.json()["status"] == "open"

        resolved = await ac.patch(f"/v1/design-agent/{pid}/comments/{cid}/resolve")
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"]

        # Mark complete → snapshots the export markdown over the locked checkpoint.
        comp = await ac.post(f"/v1/design-agent/{pid}/complete")
        assert comp.status_code == 200, comp.text
        assert comp.json()["is_complete"] is True

        exp = await ac.get(f"/v1/design-agent/{pid}/export")
        assert exp.status_code == 200, exp.text
        assert exp.headers["content-type"].startswith("text/markdown")
        md = exp.text
        # AC3: the Resolved Feedback section, the anchor sub-header, and the body.
        assert "## Resolved Feedback" in md
        assert "### Anchor `aaaa1111`" in md
        assert "make the CTA larger" in md


# ─── Export feedback: AC4 (open comment excluded from export) ─────────────────


async def test_open_comment_excluded_from_export(env, monkeypatch):
    _install_generate_mock(monkeypatch, env, [
        _write_msg(_GEN_APP_TSX), _end_msg("Built the counter."),
    ])
    async with _client(env) as ac:
        pid, _ready, _prd = await _generate_ready(ac, env)

        # One comment resolved, one left OPEN alongside it.
        resolved = await ac.post(
            f"/v1/design-agent/{pid}/comments",
            json={"anchor_id": "aaaa1111", "body": "RESOLVED_BODY_should_appear"},
        )
        assert resolved.status_code == 200, resolved.text
        rcid = resolved.json()["id"]
        opened = await ac.post(
            f"/v1/design-agent/{pid}/comments",
            json={"anchor_id": "bbbb2222", "body": "OPEN_BODY_should_NOT_appear"},
        )
        assert opened.status_code == 200, opened.text

        patched = await ac.patch(f"/v1/design-agent/{pid}/comments/{rcid}/resolve")
        assert patched.status_code == 200, patched.text

        comp = await ac.post(f"/v1/design-agent/{pid}/complete")
        assert comp.status_code == 200, comp.text

        exp = await ac.get(f"/v1/design-agent/{pid}/export")
        assert exp.status_code == 200, exp.text
        md = exp.text
        # AC4: only RESOLVED feedback is exported — the open comment's body is absent.
        assert "RESOLVED_BODY_should_appear" in md
        assert "OPEN_BODY_should_NOT_appear" not in md
        assert "## Resolved Feedback" in md


# ─── Isolation: AC6 (no network; filesystem storage; Anthropic stubbed) ───────


async def test_p4_e2e_no_network_passes_in_ci(env, monkeypatch):
    import os

    # Single mock client so we can read its call_count — the ONLY Anthropic surface.
    mock_client = _mock([_write_msg(_GEN_APP_TSX), _end_msg("Built the counter.")])
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client", lambda: mock_client
    )
    monkeypatch.setattr(
        "app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None
    )
    monkeypatch.setattr(env.routes, "vite_build", _fake_vite_build)
    monkeypatch.setattr(env.routes, "vite_build_with_repair", _fake_vite_build_with_repair)

    # AC6: no Supabase Storage bucket configured → the filesystem fallback is in use.
    assert not (os.environ.get("SUPABASE_STORAGE_BUCKET") or "").strip()

    async with _client(env) as ac:
        pid, ready, _prd = await _generate_ready(ac, env)

    # AC6: generation made exactly the scripted 2 calls — no real Anthropic traffic,
    # and nothing else reached the (mocked) client; the run never touched the network.
    assert mock_client.messages.create.call_count == 2
    # AC6: no network egress is already proven by call_count == 2 + the unset
    # SUPABASE_STORAGE_BUCKET assertion above (filesystem staging). No-bypass
    # migration: the stored bundle_url is now the app-origin authed proxy URL
    # (decoupled from the staging backend), keyed by the prototype id.
    assert f"/_da-bundle/v1/design-agent/{pid}/bundle/index.html" in ready["bundle_url"]

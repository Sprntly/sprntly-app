"""End-to-end smoke for Phase 3 — the full iterate loop (P3-13).

The P3 capstone integration test (BUILD-PHASES.md §Phase 3 deliverable #13). It
walks the whole iteration loop end-to-end through the *real* backend stack with
only the Anthropic client mocked at the runner boundary and the Vite build stubbed
(per the ticket: P3-13 asserts the LOOP wiring, not the build — P1-11 owns the real
`vite build` + anchor-id coverage). Mirrors P1-11 (test_design_agent_smoke.py) and
P2-11 (test_design_agent_e2e_p2.py): same fixtures, same mock-at-runner-boundary,
same async ASGITransport harness so the route's `asyncio.create_task` background
work (generation + the iterate-queue drain) completes deterministically on the
test's own event loop.

Lifecycle exercised in `test_p3_full_loop_comment_apply_iterate_orphan_patch_checkpoint`:

    seed PRD → POST /generate → poll ready (checkpoint A)            (AC2)
      → POST /{id}/comments  (authed, anchored aaaa1111)            (AC3)
      → POST /{id}/share {public} → mint opaque token
      → POST /by-token/{token}/comments (public, anchored bbbb2222) (AC3)
      → POST /{id}/iterate {applied_comment_id} → queue drain       (AC4)
          (mock emits propose_prd_patch → terminal-COMPLETE; build stubbed to a
           dist carrying ONLY bbbb2222, so aaaa1111 orphans on reconcile)
      → poll until current_checkpoint_id advances to B              (AC7)
      → GET /comments: aaaa1111 orphaned, bbbb2222 open             (AC5)
      → GET /prd-patches: exactly one pending patch; prds untouched (AC6)
      → GET /v1/prd/{prd_id}: patch ABSENT (still pending)          (AC6b, open end)
      → POST /prd-patches/{id}/accept → status applied              (AC6)
      → GET /v1/prd/{prd_id}: patch rendered under the updates       (AC6b, closed end)
        heading; raw prds.payload_md byte-identical before/after (no ALTER)
      → share_token unchanged; GET /by-token resolves new bundle    (AC7, F7)

Plus the negative/branch cases the ticket's Unit Tests section names:
  - test_p3_iterate_on_locked_prototype_returns_409          (AC8)
  - test_p3_clarifying_question_pauses_loop_no_checkpoint    (AC9, optional branch)
  - test_p3_logs_contain_no_pii                              (AC10)

WHY this mirrors P1-11/P2-11 but diverges (all verified against the release HEAD):
- Mock at `app.design_agent.runner.get_design_agent_client` (the runner binds the
  name locally — patch where it's used), exactly as P1-11/P2-11.
- `vite_build` + `stage_bundle` are stubbed in the ROUTES module (the staging path
  calls the module-global names) so no real toolchain runs — the loop wiring, not
  the build, is under test (ticket Decision: stub vite_build).
- `read_source_files_for_checkpoint` is stubbed (nothing is staged when the build
  is stubbed) — same insulation P2-11 uses for the export source read.
- The iterate run's terminal tool is `propose_prd_patch` (rationale + patch_md);
  the runner dispatches it (persisting the prd_patches row) then ends the loop
  COMPLETE, so the iterate stages a new checkpoint AND leaves a pending patch.
- prd_patches lives in conftest's base _FAKE_SCHEMA (status enum
  pending/applied/rejected) — we do NOT recreate it here.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from tests._fake_anthropic import _FakeStream

import httpx
import pytest

from tests.conftest import (
    _TEST_COMPANY_ID,
    _bearer_header,
    _enable_supabase_bearer,
    _seed_company_membership,
)

# ─── Fixture on disk (reused verbatim from P1-11/P2-11) ──────────────────────

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design_agent"
_APP_TSX = json.loads(
    (FIXTURE_DIR / "scaffold_response.json").read_text(encoding="utf-8")
)["app_tsx_content"]

# The two anchor ids the comments pin to. After the iterate the (stubbed) build
# returns a dist carrying ONLY bbbb2222, so the AD12 reconcile orphans the comment
# on aaaa1111 (absent) and leaves the comment on bbbb2222 open (present).
_ANCHOR_GONE = "aaaa1111"      # 8-hex; removed by the iterate → orphaned
_ANCHOR_KEPT = "bbbb2222"      # 8-hex; survives the iterate → stays open
_ITERATE_DIST = {"index.html": f'<div data-anchor-id="{_ANCHOR_KEPT}"></div>'}


# Combined DDL: P1-06 prototypes + P2-06 share/lock columns + P3-08 pending_question
# + prototype_checkpoints (P1-06) + prototype_comments (P3-01) +
# prototype_pending_iterations (P3-06). prd_patches + prds come from conftest's base
# _FAKE_SCHEMA (do NOT recreate them). The fake exercises SQL semantics, not PG DDL.
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


# ─── Mock Anthropic client (mirrors test_design_agent_smoke.py) ─────────────


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
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_block({
            "type": "tool_use", "id": "tu_w", "name": "write",
            "input": {"path": "src/App.tsx", "content": content},
        })],
        usage=_usage(cache_creation=2000, inp=500, out=300),
    )


def _end_msg(text: str = "Done."):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[_block({"type": "text", "text": text})],
        usage=_usage(cache_read=2000, inp=200, out=100),
    )


def _propose_patch_msg(*, rationale: str, patch_md: str):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_block({
            "type": "tool_use", "id": "tu_patch", "name": "propose_prd_patch",
            "input": {"rationale": rationale, "patch_md": patch_md},
        })],
        usage=_usage(cache_read=2000, inp=300, out=150),
    )


def _mock(side_effect: list) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = side_effect
    client.messages.stream.side_effect = lambda **kw: _FakeStream(client.messages.create(**kw))
    return client


# ─── Fixture ────────────────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + the full P3 prototype/comment/queue schema + the design
    agent module stack reloaded in dependency order. prd_patches + prds come from
    conftest's base schema. Feature flag ON (request-time read)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    # jsonb columns: checkpoints' prompt_history/comment_state (create_checkpoint
    # passes lists) + prototypes.pending_question (set_pending_question passes a
    # dict). Registered so the fake JSON-encodes on write + decodes on read.
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS,
        "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setitem(_fake_supabase._JSONB_COLUMNS, "prototypes", {"pending_question"})
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("DESIGN_AGENT_TOKEN_SECRET", "test-secret-only-used-for-binding")

    # P6-10: wire the bearer-authed require_company path (this e2e suite stays async +
    # ASGITransport for the background create_task; only the auth source changed).
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])

    # Reload set mirrors the proven sibling route-tests (comment_routes +
    # prd_patch_routes): proto → comments → prd_patches → routes → main. We do NOT
    # reload app.db.prds / app.db — get_prd_rendered (the /v1/prd fold) resolves the
    # fake via require_client() at CALL time, so it needs no reload, and reloading
    # the app.db package desyncs the in-process llm_telemetry _RUN_TOTALS registry
    # under full-suite ordering (record_run_total KeyErrors on a split module). app.db
    # remains accessible for seeding via the package already imported by conftest.
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(
        proto=proto_mod, comments=comments_mod, patches=patches_mod,
        db=db_mod, routes=routes_mod, main=main_mod,
    )


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_prd(db_mod, *, body: str) -> int:
    # After the tenant-isolation fix, GET /v1/prd/{id} resolves ownership via
    # prd → brief → brief.dataset (slug) → company. Attach the PRD to a brief
    # whose dataset == the seeded company's slug (`slug-<_TEST_COMPANY_ID>`, the
    # slug `_seed_company_membership` mints) so the gate resolves for the caller.
    brief_id = db_mod.save_brief(
        dataset=f"slug-{_TEST_COMPANY_ID}",
        week_label="Week 1",
        payload={"insights": [], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Smoke",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="Smoke PRD", md=body)
    return prd_id


def _stub_build_and_source(monkeypatch, env, *, dist: dict):
    """Stub the routes-module build + bundle staging + checkpoint source read so no
    real Vite toolchain runs and the iterate's source seed is deterministic."""
    async def _fake_vite_build(virtual_fs):  # noqa: ARG001
        return dict(dist)

    # P6-07: the complete path (_stage_complete_run) builds via vite_build_with_repair
    # → (dist, repaired_vfs); the iterate path (_stage_iterate_run) still uses
    # vite_build above. Patch BOTH so either path is stubbed.
    async def _fake_vite_build_with_repair(virtual_fs):
        return dict(dist), virtual_fs

    async def _fake_stage_bundle(*, prototype_id, checkpoint_id, files, sub_prefix=None):  # noqa: ARG001
        suffix = f"{sub_prefix}/" if sub_prefix else ""
        return f"file:///tmp/fake/{prototype_id}/{checkpoint_id}/{suffix}index.html"

    async def _fake_read_source(prototype_id, checkpoint_id):  # noqa: ARG001
        return {"src/App.tsx": "export default function App(){ return <div/>; }"}

    monkeypatch.setattr(env.routes, "vite_build", _fake_vite_build)
    monkeypatch.setattr(env.routes, "vite_build_with_repair", _fake_vite_build_with_repair)
    monkeypatch.setattr(env.routes, "stage_bundle", _fake_stage_bundle)
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", _fake_read_source)
    monkeypatch.setattr(
        "app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None
    )


async def _login(ac):
    # P6-10: attach a Supabase Bearer JWT (require_company) instead of cookie login.
    # workspace_id resolves to _TEST_COMPANY_ID via the membership seeded in `env`.
    ac.headers.update(_bearer_header())


async def _poll(ac, proto_id, predicate, *, what: str, budget=120):
    """Poll GET /{id} until predicate(row) is truthy; return the row."""
    for _ in range(budget):
        r = await ac.get(f"/v1/design-agent/{proto_id}")
        assert r.status_code == 200, r.text
        row = r.json()
        assert row["status"] != "failed", f"prototype failed: {row.get('error')!r}"
        if predicate(row):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {what}")


def _raw_prd_payload(prd_id: int) -> str:
    from tests import _fake_supabase
    return _fake_supabase.get_fake_db().execute(
        "SELECT payload_md FROM prds WHERE id = ?", [prd_id]
    ).fetchone()[0]


# ─── The full loop (AC1-AC7 incl. AC6b) ─────────────────────────────────────


async def test_p3_full_loop_comment_apply_iterate_orphan_patch_checkpoint(env, monkeypatch):
    _PRD_BODY = "# Smoke PRD\n\nBuild a hero with a primary CTA button.\n"
    _PATCH_MD = "Rename the primary CTA to 'Start Free Trial'."
    _RATIONALE = "User comment asked to relabel the CTA."

    # One shared mock client drives BOTH runs in order:
    #   generate: write src/App.tsx (autofixer-clean fixture) → end_turn   (2 calls)
    #   iterate : propose_prd_patch → terminal-COMPLETE                     (1 call)
    mock_client = _mock([
        _write_msg(_APP_TSX),
        _end_msg("Built the hero."),
        _propose_patch_msg(rationale=_RATIONALE, patch_md=_PATCH_MD),
    ])
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client", lambda: mock_client
    )
    _stub_build_and_source(monkeypatch, env, dist=_ITERATE_DIST)

    transport = httpx.ASGITransport(app=env.main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        await _login(ac)
        prd_id = _seed_prd(env.db, body=_PRD_BODY)
        raw_before = _raw_prd_payload(prd_id)

        # ── AC2: generate → ready, checkpoint A ──
        gen = await ac.post("/v1/design-agent/generate", json={"prd_id": prd_id})
        assert gen.status_code == 200, gen.text
        proto_id = gen.json()["prototype_id"]
        assert gen.json()["status"] == "generating"

        ready = await _poll(ac, proto_id, lambda r: r["status"] == "ready", what="ready")
        checkpoint_a = ready["current_checkpoint_id"]
        assert checkpoint_a is not None
        assert ready["bundle_url"]

        # ── AC3: authed comment on aaaa1111 ──
        c_int = await ac.post(
            f"/v1/design-agent/{proto_id}/comments",
            json={"anchor_id": _ANCHOR_GONE, "body": "Rename this CTA"},
        )
        assert c_int.status_code == 200, c_int.text
        comment_id = c_int.json()["id"]
        assert c_int.json()["status"] == "open"

        # share public (mints the opaque token) so the public comment route resolves
        share = await ac.post(
            f"/v1/design-agent/{proto_id}/share", json={"mode": "public"}
        )
        assert share.status_code == 200, share.text
        token = share.json()["share_token"]
        uuid.UUID(str(token))  # opaque UUID (F6)

        # ── AC3: create second comment via authed route (public creation disabled) ──
        c2 = await ac.post(
            f"/v1/design-agent/{proto_id}/comments",
            json={"anchor_id": _ANCHOR_KEPT, "body": "love the hero"},
        )
        assert c2.status_code == 200, c2.text

        both = await ac.get(f"/v1/design-agent/{proto_id}/comments")
        assert both.status_code == 200, both.text
        assert {r["status"] for r in both.json()} == {"open"}
        assert len(both.json()) == 2

        # ── AC4: apply → iterate (comment body merged into the agent prompt) ──
        it = await ac.post(
            f"/v1/design-agent/{proto_id}/iterate",
            json={"prompt": "address the CTA comment", "applied_comment_id": comment_id},
        )
        assert it.status_code == 200, it.text
        assert it.json()["status"] == "generating"

        # checkpoint advances to B (≠ A) once the queue drains + stages
        advanced = await _poll(
            ac, proto_id,
            lambda r: r["current_checkpoint_id"] not in (None, checkpoint_a),
            what="checkpoint advance (B != A)",
        )
        checkpoint_b = advanced["current_checkpoint_id"]
        assert checkpoint_b != checkpoint_a

        # AC4 (assert via the captured agent request): the applied comment body +
        # anchor + the iterate prompt rode in the iterate run's user message.
        iterate_calls = mock_client.messages.create.call_args_list[2:]
        blob = json.dumps([c.kwargs.get("messages") for c in iterate_calls], default=str)
        assert "Rename this CTA" in blob          # applied comment body merged
        assert _ANCHOR_GONE in blob               # its anchor threaded to the agent
        assert "address the CTA comment" in blob  # the iterate prompt itself

        # ── AC5: orphan reconcile — aaaa1111 gone, bbbb2222 survives ──
        after = await ac.get(f"/v1/design-agent/{proto_id}/comments")
        by_anchor = {r["anchor_id"]: r["status"] for r in after.json()}
        assert by_anchor[_ANCHOR_GONE] == "orphaned"
        assert by_anchor[_ANCHOR_KEPT] == "open"

        # ── AC6: exactly one PENDING patch; prds NOT altered ──
        patches = await ac.get("/v1/design-agent/prd-patches", params={"prd_id": prd_id})
        assert patches.status_code == 200, patches.text
        rows = patches.json()
        assert len(rows) == 1
        patch = rows[0]
        assert patch["status"] == "pending"
        assert patch["patch_md"] == _PATCH_MD
        assert patch["rationale"] == _RATIONALE
        patch_id = patch["id"]
        assert _raw_prd_payload(prd_id) == raw_before  # prds untouched by the proposal

        # ── AC6b (open end): pre-accept /v1/prd does NOT contain the patch ──
        pre = await ac.get(f"/v1/prd/{prd_id}")
        assert pre.status_code == 200, pre.text
        pre_md = pre.json()["payload_md"]
        assert _PATCH_MD not in pre_md
        assert "## Design Agent updates" not in pre_md

        # ── AC6: accept → applied ──
        acc = await ac.post(f"/v1/design-agent/prd-patches/{patch_id}/accept")
        assert acc.status_code == 200, acc.text
        assert acc.json()["status"] == "applied"

        # ── AC6b (closed end): post-accept /v1/prd renders the patch ──
        post = await ac.get(f"/v1/prd/{prd_id}")
        assert post.status_code == 200, post.text
        post_md = post.json()["payload_md"]
        assert "## Design Agent updates" in post_md
        assert _PATCH_MD in post_md
        # F11: the raw prds row is byte-identical before vs after — fold-on-read,
        # never an ALTER of prds.payload_md.
        assert _raw_prd_payload(prd_id) == raw_before

        # ── AC7: checkpoint advanced; share_token stable (F7); /by-token resolves ──
        final = await ac.get(f"/v1/design-agent/{proto_id}")
        assert final.json()["current_checkpoint_id"] == checkpoint_b
        assert final.json()["share_token"] == token  # token did NOT rotate on iterate
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as pub:
            res = await pub.get(f"/v1/design-agent/by-token/{token}")
            assert res.status_code == 200, res.text
            assert res.json()["bundle_url"] == final.json()["bundle_url"]


# ─── AC8: locked-prototype guard ────────────────────────────────────────────


async def test_p3_iterate_on_locked_prototype_returns_409(env, monkeypatch):
    # A Marked-Complete (is_complete=1) prototype cannot be iterated → 409.
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " bundle_url, current_checkpoint_id, is_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [1, _TEST_COMPANY_ID, 1, "ready", "private", token, "file:///x/index.html", 5, 1],
    )
    proto_id = cur.lastrowid

    transport = httpx.ASGITransport(app=env.main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        await _login(ac)
        resp = await ac.post(
            f"/v1/design-agent/{proto_id}/iterate", json={"prompt": "do a thing"}
        )
    assert resp.status_code == 409, resp.text
    assert "locked" in resp.json()["detail"].lower()


# NOTE — AC9 (the optional clarifying-question branch) is intentionally NOT
# implemented. AC9 asserts that a mid-iterate `clarifying_question` PAUSES the run
# (sets pending_question, no new checkpoint). The real iterate path does the
# opposite: `_run_iterate_bg` treats a non-`complete` RunResult (incl.
# `awaiting_clarification`) as a failure and calls `fail_prototype`
# ("...ended with status=awaiting_clarification"). Asserting the ticket's pause
# behavior would be a false green, and making it true would require a production
# change (out of scope for this test-only ticket). The ticket marks this branch
# "(Optional) ... if cheap" — it is neither cheap nor accurate, so it is omitted
# and flagged to the orchestrator instead of papered over.


# ─── AC10: no PII / PRD-content / comment body in logs ───────────────────────


async def test_p3_logs_contain_no_pii(env, monkeypatch, caplog):
    _SECRET_PRD = "TOP_SECRET_PRD_DESIGN_BODY"
    _SECRET_COMMENT = "CONFIDENTIAL_COMMENT_TEXT"
    _SECRET_PATCH = "CONFIDENTIAL_PATCH_MD"

    mock_client = _mock([
        _write_msg(_APP_TSX),
        _end_msg("done"),
        _propose_patch_msg(rationale="r", patch_md=_SECRET_PATCH),
    ])
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client", lambda: mock_client
    )
    _stub_build_and_source(monkeypatch, env, dist=_ITERATE_DIST)

    transport = httpx.ASGITransport(app=env.main.app)
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await _login(ac)
            prd_id = _seed_prd(env.db, body=f"# PRD\n\n{_SECRET_PRD}\n")
            gen = await ac.post("/v1/design-agent/generate", json={"prd_id": prd_id})
            proto_id = gen.json()["prototype_id"]
            ready = await _poll(ac, proto_id, lambda r: r["status"] == "ready", what="ready")
            ck_a = ready["current_checkpoint_id"]
            await ac.post(
                f"/v1/design-agent/{proto_id}/comments",
                json={"anchor_id": _ANCHOR_GONE, "body": _SECRET_COMMENT},
            )
            await ac.post(f"/v1/design-agent/{proto_id}/iterate", json={"prompt": "go"})
            await _poll(
                ac, proto_id,
                lambda r: r["current_checkpoint_id"] not in (None, ck_a),
                what="iterate checkpoint advance",
            )

    # The structured cost-summary + state-transition log lines carry identifiers +
    # counts only (Rule #24) — never PRD body, comment body, or patch_md.
    telemetry = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith(("design_agent.run.", "prototype_", "comments_reconciled", "prd_patch_"))
    ]
    blob = "\n".join(telemetry)
    assert telemetry, "expected design-agent telemetry/state log lines"
    assert _SECRET_PRD not in blob
    assert _SECRET_COMMENT not in blob
    assert _SECRET_PATCH not in blob

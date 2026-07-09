"""End-to-end smoke for Phase 2 — Lock / Complete / Export + public URL (P2-11).

Phase 2's required CI smoke (BUILD-PHASES.md §Phase 2 deliverable #10). It walks
the whole post-ready lifecycle through the *real* backend stack with only the
Anthropic client mocked at the runner boundary, proving the closure of:

    F6  — public `/p/<token>` resolver bypasses AuthGate; a random-UUID scan
          returns 404 (not 401) so the share gate is invisible to brute force.
    F14 — Mark Complete locks the prototype (is_complete + complete_checkpoint_id).
    F16 — Export returns the markdown brief with its four load-bearing sections.
    F17 — a WIP (resumed) prototype is NOT exportable (409).

Lifecycle exercised end-to-end on the test's own event loop:

    seed PRD → POST /generate → poll → ready
      → POST /{id}/complete            (F14, writes the prototype_exports snapshot)
      → POST /{id}/share {public}      (F6, mints the opaque share_token)
      → GET  /by-token/<token>  (UNAUTH, F6: 200 + minimum-disclosure 4-key body)
      → GET  /by-token/<rand-uuid> (UNAUTH, F6: 404 not 401)
      → GET  /{id}/export              (F16: text/markdown + 4 H2 sections)
      → POST /{id}/resume              (back to WIP)
      → GET  /{id}/export              (F17: 409)

WHY THIS MIRRORS P1-11 (test_design_agent_smoke.py) BUT DIVERGES IN PLACES — all
verified against the merged release HEAD, not the ticket's pre-merge pseudo-code:

1. Patch site. The runner does `from app.design_agent.client import
   get_design_agent_client` and calls the *local* name (runner.py:44,143), so the
   mock is installed at `app.design_agent.runner.get_design_agent_client`
   ("patch where it's used") — identical to P1-11.

2. Async harness, not sync TestClient. POST /generate fires generation via
   `asyncio.create_task` (routes/design_agent.py:176). A bare TestClient runs each
   request on a fresh per-request portal, orphaning that task. Running the app
   through `httpx.ASGITransport` inside an `async def` keeps the task on the
   test's own loop, where it completes deterministically across `await`s.

3. Build is MOCKED, not run (the documented divergence from P1-11). P1-11 owns
   the real `vite build` + anchor-id coverage (toolchain-guarded). P2-11 cares
   about the POST-READY lifecycle, so `vite_build` + `stage_bundle` are stubbed in
   the routes module — the smoke stays CI-fast, toolchain-independent, and
   insulated from the (separate) generated-code build path.

4. Source read is MONKEYPATCHED. `render_export_markdown` (invoked by the
   /complete export hook) reads the staged `virtual_fs` via
   `storage.read_source_files_for_checkpoint`. With the build stubbed nothing is
   staged, so the helper is patched to return a small deterministic dict — the
   same insulation pattern test_design_agent_export.py uses. AC #6 asserts the
   section *headers*, which are emitted whether or not source files are present.

5. Anthropic CALL COUNT is 2, not 1 (AC #9 intent, corrected against the runner).
   The minimal happy-path generation is a 2-iteration loop: iter-1 returns a
   `write` tool_use, iter-2 returns `end_turn` (agent_loop, runner.py:158-190). A
   single end_turn response can NEVER produce a `ready` prototype because end_turn
   returns BEFORE tool dispatch (runner.py:189), so the `write` is never executed
   and `virtual_fs` stays empty → the row fails. AC #9's real, testable invariant
   is "the LLM is NOT called during /complete /share /export /resume": we snapshot
   the call count right after `ready` (== 2) and assert it is UNCHANGED across the
   entire post-ready lifecycle. That is the property AC #9 protects.

PRD seeding uses the real sync `start_prd` + `complete_prd` helpers (supabase-py
is sync; there is no `insert_prd` / `asyncio.run`).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from tests._fake_anthropic import _FakeStream
from tests.conftest import (
    _bearer_header,
    _enable_supabase_bearer,
    _seed_company_membership,
)

# ─── Fixture on disk (reused verbatim from P1-11 — no new fixture needed) ────

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design_agent"
_APP_TSX = json.loads(
    (FIXTURE_DIR / "scaffold_response.json").read_text(encoding="utf-8")
)["app_tsx_content"]


# Combined DDL: P1-06 prototypes/checkpoints + P2-06 share columns + P2-09
# exports. Matches supabase/migrations/20260530000000_design_agent_sharing.sql +
# 20260530000100_design_agent_exports.sql (the fake exercises SQL semantics, not
# PG-specific DDL — booleans/jsonb are translated by the fake's encode/decode).
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
    share_mode             TEXT NOT NULL DEFAULT 'private',
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
-- P4-07: render_export_markdown now reads resolved comments (F16 Resolved Feedback
-- section), so the export-path fake DB needs this table even when no comment is seeded.
CREATE TABLE prototype_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    anchor_id    TEXT NOT NULL,
    body         TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'demo',
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT,
    user_id        TEXT
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


def _mock_design_agent_client() -> MagicMock:
    """A MagicMock Anthropic client whose `.messages.create` drives a 2-iter run:

    Iter 1 → a `write` tool_use for src/App.tsx (the agent never emits
             data-anchor-id; the Vite plugin applies it at build time — AD4).
    Iter 2 → end_turn with a one-line summary.

    Two calls is the minimal happy path (see module docstring §5): the write must
    be dispatched (tool_use turn) before the loop can end (end_turn turn).
    """
    client = MagicMock()
    client.messages.create.side_effect = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_block({
                "type": "tool_use",
                "id": "tu_1",
                "name": "write",
                "input": {"path": "src/App.tsx", "content": _APP_TSX},
            })],
            usage=_usage(cache_creation=2000, cache_read=0, inp=500, out=300),
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_block({"type": "text", "text": "Built a sign-in screen."})],
            usage=_usage(cache_creation=0, cache_read=2000, inp=200, out=100),
        ),
    ]
    client.messages.stream.side_effect = lambda **kw: _FakeStream(client.messages.create(**kw))
    return client


# ─── Fixture ────────────────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + the full P2 prototype/share/export schema + the design
    agent module stack reloaded in dependency order. Feature flag ON (request-time
    read, so no reload needed when a gate test flips it)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    # prompt_history / comment_state are jsonb in Postgres — register them so the
    # fake JSON-encodes the lists create_checkpoint passes during generation
    # (mirrors test_design_agent_smoke.py + test_design_agent_export.py).
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS,
        "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    # share_token is a plain opaque uuid4 (set_share_config) — this secret is not
    # consumed by the share path, but set it so any future binding stays hermetic.
    monkeypatch.setenv("DESIGN_AGENT_TOKEN_SECRET", "test-secret-only-used-for-binding")

    # P6-10: wire the bearer-authed require_company path (this e2e suite stays async +
    # ASGITransport for the background create_task; only the auth source changed).
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])

    # Reload in dependency order so each module rebinds to the reloaded one below
    # it. require_client() resolves the (monkeypatched) fake at CALL time, so the
    # in-memory Supabase is wired through every helper.
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_exports as exp_mod
    importlib.reload(exp_mod)
    import app.design_agent.export as ser_mod
    importlib.reload(ser_mod)
    import app.design_agent.storage as storage_mod
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(
        proto=proto_mod, exp=exp_mod, ser=ser_mod, storage=storage_mod,
        routes=routes_mod, main=main_mod, db=db_mod,
    )


# ─── The end-to-end test ─────────────────────────────────────────────────────


async def test_p2_full_lifecycle_public_share_and_export(env, monkeypatch):
    """Walk the whole P2 post-ready lifecycle through the real stack.

    Covers AC #1-#10. The build step is mocked (the lifecycle, not the build, is
    under test — P1-11 owns the real build); the Anthropic client is mocked at the
    runner boundary; the staged-source read is mocked for insulation.
    """
    # ── Mock the Anthropic client at the runner boundary (single instance so we
    #    can read its call_count for AC #9). ──
    mock_client = _mock_design_agent_client()
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client", lambda: mock_client
    )
    # Figma token resolution is best-effort; stub it so the run is hermetic (the
    # mocked agent never calls fetch_figma and figma_file_key is None anyway).
    monkeypatch.setattr(
        "app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None
    )

    # ── Mock the build + bundle staging in the routes module so no real vite
    #    build runs. _stage_complete_run calls module-global vite_build /
    #    stage_bundle (and stage_bundle twice — once for dist, once for _source).
    async def _fake_vite_build(virtual_fs):
        return {"index.html": "<html>fake</html>"}

    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs);
    # a clean build returns the source unchanged.
    async def _fake_vite_build_with_repair(virtual_fs):
        return {"index.html": "<html>fake</html>"}, virtual_fs

    async def _fake_stage_bundle(**kwargs):
        return (
            f"file:///tmp/fake-bundle/{kwargs['prototype_id']}/"
            f"{kwargs['checkpoint_id']}/index.html"
        )

    monkeypatch.setattr(env.routes, "vite_build", _fake_vite_build)
    monkeypatch.setattr(env.routes, "vite_build_with_repair", _fake_vite_build_with_repair)
    monkeypatch.setattr(env.routes, "stage_bundle", _fake_stage_bundle)

    # ── Mock the staged-source read so the export serialiser is insulated from
    #    storage (nothing is staged when the build is stubbed). render_export_
    #    markdown does a fresh `from ... import read_source_files_for_checkpoint`
    #    each call, so patching the storage-module attribute takes effect. ──
    async def _fake_read_source(prototype_id, checkpoint_id):  # noqa: ARG001
        return {"src/App.tsx": "export default function App(){ return <div/>; }"}

    monkeypatch.setattr(
        env.storage, "read_source_files_for_checkpoint", _fake_read_source
    )

    transport = httpx.ASGITransport(app=env.main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=_bearer_header()
    ) as ac:
        # 1. Seed a ready PRD with a :::design block (real sync helpers).
        prd_id = env.db.start_prd(
            brief_id=1, insight_index=0, title="Smoke", template_version=1, variant="v2"
        )
        env.db.complete_prd(
            prd_id, title="Smoke PRD",
            md="# Smoke\nbody\n:::design\nkey: value\n:::\n",
        )

        # 2. Authed via Supabase Bearer JWT (require_company → workspace_id _TEST_COMPANY_ID).

        # 3. POST /generate → background task fires the (mocked) agent loop.
        gen = await ac.post("/v1/design-agent/generate", json={"prd_id": prd_id})
        assert gen.status_code == 200, gen.text
        proto_id = gen.json()["prototype_id"]
        assert gen.json()["status"] == "generating"

        # 4-5. Poll until ready (mocked LLM + mocked build complete fast).
        async def _wait_ready():
            for _ in range(80):  # 80 * 50ms = 4s budget
                r = await ac.get(f"/v1/design-agent/{proto_id}")
                assert r.status_code == 200, r.text
                row = r.json()
                if row["status"] == "ready":
                    return row
                assert row["status"] != "failed", f"generation failed: {row.get('error')!r}"
                await asyncio.sleep(0.05)
            raise AssertionError("Prototype never reached 'ready' within 4s")

        ready_row = await _wait_ready()
        assert ready_row["bundle_url"], "bundle_url empty on a ready prototype"
        assert ready_row["current_checkpoint_id"] is not None

        # AC #9 (part 1): generation made exactly the minimal 2 LLM calls. We
        # snapshot here, then assert the count NEVER grows through the rest of the
        # lifecycle — the property AC #9 protects (no LLM call after /generate).
        calls_after_generate = mock_client.messages.create.call_count
        assert calls_after_generate == 2, (
            f"expected the minimal 2-iter generation loop, got "
            f"{calls_after_generate} calls"
        )

        # 6. POST /complete → F14 lock + writes the export snapshot row. (AC #5)
        comp = await ac.post(f"/v1/design-agent/{proto_id}/complete")
        assert comp.status_code == 200, comp.text
        comp_body = comp.json()
        assert comp_body["is_complete"] is True
        assert comp_body["complete_checkpoint_id"] is not None

        # 7. POST /share {public} → F6 mints the opaque share_token. (AC #7)
        share = await ac.post(
            f"/v1/design-agent/{proto_id}/share", json={"mode": "public"}
        )
        assert share.status_code == 200, share.text
        token = share.json()["share_token"]
        assert token, "public share must return a token"
        uuid.UUID(str(token))  # opaque UUID (F6)

        # 8. UNAUTHENTICATED resolver → 200, minimum-disclosure body. (AC #3, #8)
        # company_slug is the cosmetic /p/<slug>/<token> URL segment; the two
        # human-readable segments (company_display_slug / feature_slug) are the
        # cosmetic /p/<company>/<feature>/<token> URL segments — all three are
        # display-only, never validated on read (same trust model), and are the
        # only intentional disclosures beyond the original 4-key minimum.
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as unauth:
            res = await unauth.get(f"/v1/design-agent/by-token/{token}")
            assert res.status_code == 200, res.text
            body = res.json()
            assert set(body.keys()) == {
                "share_mode", "requires_passcode", "bundle_url", "is_complete",
                "company_slug", "company_display_slug", "feature_slug",
                "target_platform",
            }
            assert body["share_mode"] == "public"
            assert body["requires_passcode"] is False
            # No-bypass migration: both bundle_urls are app-origin proxy URLs.
            # The PUBLIC view returns a by-token-keyed proxy URL; the authed row
            # carries a by-{id}-keyed proxy URL — they intentionally differ.
            assert "/_da-bundle/v1/design-agent/by-token/" in body["bundle_url"]
            assert str(token) in body["bundle_url"]
            assert "/_da-bundle/v1/design-agent/" in ready_row["bundle_url"]
            assert "/bundle/" in ready_row["bundle_url"]
            assert body["is_complete"] is True

            # AC #4: brute-force opacity — a random UUID is 404, NOT 401/403.
            res_bad = await unauth.get(f"/v1/design-agent/by-token/{uuid.uuid4()}")
            assert res_bad.status_code == 404, res_bad.text
            assert res_bad.status_code != 401

        # 9. AUTHENTICATED export → F16: 200 text/markdown + 4 H2 sections. (AC #6)
        exp = await ac.get(f"/v1/design-agent/{proto_id}/export")
        assert exp.status_code == 200, exp.text
        assert exp.headers["content-type"].startswith("text/markdown")
        assert "attachment; filename=" in exp.headers["content-disposition"]
        md = exp.text
        for section in (
            "# Design Brief",
            "## PRD Reference",
            "## Design Spec",
            "## Generated Prototype Source",
        ):
            assert section in md, f"export markdown missing {section!r}"

        # 10. POST /resume → back to WIP; the next export is 409 (F17). (AC #7)
        resume = await ac.post(f"/v1/design-agent/{proto_id}/resume")
        assert resume.status_code == 200, resume.text
        assert resume.json()["is_complete"] is False
        exp_wip = await ac.get(f"/v1/design-agent/{proto_id}/export")
        assert exp_wip.status_code == 409, exp_wip.text
        assert exp_wip.json()["detail"] == "Mark prototype complete first"

    # AC #9 (part 2): NO Anthropic call happened during complete/share/resolver/
    # export/resume — the count is unchanged from the post-generate snapshot.
    assert mock_client.messages.create.call_count == calls_after_generate, (
        "the LLM must not be called during the post-ready lifecycle "
        "(/complete, /share, /by-token, /export, /resume)"
    )

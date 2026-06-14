"""Tests for the P3-04 orphan + re-attach logic (AD12).

Two units under test, both in `app.design_agent.runner`:

1. **`extract_anchor_ids`** — a pure, deterministic regex helper over the BUILT
   `dist_files` (the Vite plugin injects `data-anchor-id` at build time per AD4;
   the raw virtual_fs has none). No fixtures, no DB.
2. **`reconcile_comments_on_checkpoint`** — extracts the surviving anchors from a
   new bundle and delegates to P3-01's `mark_comments_orphaned`. Exercised
   against the in-memory FakeSupabaseClient with the `prototype_comments` table,
   reusing the `test_db_prototype_comments.py` DDL + reload pattern.

Plus one integration-style test that the reconcile call inside the GENERATE
staging path (`_stage_complete_run`) is BEST-EFFORT — a forced exception inside
the reconcile does NOT fail the build; the prototype still reaches `status='ready'`
and a `comments_reconcile_failed` warning is logged.

Reload discipline (mirrors the sibling P3-01 / source-staging tests): after the
fake client is wired by `isolated_settings`, we add the `prototype_comments`
table to the in-memory DB and reload `app.db.prototype_comments` + (for reconcile
tests) `app.design_agent.runner` so the runner's module-level
`mark_comments_orphaned` import binds to the reloaded, fake-wired helper.
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest

from app.design_agent.runner import extract_anchor_ids

# SQLite-compatible `prototype_comments` DDL — identical to the P3-01 test's, so
# the fake enforces the same status CHECK semantics Postgres will.
_COMMENTS_DDL = """
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
"""

# Prototype + checkpoint DDL for the integration build-non-failure test (mirrors
# test_design_agent_source_staging.py's `_PROTOTYPE_DDL`).
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


# ════════════════════════════════════════════════════════════════════════════
# extract_anchor_ids — pure (no fixtures, no DB)
# ════════════════════════════════════════════════════════════════════════════


def test_extract_anchor_ids_plain_attributes():
    # AC1 — two distinct plain attributes across one file.
    dist = {"index.js": '...data-anchor-id="aaaa1111"...data-anchor-id="bbbb2222"...'}
    assert extract_anchor_ids(dist) == {"aaaa1111", "bbbb2222"}


def test_extract_anchor_ids_escaped_js_string_form():
    # AC2 — the JS-string-escaped form `data-anchor-id=\"cccc3333\"` is matched.
    # The runtime content is backslash-quote (Vite emits the attribute inside a
    # bundled JS string literal, where the surrounding quotes are escaped).
    dist = {"assets/index.js": '"<div data-anchor-id=\\"cccc3333\\"></div>"'}
    assert extract_anchor_ids(dist) == {"cccc3333"}


def test_extract_anchor_ids_empty_when_no_attributes():
    # AC3 — no anchors anywhere → empty set.
    dist = {"index.html": "<html><body>no anchors here</body></html>"}
    assert extract_anchor_ids(dist) == set()


def test_extract_anchor_ids_dedupes():
    # AC3 — a repeated anchor id collapses to a single set entry, across files.
    dist = {
        "a.js": 'data-anchor-id="abcd1234" data-anchor-id="abcd1234"',
        "b.js": 'data-anchor-id="abcd1234"',
    }
    assert extract_anchor_ids(dist) == {"abcd1234"}


def test_extract_anchor_ids_ignores_non_hex():
    # AC4 — non-8-hex values are not matched (uppercase, non-hex chars, wrong len).
    dist = {
        "index.js": (
            'data-anchor-id="not-hex0" '       # contains non-hex chars
            'data-anchor-id="ABCDEF12" '       # uppercase (plugin emits lowercase)
            'data-anchor-id="abc123" '         # too short (6)
            'data-anchor-id="abc1234567" '     # too long (10)
            'data-anchor-id="dead beef" '      # space breaks the run
            'data-anchor-id="feedface"'        # valid 8-hex — the only match
        )
    }
    assert extract_anchor_ids(dist) == {"feedface"}


def test_extract_anchor_ids_collision_returns_single_membership():
    # AC10 ([[ad4-collision-by-design]]) — the same anchor id on multiple elements
    # (structurally-identical subtrees hash-collide) is returned ONCE. Orphaning is
    # by anchor-id STRING membership, so a comment on a collided id survives iff the
    # id appears anywhere in the bundle — not per-element.
    dist = {
        "index.js": (
            '<li data-anchor-id="cafed00d"></li>'
            '<li data-anchor-id="cafed00d"></li>'
            '<li data-anchor-id="cafed00d"></li>'
        )
    }
    assert extract_anchor_ids(dist) == {"cafed00d"}


# ════════════════════════════════════════════════════════════════════════════
# reconcile_comments_on_checkpoint — against the fake DB
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def reconcile(isolated_settings, monkeypatch):
    """Fake-Supabase + `prototype_comments` table + the comments module reloaded so
    its `require_client` rebinds to the fake.

    We deliberately do NOT reload `app.design_agent.runner`: reloading it mutates
    the runner module dict in place, which makes other suites' top-level
    `from app.design_agent.runner import RunResult` references diverge from the
    class the (in-place-rebound) `_finish` constructs. Reloading is unnecessary
    here — runner's module-level `mark_comments_orphaned` is bound to the
    prototype_comments module dict, which `importlib.reload` mutates in place, so
    runner's original helper already resolves the fake-wired `require_client`."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_COMMENTS_DDL)

    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)  # rebind require_client/utc_now from the reloaded client
    import app.design_agent.runner as runner_mod
    return SimpleNamespace(runner=runner_mod, comments=comments_mod)


def _open(comments_mod, *, prototype_id, workspace_id, anchor_id, body="x"):
    return comments_mod.insert_comment(
        prototype_id=prototype_id, workspace_id=workspace_id, anchor_id=anchor_id, body=body,
    )


def _status_by_id(comments_mod, *, prototype_id, workspace_id):
    return {
        r["id"]: r["status"]
        for r in comments_mod.list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
    }


def test_reconcile_orphans_missing_anchor_comment(reconcile):
    # AC5 — survivor 'aaaa1111' present in the bundle, 'dddd4444' gone → only the
    # latter orphans; reconcile returns 1.
    survivor = _open(reconcile.comments, prototype_id=1, workspace_id="app", anchor_id="aaaa1111")
    doomed = _open(reconcile.comments, prototype_id=1, workspace_id="app", anchor_id="dddd4444")
    dist = {"index.js": '<div data-anchor-id="aaaa1111"></div>'}

    count = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=1, workspace_id="app", dist_files=dist,
    )

    assert count == 1
    statuses = _status_by_id(reconcile.comments, prototype_id=1, workspace_id="app")
    assert statuses[doomed["id"]] == "orphaned"
    assert statuses[survivor["id"]] == "open"


def test_reconcile_keeps_surviving_anchor_open(reconcile):
    # AC5 — a comment whose anchor survives stays open (implicit re-attach); no orphan.
    survivor = _open(reconcile.comments, prototype_id=2, workspace_id="app", anchor_id="beefcafe")
    dist = {"a.js": 'data-anchor-id="beefcafe"', "b.js": "no anchors"}

    count = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=2, workspace_id="app", dist_files=dist,
    )

    assert count == 0
    statuses = _status_by_id(reconcile.comments, prototype_id=2, workspace_id="app")
    assert statuses[survivor["id"]] == "open"


def test_reconcile_leaves_resolved_untouched(reconcile):
    # AC6 — an already-resolved comment whose anchor vanished is NOT re-flipped to
    # orphaned (delegated to mark_comments_orphaned's OPEN-only filter).
    resolved = _open(reconcile.comments, prototype_id=3, workspace_id="app", anchor_id="11112222")
    reconcile.comments.resolve_comment(comment_id=resolved["id"], workspace_id="app")
    dist = {"index.js": "completely different bundle, no anchors"}

    count = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=3, workspace_id="app", dist_files=dist,
    )

    assert count == 0
    statuses = _status_by_id(reconcile.comments, prototype_id=3, workspace_id="app")
    assert statuses[resolved["id"]] == "resolved"


def test_reconcile_workspace_filtered(reconcile):
    # AC7 — a comment under workspace 'app' is reconciled for 'app' but untouched
    # when the call is for 'demo', even though the surviving set would orphan it.
    app_row = _open(reconcile.comments, prototype_id=4, workspace_id="app", anchor_id="abcd0001")
    dist_no_anchor = {"index.js": "no anchors at all"}

    # Call for the WRONG workspace: must not touch the 'app' comment.
    count_demo = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=4, workspace_id="demo", dist_files=dist_no_anchor,
    )
    assert count_demo == 0
    statuses = _status_by_id(reconcile.comments, prototype_id=4, workspace_id="app")
    assert statuses[app_row["id"]] == "open"

    # Call for the RIGHT workspace: now it orphans.
    count_app = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=4, workspace_id="app", dist_files=dist_no_anchor,
    )
    assert count_app == 1
    statuses = _status_by_id(reconcile.comments, prototype_id=4, workspace_id="app")
    assert statuses[app_row["id"]] == "orphaned"


def test_reconcile_returns_orphaned_count(reconcile):
    # AC5/AC8-count — three open comments, one anchor survives → returns 2.
    _open(reconcile.comments, prototype_id=5, workspace_id="app", anchor_id="aaaa0001")
    _open(reconcile.comments, prototype_id=5, workspace_id="app", anchor_id="aaaa0002")
    _open(reconcile.comments, prototype_id=5, workspace_id="app", anchor_id="aaaa0003")
    dist = {"index.js": '<x data-anchor-id="aaaa0001"/>'}

    count = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=5, workspace_id="app", dist_files=dist,
    )
    assert count == 2


def test_reconcile_collision_survives_while_any_element_remains(reconcile):
    # AC10 — a comment on a collided anchor id is NOT orphaned while ANY element
    # bearing that id survives in the new bundle (string membership, not per-element).
    collided = _open(reconcile.comments, prototype_id=6, workspace_id="app", anchor_id="cafed00d")
    dist = {  # two elements still share the collided id after the rebuild
        "index.js": '<li data-anchor-id="cafed00d"></li><li data-anchor-id="cafed00d"></li>',
    }

    count = reconcile.runner.reconcile_comments_on_checkpoint(
        prototype_id=6, workspace_id="app", dist_files=dist,
    )

    assert count == 0
    statuses = _status_by_id(reconcile.comments, prototype_id=6, workspace_id="app")
    assert statuses[collided["id"]] == "open"


def test_reconcile_logs_counts_only_no_anchor_or_body(reconcile, caplog):
    # AC9 (Rule #24) — a successful reconcile logs `comments_reconciled` with
    # counts only; NO anchor values, NO comment body in the log line.
    secret_anchor = "deadbeef"
    secret_body = "SECRET_COMMENT_BODY_xyz"
    _open(
        reconcile.comments, prototype_id=7, workspace_id="app",
        anchor_id=secret_anchor, body=secret_body,
    )
    # One survivor anchor + the secret anchor gone → orphan 1.
    dist = {"index.js": 'data-anchor-id="11112222"'}

    with caplog.at_level(logging.INFO, logger="app.design_agent.runner"):
        reconcile.runner.reconcile_comments_on_checkpoint(
            prototype_id=7, workspace_id="app", dist_files=dist,
        )

    recs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("comments_reconciled")]
    assert len(recs) == 1
    msg = recs[0]
    assert "prototype_id=7" in msg
    assert "surviving_anchors=1" in msg
    assert "orphaned=1" in msg
    assert secret_anchor not in msg     # no anchor value
    assert "11112222" not in msg        # no surviving-anchor value either
    assert secret_body not in msg       # no comment body (PII)


# ════════════════════════════════════════════════════════════════════════════
# Build-non-failure — reconcile inside _stage_complete_run is best-effort (AC8)
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def stage_env(isolated_settings, monkeypatch):
    """Fake-Supabase with prototypes + checkpoints + comments tables, and the
    runner / proto / route modules reloaded in dependency order so the route's
    `reconcile_comments_on_checkpoint` resolves the runner's (patchable)
    `mark_comments_orphaned`."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.executescript(_PROTOTYPE_DDL)
    db.executescript(_COMMENTS_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    # NOTE: runner is intentionally NOT reloaded (see the `reconcile` fixture for
    # why — reloading it pollutes other suites' RunResult identity). routes is
    # reloaded so its `vite_build`/`stage_bundle`/`create_checkpoint` resolve the
    # fake-wired modules; routes' `reconcile_comments_on_checkpoint` is the
    # (non-reloaded) runner function, whose module-global `mark_comments_orphaned`
    # the AC8 test patches to force the best-effort except path.
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.design_agent.runner as runner_mod
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    return SimpleNamespace(proto=proto_mod, runner=runner_mod, routes=routes_mod)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


async def test_reconcile_exception_does_not_fail_stage(stage_env, monkeypatch, caplog):
    # AC8 — a forced exception inside reconcile (mark_comments_orphaned raises) does
    # NOT propagate out of _stage_complete_run: the prototype still completes
    # 'ready' and a `comments_reconcile_failed` warning is logged.
    pid = stage_env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)

    monkeypatch.setattr(stage_env.routes, "vite_build", _async_return({"index.html": "<x/>"}))
    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs).
    monkeypatch.setattr(
        stage_env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<x/>"}, {"src/App.tsx": "x"})),
    )
    monkeypatch.setattr(stage_env.routes, "stage_bundle", _async_return("https://x.example/i.html"))

    def _boom(**kwargs):
        raise RuntimeError("SECRET_RECONCILE_blob")

    monkeypatch.setattr(stage_env.runner, "mark_comments_orphaned", _boom)

    with caplog.at_level(logging.WARNING):
        await stage_env.routes._stage_complete_run(
            prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
        )

    row = stage_env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"  # build NOT failed by the reconcile error

    warns = [r.getMessage() for r in caplog.records if r.getMessage().startswith("comments_reconcile_failed")]
    assert len(warns) == 1
    msg = warns[0]
    assert f"prototype_id={pid}" in msg
    assert "error_class=RuntimeError" in msg
    assert "SECRET_RECONCILE_blob" not in msg  # error text never in the log line


async def test_reconcile_runs_on_successful_stage(stage_env, monkeypatch):
    # Companion to AC8: on the happy path the reconcile actually runs and orphans a
    # comment whose anchor is absent from the built bundle, and the prototype is ready.
    import app.db.prototype_comments as comments_mod

    pid = stage_env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    gone = comments_mod.insert_comment(
        prototype_id=pid, workspace_id="app", anchor_id="dddd4444", body="will orphan",
    )

    # Built bundle has NO anchors → the open comment must orphan.
    monkeypatch.setattr(stage_env.routes, "vite_build", _async_return({"index.html": "<x/>"}))
    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs).
    monkeypatch.setattr(
        stage_env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<x/>"}, {"src/App.tsx": "x"})),
    )
    monkeypatch.setattr(stage_env.routes, "stage_bundle", _async_return("https://x.example/i.html"))

    await stage_env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
    )

    row = stage_env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    statuses = {
        r["id"]: r["status"]
        for r in comments_mod.list_comments(prototype_id=pid, workspace_id="app")
    }
    assert statuses[gone["id"]] == "orphaned"

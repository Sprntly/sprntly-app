"""Tests for the startup template-bump demote GATE + the one-time RESTORE.

A routine prototype template-version bump previously flipped every older 'ready'
prototype to 'invalidated' on startup (across all workspaces), which 404s the
View path and drops the PRD screen to the "Generate" CTA — even though the bundle
is a self-contained static build that still renders. Two changes are covered:

  1. GATE — the startup `invalidate_stale_prototypes(...)` call in the main.py
     lifespan is now gated behind `design_agent_invalidate_prototypes_on_template_bump`
     (default False). With the gate off, a template bump no longer demotes
     existing ready prototypes; it logs one INFO line that the demote was skipped.
  2. RESTORE — `restore_template_demoted_prototypes()` (+ a data-only idempotent
     migration with the IDENTICAL predicate) un-hides the already-demoted rows:
     'invalidated' → 'ready' for every row that still has a `bundle_url`.

Runs fully against the in-memory FakeSupabaseClient — no live Supabase and no
real DB mutation. Mirrors test_db_prototypes.py's isolated_settings / proto
fixture / module-reload style; the startup tests fire the REAL main.py lifespan
via `TestClient(main.app)` (the lifespan-guard pattern) so the gate-off
regression genuinely fails on pre-fix code.
"""
from __future__ import annotations

import importlib
import logging
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# SQLite-compatible prototypes DDL (same translation test_db_prototypes.py uses):
# the fake exercises SQL semantics, not Postgres-specific DDL.
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
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations"
    / "20260629150000_restore_template_demoted_prototypes.sql"
)


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def proto(isolated_settings):
    """The reloaded app.db.prototypes module wired to the fake Supabase, with the
    prototypes table present in the in-memory DB. Mirrors test_db_prototypes.proto.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)  # rebind require_client/utc_now from the reloaded client
    return proto_mod


@pytest.fixture
def startup_proto(fake_llm):
    """Prototypes table seeded into the app's fake DB + the reloaded proto module,
    so the REAL main.py lifespan demote/skip branch runs against a live table
    (the orphan-iteration / job tables stay unseeded and hit the existing
    missing-table guard AFTER the prototype block has already run).

    Depends on `fake_llm` (→ isolated_settings) so `TestClient(main.app)` startup
    has the LLM seam patched. The reloaded proto module and main.py's lifespan
    helpers share the same in-memory fake DB singleton, so a row inserted here is
    visible to the lifespan's demote.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    return proto_mod


# ─── Helpers ──────────────────────────────────────────────────────────────


def _seed_proto(
    *,
    status: str,
    bundle_url: str | None,
    workspace_id: str = "app",
    template_version: int = 1,
    prd_id: int = 1,
) -> int:
    """Insert one prototypes row with full control over status + bundle_url
    (so a NULL-bundle 'invalidated' row is expressible — no helper sets that).
    Returns the new row id."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    cur = db.execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, status, variant, template_version, bundle_url, share_token) "
        "VALUES (?, ?, ?, 'v1', ?, ?, ?)",
        [prd_id, workspace_id, status, template_version, bundle_url, uuid.uuid4().hex],
    )
    db.commit()
    return cur.lastrowid


def _migration_update_sql() -> str:
    """The migration's executable SQL with `--` line comments stripped (case
    preserved). The file is one UPDATE statement under a comment header."""
    lines = []
    for line in _MIGRATION_PATH.read_text().splitlines():
        code = line.split("--", 1)[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


def _status(proto, pid: int, workspace_id: str = "app") -> str | None:
    row = proto.get_prototype(prototype_id=pid, workspace_id=workspace_id)
    return row["status"] if row else None


# ─── GATE — startup demote behaviour (fires the REAL lifespan) ─────────────


def test_template_bump_with_gate_off_keeps_prototype_ready(startup_proto, monkeypatch):
    """REGRESSION: with the gate at its default (False), a startup whose template
    version exceeds an existing ready prototype's stamped version leaves that
    prototype 'ready' (NOT demoted to 'invalidated').

    FAILS on pre-fix code (which calls invalidate_stale_prototypes unconditionally
    in the lifespan), PASSES after the gate lands. Exercises the real main.py
    branch via TestClient(main.app) startup.
    """
    from app.config import settings
    from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION
    import app.main as main_mod

    # Gate is off by default — assert that, then prove startup honours it.
    assert settings.design_agent_invalidate_prototypes_on_template_bump is False

    pid = startup_proto.start_prototype(
        prd_id=1, workspace_id="app",
        template_version=DESIGN_AGENT_TEMPLATE_VERSION - 1,
    )
    startup_proto.complete_prototype(
        prototype_id=pid, workspace_id="app", bundle_url="https://x.zip")
    assert _status(startup_proto, pid) == "ready"

    with TestClient(main_mod.app):  # fire the lifespan startup
        pass

    assert _status(startup_proto, pid) == "ready"  # preserved across the bump


def test_gate_on_still_demotes(startup_proto, monkeypatch):
    """With the gate explicitly True, the prior behaviour holds: a stale ready row
    IS flipped to 'invalidated' by the startup demote (the gated function is
    unchanged and still reachable from the lifespan)."""
    import app.config as config_mod
    from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION
    import app.main as main_mod

    monkeypatch.setattr(
        config_mod.settings,
        "design_agent_invalidate_prototypes_on_template_bump",
        True,
    )

    pid = startup_proto.start_prototype(
        prd_id=1, workspace_id="app",
        template_version=DESIGN_AGENT_TEMPLATE_VERSION - 1,
    )
    startup_proto.complete_prototype(
        prototype_id=pid, workspace_id="app", bundle_url="https://x.zip")

    with TestClient(main_mod.app):
        pass

    assert _status(startup_proto, pid) == "invalidated"


def test_gate_off_emits_skipped_log(startup_proto, caplog):
    """With the gate off, startup logs exactly the skip INFO line (identifiers
    only, no PII / no bundle content) and performs NO demote."""
    from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION
    import app.main as main_mod

    pid = startup_proto.start_prototype(
        prd_id=1, workspace_id="app",
        template_version=DESIGN_AGENT_TEMPLATE_VERSION - 1,
    )
    startup_proto.complete_prototype(
        prototype_id=pid, workspace_id="app", bundle_url="https://x.zip")

    with caplog.at_level(logging.INFO, logger="app.main"):
        with TestClient(main_mod.app):
            pass

    skip_lines = [
        r for r in caplog.records
        if "prototype template-demote skipped (gate off)" in r.getMessage()
    ]
    assert len(skip_lines) == 1
    assert _status(startup_proto, pid) == "ready"  # no demote occurred


# ─── RESTORE — one-time un-hide of template-demoted rows ───────────────────


def test_restore_flips_invalidated_with_bundle_to_ready(proto):
    """An 'invalidated' row WITH a bundle_url becomes 'ready'; an 'invalidated'
    row WITHOUT a bundle_url is left untouched."""
    bundled = _seed_proto(status="invalidated", bundle_url="https://b.zip")
    no_bundle = _seed_proto(status="invalidated", bundle_url=None)

    count = proto.restore_template_demoted_prototypes()

    assert count == 1
    assert _status(proto, bundled) == "ready"
    assert _status(proto, no_bundle) == "invalidated"


def test_restore_is_idempotent(proto):
    """A second invocation immediately after the first returns 0 and changes
    nothing."""
    bundled = _seed_proto(status="invalidated", bundle_url="https://b.zip")

    assert proto.restore_template_demoted_prototypes() == 1
    assert _status(proto, bundled) == "ready"

    assert proto.restore_template_demoted_prototypes() == 0
    assert _status(proto, bundled) == "ready"


def test_restore_preserves_workspace_id(proto):
    """Invalidated rows in two distinct workspaces are each restored to 'ready'
    with workspace_id unchanged; cross-workspace isolation intact."""
    a = _seed_proto(status="invalidated", bundle_url="https://a.zip", workspace_id="app")
    d = _seed_proto(status="invalidated", bundle_url="https://d.zip", workspace_id="demo")

    count = proto.restore_template_demoted_prototypes()

    assert count == 2
    # Each row is ready ONLY under its own workspace (workspace_id unchanged).
    assert _status(proto, a, "app") == "ready"
    assert proto.get_prototype(prototype_id=a, workspace_id="demo") is None
    assert _status(proto, d, "demo") == "ready"
    assert proto.get_prototype(prototype_id=d, workspace_id="app") is None


def test_restored_prototype_is_found_by_get_by_prd(proto):
    """After the restore, find_ready_prototype_by_prd returns the row for its own
    workspace and None under a different workspace — i.e. click-PRD→view is
    restored without any PRD-layer change."""
    pid = _seed_proto(
        status="invalidated", bundle_url="https://p.zip",
        workspace_id="app", prd_id=42,
    )
    # Before: the View lookup (ready-only) cannot see the demoted row.
    assert proto.find_ready_prototype_by_prd(prd_id=42, workspace_id="app") is None

    proto.restore_template_demoted_prototypes()

    found = proto.find_ready_prototype_by_prd(prd_id=42, workspace_id="app")
    assert found is not None
    assert found["id"] == pid
    assert proto.find_ready_prototype_by_prd(prd_id=42, workspace_id="other") is None


def test_restore_no_invalidated_rows_returns_zero(proto):
    """No matching rows → returns 0, no error, the ready row is untouched."""
    ready = _seed_proto(status="ready", bundle_url="https://r.zip")

    assert proto.restore_template_demoted_prototypes() == 0
    assert _status(proto, ready) == "ready"


def test_restore_ignores_failed_and_generating(proto):
    """'failed' / 'generating' rows are never touched — only 'invalidated'."""
    failed = _seed_proto(status="failed", bundle_url="https://f.zip")
    generating = _seed_proto(status="generating", bundle_url="https://g.zip")

    assert proto.restore_template_demoted_prototypes() == 0
    assert _status(proto, failed) == "failed"
    assert _status(proto, generating) == "generating"


# ─── Migration idempotency (data-only UPDATE, identical predicate) ─────────


def test_restore_migration_applies_twice(proto):
    """Applying the data migration's UPDATE twice produces no error and the second
    apply changes no rows (idempotent). Uses the SAME predicate the Python helper
    uses, executed against the fake DB."""
    from tests import _fake_supabase

    bundled = _seed_proto(status="invalidated", bundle_url="https://b.zip")
    _seed_proto(status="invalidated", bundle_url=None)  # excluded by the predicate
    ready = _seed_proto(status="ready", bundle_url="https://r.zip")

    db = _fake_supabase.get_fake_db()
    update_sql = _migration_update_sql()

    first = db.execute(update_sql)
    db.commit()
    assert first.rowcount == 1  # only the bundled invalidated row

    second = db.execute(update_sql)
    db.commit()
    assert second.rowcount == 0  # no further changes on re-apply

    assert _status(proto, bundled) == "ready"
    assert _status(proto, ready) == "ready"

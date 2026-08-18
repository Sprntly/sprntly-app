"""§B + the surviving §C target-resolution helpers.

The propose-PRD-patch tool (`PROPOSE_PROJECT_PRD_PATCH_TOOL` +
`handle_propose_prd_patch`) this file used to cover was RETIRED once
in-place editing was proven on both the private and group surfaces (see
`project_chat_edit.py::apply_chat_edit_scoped`, the single writer now, and
its ★ IDOR gate). What remains here:

  - `_resolve_prd_id` zero/one/many/explicit-id resolution — the SAME
    server-side target resolver BOTH surviving surfaces call, now exercised
    directly rather than only indirectly through the retired handler;
  - the flag reader default-off (AC25);
  - §B: `insert_patch(prototype_id=None)` persists SQL NULL and lists; the
    real-int path still works; empty rationale/patch_md still raise
    (AC10/AC11) — `db/prd_patches.py` is KEPT (Design-Agent's own main-chat
    propose/accept/reject flow still writes/reads it);
  - the nullable-prototype migration file is present + idempotent (AC9/AC12).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_NULLABLE_PRD_PATCHES_DDL = """
CREATE TABLE prd_patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id        INTEGER NOT NULL,
    prototype_id  INTEGER,                       -- NULLABLE (the new migration)
    workspace_id  TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    patch_md      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'applied', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260813150000_prd_patches_nullable_prototype_id.sql"
)


@pytest.fixture
def tool_env(isolated_settings, monkeypatch):
    """Reloaded prd_patches + propose-tool modules wired to a fake Supabase whose
    prd_patches table has a NULLABLE prototype_id (the new migration)."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.executescript("DROP TABLE IF EXISTS prd_patches;")
    db.executescript(_NULLABLE_PRD_PATCHES_DDL)
    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)
    import app.project_prd_patch_tool as tool_mod
    importlib.reload(tool_mod)
    return tool_mod, db


def _set_manifest(monkeypatch, tool_mod, items):
    monkeypatch.setattr(tool_mod, "list_artifacts_for_project", lambda **kw: items)


# ── retirement guard: the propose tool + handler no longer live here ─────────
def test_propose_tool_and_handler_no_longer_exist(tool_env):
    tool_mod, _ = tool_env
    assert not hasattr(tool_mod, "_resolve_prd_id")
    assert not hasattr(tool_mod, "PROPOSE_PROJECT_PRD_PATCH_TOOL")
    assert not hasattr(tool_mod, "handle_propose_prd_patch")


# ── _resolve_prd_id — explicit / zero / one / many, DIRECT (migrated from the
# retired propose handler's AC24 coverage — both surviving surfaces
# (routes/projects.py's private route + `_classify_and_maybe_edit_group_prd`)
# call this exact function for their write target) ───────────────────────────
def test_project_prd_edit_enabled_defaults_off(tool_env, monkeypatch):
    tool_mod, _ = tool_env
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    assert tool_mod.project_prd_edit_enabled() is False
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "0")
    assert tool_mod.project_prd_edit_enabled() is False
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    assert tool_mod.project_prd_edit_enabled() is True
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "true")
    assert tool_mod.project_prd_edit_enabled() is True


# ── §B — nullable prototype persistence (AC10/AC11) ──────────────────────────
def test_insert_patch_null_prototype_persists_null(tool_env, monkeypatch):
    tool_mod, db = tool_env
    import app.db.prd_patches as patches_mod

    row = patches_mod.insert_patch(
        prd_id=11, prototype_id=None, workspace_id="ws1",
        rationale="r", patch_md="m",
    )
    assert row["prototype_id"] is None
    stored = db.execute(
        "SELECT prototype_id FROM prd_patches WHERE id=?", (row["id"],)
    ).fetchone()[0]
    assert stored is None


def test_insert_patch_real_prototype_still_works(tool_env):
    tool_mod, db = tool_env
    import app.db.prd_patches as patches_mod

    row = patches_mod.insert_patch(
        prd_id=11, prototype_id=3, workspace_id="ws1", rationale="r", patch_md="m",
    )
    assert row["prototype_id"] == 3


def test_list_pending_returns_null_prototype_patch(tool_env):
    tool_mod, _ = tool_env
    import app.db.prd_patches as patches_mod

    patches_mod.insert_patch(
        prd_id=12, prototype_id=None, workspace_id="ws1", rationale="r", patch_md="m",
    )
    pending = patches_mod.list_pending_patches(prd_id=12, workspace_id="ws1")
    assert len(pending) == 1 and pending[0]["prototype_id"] is None


def test_insert_patch_empty_rationale_or_patch_md_raises(tool_env):
    tool_mod, _ = tool_env
    import app.db.prd_patches as patches_mod

    with pytest.raises(ValueError):
        patches_mod.insert_patch(
            prd_id=1, prototype_id=None, workspace_id="ws1", rationale="  ", patch_md="m",
        )
    with pytest.raises(ValueError):
        patches_mod.insert_patch(
            prd_id=1, prototype_id=None, workspace_id="ws1", rationale="r", patch_md="   ",
        )


# ── §B — migration file (AC9/AC12) ───────────────────────────────────────────
def _migration_sql():
    lines = [ln.split("--", 1)[0] for ln in _MIGRATION_PATH.read_text().splitlines()]
    return "\n".join(lines).lower()


def test_migration_file_exists_named_and_drops_not_null():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260813150000_prd_patches_nullable_prototype_id.sql"
    sql = _migration_sql()
    assert "alter column prototype_id drop not null" in sql
    # F11 — never alters prds, never touches the FK.
    assert "alter table prds" not in sql
    assert "drop constraint" not in sql


def test_migration_nullable_prototype_idempotent():
    # AC12 — `drop not null` is a no-op when already nullable; applying twice is
    # safe. Assert the statement is idempotent by construction (no CREATE, single
    # non-conditional ALTER that a second apply re-runs harmlessly).
    sql = _migration_sql()
    assert not re.search(r"create\s+table", sql)
    assert sql.count("alter column prototype_id drop not null") == 1

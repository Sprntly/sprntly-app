"""§C + §D + §B — the project propose-PRD-patch tool, its IDOR gate proved at
the WRITE, and the nullable-prototype helper widening.

PER-PATH MUTATION PROOFS (the IDOR must be provably closed at the write): every
refusal path asserts a real `select count(*) from prd_patches` is UNCHANGED
before/after the handler runs, against the in-memory SQLite the fake Supabase
client backs (the same SQL-semantics harness the sibling
`test_design_agent_prd_patches.py` uses). The genuine Postgres five-table
fan-out is exercised by the env-gated `test_project_individual_prd_edit_live.py`.

  - cross-project prd_id → ZERO rows + "only edit a PRD attached to this
    project" refusal (AC20);
  - cross-tenant prd_id → ZERO rows (AC21);
  - manifest read error → ZERO rows + "couldn't verify" (fail-closed) (AC22);
  - own-project prd_id → exactly one status='pending' row, prototype_id IS NULL,
    workspace_id == company_id (AC23);
  - prd_id resolution zero/one/many (AC24);
  - the tool is NOT in the Design-Agent ACTION_TOOLS/SENTINEL_TOOLS (AC19);
  - §B: insert_patch(prototype_id=None) persists SQL NULL and lists;
    the real-int path still works; empty rationale/patch_md still raise (AC10/AC11);
  - the migration file is nullable + idempotent (AC9/AC12).
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
    / "supabase" / "migrations" / "20260812130000_prd_patches_nullable_prototype_id.sql"
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


def _count(db, prd_id):
    return db.execute(
        "SELECT COUNT(*) FROM prd_patches WHERE prd_id = ?", (prd_id,)
    ).fetchone()[0]


def _set_manifest(monkeypatch, tool_mod, items):
    """Both the tool (resolution) and the gate (assert) read the manifest."""
    import app.project_prd_gate as gate

    monkeypatch.setattr(tool_mod, "list_artifacts_for_project", lambda **kw: items)
    monkeypatch.setattr(gate, "list_artifacts_for_project", lambda **kw: items)


# ── AC19 — registry boundary (AD17) ──────────────────────────────────────────
def test_propose_tool_not_in_design_agent_registry(tool_env):
    tool_mod, _ = tool_env
    from app.design_agent.tools import ACTION_TOOLS, SENTINEL_TOOLS

    assert tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL["name"] == "propose_prd_patch"
    assert tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL not in ACTION_TOOLS
    assert tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL not in SENTINEL_TOOLS
    # It's a plain dict tool, not a design-agent ToolDef.
    assert isinstance(tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL, dict)


def test_tool_description_has_negative_space(tool_env):
    tool_mod, _ = tool_env
    desc = tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL["description"]
    # ≥3-4 sentences and explicit "do NOT" guidance.
    assert desc.count(".") >= 4
    assert "Do NOT" in desc or "not call" in desc.lower()


# ── AC20 — cross-project write → ZERO rows ───────────────────────────────────
def test_propose_cross_project_writes_zero_rows(tool_env, monkeypatch):
    tool_mod, db = tool_env
    # PRD 5 is valid in the company but on ANOTHER project → not on THIS manifest.
    _set_manifest(monkeypatch, tool_mod, [{"type": "prd", "id": 9, "title": "Mine"}])
    before = _count(db, 5)
    out = tool_mod.handle_propose_prd_patch(
        {"prd_id": 5, "rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert _count(db, 5) == before == 0
    assert "only edit a PRD that's attached to this project" in out


# ── AC21 — cross-tenant write → ZERO rows ────────────────────────────────────
def test_propose_cross_tenant_writes_zero_rows(tool_env, monkeypatch):
    tool_mod, db = tool_env
    # A foreign company's PRD never appears in c1's fan-out.
    _set_manifest(monkeypatch, tool_mod, [{"type": "prd", "id": 9, "title": "Mine"}])
    before = _count(db, 777)
    out = tool_mod.handle_propose_prd_patch(
        {"prd_id": 777, "rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert _count(db, 777) == before == 0
    assert "only edit a PRD that's attached to this project" in out


# ── AC22 — gate-error is fail-closed → ZERO rows ─────────────────────────────
def test_propose_gate_error_writes_zero_rows(tool_env, monkeypatch):
    tool_mod, db = tool_env
    import app.project_prd_gate as gate

    # Resolution succeeds (explicit id) but the gate's manifest read raises.
    monkeypatch.setattr(tool_mod, "list_artifacts_for_project", lambda **kw: [{"type": "prd", "id": 5}])

    def _boom(**kw):  # noqa: ARG001
        raise RuntimeError("manifest down")

    monkeypatch.setattr(gate, "list_artifacts_for_project", _boom)
    before = _count(db, 5)
    out = tool_mod.handle_propose_prd_patch(
        {"prd_id": 5, "rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert _count(db, 5) == before == 0
    assert "couldn't verify" in out


# ── AC23 — own-project write persists a pending patch ────────────────────────
def test_propose_own_project_persists_pending_patch(tool_env, monkeypatch):
    tool_mod, db = tool_env
    _set_manifest(monkeypatch, tool_mod, [{"type": "prd", "id": 5, "title": "Mine"}])
    out = tool_mod.handle_propose_prd_patch(
        {"prd_id": 5, "rationale": "tighten scope", "patch_md": "## Scope\n\nMVP only."},
        project_id=1, dataset="d", company_id="comp-uuid", workspace_id="comp-uuid",
    )
    assert "pending your review" in out
    row = db.execute(
        "SELECT prd_id, prototype_id, workspace_id, status FROM prd_patches WHERE prd_id=5"
    ).fetchone()
    assert row is not None
    prd_id, prototype_id, workspace_id, status = row
    assert prd_id == 5
    assert prototype_id is None          # §B nullable — no prototype anchor
    assert workspace_id == "comp-uuid"   # == company_id (accept/reject filter key)
    assert status == "pending"

    # Surfaces via list_pending_patches under that workspace.
    import app.db.prd_patches as patches_mod
    pending = patches_mod.list_pending_patches(prd_id=5, workspace_id="comp-uuid")
    assert len(pending) == 1
    assert pending[0]["prototype_id"] is None


# ── AC24 — prd_id resolution zero / one / many ───────────────────────────────
def test_propose_prd_id_resolution_zero_one_many(tool_env, monkeypatch):
    tool_mod, db = tool_env

    # ZERO PRDs → refusal, no write.
    _set_manifest(monkeypatch, tool_mod, [{"type": "report", "id": 1}])
    out0 = tool_mod.handle_propose_prd_patch(
        {"rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert "no PRD" in out0
    assert db.execute("SELECT COUNT(*) FROM prd_patches").fetchone()[0] == 0

    # EXACTLY ONE PRD → resolved + written.
    _set_manifest(monkeypatch, tool_mod, [{"type": "prd", "id": 42, "title": "Solo"}])
    out1 = tool_mod.handle_propose_prd_patch(
        {"rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert "pending your review" in out1
    assert _count(db, 42) == 1

    # MANY PRDs, no prd_id → disambiguation listing ids, no write.
    _set_manifest(monkeypatch, tool_mod, [
        {"type": "prd", "id": 7, "title": "Alpha"},
        {"type": "prd", "id": 8, "title": "Beta"},
    ])
    outN = tool_mod.handle_propose_prd_patch(
        {"rationale": "r", "patch_md": "m"},
        project_id=1, dataset="d", company_id="c1", workspace_id="c1",
    )
    assert "id 7" in outN and "id 8" in outN
    assert _count(db, 7) == 0 and _count(db, 8) == 0


# ── AC25 — flag reader default-off ───────────────────────────────────────────
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
    assert _MIGRATION_PATH.name == "20260812130000_prd_patches_nullable_prototype_id.sql"
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

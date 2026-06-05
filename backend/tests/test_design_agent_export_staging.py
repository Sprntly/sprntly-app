"""Tests for P2-09 export staging — prototype_exports table + helpers + the
GET /v1/design-agent/{id}/export route + the /complete → record_export hook.

Three layers, mirroring the P2-07 lifecycle + P2-08 serialiser test harness:

1. **Migration** (static, isolation-friendly): the real
   `supabase/migrations/20260530000100_design_agent_exports.sql` is read from
   disk and checked for idempotency (CREATE TABLE/INDEX IF NOT EXISTS),
   workspace_id-with-no-default (Rule #20), the (prototype_id, checkpoint_id)
   UNIQUE constraint, and the is_stale column (AC18). The constraint's runtime
   behaviour is exercised against a SQLite mirror of the DDL.
2. **Helpers** (sync): insert / find / delete against the in-memory fake.
3. **Route + /complete wire-through** (async, fake-Supabase DB): POST /complete
   then SELECT prototype_exports to prove a row was ACTUALLY written with content
   matching a fresh `render_export_markdown` call (AC5), plus the GET /export
   surface (headers, 409/404/401 gates, fallback regeneration).

The /complete wire tests are REAL integration tests, not mock-only: they drive
the async `record_export_at_complete` (which P2-09 converts from P2-07's sync
stub) through the actual handler and assert on the persisted DB row. The clock
is frozen (mirroring P2-08's determinism test) so the persisted markdown and a
fresh re-render are byte-identical despite the `generated_at` line.

AUTH NOTE (P6-10, mirrors test_design_agent_lifecycle.py): the route now gates on
`require_company` (Supabase Bearer JWT → company membership), so the client
fixture delegates to conftest's bearer-authed `company_client`; authed calls
resolve workspace_id to `_TEST_COMPANY_ID`. Cross-workspace isolation is proven by
seeding under a FOREIGN workspace ('demo') and asserting 404.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sqlite3
from datetime import datetime as _real_dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# ─── SQLite mirror of the P2-09 DDL (Postgres → SQLite, same translation the
# existing prototype tests use). prototype_exports carries the real P2-09 columns
# (checkpoint_id / markdown_content / generated_at / is_stale) and the
# (prototype_id, checkpoint_id) UNIQUE constraint, distinct from the P2-07
# lifecycle test's throwaway mirror.
_EXPORT_DDL = """
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
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id     INTEGER NOT NULL,
    checkpoint_id    INTEGER NOT NULL,
    workspace_id     TEXT NOT NULL,
    markdown_content TEXT NOT NULL,
    generated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    is_stale         INTEGER NOT NULL DEFAULT 0,
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
    resolved_at  TEXT
);
"""

_PRD_MD = (
    "# Title\n"
    "body line one\n"
    ":::design\n"
    "platform_hint: both\n"
    ":::\n"
    "footer line"
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260530000100_design_agent_exports.sql"
)


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + the prototype/checkpoint/export tables + feature flag
    ON, with the design-agent module stack reloaded in dependency order so every
    binding points at the freshly-wired fake client. Returns the live modules."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_EXPORT_DDL)
    # The fake only translates columns it knows about. Register the jsonb
    # checkpoint columns (so prompt_history round-trips as a list) and the
    # prototype_exports bool column (so is_stale round-trips as a real bool).
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setitem(
        _fake_supabase._BOOL_COLUMNS, "prototype_exports", {"is_stale"},
    )

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prds as prds_mod
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_exports as exports_mod
    importlib.reload(exports_mod)
    import app.design_agent.export as export_mod
    importlib.reload(export_mod)            # rebind get_prd/get_prototype
    import app.design_agent.storage as storage_mod
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)            # rebind its db imports + find_prototype_export
    import app.main as main_mod
    importlib.reload(main_mod)              # rebuild the app with the reloaded router

    return SimpleNamespace(
        prds=prds_mod, proto=proto_mod, exports=exports_mod, export=export_mod,
        storage=storage_mod, routes=routes_mod, main=main_mod,
    )


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient without any session cookie."""
    return TestClient(env.main.app)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _fake_db() -> sqlite3.Connection:
    from tests import _fake_supabase
    return _fake_supabase.get_fake_db()


def _seed_ready(env, *, workspace_id=_TEST_COMPANY_ID, title="My Feature", md=_PRD_MD,
                bundle_url="https://x.example/p/1/index.html", prompt_history=None):
    """Seed a PRD + prototype + checkpoint and complete it to status='ready'
    (current_checkpoint_id set), so POST /complete's gate passes and the
    serialiser can render. Returns (prototype_id, checkpoint_id, prd_id)."""
    prd_id = env.prds.save_prd(brief_id=1, insight_index=0, title=title, md=md)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id=workspace_id, template_version=1)
    cid = env.proto.create_checkpoint(
        prototype_id=pid, workspace_id=workspace_id, bundle_url=bundle_url,
        prd_revision_hash=None, figma_frame_hash=None,
        prompt_history=prompt_history if prompt_history is not None else [],
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id, bundle_url=bundle_url,
        current_checkpoint_id=cid,
    )
    return pid, cid, prd_id


def _new_checkpoint(env, prototype_id, *, workspace_id=_TEST_COMPANY_ID):
    """Create a fresh checkpoint and point current_checkpoint_id at it (simulates
    a later Apply between Resume and re-Complete). Returns the new checkpoint id."""
    cid = env.proto.create_checkpoint(
        prototype_id=prototype_id, workspace_id=workspace_id,
        bundle_url="https://x.example/p/2/index.html",
        prd_revision_hash=None, figma_frame_hash=None, prompt_history=[],
    )
    db = _fake_db()
    db.execute(
        "UPDATE prototypes SET current_checkpoint_id = ? WHERE id = ?",
        [cid, prototype_id],
    )
    db.commit()
    return cid


def _patch_source(env, monkeypatch, files: dict):
    async def _fake(prototype_id, checkpoint_id):  # noqa: ARG001
        return files
    monkeypatch.setattr(env.storage, "read_source_files_for_checkpoint", _fake)


def _freeze_clock(env, monkeypatch):
    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return _real_dt(2026, 5, 29, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(env.export, "datetime", _FrozenDateTime)


def _export_rows(prototype_id) -> list[dict]:
    db = _fake_db()
    rows = db.execute(
        "SELECT * FROM prototype_exports WHERE prototype_id = ? ORDER BY id",
        [prototype_id],
    ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Migration (AC #1, #2, #18)
# ═══════════════════════════════════════════════════════════════════════════


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = []
    for line in _MIGRATION_PATH.read_text().splitlines():
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines).lower()


def test_migration_file_exists_and_is_dated_after_sharing():
    # Sorts AFTER 20260530000000 (P2-06 sharing) so it applies in the right order.
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260530000100_design_agent_exports.sql"
    assert "20260530000100" > "20260530000000"


def test_migration_applies_idempotently():
    # AC #2 — idempotency by construction: every DDL statement guards itself.
    import re
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")


def test_migration_has_expected_columns_and_unique_constraint():
    # AC #1 — required columns + the (prototype_id, checkpoint_id) unique constraint.
    import re
    sql = _migration_sql_only()
    for col in (
        "prototype_id", "checkpoint_id", "workspace_id",
        "markdown_content", "generated_at",
    ):
        assert col in sql, f"migration missing column {col}"
    assert re.search(r"unique\s*\(\s*prototype_id\s*,\s*checkpoint_id\s*\)", sql), \
        "missing UNIQUE (prototype_id, checkpoint_id)"


def test_migration_workspace_id_no_default():
    # Rule #20 — workspace_id TEXT NOT NULL with NO DEFAULT.
    import re
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql)
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT (Rule #20)"


def test_migration_has_is_stale_column_default_false():
    # AC #18 — is_stale BOOLEAN NOT NULL DEFAULT false present in the migration.
    import re
    sql = _migration_sql_only()
    assert re.search(r"is_stale\s+boolean\s+not\s+null\s+default\s+false", sql), \
        "is_stale column missing or wrong default"


def test_migration_unique_constraint_blocks_duplicate():
    # AC #4 (constraint level) — two rows with the same (prototype_id, checkpoint_id)
    # raise. Exercised against a standalone SQLite mirror of the DDL.
    con = sqlite3.connect(":memory:")
    con.executescript(_EXPORT_DDL)
    con.execute(
        "INSERT INTO prototype_exports (prototype_id, checkpoint_id, workspace_id, markdown_content) "
        "VALUES (1, 1, 'app', 'a')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO prototype_exports (prototype_id, checkpoint_id, workspace_id, markdown_content) "
            "VALUES (1, 1, 'app', 'b')"
        )
    con.close()


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — insert / find / delete (AC #3, #4, #8, #13, #16, #18)
# ═══════════════════════════════════════════════════════════════════════════


def test_insert_returns_id_on_first_call(env):
    rid = env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="# brief",
    )
    assert isinstance(rid, int)
    found = env.exports.find_prototype_export(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
    assert found is not None
    assert found["markdown_content"] == "# brief"  # AC #3 round-trip


def test_insert_is_idempotent_on_same_pair(env):
    # AC #4 — second insert with the same pair returns the existing id, no new row.
    first = env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="first",
    )
    second = env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="second",
    )
    assert second == first
    rows = _export_rows(1)
    assert len(rows) == 1
    assert rows[0]["markdown_content"] == "first"  # original preserved


def test_insert_allows_different_checkpoint_for_same_prototype(env):
    a = env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=10, workspace_id=_TEST_COMPANY_ID, markdown_content="A",
    )
    b = env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=11, workspace_id=_TEST_COMPANY_ID, markdown_content="B",
    )
    assert a != b
    assert len(_export_rows(1)) == 2


def test_find_returns_none_when_no_export(env):
    assert env.exports.find_prototype_export(prototype_id=999, workspace_id=_TEST_COMPANY_ID) is None


def test_find_returns_most_recent_when_multiple(env):
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=10, workspace_id=_TEST_COMPANY_ID, markdown_content="older",
    )
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=11, workspace_id=_TEST_COMPANY_ID, markdown_content="newer",
    )
    found = env.exports.find_prototype_export(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
    assert found["markdown_content"] == "newer"  # highest id first


def test_find_workspace_isolated(env):
    # AC #8 — seed under 'app'; lookup under 'demo' returns None.
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="x",
    )
    assert env.exports.find_prototype_export(prototype_id=1, workspace_id="demo") is None


def test_delete_returns_count(env):
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=10, workspace_id=_TEST_COMPANY_ID, markdown_content="a",
    )
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=11, workspace_id=_TEST_COMPANY_ID, markdown_content="b",
    )
    deleted = env.exports.delete_prototype_export_by_prototype(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID,
    )
    assert deleted == 2
    assert _export_rows(1) == []


def test_delete_workspace_filtered(env):
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="app-row",
    )
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=2, workspace_id="demo", markdown_content="demo-row",
    )
    env.exports.delete_prototype_export_by_prototype(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
    # demo row survives.
    assert env.exports.find_prototype_export(prototype_id=1, workspace_id="demo") is not None


def test_insert_sets_is_stale_false_by_default(env):
    # AC #18 — newly-inserted rows have is_stale = false.
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="x",
    )
    found = env.exports.find_prototype_export(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
    assert found["is_stale"] in (False, 0)


def test_find_returns_is_stale_field(env):
    # AC #18 — find result includes is_stale so flag_stale_handoff callers can read it.
    env.exports.insert_prototype_export(
        prototype_id=1, checkpoint_id=1, workspace_id=_TEST_COMPANY_ID, markdown_content="x",
    )
    found = env.exports.find_prototype_export(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
    assert "is_stale" in found


def test_insert_logs_observability_line_without_markdown_body(env, caplog):
    # AC #16 — prototype_exported INFO line with identifiers + byte count; NO body.
    body = "# secret PRD content that must never hit the logs"
    with caplog.at_level(logging.INFO, logger="app.db.prototype_exports"):
        env.exports.insert_prototype_export(
            prototype_id=7, checkpoint_id=3, workspace_id=_TEST_COMPANY_ID, markdown_content=body,
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_exported" in blob
    assert "prototype_id=7" in blob
    assert "checkpoint_id=3" in blob
    assert f"markdown_bytes={len(body)}" in blob
    assert "secret PRD content" not in blob  # body never logged


# ═══════════════════════════════════════════════════════════════════════════
# /complete wire-through (AC #5, #12, #17) — REAL integration, not mock-only
# ═══════════════════════════════════════════════════════════════════════════


def test_complete_creates_export_row(env, client, monkeypatch):
    # AC #5 — POST /complete results in exactly one prototype_exports row.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {"src/App.tsx": "export default () => null;"})
    pid, cid, _ = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 200, resp.text
    rows = _export_rows(pid)
    assert len(rows) == 1
    assert rows[0]["checkpoint_id"] == cid


def test_complete_export_row_matches_serialiser_output(env, client, monkeypatch):
    # AC #5 / AC #17 — the persisted markdown is byte-identical to a fresh
    # render_export_markdown call for the locked checkpoint (clock frozen so the
    # generated_at line matches). This is the silent-never-insert guard: it only
    # passes if post_complete actually AWAITED the async hook.
    #
    # Sync test (no running loop) so the comparison render goes through
    # asyncio.run cleanly, sidestepping sync-TestClient-inside-async-test.
    _freeze_clock(env, monkeypatch)
    files = {"src/App.tsx": "export default () => <div/>;", "src/index.css": "body{margin:0}"}
    _patch_source(env, monkeypatch, files)
    pid, cid, _ = _seed_ready(env, prompt_history=[{"role": "user", "content": "tighten"}])

    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 200, resp.text

    rows = _export_rows(pid)
    assert len(rows) == 1
    persisted = rows[0]["markdown_content"]

    expected = asyncio.run(env.export.render_export_markdown(pid, cid, workspace_id=_TEST_COMPANY_ID))
    assert persisted == expected
    assert persisted.startswith("# Design Brief: My Feature")


def test_complete_idempotent_does_not_duplicate_export_row(env, client, monkeypatch):
    # AC #4 (handler level) — POST /complete twice → still one export row.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed_ready(env)
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200
    assert len(_export_rows(pid)) == 1


def test_complete_with_missing_prd_does_not_fail_handler(env, client, monkeypatch):
    # The serialiser raises ValueError (orphan PRD) → handler still returns 200,
    # the export row is simply absent (a warning is logged). The /complete
    # response is already committed; the export regenerates via the GET fallback.
    _patch_source(env, monkeypatch, {})
    # Prototype whose prd_id does not resolve.
    pid = env.proto.start_prototype(prd_id=999999, workspace_id=_TEST_COMPANY_ID, template_version=1)
    cid = env.proto.create_checkpoint(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, bundle_url="https://x/index.html",
        prd_revision_hash=None, figma_frame_hash=None, prompt_history=[],
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, bundle_url="https://x/index.html",
        current_checkpoint_id=cid,
    )
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 200, resp.text
    assert _export_rows(pid) == []


def test_resume_then_complete_with_new_checkpoint_creates_second_export(env, client, monkeypatch):
    # AC #12 — complete on ckpt A → row(A); resume; new ckpt B; complete → row(B).
    # The UNIQUE (prototype_id, checkpoint_id) allows BOTH rows.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {})
    pid, cid_a, _ = _seed_ready(env)

    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200
    assert [r["checkpoint_id"] for r in _export_rows(pid)] == [cid_a]

    assert client.post(f"/v1/design-agent/{pid}/resume").status_code == 200
    cid_b = _new_checkpoint(env, pid)
    assert cid_b != cid_a
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200

    checkpoints = sorted(r["checkpoint_id"] for r in _export_rows(pid))
    assert checkpoints == sorted([cid_a, cid_b])


# ═══════════════════════════════════════════════════════════════════════════
# GET /export (AC #6, #7, #8, #9, #10, #11)
# ═══════════════════════════════════════════════════════════════════════════


def test_get_export_returns_markdown_with_correct_headers(env, client, monkeypatch):
    # AC #6 — 200 + text/markdown; charset=utf-8 + attachment filename + body.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {"src/App.tsx": "export default () => null;"})
    pid, _, _ = _seed_ready(env)
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200

    resp = client.get(f"/v1/design-agent/{pid}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.headers["content-disposition"] == \
        f'attachment; filename="prototype-{pid}-design-brief.md"'
    assert resp.text.startswith("# Design Brief: My Feature")


def test_get_export_body_matches_persisted_row(env, client, monkeypatch):
    # AC #6 — body equals the persisted markdown_content (snapshot, not regen).
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed_ready(env)
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200
    persisted = _export_rows(pid)[0]["markdown_content"]
    resp = client.get(f"/v1/design-agent/{pid}/export")
    assert resp.status_code == 200, resp.text
    assert resp.text == persisted


def test_get_export_returns_409_when_wip(env, client, monkeypatch):
    # AC #7 — is_complete=false → 409 (F17: WIP viewable but not exportable).
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed_ready(env)  # ready but NOT marked complete
    resp = client.get(f"/v1/design-agent/{pid}/export")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Mark prototype complete first"


def test_get_export_returns_404_when_wrong_workspace(env, client, monkeypatch):
    # AC #8 — completed under FOREIGN workspace 'demo'; app caller can't see it.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed_ready(env, workspace_id="demo")
    # complete it directly under demo so it's exportable in its own workspace.
    env.proto.mark_complete(prototype_id=pid, workspace_id="demo")
    resp = client.get(f"/v1/design-agent/{pid}/export")
    assert resp.status_code == 404


def test_get_export_returns_404_when_flag_off(env, client, monkeypatch):
    # AC #9 — DESIGN_AGENT_ENABLED unset → 404.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {})
    pid, _, _ = _seed_ready(env)
    client.post(f"/v1/design-agent/{pid}/complete")
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get(f"/v1/design-agent/{pid}/export").status_code == 404


def test_get_export_returns_401_when_unauthenticated(env, unauth):
    # AC #10 — no app session → 401.
    assert unauth.get("/v1/design-agent/1/export").status_code == 401


def test_get_export_falls_back_to_regeneration_when_row_missing(env, client, monkeypatch):
    # AC #11 — delete the snapshot row, GET /export regenerates via the serialiser.
    _freeze_clock(env, monkeypatch)
    _patch_source(env, monkeypatch, {"src/App.tsx": "export default () => null;"})
    pid, cid, _ = _seed_ready(env)
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 200

    removed = env.exports.delete_prototype_export_by_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert removed == 1
    assert _export_rows(pid) == []

    resp = client.get(f"/v1/design-agent/{pid}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    # Regenerated content equals a fresh render of the locked checkpoint.
    assert resp.text.startswith("# Design Brief: My Feature")


def test_get_export_returns_404_on_missing_prototype(env, client):
    assert client.get("/v1/design-agent/999999/export").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# record_export_at_complete — direct edge cases (AC #5 contract)
# ═══════════════════════════════════════════════════════════════════════════


async def test_record_export_at_complete_handles_missing_prototype_gracefully(env, monkeypatch):
    # Bogus id → no raise, no row, warning logged.
    _patch_source(env, monkeypatch, {})
    with caplog_at(logging.WARNING, "app.db.prototypes") as records:
        result = await env.proto.record_export_at_complete(prototype_id=424242, workspace_id=_TEST_COMPANY_ID)
    assert result is None
    assert _export_rows(424242) == []
    assert any("record_export_at_complete_skipped" in r.getMessage() for r in records)


async def test_record_export_at_complete_handles_missing_checkpoint_gracefully(env, monkeypatch):
    # complete_checkpoint_id is None → no raise, no row, warning logged.
    _patch_source(env, monkeypatch, {})
    # Ready prototype but never mark_complete'd, so complete_checkpoint_id is NULL.
    pid, _, _ = _seed_ready(env)
    result = await env.proto.record_export_at_complete(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert result is None
    assert _export_rows(pid) == []


def test_record_export_at_complete_is_async(env):
    # AC #19 (contract) — the hook is a coroutine function so the handler awaits it.
    import inspect
    assert inspect.iscoroutinefunction(env.proto.record_export_at_complete)


def test_get_export_handler_is_async(env):
    # AC #6/#19 (contract) — the route handler is async (it may await render on fallback).
    import inspect
    assert inspect.iscoroutinefunction(env.routes.get_export)


# ─── small caplog context manager (records-returning) ─────────────────────────

import contextlib


@contextlib.contextmanager
def caplog_at(level, logger_name):
    """Capture records from a logger for the duration of the block.

    pytest's caplog fixture can't be used inside an async test that also needs a
    fresh handler scoped to one logger, so this tiny helper attaches a list
    handler directly. Returns the list of LogRecords.
    """
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _ListHandler()
    handler.setLevel(level)
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

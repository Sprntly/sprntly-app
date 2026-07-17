"""Tests for the `prototype_comments` DB helpers + migration (P3-01).

Sibling to `test_db_prototypes.py` / `test_db_prototypes_sharing.py` (kept
separate so the P3-01 diff stays focused). Covers `insert_comment`,
`list_comments`, `resolve_comment`, `list_open_comment_anchor_ids`,
`mark_comments_orphaned`, the new migration's idempotency / CHECK-constraint
shape, and workspace isolation.

Runs fully in isolation against the in-memory FakeSupabaseClient (no live
Supabase). We reuse conftest's `isolated_settings` fixture for env + module
reload + fake-client wiring, then add the `prototype_comments` table to the
already-seeded in-memory DB so we never touch the shared test scaffolding. The
status CHECK is inlined in the test DDL so the SQLite fake enforces the same
semantics Postgres will; the migration's idempotency is verified at the
SQL-string level (same convention as the sibling tests — no live Postgres in the
P3 dev env).
"""
from __future__ import annotations

import importlib
import logging
import re
import sqlite3
from pathlib import Path

import pytest

# SQLite-compatible end-state of `prototype_comments` after the P3-01 migration.
# Postgres-only constructs (bigint identity, timestamptz, RLS, the FK reference,
# the separate ALTER ... ADD CONSTRAINT) are translated/omitted the same way the
# sibling test DDLs do — the fake exercises SQL semantics, not Postgres DDL. The
# status CHECK is inlined so the fake rejects illegal values exactly as Postgres.
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
    pin_x_pct          REAL,
    pin_y_pct          REAL,
    resolved_anchor_id TEXT,
    user_id            TEXT,
    origin        TEXT NOT NULL DEFAULT 'internal'
                  CHECK (origin IN ('internal', 'public')),
    visitor_id    TEXT
);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260601000000_design_agent_comments.sql"
)


@pytest.fixture
def comments(isolated_settings, monkeypatch):
    """The reloaded app.db.prototype_comments module wired to the fake Supabase,
    with the prototype_comments table present in the in-memory DB."""
    from tests import _fake_supabase

    # Add the new table on top of conftest's already-reset fake schema.
    _fake_supabase.get_fake_db().executescript(_COMMENTS_DDL)

    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)  # rebind require_client/utc_now from the reloaded client
    return comments_mod


# ─── Migration file (string-level — isolation-friendly, no live Postgres) ──


def _migration_raw() -> str:
    return _MIGRATION_PATH.read_text()


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = []
    for line in _migration_raw().splitlines():
        code = line.split("--", 1)[0]
        lines.append(code)
    return "\n".join(lines).lower()


def test_migration_file_exists_and_is_dated_correctly():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260601000000_design_agent_comments.sql"


def test_migration_declares_all_columns():
    # AC #1 — the columns + their key attributes are present in the migration.
    sql = _migration_sql_only()
    for col in (
        "id", "prototype_id", "workspace_id", "anchor_id", "body",
        "author", "status", "created_at", "resolved_at",
    ):
        assert col in sql, f"migration missing column {col}"
    assert "prototype_id  bigint not null references prototypes(id) on delete cascade" in sql
    assert "anchor_id     text   not null" in sql
    assert "body          text   not null" in sql
    assert "author        text   not null default 'demo'" in sql
    assert "status        text   not null default 'open'" in sql
    assert "resolved_at   timestamptz" in sql


def test_migration_applies_idempotently():
    # AC #2 — structural idempotency (apply-twice verified at the SQL-string level;
    # a live-Postgres apply-twice is deferred to a phase smoke, same convention as
    # the sibling tests).
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    # The status CHECK must be preceded by a DROP CONSTRAINT IF EXISTS.
    name = "prototype_comments_status_check"
    assert f"drop constraint if exists {name}" in sql, f"{name} not dropped-before-add"
    assert f"add constraint {name}" in sql
    assert sql.index(f"drop constraint if exists {name}") < sql.index(f"add constraint {name}")


def test_migration_workspace_id_no_default():
    # AC #1 / Rule #20 — workspace_id TEXT NOT NULL with NO DEFAULT.
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql), "workspace_id column missing"
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT (Rule #20)"


def test_migration_check_constraint_lists_three_statuses():
    # AC #3 — the CHECK constrains status to exactly the three legal values.
    sql = _migration_sql_only()
    assert "check (status in ('open', 'resolved', 'orphaned'))" in sql


def test_migration_uses_rls_no_policies():
    sql = _migration_sql_only()
    assert sql.count("enable row level security") == 1
    assert "create policy" not in sql


def test_status_check_constraint_rejects_invalid(comments):
    # AC #3 — inserting status='broken' raises a DB integrity error.
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO prototype_comments "
            "(prototype_id, workspace_id, anchor_id, body, status) "
            "VALUES (?, ?, ?, ?, ?)",
            [1, "app", "aaaa1111", "hi", "broken"],
        )


# ─── Creation (insert_comment) ─────────────────────────────────────────────


def test_insert_comment_returns_open_row(comments):
    # AC #4 — insert returns a row with matching anchor/body, status='open', author='demo'.
    row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="needs spacing",
    )
    assert row["anchor_id"] == "aaaa1111"
    assert row["body"] == "needs spacing"
    assert row["status"] == "open"
    assert row["author"] == "demo"
    assert isinstance(row["id"], int) and row["id"] > 0


def test_insert_comment_round_trips_via_list(comments):
    # AC #4 — insert + list_comments returns the matching row.
    comments.insert_comment(
        prototype_id=42, workspace_id="app", anchor_id="bbbb2222", body="move this up",
    )
    rows = comments.list_comments(prototype_id=42, workspace_id="app")
    assert len(rows) == 1
    assert rows[0]["anchor_id"] == "bbbb2222"
    assert rows[0]["body"] == "move this up"
    assert rows[0]["status"] == "open"
    assert rows[0]["author"] == "demo"


def test_insert_comment_empty_anchor_raises(comments):
    # AC #5
    with pytest.raises(ValueError):
        comments.insert_comment(prototype_id=1, workspace_id="app", anchor_id="", body="x")


def test_insert_comment_empty_body_raises(comments):
    # AC #5 — whitespace-only body is empty.
    with pytest.raises(ValueError):
        comments.insert_comment(
            prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="   ",
        )


# ─── Retrieval (list_comments / list_open_comment_anchor_ids) ──────────────


def test_list_comments_returns_all_statuses_ordered(comments):
    # AC #4 — open + resolved + orphaned all returned, created_at non-decreasing.
    c_open = comments.insert_comment(
        prototype_id=7, workspace_id="app", anchor_id="a-open", body="still open",
    )
    c_res = comments.insert_comment(
        prototype_id=7, workspace_id="app", anchor_id="a-res", body="to resolve",
    )
    c_orph = comments.insert_comment(
        prototype_id=7, workspace_id="app", anchor_id="a-orph", body="to orphan",
    )
    comments.resolve_comment(comment_id=c_res["id"], workspace_id="app")
    comments.mark_comments_orphaned(
        prototype_id=7, workspace_id="app", surviving_anchor_ids={"a-open"},
    )
    rows = comments.list_comments(prototype_id=7, workspace_id="app")
    assert len(rows) == 3
    by_id = {r["id"]: r["status"] for r in rows}
    assert by_id[c_open["id"]] == "open"
    assert by_id[c_res["id"]] == "resolved"
    assert by_id[c_orph["id"]] == "orphaned"
    created = [r["created_at"] for r in rows]
    assert created == sorted(created), "list_comments must be created_at-ascending"


def test_list_comments_workspace_isolated(comments):
    # AC #10 — insert under 'app'; list under 'demo' returns [].
    comments.insert_comment(
        prototype_id=5, workspace_id="app", anchor_id="aaaa1111", body="app-only",
    )
    assert comments.list_comments(prototype_id=5, workspace_id="demo") == []
    assert len(comments.list_comments(prototype_id=5, workspace_id="app")) == 1


def test_list_open_anchor_ids_excludes_resolved_and_orphaned(comments):
    # AC #9 — only OPEN comments' anchors returned, distinct.
    comments.insert_comment(prototype_id=3, workspace_id="app", anchor_id="a1", body="one")
    comments.insert_comment(prototype_id=3, workspace_id="app", anchor_id="a1", body="dup anchor")
    comments.insert_comment(prototype_id=3, workspace_id="app", anchor_id="a2", body="two")
    to_resolve = comments.insert_comment(
        prototype_id=3, workspace_id="app", anchor_id="a3", body="resolve me",
    )
    comments.insert_comment(prototype_id=3, workspace_id="app", anchor_id="a4", body="orphan me")
    comments.resolve_comment(comment_id=to_resolve["id"], workspace_id="app")
    comments.mark_comments_orphaned(
        prototype_id=3, workspace_id="app", surviving_anchor_ids={"a1", "a2"},
    )
    result = comments.list_open_comment_anchor_ids(prototype_id=3, workspace_id="app")
    assert set(result) == {"a1", "a2"}
    assert len(result) == 2, "anchors must be distinct"


def test_list_open_anchor_ids_workspace_filtered(comments):
    # AC #9 — open anchors under 'app' are invisible to a 'demo' query.
    comments.insert_comment(prototype_id=8, workspace_id="app", anchor_id="a1", body="x")
    assert comments.list_open_comment_anchor_ids(prototype_id=8, workspace_id="demo") == []


# ─── Update / resolve ──────────────────────────────────────────────────────


def test_resolve_comment_sets_resolved_status_and_timestamp(comments):
    # AC #6
    row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="x",
    )
    updated = comments.resolve_comment(comment_id=row["id"], workspace_id="app")
    assert updated is not None
    assert updated["status"] == "resolved"
    assert updated["resolved_at"] is not None


def test_resolve_comment_idempotent(comments):
    # AC #6 — resolving twice returns the row without error.
    row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="x",
    )
    first = comments.resolve_comment(comment_id=row["id"], workspace_id="app")
    second = comments.resolve_comment(comment_id=row["id"], workspace_id="app")
    assert first is not None and second is not None
    assert second["status"] == "resolved"


def test_resolve_comment_wrong_workspace_returns_none(comments):
    # AC #7 — resolve under the wrong workspace returns None and does not flip the row.
    row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="x",
    )
    assert comments.resolve_comment(comment_id=row["id"], workspace_id="demo") is None
    # The comment under 'app' is untouched (still open).
    app_rows = comments.list_comments(prototype_id=1, workspace_id="app")
    assert app_rows[0]["status"] == "open"


# ─── Orphan (mark_comments_orphaned) ───────────────────────────────────────


def test_mark_orphaned_flips_only_non_surviving_open(comments):
    # AC #8 — non-surviving open → orphaned; surviving open → stays open.
    survivor = comments.insert_comment(
        prototype_id=2, workspace_id="app", anchor_id="aaaa1111", body="keep",
    )
    doomed = comments.insert_comment(
        prototype_id=2, workspace_id="app", anchor_id="dddd4444", body="gone",
    )
    count = comments.mark_comments_orphaned(
        prototype_id=2, workspace_id="app", surviving_anchor_ids={"aaaa1111"},
    )
    assert count == 1
    rows = {r["id"]: r["status"] for r in comments.list_comments(prototype_id=2, workspace_id="app")}
    assert rows[survivor["id"]] == "open"
    assert rows[doomed["id"]] == "orphaned"


def test_mark_orphaned_leaves_resolved_untouched(comments):
    # AC #8 — a resolved comment whose anchor is gone is NOT re-flipped.
    resolved = comments.insert_comment(
        prototype_id=2, workspace_id="app", anchor_id="gone", body="already resolved",
    )
    comments.resolve_comment(comment_id=resolved["id"], workspace_id="app")
    count = comments.mark_comments_orphaned(
        prototype_id=2, workspace_id="app", surviving_anchor_ids={"other"},
    )
    assert count == 0  # the only candidate was resolved, not open
    rows = {r["id"]: r["status"] for r in comments.list_comments(prototype_id=2, workspace_id="app")}
    assert rows[resolved["id"]] == "resolved"


def test_mark_orphaned_returns_count(comments):
    # AC #8 — returns the number of comments orphaned.
    for anchor in ("a1", "a2", "a3"):
        comments.insert_comment(prototype_id=4, workspace_id="app", anchor_id=anchor, body="x")
    count = comments.mark_comments_orphaned(
        prototype_id=4, workspace_id="app", surviving_anchor_ids={"a1"},
    )
    assert count == 2


def test_mark_orphaned_workspace_filtered(comments):
    # AC #8 — orphaning under 'app' does not touch a row under 'demo'.
    app_row = comments.insert_comment(
        prototype_id=6, workspace_id="app", anchor_id="x", body="app",
    )
    demo_row = comments.insert_comment(
        prototype_id=6, workspace_id="demo", anchor_id="x", body="demo",
    )
    comments.mark_comments_orphaned(
        prototype_id=6, workspace_id="app", surviving_anchor_ids=set(),
    )
    app_rows = {r["id"]: r["status"] for r in comments.list_comments(prototype_id=6, workspace_id="app")}
    demo_rows = {r["id"]: r["status"] for r in comments.list_comments(prototype_id=6, workspace_id="demo")}
    assert app_rows[app_row["id"]] == "orphaned"
    assert demo_rows[demo_row["id"]] == "open"


# ─── Observability (AC #11 / Rule #24) ─────────────────────────────────────


def test_logs_identifiers_only_no_body(comments, caplog):
    # AC #11 — comment_created / comment_resolved / comments_orphaned INFO lines;
    # NO comment body in any log line (PII).
    secret_body = "SECRET_COMMENT_BODY_VALUE"
    with caplog.at_level(logging.INFO, logger="app.db.prototype_comments"):
        row = comments.insert_comment(
            prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body=secret_body,
        )
        comments.resolve_comment(comment_id=row["id"], workspace_id="app")
        # Insert a second open comment, then orphan it, to exercise the orphan log.
        comments.insert_comment(
            prototype_id=1, workspace_id="app", anchor_id="dddd4444", body="another secret",
        )
        comments.mark_comments_orphaned(
            prototype_id=1, workspace_id="app", surviving_anchor_ids=set(),
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"comment_created prototype_id=1 comment_id={row['id']} anchor_id=aaaa1111" in blob
    assert f"comment_resolved comment_id={row['id']}" in blob
    assert "comments_orphaned prototype_id=1" in blob
    assert secret_body not in blob
    assert "another secret" not in blob


# ─── Durable comment position (DB-level) ────────────────────────────────────

_POSITION_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260606000002_design_agent_comment_position.sql"
)


def test_insert_comment_persists_position_fields(comments):
    # Position round-trip: insert with all three position fields → list_comments
    # returns a row carrying the exact values.
    row = comments.insert_comment(
        prototype_id=1,
        workspace_id="app",
        anchor_id="pin-1",
        body="check the button",
        pin_x_pct=12.5,
        pin_y_pct=63.0,
        resolved_anchor_id="fb3007b5",
    )
    assert row["pin_x_pct"] == pytest.approx(12.5)
    assert row["pin_y_pct"] == pytest.approx(63.0)
    assert row["resolved_anchor_id"] == "fb3007b5"

    rows = comments.list_comments(prototype_id=1, workspace_id="app")
    assert len(rows) == 1
    assert rows[0]["pin_x_pct"] == pytest.approx(12.5)
    assert rows[0]["pin_y_pct"] == pytest.approx(63.0)
    assert rows[0]["resolved_anchor_id"] == "fb3007b5"


def test_insert_comment_without_position_stores_null(comments):
    # A comment inserted without position kwargs (the right-click anchor path)
    # stores null for all three columns; the insert payload omits those keys.
    row = comments.insert_comment(
        prototype_id=1,
        workspace_id="app",
        anchor_id="abc12345",
        body="anchor comment",
    )
    assert row.get("pin_x_pct") is None
    assert row.get("pin_y_pct") is None
    assert row.get("resolved_anchor_id") is None

    rows = comments.list_comments(prototype_id=1, workspace_id="app")
    assert rows[0].get("pin_x_pct") is None
    assert rows[0].get("pin_y_pct") is None
    assert rows[0].get("resolved_anchor_id") is None


def test_insert_comment_existing_callsites_unaffected(comments):
    # Regression guard: existing call-sites that pass only the prior positional/keyword
    # set (no position) still succeed unchanged. The optional-kwarg widening must not
    # break any existing caller.
    row = comments.insert_comment(
        prototype_id=7,
        workspace_id="app",
        anchor_id="legacy-anchor",
        body="legacy call",
        author="demo",
    )
    assert row["status"] == "open"
    assert row["anchor_id"] == "legacy-anchor"
    assert row.get("pin_x_pct") is None


# ─── Comment origin + visitor identity (public/internal isolation) ─────────

_ORIGIN_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260716120000_prototype_comments_origin_visitor.sql"
)


def test_insert_comment_origin_default_internal(comments):
    # Default origin is 'internal' (carried by the column DEFAULT); an explicit
    # 'public' is stored as-is.
    default_row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="team note",
    )
    assert default_row["origin"] == "internal"

    public_row = comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="bbbb2222", body="viewer note",
        origin="public", visitor_id="visitor-abc-000000000001",
    )
    assert public_row["origin"] == "public"
    assert public_row["visitor_id"] == "visitor-abc-000000000001"


def test_insert_comment_rejects_unknown_origin(comments):
    # An origin outside {'internal','public'} is a programming bug → ValueError,
    # and nothing is written.
    with pytest.raises(ValueError):
        comments.insert_comment(
            prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="x",
            origin="bogus",
        )
    assert comments.list_comments(prototype_id=1, workspace_id="app") == []


def test_list_comments_origin_filter(comments):
    # origin=None returns ALL rows (today's behaviour); origin='public' returns
    # only public rows. The origin filter is additional to the workspace filter,
    # never a replacement.
    comments.insert_comment(
        prototype_id=9, workspace_id="app", anchor_id="a-int", body="internal row",
    )
    comments.insert_comment(
        prototype_id=9, workspace_id="app", anchor_id="a-pub", body="public row",
        origin="public", visitor_id="visitor-xyz-000000000001",
    )
    all_rows = comments.list_comments(prototype_id=9, workspace_id="app")
    assert len(all_rows) == 2

    public_rows = comments.list_comments(prototype_id=9, workspace_id="app", origin="public")
    assert [r["body"] for r in public_rows] == ["public row"]

    # Workspace isolation still applies WITH the origin filter.
    assert comments.list_comments(prototype_id=9, workspace_id="demo", origin="public") == []


def test_insert_payload_omits_default_origin_and_null_visitor(comments, monkeypatch):
    # Conditional-write pin: an internal insert's payload carries NEITHER key
    # (the DB default carries 'internal'; null visitor is honest absence); a
    # public insert carries both. This keeps every insert path compatible with
    # schemas that predate the two columns.
    captured: list[dict] = []
    real_client = comments.require_client()

    class _TableSpy:
        def __init__(self, inner):
            self._inner = inner

        def insert(self, payload):
            captured.append(dict(payload))
            return self._inner.insert(payload)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class _ClientSpy:
        def table(self, name):
            return _TableSpy(real_client.table(name))

        def __getattr__(self, name):
            return getattr(real_client, name)

    monkeypatch.setattr(comments, "require_client", lambda: _ClientSpy())

    comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="aaaa1111", body="internal row",
    )
    assert "origin" not in captured[0]
    assert "visitor_id" not in captured[0]

    comments.insert_comment(
        prototype_id=1, workspace_id="app", anchor_id="bbbb2222", body="public row",
        origin="public", visitor_id="visitor-spy-000000000001",
    )
    assert captured[1]["origin"] == "public"
    assert captured[1]["visitor_id"] == "visitor-spy-000000000001"


def test_origin_migration_idempotent_and_complete():
    # Idempotency at the SQL-string level (house convention — a live-Postgres
    # apply-twice is deferred to the phase smoke): every ADD COLUMN carries
    # IF NOT EXISTS; both columns are declared; origin is NOT NULL DEFAULT
    # 'internal' with the two-value CHECK; visitor_id is nullable with NO
    # DEFAULT.
    assert _ORIGIN_MIGRATION_PATH.exists()
    sql = _ORIGIN_MIGRATION_PATH.read_text().lower()
    lines_no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())

    add_column_blocks = re.findall(
        r"add\s+column\s+(if\s+not\s+exists)?\s*\w+", lines_no_comments
    )
    assert add_column_blocks, "migration declares no ADD COLUMN at all"
    for block in add_column_blocks:
        assert block, "found an ALTER TABLE ADD COLUMN without IF NOT EXISTS — not idempotent"

    assert re.search(r"origin\s+text\s+not\s+null\s+default\s+'internal'", lines_no_comments)
    assert "check (origin in ('internal', 'public'))" in lines_no_comments
    assert re.search(r"visitor_id\s+text", lines_no_comments)
    assert not re.search(r"visitor_id\s+text\s+not\s+null", lines_no_comments), \
        "visitor_id must stay nullable (internal rows have no visitor identity)"
    assert not re.search(r"visitor_id\s+text\s+default", lines_no_comments), \
        "visitor_id must NOT carry a DEFAULT (null is honest absence)"
    assert "create table" not in lines_no_comments  # additive only — no new table


def test_migration_idempotent_apply_twice():
    # The position migration uses `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for
    # each new column, which is the Postgres-idempotency primitive (verified at
    # string level — same convention as test_migration_applies_idempotently above;
    # a live-Postgres apply-twice is deferred to the phase smoke run).
    sql = _POSITION_MIGRATION_PATH.read_text().lower()
    # Strip line comments so the check isn't confused by comment text.
    lines_no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    # Every ADD COLUMN must carry IF NOT EXISTS.
    add_column_blocks = re.findall(r"add\s+column\s+(if\s+not\s+exists)?\s*\w+", lines_no_comments)
    for block in add_column_blocks:
        assert block, "found an ALTER TABLE ADD COLUMN without IF NOT EXISTS — not idempotent"
    # All three new columns are declared in the migration.
    assert "pin_x_pct" in lines_no_comments
    assert "pin_y_pct" in lines_no_comments
    assert "resolved_anchor_id" in lines_no_comments
    # The file must exist at the working-tree path (not a git-rev reference).
    assert _POSITION_MIGRATION_PATH.exists()

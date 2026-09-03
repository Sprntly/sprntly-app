"""In-memory PostgREST/supabase-py stand-in for tests.

Tests need fast, isolated storage without an external DB. The
production code in `app.db.*` talks to `supabase_client().table(...)`;
this fake satisfies that interface using a per-test in-memory SQLite
under the hood so SQL semantics (auto-increment, UNIQUE, etc.) match
real Supabase closely enough for our tests.

Only the operations our helpers actually call are implemented:
    table(name)
      .select(cols, count=...)
      .insert(row | rows[])
      .upsert(row | rows[], on_conflict=col)
      .update(patch)
      .delete()
      .eq(col, val)
      .in_(col, vals)
      .order(col, desc=True/False)
      .limit(n)
      .execute()  -> SimpleNamespace(data=[...], count=Optional[int])

Schema is provided once via `seed_schema(sql)` at test setup. PKs and
unique constraints are honored.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace
from typing import Any, Iterable


# Serializes every fake-DB access (and the per-test reset that closes the
# connection). The single :memory: connection is shared across threads
# (check_same_thread=False), and background work — e.g. the PRD Part-B
# pre-warm's `asyncio.to_thread(ensure_impl_spec, ...)` — can outlive the
# request/test that spawned it and race a concurrently-running query or the
# next test's reset_fake_db() close. Concurrent use of one sqlite3 connection
# raises "sqlite3.InterfaceError: bad parameter or other API misuse" (an
# order-dependent pytest-integration flake). This lock makes the
# "we serialize writes ourselves" contract real, so cross-thread access can
# never overlap and reset never closes a connection mid-query.
_LOCK = threading.RLock()


# Module-level singleton so the same fake survives within one test.
_DB: sqlite3.Connection | None = None
_DDL: str = ""


def get_fake_db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        _DB = sqlite3.connect(":memory:")
        _DB.row_factory = sqlite3.Row
        if _DDL:
            _DB.executescript(_DDL)
    return _DB


def reset_fake_db(ddl: str) -> None:
    """Wipe the in-memory DB and re-create from DDL. Called per-test."""
    global _DB, _DDL
    # Hold _LOCK across close+reopen so a still-running background query (which
    # takes _LOCK for its full execute()) finishes before we close the old
    # connection — never closing it mid-statement.
    with _LOCK:
        _DDL = ddl
        if _DB is not None:
            _DB.close()
        # check_same_thread=False — FastAPI TestClient hops threads;
        # we serialize writes ourselves (via _LOCK) so it's safe.
        _DB = sqlite3.connect(":memory:", check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.executescript(ddl)


# Postgres jsonb columns return Python dicts/lists in supabase-py. We
# store them as JSON-text in SQLite under the hood and translate at the
# boundary so callers see real dicts.
_JSONB_COLUMNS: dict[str, set[str]] = {
    "briefs":               {"payload"},
    "crucible_runs":         {"coverage_notes", "prioritisation"},
    "crucible_findings":     {"claim_ids", "surfaced_by", "assumed_params",
                              "impact", "confidence"},
    "crucible_ledger":       {"claim_ids"},
    "crucible_goal_definitions": {"population", "conflicts_found"},
    "crucible_backfill_runs": {"skipped_counts"},
    "ask_log":              {"citations"},
    "cached_asks":          {"response"},
    "ask_jobs":             {"response"},
    "website_analysis_jobs": {"result"},
    "company_research_runs": {"stages", "records"},
    "llm_context_jobs":     {"result"},
    "companies":            {"coworker_names", "kpi_tree", "competitors", "business_context", "notification_settings", "feature_flags", "icp", "tone_voice"},
    "products":             {"surfaces", "personas", "monetization"},
    "workspace_invites":    {"workspace_ids"},
    "connections":          {"config"},
    "org_invites":          {"feature_flags"},
    "github_installations": {"permissions", "events"},
    # ---- KG foundation (jsonb + array + vector columns; the fake JSON-encodes) ----
    "kg_source":         {"config"},
    "kg_entity":         {"aliases", "properties", "embedding", "provenance"},
    "kg_signal":         {"properties", "embedding", "provenance"},
    "kg_relationship":   {"properties", "provenance"},
    "agent_decision_log": {"factors", "output", "kg_refs"},
    "enterprise_config": {"overrides"},
    "ticket_edits":      {"acceptance_criteria", "assignee", "subtasks", "custom_fields"},
    "prd_tickets":       {"stories", "relayout"},
    # Standalone ticket sets carry the same `stories` payload shape as
    # prd_tickets — the whole point of the second home.
    "ticket_sets":       {"stories", "relayout"},
    "call_transcripts":  {"payload"},
    "prd_ticket_sync":   {"statuses"},
    "tracker_meta":      {"meta"},
    "prd_input_questions": {"options"},
    "conversation_turns":  {"attachments", "reply"},
    # text[] + vector(1536) — JSON-encoded in the mirror.
    "document_catalog":    {"topics", "embedding"},
    "design_agent_map_cache": {"payload"},
    "design_agent_jobs":      {"payload"},  # Tier 2 worker queue
    "pipeline_runs":          {"stages"},   # per-stage results JSONB
}

# Postgres bool columns surface as bool in supabase-py; SQLite stores 0/1.
_BOOL_COLUMNS: dict[str, set[str]] = {
    "companies":            {"use_platform_key", "prototype_enabled"},
    "org_invites":          {"prototype_enabled", "use_platform_key"},
    "briefs":               {"is_current"},
    "github_installations": {"suspended"},
    "github_pull_requests": {"is_draft"},
    "workspaces":           {"is_default"},
    "products":             {"is_primary"},
    "ideation_items":       {"shortlisted"},
    "kg_signal":            {"evidence_eligible"},
    "artifact_templates":   {"is_active"},
    "project_memory_summary": {"stale"},
    "delegation_followups": {"muted"},
}


def _encode_row(table: str, row: dict) -> dict:
    out = dict(row)
    for col in _JSONB_COLUMNS.get(table, set()):
        if col in out and not isinstance(out[col], (str, type(None))):
            out[col] = json.dumps(out[col])
    for col in _BOOL_COLUMNS.get(table, set()):
        if col in out and isinstance(out[col], bool):
            out[col] = 1 if out[col] else 0
    return out


def _project_row(row: dict | None, cols: str) -> dict | None:
    """Keep only the selected columns, mirroring PostgREST.

    `*`, an embedded resource ("a,b(c)"), or anything with a modifier is passed
    through untouched — the point is to catch a plainly forgotten column, not
    to reimplement PostgREST's grammar and start failing on valid selects.
    """
    if row is None:
        return None
    spec = (cols or "*").strip()
    if not spec or spec == "*" or "(" in spec or ":" in spec:
        return row
    wanted = {c.strip() for c in spec.split(",") if c.strip()}
    if not wanted or "*" in wanted:
        return row
    return {k: v for k, v in row.items() if k in wanted}


def _decode_row(table: str, row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for col in _JSONB_COLUMNS.get(table, set()):
        if col in out and out[col] is not None:
            try:
                out[col] = json.loads(out[col])
            except (TypeError, ValueError):
                pass
    for col in _BOOL_COLUMNS.get(table, set()):
        if col in out and out[col] is not None:
            out[col] = bool(out[col])
    return out


class _Query:
    def __init__(self, table: str):
        self.table = table
        self._kind: str = "select"
        self._cols: str = "*"
        self._eqs: list[tuple[str, Any]] = []
        self._ins: list[tuple[str, list]] = []
        self._raw_where: list[str] = []
        self._raw_args: list = []
        self._negate_next: bool = False
        self._order: tuple[str, bool, bool | None] | None = None
        self._offset: int = 0
        self._limit: int | None = None
        self._values: list[dict] = []
        self._patch: dict = {}
        self._on_conflict: str | None = None
        self._ignore_duplicates: bool = False
        self._count_mode: str | None = None

    # ── verbs ──────────────────────────────────────────────────────

    def select(self, cols: str = "*", count: str | None = None) -> "_Query":
        self._kind = "select"
        self._cols = cols
        self._count_mode = count
        return self

    def insert(self, row_or_rows) -> "_Query":
        self._kind = "insert"
        self._values = [row_or_rows] if isinstance(row_or_rows, dict) else list(row_or_rows)
        return self

    def upsert(
        self,
        row_or_rows,
        on_conflict: str | None = None,
        ignore_duplicates: bool = False,
    ) -> "_Query":
        self._kind = "upsert"
        self._values = [row_or_rows] if isinstance(row_or_rows, dict) else list(row_or_rows)
        self._on_conflict = on_conflict
        # supabase-py's `ignore_duplicates=True` is ON CONFLICT DO NOTHING —
        # the existing row wins and keeps every column it already had. Without
        # it the branch below builds a DO UPDATE and a repeat write silently
        # overwrites columns the caller meant to preserve (whitelist signups
        # keep their ORIGINAL created_at this way).
        self._ignore_duplicates = ignore_duplicates
        return self

    def update(self, patch: dict) -> "_Query":
        self._kind = "update"
        self._patch = patch
        return self

    def delete(self) -> "_Query":
        self._kind = "delete"
        return self

    # ── filters ────────────────────────────────────────────────────

    def eq(self, col: str, val: Any) -> "_Query":
        self._eqs.append((col, val))
        return self

    def neq(self, col: str, val: Any) -> "_Query":
        """`col != ?` (PostgREST `.neq`). NULL columns are excluded in both
        engines (NULL != x is NULL → false), matching Postgres."""
        self._raw_where.append(f"{col} != ?")
        self._raw_args.append(val)
        return self

    def in_(self, col: str, vals: Iterable) -> "_Query":
        self._ins.append((col, list(vals)))
        return self

    def ilike(self, col: str, val: Any) -> "_Query":
        """Case-insensitive match. SQLite's LIKE is ASCII case-insensitive,
        which mirrors PostgREST `.ilike`. ESCAPE '\\' matches Postgres' default
        escape character so backslash-escaped patterns (the team email lookup
        escapes %/_ in emails) behave identically; patterns without
        backslashes (the GitHub-login lookup) are unaffected."""
        self._raw_where.append(f"{col} LIKE ? ESCAPE '\\'")
        self._raw_args.append(val)
        return self

    def like(self, col: str, val: Any) -> "_Query":
        """Pattern match (PostgREST `.like`). SQLite LIKE is ASCII
        case-insensitive where Postgres LIKE is sensitive — fine for our
        callers (prefix matches on lowercase ticket keys)."""
        self._raw_where.append(f"{col} LIKE ?")
        self._raw_args.append(val)
        return self

    def lt(self, col: str, val: Any) -> "_Query":
        """`col < ?` — used by the map-cache expiry sweep."""
        self._raw_where.append(f"{col} < ?")
        self._raw_args.append(val)
        return self

    def gt(self, col: str, val: Any) -> "_Query":
        """`col > ?` — used by the company-research in-flight guard (a
        'running' row YOUNGER than the orphan cutoff)."""
        self._raw_where.append(f"{col} > ?")
        self._raw_args.append(val)
        return self

    def gte(self, col: str, val: Any) -> "_Query":
        """`col >= ?` — used by the transcript viewer's date-range filter."""
        self._raw_where.append(f"{col} >= ?")
        self._raw_args.append(val)
        return self

    @property
    def not_(self) -> "_Query":
        """Negate the next filter. Mirrors supabase-py's `.not_.is_(...)`."""
        self._negate_next = True
        return self

    def is_(self, col: str, val: Any) -> "_Query":
        """`.is_(col, "null")` → `col IS NULL`; with `.not_` → `IS NOT NULL`.
        Only the NULL form is needed by callers."""
        negate = getattr(self, "_negate_next", False)
        self._negate_next = False
        if isinstance(val, str) and val.lower() == "null":
            self._raw_where.append(f"{col} IS {'NOT ' if negate else ''}NULL")
        else:
            op = "IS NOT" if negate else "IS"
            self._raw_where.append(f"{col} {op} ?")
            self._raw_args.append(val)
        return self

    def order(
        self,
        col: str,
        desc: bool = False,
        nullsfirst: bool | None = None,
        foreign_table: str | None = None,
    ) -> "_Query":
        """Mirrors postgrest-py's signature, `nullsfirst` included.

        Not decoration: `crucible_findings` orders impact DESC NULLS LAST
        because an unsizeable finding must sort after sized ones without being
        treated as zero, and SQLite's default (NULLs first under DESC) puts them
        at the TOP — a fake that ignored the argument would have every
        unsizeable finding ranked highest and the test would still pass.
        """
        self._order = (col, desc, nullsfirst)
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = n
        return self

    def range(self, start: int, end: int) -> "_Query":
        """PostgREST's range is INCLUSIVE at both ends, so `range(0, 999)` is
        1000 rows. Getting that off by one here would make a paging test pass
        against a fake that pages differently from the server."""
        self._limit = end - start + 1
        self._offset = start
        return self

    # ── execute ────────────────────────────────────────────────────

    def _where_clause(self) -> tuple[str, list]:
        parts: list[str] = []
        args: list = []
        for col, val in self._eqs:
            parts.append(f"{col} = ?")
            args.append(val)
        for col, vals in self._ins:
            if not vals:
                # `column IN ()` is invalid SQL; force false.
                parts.append("1 = 0")
                continue
            placeholders = ",".join("?" for _ in vals)
            parts.append(f"{col} IN ({placeholders})")
            args.extend(vals)
        for raw in self._raw_where:
            parts.append(raw)
        args.extend(self._raw_args)
        if not parts:
            return "", args
        return " WHERE " + " AND ".join(parts), args

    def execute(self) -> SimpleNamespace:
        # Serialize the whole operation: get_fake_db() + every db.execute/commit
        # here runs under one lock so a background thread's query can never
        # interleave with another thread's (concurrent use of a single sqlite3
        # connection is "API misuse") nor with reset_fake_db() closing it.
        with _LOCK:
            return self._execute_locked()

    def _execute_locked(self) -> SimpleNamespace:
        db = get_fake_db()
        if self._kind == "select":
            where, args = self._where_clause()
            order_sql = ""
            if self._order:
                col, desc, nullsfirst = self._order
                order_sql = f" ORDER BY {col} {'DESC' if desc else 'ASC'}"
                if nullsfirst is not None:
                    order_sql += " NULLS FIRST" if nullsfirst else " NULLS LAST"
            limit_sql = f" LIMIT {self._limit}" if self._limit else ""
            if self._offset:
                limit_sql = (limit_sql or " LIMIT -1") + f" OFFSET {self._offset}"
            sql = f"SELECT * FROM {self.table}{where}{order_sql}{limit_sql}"
            cursor = db.execute(sql, args)
            rows = [_decode_row(self.table, r) for r in cursor.fetchall()]
            # HONOUR THE PROJECTION. This used to build `SELECT *` and ignore
            # `.select(cols)` entirely, so a caller that forgot a column got it
            # anyway here and failed only in production. That is not
            # hypothetical: `routes/crucible.py` read `provenance` while its
            # query never selected it, and 452 passing tests could not see it.
            # Filtered in Python rather than in SQL so embedded resources
            # ("a,b(c)") and `*` keep working unchanged.
            rows = [_project_row(r, self._cols) for r in rows]
            count = None
            if self._count_mode == "exact":
                c = db.execute(f"SELECT COUNT(*) FROM {self.table}{where}", args).fetchone()
                count = c[0]
            return SimpleNamespace(data=rows, count=count)

        if self._kind == "insert":
            inserted = []
            for v in self._values:
                row = _encode_row(self.table, v)
                cols = list(row.keys())
                placeholders = ",".join("?" for _ in cols)
                col_sql = ",".join(cols)
                cur = db.execute(
                    f"INSERT INTO {self.table} ({col_sql}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                # Pull the actual row back so identity columns are populated.
                pk_val = cur.lastrowid
                fetched = db.execute(
                    f"SELECT * FROM {self.table} WHERE rowid = ?", [pk_val]
                ).fetchone()
                if fetched:
                    inserted.append(_decode_row(self.table, fetched))
            db.commit()
            return SimpleNamespace(data=inserted, count=None)

        if self._kind == "upsert":
            # SQLite supports ON CONFLICT DO UPDATE since 3.24. We
            # rebuild the row with all caller-supplied columns updated.
            inserted = []
            for v in self._values:
                row = _encode_row(self.table, v)
                cols = list(row.keys())
                placeholders = ",".join("?" for _ in cols)
                col_sql = ",".join(cols)
                if self._on_conflict:
                    # supabase-py accepts a comma-separated list for composite
                    # uniques (e.g. "workspace_id,provider"). Treat each piece
                    # as a conflict-key column so it's excluded from the
                    # DO UPDATE SET clause (setting the conflict key to its
                    # existing value is wasteful and PG rejects it).
                    conflict_keys = {
                        k.strip() for k in self._on_conflict.split(",") if k.strip()
                    }
                    update_assignments = ",".join(
                        f"{c} = excluded.{c}" for c in cols if c not in conflict_keys
                    )
                    conflict_sql = (
                        f" ON CONFLICT({self._on_conflict}) DO UPDATE SET {update_assignments}"
                        if update_assignments and not self._ignore_duplicates else
                        f" ON CONFLICT({self._on_conflict}) DO NOTHING"
                    )
                else:
                    conflict_sql = ""
                db.execute(
                    f"INSERT INTO {self.table} ({col_sql}) VALUES ({placeholders}){conflict_sql}",
                    [row[c] for c in cols],
                )
            db.commit()
            # Return upserted rows looked up by on_conflict key(s), or all
            # of self._values. Composite keys arrive comma-separated.
            if self._on_conflict:
                conflict_keys = [
                    k.strip() for k in self._on_conflict.split(",") if k.strip()
                ]
                fetched_rows: list = []
                for v in self._values:
                    if not all(k in v for k in conflict_keys):
                        continue
                    where_sql = " AND ".join(f"{k} = ?" for k in conflict_keys)
                    args = [v[k] for k in conflict_keys]
                    found = db.execute(
                        f"SELECT * FROM {self.table} WHERE {where_sql}", args
                    ).fetchall()
                    fetched_rows.extend(found)
                if fetched_rows:
                    inserted = [_decode_row(self.table, r) for r in fetched_rows]
            return SimpleNamespace(data=inserted, count=None)

        if self._kind == "update":
            where, args = self._where_clause()
            patch = _encode_row(self.table, self._patch)
            set_sql = ", ".join(f"{c} = ?" for c in patch.keys())
            # Which rows the UPDATE will hit, resolved BEFORE it runs.
            #
            # PostgREST issues `UPDATE ... WHERE ... RETURNING *`, so the rows
            # it returns are the ones it changed. Re-running the same WHERE
            # afterwards is NOT the same thing: when the patch writes a column
            # the filter also tests — a compare-and-set like
            # `.eq("version", 3).update({"version": 4})` — the post-update
            # SELECT matches nothing and the caller reads a successful write as
            # a failed one. That divergence is invisible until some feature
            # relies on it, and then it fails only under test, where the code is
            # correct. Pinning the rowids first makes the fake return what
            # RETURNING would.
            target_ids = [
                r[0] for r in db.execute(
                    f"SELECT rowid FROM {self.table}{where}", args
                ).fetchall()
            ]
            db.execute(f"UPDATE {self.table} SET {set_sql}{where}", list(patch.values()) + args)
            db.commit()
            if not target_ids:
                return SimpleNamespace(data=[], count=None)
            placeholders = ",".join("?" for _ in target_ids)
            rows = db.execute(
                f"SELECT * FROM {self.table} WHERE rowid IN ({placeholders})",
                target_ids,
            ).fetchall()
            return SimpleNamespace(data=[_decode_row(self.table, r) for r in rows], count=None)

        if self._kind == "delete":
            where, args = self._where_clause()
            cur = db.execute(f"DELETE FROM {self.table}{where}", args)
            db.commit()
            return SimpleNamespace(data=[], count=cur.rowcount)

        raise RuntimeError(f"Unknown query kind: {self._kind}")


class _FakeRpc:
    """A `.execute()`-able stand-in for a supabase-py RPC call."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows, count=len(self._rows))


class FakeSupabaseClient:
    """Quacks like supabase-py's Client for our usage."""
    def table(self, name: str) -> _Query:
        return _Query(name)

    # Postgres functions (e.g. `llm_usage_summary`) have no SQLite equivalent,
    # so RPCs return whatever a test registers here. Records the args so a test
    # can assert the workspace filter and date range that were requested.
    rpc_returns: dict[str, list[dict]] = {}
    rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, fn: str, params: dict | None = None) -> _FakeRpc:
        FakeSupabaseClient.rpc_calls.append((fn, dict(params or {})))
        return _FakeRpc(FakeSupabaseClient.rpc_returns.get(fn, []))

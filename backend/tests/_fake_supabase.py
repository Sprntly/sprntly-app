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
from types import SimpleNamespace
from typing import Any, Iterable


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
    _DDL = ddl
    if _DB is not None:
        _DB.close()
    # check_same_thread=False — FastAPI TestClient hops threads;
    # we serialize writes ourselves so it's safe.
    _DB = sqlite3.connect(":memory:", check_same_thread=False)
    _DB.row_factory = sqlite3.Row
    _DB.executescript(ddl)


# Postgres jsonb columns return Python dicts/lists in supabase-py. We
# store them as JSON-text in SQLite under the hood and translate at the
# boundary so callers see real dicts.
_JSONB_COLUMNS: dict[str, set[str]] = {
    "briefs":               {"payload"},
    "ask_log":              {"citations"},
    "cached_asks":          {"response"},
    "companies":            {"coworker_names", "kpi_tree", "competitors", "business_context"},
    "connections":          {"config"},
    "github_installations": {"permissions", "events"},
    # ---- KG foundation (jsonb + array + vector columns; the fake JSON-encodes) ----
    "kg_source":         {"config"},
    "kg_entity":         {"aliases", "properties", "embedding", "provenance"},
    "kg_signal":         {"properties", "embedding", "provenance"},
    "kg_relationship":   {"properties", "provenance"},
    "agent_decision_log": {"factors", "output", "kg_refs"},
    "enterprise_config": {"overrides"},
}

# Postgres bool columns surface as bool in supabase-py; SQLite stores 0/1.
_BOOL_COLUMNS: dict[str, set[str]] = {
    "briefs":               {"is_current"},
    "github_installations": {"suspended"},
    "github_pull_requests": {"is_draft"},
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
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._values: list[dict] = []
        self._patch: dict = {}
        self._on_conflict: str | None = None
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

    def upsert(self, row_or_rows, on_conflict: str | None = None) -> "_Query":
        self._kind = "upsert"
        self._values = [row_or_rows] if isinstance(row_or_rows, dict) else list(row_or_rows)
        self._on_conflict = on_conflict
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

    def in_(self, col: str, vals: Iterable) -> "_Query":
        self._ins.append((col, list(vals)))
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

    def order(self, col: str, desc: bool = False) -> "_Query":
        self._order = (col, desc)
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = n
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
        db = get_fake_db()
        if self._kind == "select":
            where, args = self._where_clause()
            order_sql = ""
            if self._order:
                col, desc = self._order
                order_sql = f" ORDER BY {col} {'DESC' if desc else 'ASC'}"
            limit_sql = f" LIMIT {self._limit}" if self._limit else ""
            sql = f"SELECT * FROM {self.table}{where}{order_sql}{limit_sql}"
            cursor = db.execute(sql, args)
            rows = [_decode_row(self.table, r) for r in cursor.fetchall()]
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
                        if update_assignments else
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
            sql = f"UPDATE {self.table} SET {set_sql}{where}"
            db.execute(sql, list(patch.values()) + args)
            db.commit()
            rows = db.execute(f"SELECT * FROM {self.table}{where}", args).fetchall()
            return SimpleNamespace(data=[_decode_row(self.table, r) for r in rows], count=None)

        if self._kind == "delete":
            where, args = self._where_clause()
            cur = db.execute(f"DELETE FROM {self.table}{where}", args)
            db.commit()
            return SimpleNamespace(data=[], count=cur.rowcount)

        raise RuntimeError(f"Unknown query kind: {self._kind}")


class FakeSupabaseClient:
    """Quacks like supabase-py's Client for our usage."""
    def table(self, name: str) -> _Query:
        return _Query(name)

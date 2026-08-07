"""Per-(company, provider) cooldown for sweep-persist enrichment/extraction
(app/db/sweep_persist_cooldown.py).

A lightweight in-memory stand-in for the Supabase client is used here rather
than the full tests/_fake_supabase.py harness (that requires the table to be
hand-mirrored into conftest._FAKE_SCHEMA, which kg_ingest_ledger — the
sibling this module follows — is not either): `in_cooldown`/`mark_run` both
accept an explicit `client=` for exactly this kind of direct test, mirroring
`app.db.kg_ingest_ledger`'s own testability contract.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from app.db import sweep_persist_cooldown as cd


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client):
        self._client = client
        self._filters: dict[str, object] = {}
        self._gt = None
        self._limit = None
        self._upsert_row = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gt(self, col, val):
        assert col == "last_run_at"
        self._gt = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def upsert(self, row, on_conflict=None):
        assert on_conflict == "enterprise_id,provider"
        self._upsert_row = row
        return self

    def execute(self):
        if self._upsert_row is not None:
            key = (self._upsert_row["enterprise_id"], self._upsert_row["provider"])
            self._client.rows[key] = self._upsert_row["last_run_at"]
            return _Resp([self._upsert_row])
        key = (self._filters.get("enterprise_id"), self._filters.get("provider"))
        last = self._client.rows.get(key)
        data = []
        if last is not None and (self._gt is None or last > self._gt):
            data = [{"provider": key[1]}]
        if self._limit is not None:
            data = data[: self._limit]
        return _Resp(data)


class _FakeClient:
    """rows: (enterprise_id, provider) -> last_run_at ISO string."""

    def __init__(self):
        self.rows: dict[tuple[str, str], str] = {}

    def table(self, name):
        assert name == "sweep_persist_cooldown"
        return _Query(self)


# ─────────────────────────── round-trip semantics ───────────────────────────


def test_mark_run_then_in_cooldown_true_within_the_window():
    client = _FakeClient()
    cd.mark_run("ent-A", "clickup", client=client)
    assert cd.in_cooldown("ent-A", "clickup", hours=6, client=client) is True


def test_in_cooldown_false_before_any_run_is_marked():
    client = _FakeClient()
    assert cd.in_cooldown("ent-A", "clickup", hours=6, client=client) is False


def test_in_cooldown_false_once_the_window_has_passed():
    client = _FakeClient()
    stale = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    client.rows[("ent-A", "clickup")] = stale
    assert cd.in_cooldown("ent-A", "clickup", hours=6, client=client) is False


def test_cooldown_is_scoped_per_provider():
    """AC-A2 applies per provider — marking clickup must not cool down
    confluence for the same company."""
    client = _FakeClient()
    cd.mark_run("ent-A", "clickup", client=client)
    assert cd.in_cooldown("ent-A", "confluence", hours=6, client=client) is False


def test_cooldown_is_scoped_per_enterprise():
    """No cross-tenant leak: company A's cooldown must not gate company B."""
    client = _FakeClient()
    cd.mark_run("ent-A", "clickup", client=client)
    assert cd.in_cooldown("ent-B", "clickup", hours=6, client=client) is False


def test_mark_run_is_upsert_not_insert_only():
    """A second mark_run for the same pair must not error and must refresh
    the window (proven indirectly: a stale row overwritten by a fresh
    mark_run reads back as in-cooldown)."""
    client = _FakeClient()
    stale = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    client.rows[("ent-A", "clickup")] = stale
    assert cd.in_cooldown("ent-A", "clickup", hours=6, client=client) is False

    cd.mark_run("ent-A", "clickup", client=client)
    assert cd.in_cooldown("ent-A", "clickup", hours=6, client=client) is True


# ─────────────────────────── fail-open (advisory only) ──────────────────────


def test_in_cooldown_fails_open_without_a_client(monkeypatch):
    class _Boom:
        def table(self, *a, **k):
            raise RuntimeError("no db")

    monkeypatch.setattr(cd, "require_client", lambda: _Boom())
    assert cd.in_cooldown("ent-A", "clickup", hours=6) is False


def test_mark_run_swallows_a_write_failure(monkeypatch):
    class _Boom:
        def table(self, *a, **k):
            raise RuntimeError("no db")

    monkeypatch.setattr(cd, "require_client", lambda: _Boom())
    cd.mark_run("ent-A", "clickup")  # must not raise


# ─────────────────────────── AC-A6 — persisted, not in-process ──────────────


def test_module_holds_no_in_process_cooldown_state():
    """AC-A6: the ONLY thing `in_cooldown` consults is the passed-in/require_
    client() store — there is no module-level dict/set this module could be
    quietly caching cooldown state in, which would not survive a restart
    (unlike the DB row `mark_run` writes)."""
    src = inspect.getsource(cd)
    assert "= {}" not in src
    assert "= set()" not in src

"""Tests for the SQLite → Supabase backfill script.

We exercise:
  - per-table row mappers correctly translate column renames + jsonb
    decoding
  - backfill_one() upserts in batches and reports correct row counts
  - backfill_one() in --dry-run reads but does not write
  - backfill_one() raises on Supabase failure (so a script run halts
    rather than silently dropping rows)
  - end-to-end main() against a seeded SQLite + mocked supabase client
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from scripts import backfill_sqlite_to_supabase as backfill


@pytest.fixture(autouse=True)
def reload_backfill_after_isolated_settings(isolated_settings):
    """Conftest's isolated_settings reloads app.config but not modules that
    captured `settings` at import time. The backfill script does — reload
    it here so settings.db_path points at the per-test SQLite file.
    """
    import importlib
    importlib.reload(backfill)
    yield


# ─────────────────────── row mappers ───────────────────────


def _row(d: dict) -> sqlite3.Row:
    """Build a sqlite3.Row from a dict via an in-memory query."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    cols = ", ".join(d.keys())
    placeholders = ", ".join("?" for _ in d)
    c.execute(f"CREATE TABLE t ({cols})")
    c.execute(f"INSERT INTO t ({cols}) VALUES ({placeholders})", tuple(d.values()))
    row = c.execute("SELECT * FROM t").fetchone()
    c.close()
    return row


def test_row_briefs_decodes_payload_and_bool(isolated_settings):
    r = _row({
        "id": 5,
        "dataset": "asurion",
        "generated_at": "2026-05-25T10:00:00+00:00",
        "week_label": "W21",
        "payload_json": json.dumps({"insights": [], "_schema_version": 3}),
        "is_current": 1,
    })
    out = backfill._row_briefs(r)
    assert out["id"] == 5
    assert out["dataset"] == "asurion"
    assert out["payload"] == {"insights": [], "_schema_version": 3}
    assert out["is_current"] is True


def test_row_ask_log_decodes_citations(isolated_settings):
    r = _row({
        "id": 1,
        "asked_at": "2026-05-25T10:00:00+00:00",
        "question": "why?",
        "answer": "because",
        "citations_json": json.dumps([{"source": "doc1"}]),
    })
    out = backfill._row_ask_log(r)
    assert out["citations"] == [{"source": "doc1"}]


def test_row_connections_decodes_config(isolated_settings):
    r = _row({
        "id": "abc",
        "provider": "github",
        "status": "active",
        "google_email": None,
        "scopes": "repo",
        "token_json_encrypted": "ENCRYPTED",
        "config_json": json.dumps({"folder": "x"}),
        "last_sync_at": None,
        "last_sync_error": None,
        "created_at": "2026-05-25T10:00:00+00:00",
        "updated_at": "2026-05-25T10:00:00+00:00",
    })
    out = backfill._row_connections(r)
    assert out["config"] == {"folder": "x"}
    assert out["token_json_encrypted"] == "ENCRYPTED"


def test_decode_json_handles_garbage(isolated_settings):
    assert backfill._decode_json(None, []) == []
    assert backfill._decode_json("", {}) == {}
    assert backfill._decode_json("not-json", {"fallback": True}) == {"fallback": True}


# ─────────────────────── backfill_one ───────────────────────


def _seed_briefs(db_path: str, n: int) -> None:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    for i in range(1, n + 1):
        c.execute(
            "INSERT INTO briefs (dataset, week_label, payload_json, is_current) "
            "VALUES (?, ?, ?, ?)",
            (f"acme{i}", f"W{i}", json.dumps({"i": i}), 1 if i == n else 0),
        )
    c.commit()
    c.close()


def test_backfill_one_dry_run_does_not_call_supabase(isolated_settings):
    _seed_briefs(str(isolated_settings["db_path"]), 3)
    fake_client = MagicMock()

    sqlite = backfill.open_sqlite()
    n = backfill.backfill_one(
        fake_client, sqlite, "briefs", "SELECT * FROM briefs",
        backfill._row_briefs, "id", dry_run=True,
    )
    assert n == 3
    fake_client.table.assert_not_called()


def test_backfill_one_writes_in_batches(isolated_settings):
    _seed_briefs(str(isolated_settings["db_path"]), 7)
    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    sqlite = backfill.open_sqlite()
    n = backfill.backfill_one(
        fake_client, sqlite, "briefs", "SELECT * FROM briefs",
        backfill._row_briefs, "id", dry_run=False, batch_size=3,
    )
    assert n == 7
    # 7 rows / batch=3 → 3 calls (3, 3, 1).
    assert fake_table.upsert.call_count == 3
    # Each call passes a list + on_conflict kwarg.
    first_call_args, first_call_kwargs = fake_table.upsert.call_args_list[0]
    assert len(first_call_args[0]) == 3
    assert first_call_kwargs["on_conflict"] == "id"


def test_backfill_one_empty_table_returns_zero(isolated_settings):
    fake_client = MagicMock()
    sqlite = backfill.open_sqlite()
    n = backfill.backfill_one(
        fake_client, sqlite, "briefs", "SELECT * FROM briefs",
        backfill._row_briefs, "id", dry_run=False,
    )
    assert n == 0
    fake_client.table.assert_not_called()


def test_backfill_one_raises_on_supabase_failure(isolated_settings):
    """If Supabase rejects a batch we must stop, not silently skip rows."""
    _seed_briefs(str(isolated_settings["db_path"]), 2)
    fake_table = MagicMock()
    fake_table.upsert.return_value.execute.side_effect = RuntimeError("nope")
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    sqlite = backfill.open_sqlite()
    with pytest.raises(RuntimeError):
        backfill.backfill_one(
            fake_client, sqlite, "briefs", "SELECT * FROM briefs",
            backfill._row_briefs, "id", dry_run=False,
        )


# ─────────────────────── main() integration ───────────────────────


def test_main_dry_run_does_not_require_supabase_client(isolated_settings):
    """Dry-run should never hit Supabase, so missing env should be fine."""
    _seed_briefs(str(isolated_settings["db_path"]), 2)
    with patch("scripts.backfill_sqlite_to_supabase.supabase_client", return_value=None):
        rc = backfill.main(["--dry-run", "--only", "briefs"])
    assert rc == 0


def test_main_real_run_requires_supabase_client(isolated_settings):
    with patch("scripts.backfill_sqlite_to_supabase.supabase_client", return_value=None):
        with pytest.raises(SystemExit):
            backfill.main(["--only", "briefs"])


def test_main_only_filter_rejects_unknown_table(isolated_settings):
    with pytest.raises(SystemExit):
        backfill.main(["--only", "nonexistent_table"])


def test_main_filters_tables_by_only(isolated_settings):
    _seed_briefs(str(isolated_settings["db_path"]), 1)
    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table
    with patch("scripts.backfill_sqlite_to_supabase.supabase_client", return_value=fake_client):
        backfill.main(["--only", "briefs"])
    # Only 'briefs' should have been touched — not prds, datasets, etc.
    called_tables = [call.args[0] for call in fake_client.table.call_args_list]
    assert called_tables == ["briefs"]

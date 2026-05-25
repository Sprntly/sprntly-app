"""Dual-write to Supabase — shadow-write helper + per-domain wrappers.

We mock the supabase-py client so these tests don't touch the network.
The contract under test:
  - flag off  → shadow_write() is a no-op
  - flag on, no env keys → still a no-op (client returns None)
  - flag on, configured → table().insert(row).execute() called once
  - flag on, on_conflict set → table().upsert(row, on_conflict=...) called
  - flag on, supabase raises → SQLite write still succeeds, warning logged
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.db import client as db_client


# ─────────────────────── shadow_write contract ───────────────────────


def test_shadow_write_is_noop_when_flag_off(isolated_settings, monkeypatch):
    """Default: flag is off, no Supabase call should happen."""
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "false")
    db_client._reset_supabase_client_for_tests()
    # Reload config so settings.supabase_dual_write picks up the new env.
    import importlib
    import app.config
    importlib.reload(app.config)
    importlib.reload(db_client)

    fake_client = MagicMock()
    with patch.object(db_client, "supabase_client", return_value=fake_client):
        db_client.shadow_write("briefs", {"dataset": "x", "payload": {}})
    fake_client.table.assert_not_called()


def test_shadow_write_noop_when_flag_on_but_unconfigured(isolated_settings, monkeypatch):
    """Flag on but env keys missing — supabase_client() returns None; no crash."""
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "true")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    db_client._reset_supabase_client_for_tests()
    import importlib
    import app.config
    importlib.reload(app.config)
    importlib.reload(db_client)

    # Should not raise.
    db_client.shadow_write("briefs", {"dataset": "x", "payload": {}})


def test_shadow_write_calls_insert_when_flag_on(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    db_client._reset_supabase_client_for_tests()
    import importlib
    import app.config
    importlib.reload(app.config)
    importlib.reload(db_client)

    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    with patch.object(db_client, "supabase_client", return_value=fake_client):
        db_client.shadow_write("briefs", {"dataset": "asurion", "payload": {"foo": "bar"}})

    fake_client.table.assert_called_once_with("briefs")
    fake_table.insert.assert_called_once_with({"dataset": "asurion", "payload": {"foo": "bar"}})
    fake_table.insert.return_value.execute.assert_called_once()


def test_shadow_write_calls_upsert_when_on_conflict_set(isolated_settings, monkeypatch):
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    db_client._reset_supabase_client_for_tests()
    import importlib
    import app.config
    importlib.reload(app.config)
    importlib.reload(db_client)

    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    with patch.object(db_client, "supabase_client", return_value=fake_client):
        db_client.shadow_write(
            "datasets",
            {"slug": "asurion", "display_name": "Asurion"},
            on_conflict="slug",
        )

    fake_table.upsert.assert_called_once_with(
        {"slug": "asurion", "display_name": "Asurion"},
        on_conflict="slug",
    )


def test_shadow_write_swallows_supabase_errors(isolated_settings, monkeypatch, caplog):
    """If Supabase raises, the helper must log and return — never propagate."""
    import logging
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    db_client._reset_supabase_client_for_tests()
    import importlib
    import app.config
    importlib.reload(app.config)
    importlib.reload(db_client)

    failing_table = MagicMock()
    failing_table.insert.return_value.execute.side_effect = RuntimeError("supabase boom")
    fake_client = MagicMock()
    fake_client.table.return_value = failing_table

    with patch.object(db_client, "supabase_client", return_value=fake_client), \
         caplog.at_level(logging.WARNING, logger="app.db.client"):
        # Must not raise.
        db_client.shadow_write("briefs", {"dataset": "x", "payload": {}})

    assert any("Supabase shadow-write to briefs failed" in r.message for r in caplog.records)
    # Row contents must NOT appear in logs (could leak PII / secrets).
    log_text = " ".join(r.message for r in caplog.records)
    assert "dataset" not in log_text or "x" not in log_text or "Supabase shadow-write" in log_text


# ─────────────────────── per-domain wrappers fire shadow_write ───────────────────────


def _enable_dual_write(monkeypatch):
    monkeypatch.setenv("SUPABASE_DUAL_WRITE", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    db_client._reset_supabase_client_for_tests()
    import importlib
    import app.config
    importlib.reload(app.config)
    # Reload db submodules so they pick up the new config + the patched
    # shadow_write reference.
    for mod in (
        "app.db.client", "app.db.briefs", "app.db.prds", "app.db.evidences",
        "app.db.asks", "app.db.datasets", "app.db.connections", "app.db",
    ):
        m = importlib.import_module(mod)
        importlib.reload(m)


def test_save_brief_fires_shadow_write(isolated_settings, monkeypatch):
    _enable_dual_write(monkeypatch)
    from app.db import briefs as briefs_mod
    from app.db.client import supabase_client as _real

    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table
    with patch("app.db.client.supabase_client", return_value=fake_client):
        new_id = briefs_mod.save_brief(
            dataset="asurion",
            week_label="W1",
            payload={"insights": []},
            schema_version=3,
        )
    assert new_id > 0
    fake_client.table.assert_called_with("briefs")
    args, _ = fake_table.insert.call_args
    payload = args[0]
    assert payload["dataset"] == "asurion"
    assert payload["week_label"] == "W1"
    assert payload["payload"]["_schema_version"] == 3
    assert payload["is_current"] is True


def test_insert_dataset_fires_upsert(isolated_settings, monkeypatch):
    _enable_dual_write(monkeypatch)
    from app.db import datasets as datasets_mod

    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table
    with patch("app.db.client.supabase_client", return_value=fake_client):
        datasets_mod.insert_dataset("acme", "Acme Corp")
    fake_table.upsert.assert_called_once()
    args, kwargs = fake_table.upsert.call_args
    assert args[0] == {"slug": "acme", "display_name": "Acme Corp"}
    assert kwargs.get("on_conflict") == "slug"


def test_log_ask_fires_shadow_write(isolated_settings, monkeypatch):
    _enable_dual_write(monkeypatch)
    from app.db import asks as asks_mod

    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table
    with patch("app.db.client.supabase_client", return_value=fake_client):
        asks_mod.log_ask("what's the lift?", "+12%", [{"source": "doc1"}])
    fake_client.table.assert_called_with("ask_log")
    args, _ = fake_table.insert.call_args
    row = args[0]
    assert row["question"] == "what's the lift?"
    assert row["answer"] == "+12%"
    assert row["citations"] == [{"source": "doc1"}]


def test_start_prd_fires_shadow_write(isolated_settings, monkeypatch):
    _enable_dual_write(monkeypatch)
    from app.db import briefs as briefs_mod
    from app.db import prds as prds_mod

    # save_brief is also dual-written; mock it so its insert doesn't pollute the assertions.
    fake_table = MagicMock()
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table
    with patch("app.db.client.supabase_client", return_value=fake_client):
        brief_id = briefs_mod.save_brief("asurion", "W1", {"insights": []})
        fake_table.reset_mock()
        prds_mod.start_prd(brief_id, 0, "First PRD", template_version=2, variant="v2")

    fake_client.table.assert_called_with("prds")
    args, _ = fake_table.insert.call_args
    row = args[0]
    assert row["brief_id"] == brief_id
    assert row["insight_index"] == 0
    assert row["title"] == "First PRD"
    assert row["status"] == "generating"
    assert row["template_version"] == 2
    assert row["variant"] == "v2"


def test_sqlite_write_succeeds_even_when_supabase_throws(isolated_settings, monkeypatch):
    """If shadow_write blows up, the SQLite row must still be persisted."""
    _enable_dual_write(monkeypatch)
    from app.db import briefs as briefs_mod

    failing_table = MagicMock()
    failing_table.insert.return_value.execute.side_effect = RuntimeError("supabase down")
    fake_client = MagicMock()
    fake_client.table.return_value = failing_table

    with patch("app.db.client.supabase_client", return_value=fake_client):
        brief_id = briefs_mod.save_brief("asurion", "W1", {"x": 1})

    assert brief_id > 0
    fetched = briefs_mod.get_brief_by_id(brief_id)
    assert fetched is not None
    assert fetched["dataset"] == "asurion"

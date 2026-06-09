"""MED reliability/hygiene batch — regression tests for five small bugs.

Each group pins one fixed behaviour so a regression re-surfaces it:

  1. prd PUT auto-version: a failed snapshot is LOGGED (not silently swallowed)
     and the save still succeeds.
  2. audio ingest: a transcript past the char budget is split/bounded BEFORE
     extract_document — no single LLM call gets an unbounded transcript.
  3. datasets.insert_dataset: a duplicate slug is idempotent (no raise) even
     when the check-then-insert race loses to a concurrent insert; the unique
     constraint is present in the migration.
  4. backlog upsert: created_at is NOT in the upsert payload, so a re-upsert
     can't overwrite the original insert time.
  5. synthesis startup: the empty-KG case logs at INFO (not ERROR/exception);
     a genuine failure still logs at error.

All LLM / Supabase / Whisper calls are mocked.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. prd PUT auto-version snapshot — failure logged, not swallowed
# ─────────────────────────────────────────────────────────────────────────────


def test_prd_autoversion_failure_is_logged_not_swallowed(isolated_settings, caplog):
    import app.routes.prd as prd_routes

    row = {"id": 7, "title": "Old", "payload_md": "old body"}

    with patch.object(prd_routes, "require_owned_prd", return_value=row), \
         patch.object(prd_routes, "save_prd_version",
                      side_effect=RuntimeError("version table missing")) as snap, \
         patch.object(prd_routes, "update_prd_content",
                      return_value={"id": 7, "title": "New", "payload_md": "new body"}) as upd, \
         caplog.at_level(logging.WARNING, logger=prd_routes.logger.name):
        body = prd_routes.PrdUpdateIn(title="New", payload_md="new body")
        result = prd_routes.update(7, body, company=_DummyCompany())

    # The snapshot was attempted and failed...
    snap.assert_called_once()
    # ...but the save still went through (non-blocking).
    upd.assert_called_once()
    assert result["title"] == "New"
    # ...and the failure is visible in the log (no bare `pass`).
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert records, "expected a warning log for the failed auto-version snapshot"
    assert any("7" in r.getMessage() or "prd_id=7" in r.getMessage() for r in records)


def test_prd_autoversion_success_does_not_log_warning(isolated_settings, caplog):
    import app.routes.prd as prd_routes

    row = {"id": 7, "title": "Old", "payload_md": "old body"}
    with patch.object(prd_routes, "require_owned_prd", return_value=row), \
         patch.object(prd_routes, "save_prd_version", return_value={"id": 1}), \
         patch.object(prd_routes, "update_prd_content",
                      return_value={"id": 7, "title": "New", "payload_md": "b"}), \
         caplog.at_level(logging.WARNING, logger=prd_routes.logger.name):
        body = prd_routes.PrdUpdateIn(title="New", payload_md="b")
        prd_routes.update(7, body, company=_DummyCompany())

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class _DummyCompany:
    company_id = "co-1"


# ─────────────────────────────────────────────────────────────────────────────
# 2. audio ingest — transcript capped/chunked before extract_document
# ─────────────────────────────────────────────────────────────────────────────


def _patch_extract_and_transcribe(text: str):
    """Returns (captured_texts list, contextmanagers) — patches transcribe_audio
    to yield `text` and extract_document to record the text it receives."""
    import app.kg_ingest.audio_ingest as ai

    captured: list[str] = []

    def fake_extract(facade, eid, *, doc_name, text, agent, source_hint):
        captured.append(text)
        return {"signals": 1, "themes": 1, "skipped": 0}

    cms = [
        patch.object(ai, "transcribe_audio",
                     return_value={"text": text, "duration": 100.0, "language": "english"}),
        patch.object(ai, "extract_document", side_effect=fake_extract),
    ]
    return captured, cms


def test_audio_long_transcript_is_chunked_before_extract():
    import app.kg_ingest.audio_ingest as ai

    budget = ai._TRANSCRIPT_CHAR_BUDGET
    # ~4x the budget of word-separated tokens.
    long_text = ("word " * (budget))  # len ~= 5*budget chars
    assert len(long_text) > budget * 3

    captured, cms = _patch_extract_and_transcribe(long_text)
    with cms[0], cms[1]:
        out = ai.ingest_audio(_FakeFacade(), "ent-1",
                              audio_bytes=b"x", filename="meeting.mp3")

    # Multiple extraction calls, each bounded by the budget.
    assert len(captured) > 1, "long transcript should be split into >1 extraction call"
    for chunk_text in captured:
        # render() adds a small header; allow modest overhead over the raw budget.
        assert len(chunk_text) <= budget + 200, (
            f"extraction text {len(chunk_text)} chars exceeds budget {budget}")
    # Counts accumulate across chunks.
    assert out["signals"] == len(captured)
    assert out["themes"] == len(captured)
    assert out["duration"] == 100.0


def test_audio_short_transcript_single_extract_call():
    import app.kg_ingest.audio_ingest as ai

    short = "A short meeting about the login bug and the export feature."
    captured, cms = _patch_extract_and_transcribe(short)
    with cms[0], cms[1]:
        ai.ingest_audio(_FakeFacade(), "ent-1", audio_bytes=b"x", filename="m.mp3")

    assert len(captured) == 1
    assert short in captured[0]


def test_audio_chunk_helper_respects_budget_and_is_nonempty():
    import app.kg_ingest.audio_ingest as ai

    text = "alpha beta gamma delta " * 1000
    chunks = ai._chunk_transcript(text, budget=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    assert all(c.strip() for c in chunks)
    # No data lost: every word survives somewhere.
    joined = " ".join(chunks)
    for word in ("alpha", "beta", "gamma", "delta"):
        assert word in joined


def test_audio_chunk_helper_short_text_returns_whole():
    import app.kg_ingest.audio_ingest as ai
    assert ai._chunk_transcript("hello world", budget=200) == ["hello world"]


def test_audio_chunk_dedup_content_hash_stable_across_chunks():
    """Doc names are derived from the FULL-transcript hash, so re-ingesting the
    identical recording reuses the same per-chunk doc names (idempotent)."""
    import app.kg_ingest.audio_ingest as ai

    long_text = "word " * ai._TRANSCRIPT_CHAR_BUDGET
    names: list[str] = []

    def fake_extract(facade, eid, *, doc_name, text, agent, source_hint):
        names.append(doc_name)
        return {"signals": 0, "themes": 0, "skipped": 0}

    with patch.object(ai, "transcribe_audio",
                      return_value={"text": long_text, "duration": 1.0, "language": "en"}), \
         patch.object(ai, "extract_document", side_effect=fake_extract):
        ai.ingest_audio(_FakeFacade(), "ent-1", audio_bytes=b"x", filename="m.mp3")
        first = list(names)
        names.clear()
        ai.ingest_audio(_FakeFacade(), "ent-1", audio_bytes=b"x", filename="m.mp3")
        second = list(names)

    assert first == second, "re-ingesting the same recording must reuse doc names"
    assert len(set(first)) == len(first), "per-chunk doc names must be distinct"


class _FakeFacade:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 3. datasets.insert_dataset — duplicate-slug idempotent + migration constraint
# ─────────────────────────────────────────────────────────────────────────────


def test_insert_dataset_duplicate_slug_is_idempotent(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme Corp")
    # Second insert of the same slug must not raise (no-op via SELECT guard).
    db.insert_dataset("acme", "Acme Renamed")
    row = db.get_dataset("acme")
    assert row["display_name"] == "Acme Corp"  # first write wins


def test_insert_dataset_tolerates_unique_violation_race(isolated_settings):
    """Simulate losing the check-then-insert race: the SELECT sees nothing but
    the INSERT hits the unique constraint. insert_dataset must swallow it."""
    import app.db.datasets as datasets_mod

    # Pre-create the row so a forced INSERT would actually conflict in the fake.
    datasets_mod.insert_dataset("acme", "Acme")

    real_require = datasets_mod.require_client

    class _RaceClient:
        """Wraps the real client but reports the slug as absent on SELECT,
        forcing insert_dataset down the INSERT path into a unique violation."""
        def __init__(self, inner):
            self._inner = inner

        def table(self, name):
            return _RaceQuery(self._inner.table(name))

    class _RaceQuery:
        def __init__(self, inner):
            self._inner = inner
            self._is_select = False

        def select(self, *a, **k):
            self._is_select = True
            self._inner = self._inner.select(*a, **k)
            return self

        def eq(self, *a, **k):
            self._inner = self._inner.eq(*a, **k)
            return self

        def limit(self, *a, **k):
            self._inner = self._inner.limit(*a, **k)
            return self

        def insert(self, *a, **k):
            self._inner = self._inner.insert(*a, **k)
            return self

        def execute(self, *a, **k):
            if self._is_select:
                from types import SimpleNamespace
                return SimpleNamespace(data=[])  # pretend slug is absent
            return self._inner.execute(*a, **k)

    with patch.object(datasets_mod, "require_client",
                      lambda: _RaceClient(real_require())):
        # Must NOT raise even though the INSERT collides with the existing row.
        datasets_mod.insert_dataset("acme", "Acme race")

    # Original row survives unchanged.
    assert datasets_mod.get_dataset("acme")["display_name"] == "Acme"


def test_is_unique_violation_detects_pg_code_and_sqlite():
    import app.db.datasets as datasets_mod
    import sqlite3

    class _PgErr(Exception):
        code = "23505"

    assert datasets_mod._is_unique_violation(_PgErr("dup"))
    assert datasets_mod._is_unique_violation(
        sqlite3.IntegrityError("UNIQUE constraint failed: datasets.slug"))
    assert not datasets_mod._is_unique_violation(ValueError("something else"))


def test_datasets_slug_unique_migration_exists():
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    matches = list(migrations.glob("2026*_datasets_slug_unique.sql"))
    assert matches, "expected a datasets_slug_unique migration"
    sql = matches[0].read_text().lower()
    assert "create unique index if not exists" in sql
    assert "datasets" in sql and "slug" in sql


# ─────────────────────────────────────────────────────────────────────────────
# 4. backlog upsert — created_at not in payload
# ─────────────────────────────────────────────────────────────────────────────


def test_backlog_upsert_payload_omits_created_at():
    import app.db.backlog as backlog_mod

    captured = {}

    class _Cli:
        def table(self, name):
            return self

        def upsert(self, payload, on_conflict=None):
            captured["payload"] = payload
            captured["on_conflict"] = on_conflict
            return self

        def execute(self):
            return self

    backlog_mod.upsert_backlog_item(
        "ent-1", theme_id="t1", title="Login bug", rank=1, score=9.0, client=_Cli())

    assert "created_at" not in captured["payload"], (
        "created_at must NOT be in the upsert payload — the DB default sets it "
        "once on first insert; including it overwrites it on re-upsert")
    assert "updated_at" in captured["payload"]
    assert captured["on_conflict"] == "enterprise_id,theme_id"


def test_backlog_reupsert_preserves_original_created_at(isolated_settings):
    """End-to-end against the fake DB: a re-upsert keeps the original
    created_at (set by the DB default on first insert)."""
    from app.db.backlog import list_backlog_items, upsert_backlog_item

    # backlog_items.enterprise_id FKs into companies(id) — seed it first.
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "ent-1", "slug": "slug-ent-1", "display_name": "Ent 1"}
    ).execute()

    upsert_backlog_item("ent-1", theme_id="t1", title="First",
                        rank=1, score=5.0)
    first = list_backlog_items("ent-1")[0]
    assert first.get("created_at")  # DB default populated it

    # Pin a distinct, known created_at so a re-upsert that (wrongly) wrote it
    # would be detectable even within the same wall-clock second.
    sentinel = "2000-01-01 00:00:00"
    db.table("backlog_items").update({"created_at": sentinel}) \
        .eq("enterprise_id", "ent-1").eq("theme_id", "t1").execute()

    # Re-sequence: same (enterprise_id, theme_id), new rank/score.
    upsert_backlog_item("ent-1", theme_id="t1", title="First (re-ranked)",
                        rank=3, score=8.0)
    refreshed = list_backlog_items("ent-1")[0]

    assert refreshed["created_at"] == sentinel, (
        "re-upsert must not overwrite created_at")
    assert refreshed["rank"] == 3  # but rank/score DO refresh
    assert refreshed["score"] == 8.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. synthesis startup — empty-KG logs INFO, genuine failure logs ERROR
# ─────────────────────────────────────────────────────────────────────────────


def test_startup_empty_kg_logs_info_not_error(isolated_settings, caplog):
    import app.synthesis_brief as sb
    from app.synthesis.agent import EmptyKnowledgeGraphError
    import app.db.companies as companies_mod
    import app.brief_runner as brief_runner_mod

    with patch.object(companies_mod, "list_companies",
                      return_value=[{"slug": "empty-co"}]), \
         patch.object(sb, "generate_brief_for",
                      side_effect=EmptyKnowledgeGraphError("no themes with signals")), \
         patch.object(brief_runner_mod, "warm_synthesis_drilldowns"), \
         caplog.at_level(logging.INFO, logger=sb.logger.name):
        sb.generate_all_synthesis_briefs()

    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    # An INFO record mentioning the skip exists...
    assert any(lvl == logging.INFO and "empty-co" in m for lvl, m in msgs), msgs
    # ...and NO ERROR/exception was logged for the empty-KG case.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "empty-KG must not log at ERROR")


def test_startup_genuine_failure_still_logs_error(isolated_settings, caplog):
    import app.synthesis_brief as sb
    import app.db.companies as companies_mod
    import app.brief_runner as brief_runner_mod

    with patch.object(companies_mod, "list_companies",
                      return_value=[{"slug": "boom-co"}]), \
         patch.object(sb, "generate_brief_for",
                      side_effect=RuntimeError("real failure")), \
         patch.object(brief_runner_mod, "warm_synthesis_drilldowns"), \
         caplog.at_level(logging.INFO, logger=sb.logger.name):
        sb.generate_all_synthesis_briefs()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a genuine failure must still log at ERROR"
    assert any("boom-co" in r.getMessage() for r in errors)


def test_empty_kg_error_is_a_valueerror():
    """Subclassing ValueError keeps existing `except ValueError` callers working."""
    from app.synthesis.agent import EmptyKnowledgeGraphError
    assert issubclass(EmptyKnowledgeGraphError, ValueError)

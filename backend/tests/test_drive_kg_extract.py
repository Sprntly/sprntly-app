"""Tests for Google Drive → KG extraction (kg_ingest.drive_extract).

Drive bypasses the token-based PULLERS registry: google_drive_sync hands
changed files here as DriveDocs, and each file is chunk-extracted as its own
document (origin="upload" — Drive is a documents source) plus a per-file
kg_source provenance row.
kg_file_mtime advances only for fully-extracted files, so failures retry on
the next sync.
"""
from __future__ import annotations

import json

from app.kg_ingest import drive_extract
from app.kg_ingest.drive_extract import (
    DriveDoc,
    _chunks,
    _record_kg_result,
    extract_drive_docs,
)


class FakeFacade:
    def __init__(self):
        self.sources = []

    def create_source(self, enterprise_id, source):
        self.sources.append(source)
        return source


def _doc(**kw):
    base = dict(file_id="fileaaaa01", name="roadmap", modified="2026-07-01T00:00:00Z",
                text="Customers churn over missing SSO.", mime="text/plain",
                link="https://drive.google.com/x")
    base.update(kw)
    return DriveDoc(**base)


# ────────────────────────────── chunking ──────────────────────────────

def test_chunks_short_text_is_single_chunk():
    assert _chunks("one\ntwo\n") == ["one\ntwo\n"]


def test_chunks_splits_on_line_boundaries():
    line = "x" * 1000 + "\n"
    text = line * 14  # 14k chars > 2 × 6k budget
    parts = _chunks(text)
    assert len(parts) == 3
    assert "".join(parts) == text
    assert all(len(p) <= 6000 for p in parts)


def test_chunks_overlong_single_line_kept_whole():
    text = "y" * 9000
    assert _chunks(text) == [text]


# ─────────────────────────── extract_drive_docs ───────────────────────────

def test_extract_writes_upload_origin_and_source_row(monkeypatch):
    seen = []

    def fake_extract(facade, company_id, *, doc_name, text, agent=None,
                     source_hint=None, origin=None):
        seen.append({"doc_name": doc_name, "text": text, "agent": agent,
                     "source_hint": source_hint, "origin": origin})
        return {"signals": 2, "themes": 1, "skipped": 0}

    monkeypatch.setattr(drive_extract, "extract_document", fake_extract)
    facade = FakeFacade()

    r = extract_drive_docs(facade, "co-1", [_doc()])

    # "upload", NOT "connector": Drive is a documents source, and the only
    # consumer of origin is the brief gate's upload-only relaxation — a
    # connector origin here would disable it for uploads+Drive tenants.
    assert seen[0]["origin"] == "upload"
    assert seen[0]["agent"] == "ingest:google_drive"
    assert "Google Drive" in seen[0]["source_hint"]
    assert seen[0]["doc_name"] == "roadmap"  # single chunk → bare name
    assert r["files"] == 1 and r["signals"] == 2
    assert r["ok"] == {"fileaaaa01": "2026-07-01T00:00:00Z"}
    assert r["errors"] == []

    # Per-file provenance row in the source registry.
    src = facade.sources[0]
    assert src.source_type == "google_drive"
    assert src.label == "roadmap"
    assert src.config["file_id"] == "fileaaaa01"
    assert src.config["link"] == "https://drive.google.com/x"


def test_extract_chunks_large_doc_with_part_names(monkeypatch):
    names = []
    monkeypatch.setattr(
        drive_extract, "extract_document",
        lambda f, c, *, doc_name, **kw: names.append(doc_name)
        or {"signals": 1, "themes": 0, "skipped": 0},
    )
    text = ("z" * 1000 + "\n") * 13  # → 3 chunks
    r = extract_drive_docs(FakeFacade(), "co-1", [_doc(text=text)])
    assert names == ["roadmap (part 1/3)", "roadmap (part 2/3)",
                     "roadmap (part 3/3)"]
    assert r["signals"] == 3


def test_extract_truncates_at_max_chars(monkeypatch):
    total = []
    monkeypatch.setattr(
        drive_extract, "extract_document",
        lambda f, c, *, text, **kw: total.append(len(text))
        or {"signals": 0, "themes": 0, "skipped": 0},
    )
    text = ("w" * 1000 + "\n") * 100  # 100k chars > 60k cap
    extract_drive_docs(FakeFacade(), "co-1", [_doc(text=text)])
    assert sum(total) <= drive_extract._MAX_KG_CHARS


def test_extract_error_isolated_per_file(monkeypatch):
    def flaky(facade, company_id, *, doc_name, **kw):
        if doc_name.startswith("bad"):
            raise RuntimeError("llm down")
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(drive_extract, "extract_document", flaky)
    facade = FakeFacade()
    r = extract_drive_docs(facade, "co-1", [
        _doc(file_id="badfile001", name="bad-doc"),
        _doc(file_id="goodfile01", name="good-doc"),
    ])
    # The bad file is reported and NOT marked ok (so it retries next sync);
    # the good file still lands with its source row.
    assert r["ok"] == {"goodfile01": "2026-07-01T00:00:00Z"}
    assert len(r["errors"]) == 1 and "bad-doc" in r["errors"][0]
    assert [s.label for s in facade.sources] == ["good-doc"]


def test_extract_source_row_id_stable_across_versions(monkeypatch):
    monkeypatch.setattr(
        drive_extract, "extract_document",
        lambda *a, **kw: {"signals": 0, "themes": 0, "skipped": 0},
    )
    facade = FakeFacade()
    extract_drive_docs(facade, "co-1", [_doc(modified="v1")])
    extract_drive_docs(facade, "co-1", [_doc(modified="v2", text="edited")])
    # Same file → same upsert id (one registry row per file, not per edit).
    assert facade.sources[0].id == facade.sources[1].id
    assert facade.sources[1].config["modified"] == "v2"


# ─────────────────────────── _record_kg_result ───────────────────────────

def _fake_db(monkeypatch, config: dict):
    state = {
        "row": {"config_json": json.dumps(config)},
        "patched": None, "sync": None,
    }
    monkeypatch.setattr(drive_extract.db, "get_connection",
                        lambda cid, prov: state["row"])
    monkeypatch.setattr(
        drive_extract.db, "patch_connection_config",
        lambda cid, prov, cfg: state.update(patched=cfg) or state["row"],
    )
    monkeypatch.setattr(
        drive_extract.db, "update_connection_sync",
        lambda cid, prov, **kw: state.update(sync=kw),
    )
    return state


def test_record_advances_kg_mtime_only_for_ok_files(monkeypatch):
    state = _fake_db(monkeypatch, {"kg_file_mtime": {"oldfile001": "t0"}})
    _record_kg_result("co-1", {"newfile001": "t1"}, errors=[])
    assert state["patched"]["kg_file_mtime"] == {
        "oldfile001": "t0", "newfile001": "t1"}
    # No errors → no error stamp (never clobber the corpus sync's stamp).
    assert state["sync"] is None


def test_record_stamps_error_without_advancing_failed_files(monkeypatch):
    state = _fake_db(monkeypatch, {})
    _record_kg_result("co-1", {}, errors=["roadmap: boom"])
    assert state["patched"] is None
    assert "1 file(s) failed" in state["sync"]["last_sync_error"]


def test_record_never_raises(monkeypatch):
    monkeypatch.setattr(drive_extract.db, "get_connection",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("db")))
    _record_kg_result("co-1", {"f": "t"}, errors=[])  # must not raise


# ────────────────────────── kickoff_drive_extract ──────────────────────────

def test_kickoff_starts_daemon_thread(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(drive_extract.threading, "Thread", FakeThread)
    docs = [_doc()]
    assert drive_extract.kickoff_drive_extract("co-1", docs) is True
    assert started["started"] and started["daemon"] is True
    assert started["args"] == ("co-1", docs)


def test_kickoff_empty_docs_is_noop():
    assert drive_extract.kickoff_drive_extract("co-1", []) is False


def test_kickoff_never_raises_on_thread_failure(monkeypatch):
    monkeypatch.setattr(
        drive_extract.threading, "Thread",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no threads")),
    )
    assert drive_extract.kickoff_drive_extract("co-1", [_doc()]) is False

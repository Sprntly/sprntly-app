"""Google Drive → document catalog registration, and its retry semantics.

Drive is the ONE writer where a registration failure must NOT be swallowed
locally. Everywhere else "log and continue" is right; here it is a data-loss
bug, and the test that proves the difference is
`test_a_registration_failure_does_not_advance_the_file_watermark`.

The mechanism, and why it is subtle: `extract_drive_docs` isolates errors per
file, and a file only enters `ok` — the map `_record_kg_result` uses to
advance `kg_file_mtime` — when its whole body completed. Drive re-fetches a
file only when its `modifiedTime` changes. So a locally-caught registration
error would mark the file successfully extracted, advance its watermark, and
leave it PERMANENTLY unregistered: never retried until a human edits the file
in Drive. Letting the error propagate to the existing per-file handler costs
one re-extraction on the next sync and costs nothing else — the other files in
the batch still succeed, which is the isolation guarantee that matters.
"""
from __future__ import annotations

import json

from app.kg_ingest import drive_extract
from app.kg_ingest.drive_extract import DriveDoc, extract_drive_docs


class FakeFacade:
    def __init__(self):
        self.sources = []

    def create_source(self, enterprise_id, source):
        self.sources.append(source)
        return source


def _doc(**kw):
    base = dict(
        file_id="fileaaaa01", name="Q3 roadmap", modified="2026-07-01T00:00:00Z",
        text="Customers churn over missing SSO.", mime="text/plain",
        link="https://drive.google.com/file/d/fileaaaa01",
    )
    base.update(kw)
    return DriveDoc(**base)


def _stub_extraction(monkeypatch):
    monkeypatch.setattr(
        drive_extract, "extract_document",
        lambda *a, **k: {"signals": 1, "themes": 1, "skipped": 0},
    )


# ────────────────────────── T6: the Drive writer ──────────────────────────


def test_drive_registers_a_catalog_row_pointing_at_the_file(
    isolated_settings, monkeypatch
):
    """T6/AC11: provider, scope and external id are the Drive file's own, and
    the row is a POINTER — title, link, date, summary — with NO body."""
    _stub_extraction(monkeypatch)
    calls = []
    monkeypatch.setattr(
        drive_extract.document_catalog, "register_document",
        lambda company_id, **kw: calls.append((company_id, kw)),
    )

    extract_drive_docs(FakeFacade(), "co-1", [_doc()])

    assert len(calls) == 1
    company_id, kw = calls[0]
    assert company_id == "co-1"
    assert kw["provider"] == "google_drive"
    assert kw["external_id"] == "fileaaaa01"
    assert kw["title"] == "Q3 roadmap"
    assert kw["doc_date"] == "2026-07-01T00:00:00Z"
    assert kw["url"] == "https://drive.google.com/file/d/fileaaaa01"
    # Company-scoped, never session-scoped: a Drive file belongs to the
    # workspace, not to one chat.
    assert kw.get("conversation_id") is None
    # No copy of the file. Drive content already lives in the dataset corpus
    # (google_drive_sync → datasets.ingest_file), so storing it again here
    # would be duplicate storage of a customer's documents.
    assert kw.get("body_text") is None
    # The summary and hash still read the whole converted markdown.
    assert kw["get_text"]() == "Customers churn over missing SSO."
    assert kw["content_hash"]


def test_drive_hashes_the_full_markdown_not_the_extraction_slice(
    isolated_settings, monkeypatch
):
    """The KG leg truncates at _MAX_KG_CHARS. The catalog hash must not: an
    edit past that point has to re-hash, or the summary freezes at an old
    version exactly as it would for a long Confluence page."""
    _stub_extraction(monkeypatch)
    calls = []
    monkeypatch.setattr(
        drive_extract.document_catalog, "register_document",
        lambda company_id, **kw: calls.append(kw),
    )

    shared = "y" * (drive_extract._MAX_KG_CHARS + 1_000)
    extract_drive_docs(FakeFacade(), "co-1", [_doc(text=shared + "ENDING A")])
    extract_drive_docs(FakeFacade(), "co-1", [_doc(text=shared + "ENDING B")])

    assert calls[0]["content_hash"] != calls[1]["content_hash"]


# ───────────────── T7a: retry semantics (AC11a, load-bearing) ─────────────


def test_a_registration_failure_does_not_advance_the_file_watermark(
    isolated_settings, monkeypatch
):
    """T7a (AC11a). File X's registration raises; file Y's succeeds.

    X must be EXCLUDED from `ok` — so `_record_kg_result` never advances its
    `kg_file_mtime` and the next sync re-fetches it — while Y's signals and
    watermark land normally. A locally-caught failure passes every other
    assertion in this file and fails exactly this one, which is the whole
    reason it exists.
    """
    _stub_extraction(monkeypatch)

    def _register(company_id, **kw):
        if kw["external_id"] == "file-x":
            raise RuntimeError("catalog write failed")

    monkeypatch.setattr(
        drive_extract.document_catalog, "register_document", _register
    )

    result = extract_drive_docs(
        FakeFacade(), "co-1",
        [_doc(file_id="file-x", name="X", modified="2026-07-02T00:00:00Z"),
         _doc(file_id="file-y", name="Y", modified="2026-07-03T00:00:00Z")],
    )

    assert "file-x" not in result["ok"], (
        "a file whose catalog registration failed was marked successfully "
        "extracted — its kg_file_mtime would advance and Drive, which only "
        "re-fetches on a changed modifiedTime, would never retry it"
    )
    assert result["ok"] == {"file-y": "2026-07-03T00:00:00Z"}
    assert result["files"] == 1
    assert len(result["errors"]) == 1
    assert "X" in result["errors"][0]

    # And the watermark itself: only Y's advances.
    stored = {}
    monkeypatch.setattr(
        drive_extract.db, "get_connection",
        lambda cid, provider: {"config_json": json.dumps(
            {"kg_file_mtime": {"file-x": "2026-06-01T00:00:00Z"}}
        )},
    )
    monkeypatch.setattr(
        drive_extract.db, "patch_connection_config",
        lambda cid, provider, config: stored.update(config),
    )
    monkeypatch.setattr(
        drive_extract.db, "update_connection_sync", lambda *a, **k: None
    )
    drive_extract._record_kg_result("co-1", result["ok"], result["errors"])

    assert stored["kg_file_mtime"]["file-x"] == "2026-06-01T00:00:00Z", (
        "file X's watermark advanced despite its registration failing"
    )
    assert stored["kg_file_mtime"]["file-y"] == "2026-07-03T00:00:00Z"


def test_one_files_registration_failure_does_not_stop_the_batch(
    isolated_settings, monkeypatch
):
    """AC12's guarantee still holds at this call site — it is satisfied by the
    SURROUNDING per-file isolation, not by a local catch."""
    _stub_extraction(monkeypatch)

    def _register(company_id, **kw):
        if kw["external_id"] == "file-x":
            raise RuntimeError("boom")

    monkeypatch.setattr(
        drive_extract.document_catalog, "register_document", _register
    )
    result = extract_drive_docs(
        FakeFacade(), "co-1",
        [_doc(file_id="file-x"), _doc(file_id="file-y"), _doc(file_id="file-z")],
    )
    assert set(result["ok"]) == {"file-y", "file-z"}
    assert result["signals"] == 3, "extraction stopped early for the good files"

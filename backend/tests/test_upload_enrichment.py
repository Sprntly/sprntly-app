"""Second-pass model read for unparsed uploads (app/upload_enrichment.py).

The upload never blocks on a model: the converter stores a placeholder, and
this pass rewrites it in place once the model can read the original bytes. The
payoff of the A1 stub fix — because a stub is never RECORDED as ingested, a
recovered file flows into the KG on the next sync with no re-upload.
"""
from __future__ import annotations

from unittest.mock import patch

from app import upload_enrichment
from app.ingest import convert, is_unparsed_stub

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ─────────────────── dataset corpus path (upload strips) ───────────────────


def _seed_corpus(isolated_settings, *, filename: str, data: bytes) -> tuple:
    """Write a raw original + its converted .md, exactly as ingest_file does."""
    from app.datasets import dataset_path, raw_path
    from app.ingest import md_filename

    base = dataset_path("acme")
    raw = raw_path("acme")
    base.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / filename).write_bytes(data)
    md = base / md_filename(filename)
    md.write_text(convert(filename, data), encoding="utf-8")
    return md, raw / filename


def test_corpus_stub_is_replaced_by_the_model_read(isolated_settings):
    md, _ = _seed_corpus(isolated_settings, filename="whiteboard.png", data=PNG)
    assert is_unparsed_stub(md.read_text(encoding="utf-8"))  # precondition

    with patch("app.upload_enrichment.read_file",
               return_value="# Whiteboard\n\nUsers keep asking for SSO."):
        totals = upload_enrichment.enrich_dataset_corpus("co-1", "acme")

    assert totals["read"] == 1
    recovered = md.read_text(encoding="utf-8")
    assert "Users keep asking for SSO." in recovered
    assert not is_unparsed_stub(recovered)


def test_corpus_readable_files_are_left_untouched(isolated_settings):
    md, _ = _seed_corpus(
        isolated_settings, filename="notes.txt", data=b"customers want SSO",
    )
    before = md.read_text(encoding="utf-8")

    with patch("app.upload_enrichment.read_file") as mock_read:
        totals = upload_enrichment.enrich_dataset_corpus("co-1", "acme")

    mock_read.assert_not_called()
    assert totals["read"] == 0
    assert md.read_text(encoding="utf-8") == before


def test_corpus_failed_read_keeps_the_stub_for_a_later_retry(isolated_settings):
    """The whole point of failing open: an unreadable file stays eligible."""
    md, _ = _seed_corpus(isolated_settings, filename="shot.png", data=PNG)

    with patch("app.upload_enrichment.read_file", return_value=None):
        totals = upload_enrichment.enrich_dataset_corpus("co-1", "acme")

    assert totals["failed"] == 1
    assert is_unparsed_stub(md.read_text(encoding="utf-8"))


def test_corpus_enrichment_is_bounded_per_run(isolated_settings):
    for i in range(upload_enrichment.MAX_FILES_PER_RUN + 5):
        _seed_corpus(isolated_settings, filename=f"shot{i}.png", data=PNG)

    with patch("app.upload_enrichment.read_file", return_value="text") as mock_read:
        totals = upload_enrichment.enrich_dataset_corpus("co-1", "acme")

    assert totals["read"] == upload_enrichment.MAX_FILES_PER_RUN
    assert mock_read.call_count == upload_enrichment.MAX_FILES_PER_RUN


def test_corpus_enrichment_is_a_no_op_for_an_unknown_dataset(isolated_settings):
    assert upload_enrichment.enrich_dataset_corpus("co-1", "nope") == {
        "read": 0, "failed": 0, "skipped": 0,
    }


def test_corpus_enrichment_never_raises(isolated_settings):
    """Fully isolated — a storage failure is logged, not propagated."""
    _seed_corpus(isolated_settings, filename="shot.png", data=PNG)
    with patch("app.upload_enrichment.read_file", side_effect=RuntimeError("boom")):
        totals = upload_enrichment.enrich_dataset_corpus("co-1", "acme")
    assert totals["read"] == 0


# ─────────────────── document sources (uploads connector) ───────────────────


def test_document_source_stub_is_replaced(isolated_settings):
    from app.db.client import require_client
    from app.document_sources import (
        add_document_file,
        create_document_source,
        list_source_files,
    )

    require_client().table("companies").insert(
        {"id": "co-1", "slug": "acme", "display_name": "Acme"}
    ).execute()
    src = create_document_source("co-1", name="Research", description="")
    add_document_file("co-1", src.id, filename="whiteboard.png", data=PNG)

    stored = list_source_files("co-1", src.id)[0]
    assert is_unparsed_stub(stored.extracted_text)  # precondition

    with patch("app.upload_enrichment.read_file",
               return_value="Users keep asking for SSO."):
        totals = upload_enrichment.enrich_document_sources("co-1")

    assert totals["read"] == 1
    after = list_source_files("co-1", src.id)[0]
    assert after.extracted_text == "Users keep asking for SSO."
    assert after.id == stored.id  # same row — nothing downstream re-points


def test_document_source_enrichment_no_sources_is_clean(isolated_settings):
    assert upload_enrichment.enrich_document_sources("co-nothing") == {
        "read": 0, "failed": 0, "skipped": 0,
    }


# ─────────────────────────── kickoff ───────────────────────────


def test_kickoff_never_raises_into_the_upload(isolated_settings):
    with patch("app.upload_enrichment.threading.Thread",
               side_effect=RuntimeError("no threads")):
        assert upload_enrichment.kickoff_upload_enrichment("co-1", "acme") is False

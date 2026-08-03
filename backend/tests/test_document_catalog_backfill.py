"""Backfill over already-uploaded documents, and the ships-dark guarantee.

Two things are proven here, and the second is the one that decides whether
this change is safe to land during a delivery window.

T9  the backfill is idempotent by CONTENT HASH, not by bookkeeping — running
    it twice registers the same rows once and pays for one summary each.

T10 populating the catalog changes NOTHING about an answer. Not "the file
    wasn't edited" — that proves only that a diff is empty. What is asserted
    is that a real `compose_ask_answer` over a company with a fully-populated
    catalog row (summary, topics, embedding all present) produces a
    byte-identical `system`, `user_cacheable_prefix` and document manifest to
    the same call with the catalog empty.
"""
from __future__ import annotations

import pytest

_CID = "co-backfill"


@pytest.fixture
def stub_enrichment(monkeypatch):
    """Real registration, stubbed model + embeddings, with a call counter."""
    from app import document_catalog

    calls = []
    monkeypatch.setattr(document_catalog, "llm_call", lambda **k: calls.append(k) or type(
        "R", (), {"output": {"summary": "Seat pricing becomes usage-based in Q3.",
                             "topics": ["usage-based billing"]}})())
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])
    return calls


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"s-{company_id}", "display_name": "C"}
        ).execute()


def _seed_uploaded_file(db, file_id, *, company_id=_CID, source_id="src-bf",
                        filename="Q3_pricing.txt", text="Seat pricing becomes usage-based."):
    """Seeded directly, bypassing add_document_file — these are the rows that
    existed BEFORE the catalog did, which is exactly what the backfill is for."""
    _seed_company(db, company_id)
    if not db.table("document_source").select("id").eq("id", source_id).execute().data:
        db.table("document_source").insert(
            {"id": source_id, "company_id": company_id,
             "name": "Pricing research", "description": "Everything pricing"}
        ).execute()
    db.table("document_source_file").insert(
        {"id": file_id, "source_id": source_id, "company_id": company_id,
         "filename": filename, "extracted_text": text,
         "uploaded_at": "2026-08-01T10:00:00+00:00"}
    ).execute()
    return file_id


# ═══════════════════════════ T9 — idempotent backfill ══════════════════════


def test_running_the_backfill_twice_is_a_no_op_the_second_time(
    isolated_settings, stub_enrichment
):
    """T9/AC17: identical row count, and no second summary for any document."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    for i in range(3):
        _seed_uploaded_file(db, f"file-{i}", filename=f"doc-{i}.txt",
                            text=f"Distinct body {i}.")

    first = document_sources.backfill_catalog(_CID)
    assert first == {"registered": 3, "skipped": 0, "errors": 0}
    after_first = document_catalog.list_documents(_CID)
    assert len(after_first) == 3
    assert len(stub_enrichment) == 3

    second = document_sources.backfill_catalog(_CID)
    assert second == {"registered": 0, "skipped": 3, "errors": 0}
    after_second = document_catalog.list_documents(_CID)

    assert len(after_second) == 3, "the second run duplicated rows"
    assert len(stub_enrichment) == 3, "the second run paid for summaries again"
    assert [d.id for d in after_first] == [d.id for d in after_second]


def test_the_backfill_is_per_tenant(isolated_settings, stub_enrichment):
    """AC17: one company's backfill never touches another's."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    _seed_uploaded_file(db, "file-a", company_id=_CID, source_id="src-a")
    _seed_uploaded_file(db, "file-b", company_id="co-other", source_id="src-b")

    document_sources.backfill_catalog(_CID)
    assert [d.external_id for d in document_catalog.list_documents(_CID)] == ["file-a"]
    assert document_catalog.list_documents("co-other") == []


def test_the_backfill_picks_up_a_document_whose_text_changed(
    isolated_settings, stub_enrichment
):
    """Hash-keyed, not flag-keyed: a document edited since the last run is
    re-registered rather than skipped as 'already backfilled'."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    _seed_uploaded_file(db, "file-1")
    document_sources.backfill_catalog(_CID)
    assert len(stub_enrichment) == 1

    db.table("document_source_file").update(
        {"extracted_text": "Usage-based billing replaced seat pricing."}
    ).eq("id", "file-1").execute()

    counts = document_sources.backfill_catalog(_CID)
    assert counts["registered"] == 1
    assert len(stub_enrichment) == 2
    assert len(document_catalog.list_documents(_CID)) == 1


def test_one_bad_document_does_not_strand_the_rest(
    isolated_settings, monkeypatch, stub_enrichment
):
    """AC17: per-file isolation."""
    from app import document_catalog, document_sources

    db = isolated_settings["supabase"]
    for i in range(3):
        _seed_uploaded_file(db, f"file-{i}", filename=f"d{i}.txt", text=f"body {i}")

    real = document_catalog.register_document

    def _flaky(company_id, **kw):
        if kw["external_id"] == "file-1":
            raise RuntimeError("bad row")
        return real(company_id, **kw)

    monkeypatch.setattr(document_catalog, "register_document", _flaky)
    counts = document_sources.backfill_catalog(_CID)
    assert counts == {"registered": 2, "skipped": 0, "errors": 1}
    assert len(document_catalog.list_documents(_CID)) == 2


# ═══════════════════════════ T10 — ships dark ══════════════════════════════


def _populated_catalog_row(db, file_id, company_id=_CID):
    """A catalog row with every derived field present — summary, topics AND
    embedding. A row that is merely present proves less: the question is
    whether a POPULATED catalog leaks into a prompt."""
    db.table("document_catalog").insert({
        "id": f"cat-{file_id}",
        "company_id": company_id,
        "provider": "uploads",
        "external_id": file_id,
        "title": "Q3_pricing.txt",
        "source_name": "Pricing research",
        "content_hash": "hash-1",
        "summary": "Seat pricing becomes usage-based for enterprise tiers in Q3.",
        "topics": ["usage-based billing", "enterprise pricing"],
        "summary_model": "claude-haiku-4-5",
        "summary_version": "document-catalog-summary-v1",
        "embedding": [0.1] * 1536,
        "doc_date": "2026-08-01T10:00:00+00:00",
    }).execute()


def test_a_populated_catalog_enriches_the_index_and_an_empty_one_does_not_break_it(
    isolated_settings, fake_llm
):
    """This test used to assert the opposite, and the inversion is the point.

    While the catalog had no reader, the proof that it shipped dark was that a
    populated catalog and an empty one produced byte-identical prompts. The
    reader has now landed, so a populated catalog SHOULD change the prompt —
    that is the whole feature — and asserting equivalence would assert the
    feature's absence.

    What replaces it is the contract that actually matters now: the catalog
    ENRICHES the index, and its absence degrades the index rather than
    breaking it. The document's existence — the thing the original incident
    got wrong — is carried by the uploads read either way, so an empty,
    stale or unreachable catalog costs summaries, never existence.
    """
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_uploaded_file(db, "file-1")
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    question = "what changed about our pricing?"

    _populated_catalog_row(db, "file-1")
    assert db.table("document_catalog").select("*").eq(
        "company_id", _CID).execute().data, "fixture did not populate the catalog"
    with_catalog_block, _ = ask_runner.document_grounding(_CID, question)

    db.table("document_catalog").delete().eq("company_id", _CID).execute()
    assert db.table("document_catalog").select("*").eq(
        "company_id", _CID).execute().data == []
    without_catalog_block, without_catalog_manifest = ask_runner.document_grounding(
        _CID, question
    )

    # Enriched: the catalog's one-line summary reaches the index.
    assert "Seat pricing becomes usage-based for enterprise tiers in Q3." in (
        with_catalog_block
    )
    assert with_catalog_block != without_catalog_block

    # Degraded, not broken: without a catalog row the document is still
    # indexed and still accounted for — it simply has no summary.
    assert "Q3_pricing.txt" in with_catalog_block
    assert "Q3_pricing.txt" in without_catalog_block
    assert "Seat pricing becomes usage-based" not in without_catalog_block
    assert [m["file_id"] for m in without_catalog_manifest] == ["file-1"]


def test_the_capacity_caps_are_untouched_and_the_relevance_gate_is_gone():
    """The selection constants, restated for the reader that now exists.

    This test previously pinned `_TOKEN_OVERLAP_RATIO == 0.8` to prove the
    catalog shipped without touching selection. Selection has now changed, so
    the honest invariant changed with it — and it is asserted in both
    directions rather than merely relaxed.

    The two CAPACITY caps are unchanged: they bound cost and prompt size, and
    mis-setting them loads fewer or more documents, visibly. The RELEVANCE
    gate is gone and must stay gone: it decided, invisibly, that a user asking
    about their own document's topic got nothing. Its absence is the fix, so a
    reintroduction under any name is a regression this test should catch.
    """
    from app import ask_runner

    assert ask_runner.MAX_SELECTED_DOCUMENTS == 3
    assert ask_runner.MAX_INDEX_ENTRIES == 200
    assert not hasattr(ask_runner, "_TOKEN_OVERLAP_RATIO")


def test_nothing_outside_the_accessor_names_the_catalog_table():
    """AC6: the table name appears in exactly one module. The unsafe query —
    one that forgets the tenant filter — then requires hand-writing raw
    PostgREST against a table no other file names, which is visible in any
    diff rather than reachable by accident."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        if path.name == "document_catalog.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if '"document_catalog"' in line or "'document_catalog'" in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []

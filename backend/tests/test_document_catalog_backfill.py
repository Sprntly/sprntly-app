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


def test_a_populated_catalog_changes_nothing_about_an_answer(
    isolated_settings, fake_llm
):
    """T10/AC18, the ships-dark proof.

    Same company, same question, same uploaded document. Once with a fully
    populated catalog row, once with the catalog empty. The composed prompt
    and the document manifest must be byte-identical — that is what "nothing
    reads the catalog" means operationally.
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
    with_catalog_block, with_catalog_manifest = ask_runner.document_grounding(
        _CID, question
    )
    ask_runner.compose_ask_answer("asurion", question, enterprise_id=_CID)
    with_catalog = fake_llm["calls"][-1]

    db.table("document_catalog").delete().eq("company_id", _CID).execute()
    assert db.table("document_catalog").select("*").eq(
        "company_id", _CID).execute().data == []
    without_catalog_block, without_catalog_manifest = ask_runner.document_grounding(
        _CID, question
    )
    ask_runner.compose_ask_answer("asurion", question, enterprise_id=_CID)
    without_catalog = fake_llm["calls"][-1]

    assert with_catalog["system"] == without_catalog["system"]
    assert (
        with_catalog["kwargs"]["user_cacheable_prefix"]
        == without_catalog["kwargs"]["user_cacheable_prefix"]
    )
    assert with_catalog["user"] == without_catalog["user"]
    assert with_catalog_block == without_catalog_block
    assert with_catalog_manifest == without_catalog_manifest

    # And the assertion has teeth: the document really was in the prompt, so a
    # catalog leak would have had somewhere to show up.
    assert "Q3_pricing.txt" in with_catalog_block


def test_the_selection_constants_are_untouched():
    """AC18: `_select_documents` and its constants are what this change
    deliberately does NOT touch — the behaviour change lives in the reader,
    which ships separately."""
    from app import ask_runner

    assert ask_runner._TOKEN_OVERLAP_RATIO == 0.8
    assert ask_runner.MAX_SELECTED_DOCUMENTS == 3
    assert ask_runner.MAX_INDEX_ENTRIES == 200


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

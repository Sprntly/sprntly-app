"""Tests for the uploaded-document existence-vs-retrieval contract on the ask
path.

The incident this closes: a workspace uploaded a file, extraction succeeded
(the `document_source_file` row holds its text), and minutes later a
question naming that file by title was answered with a flat denial that any
such document existed anywhere — because nothing on the ask path ever read
`document_source_file`. This suite covers the two reads
(`app.document_sources.list_company_files` / `get_file_text`), the pure
selection + rendering (`app.ask_runner.document_grounding`), and the wiring
into `compose_ask_answer` / `qa_agent._answer_single_shot` / `GET
/v1/ask/{id}`.

Seeded directly against the SQLite mirror's `document_source` /
`document_source_file` tables (`tests/conftest.py`) rather than through
`add_document_file`, so the extracted text and filename extension are fully
controlled without running the real conversion pipeline.
"""
from __future__ import annotations

import inspect

import pytest

from tests._fake_supabase import _Query

_CID = "co-docs"
_OTHER_CID = "co-docs-other"


# ─────────────────────────── seeding helpers ───────────────────────────


def _seed_company(db, company_id=_CID):
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": f"slug-{company_id}", "display_name": company_id}
        ).execute()


def _seed_source(db, source_id, *, company_id=_CID, name="Competitive research"):
    _seed_company(db, company_id)
    db.table("document_source").insert(
        {"id": source_id, "company_id": company_id, "name": name, "description": ""}
    ).execute()
    return source_id


def _seed_file(
    db, file_id, source_id, *, company_id=_CID, filename="notes.txt",
    extracted_text="", uploaded_at="2026-08-02T00:00:00+00:00",
):
    db.table("document_source_file").insert(
        {
            "id": file_id,
            "source_id": source_id,
            "company_id": company_id,
            "filename": filename,
            "extracted_text": extracted_text,
            "uploaded_at": uploaded_at,
        }
    ).execute()
    return file_id


def _seed_incident_fixture(db, company_id=_CID):
    """The CEO's exact incident: one source, one file, real extracted text."""
    src = _seed_source(db, "src-1", company_id=company_id, name="Competitive research")
    text = "Sprntly ships faster iteration cycles than Productboard on PRD-to-prototype turnaround." * 20
    _seed_file(
        db, "file-1", src, company_id=company_id,
        filename="Sprntly_vs_Productboard_Comparison.docx",
        extracted_text=text, uploaded_at="2026-08-02T10:00:00+00:00",
    )
    return src, "file-1", text


# ═══════════════════════════ Regression (fail on unfixed base) ═════════════


def test_uploaded_document_reaches_the_answer_prompt(isolated_settings, fake_llm):
    """The incident itself: an uploaded document's extracted_text must reach
    the composed answer prompt when the question names it by title."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _, _, text = _seed_incident_fixture(db)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion",
        "What does the Sprntly_vs_Productboard_Comparison document say?",
        enterprise_id=_CID,
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix is not None
    assert text in prefix


def test_uploaded_document_present_in_index_even_when_not_selected(
    isolated_settings, fake_llm
):
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_incident_fixture(db)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "Summarise our onboarding funnel drop-off analysis",
        enterprise_id=_CID,
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert "Sprntly_vs_Productboard_Comparison.docx" in prefix
    assert "## Index" in prefix
    assert "## Contents loaded for this question" not in prefix


def test_skill_answer_path_also_sees_uploaded_documents(isolated_settings, monkeypatch):
    import app.qa_agent as qa
    from app.qa_agent import RouteDecision, _answer_single_shot

    db = isolated_settings["supabase"]
    _, _, text = _seed_incident_fixture(db)
    captured = {}

    def _answer_out(**k):
        captured.update(k)
        return _Result(
            {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9,
             "unanswered": ""}
        )

    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out(**k))
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id="roadmap", confidence=1.0, source="slash")
    _answer_single_shot(
        decision, _CID,
        "What does the Sprntly_vs_Productboard_Comparison document say?", [],
    )

    prefix = captured["user_cacheable_prefix"]
    assert prefix is not None
    assert text in prefix


class _Result:
    def __init__(self, output):
        self.output = output


# ═══════════════════════════ Creation ═══════════════════════════════════════


def test_list_company_files_returns_refs_newest_first(isolated_settings):
    from app.document_sources import list_company_files

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="a.txt", uploaded_at="2026-08-01T00:00:00+00:00")
    _seed_file(db, "f2", src, filename="b.txt", uploaded_at="2026-08-03T00:00:00+00:00")
    _seed_file(db, "f3", src, filename="c.txt", uploaded_at="2026-08-02T00:00:00+00:00")

    refs = list_company_files(_CID)

    assert [r.id for r in refs] == ["f2", "f3", "f1"]
    r = next(r for r in refs if r.id == "f2")
    assert r.source_id == src
    assert r.source_name == "Competitive research"
    assert r.filename == "b.txt"
    assert r.uploaded_at == "2026-08-03T00:00:00+00:00"


def test_list_company_files_does_not_select_extracted_text(isolated_settings, monkeypatch):
    from app.document_sources import list_company_files

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, extracted_text="SECRET BODY TEXT")

    captured_selects = []
    orig_select = _Query.select

    def _spy_select(self, cols="*", count=None):
        captured_selects.append(cols)
        return orig_select(self, cols, count)

    monkeypatch.setattr(_Query, "select", _spy_select)

    list_company_files(_CID)

    for cols in captured_selects:
        assert "extracted_text" not in cols


class _StubResult:
    def __init__(self, data):
        self.data = data


class _StubTable:
    """Minimal select().eq().execute() chain returning fixed rows —
    bypasses the fake DB's (Postgres-matching) FK enforcement so the
    join-miss defensive path is reachable in a test, even though the real
    schema's `ON DELETE CASCADE` FK means it can't occur via normal inserts."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return _StubResult(self._rows)


class _StubClient:
    def __init__(self, files, sources):
        self._tables = {"document_source_file": files, "document_source": sources}

    def table(self, name):
        return _StubTable(self._tables[name])


def test_list_company_files_keeps_file_with_missing_parent_source(
    isolated_settings, monkeypatch
):
    import app.document_sources as document_sources_mod
    from app.document_sources import list_company_files

    stub = _StubClient(
        files=[
            {"id": "orphan-1", "source_id": "does-not-exist", "filename": "orphan.txt",
             "uploaded_at": "2026-08-01T00:00:00+00:00"},
        ],
        sources=[],
    )
    monkeypatch.setattr(document_sources_mod, "require_client", lambda: stub)

    refs = list_company_files(_CID)

    assert len(refs) == 1
    assert refs[0].source_name == ""


def test_get_file_text_returns_stored_text(isolated_settings):
    from app.document_sources import get_file_text

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, extracted_text="the stored body")

    assert get_file_text(_CID, "f1") == "the stored body"


def test_document_grounding_renders_index_header_and_entries(isolated_settings):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="a.txt")
    _seed_file(db, "f2", src, filename="b.txt")

    block, _ = document_grounding(_CID, "unrelated question")

    lines = block.splitlines()
    assert lines[0] == "# UPLOADED DOCUMENTS"
    assert any(l.startswith("- a.txt (source:") for l in lines)
    assert any(l.startswith("- b.txt (source:") for l in lines)


# ═══════════════════════════ Retrieval / selection ═════════════════════════


def _refs(*specs):
    from app.document_sources import DocumentFileRef

    out = []
    for i, (filename, source_name, uploaded_at) in enumerate(specs):
        out.append(
            DocumentFileRef(
                id=f"id-{i}", source_id=f"src-{i}", source_name=source_name,
                filename=filename, uploaded_at=uploaded_at,
            )
        )
    return out


def test_select_documents_matches_underscored_title():
    from app.ask_runner import _select_documents

    refs = _refs(("Sprntly_vs_Productboard_Comparison.docx", "Competitive research", "2026-08-02"))
    out = _select_documents("Tell me about Sprntly_vs_Productboard_Comparison", refs)
    assert [r.id for r in out] == ["id-0"]


def test_select_documents_matches_spaced_title():
    from app.ask_runner import _select_documents

    refs = _refs(("Sprntly_vs_Productboard_Comparison.docx", "Competitive research", "2026-08-02"))
    out = _select_documents("What does Sprntly vs Productboard Comparison say?", refs)
    assert [r.id for r in out] == ["id-0"]


def test_select_documents_matches_title_with_extension():
    from app.ask_runner import _select_documents

    refs = _refs(("Sprntly_vs_Productboard_Comparison.docx", "Competitive research", "2026-08-02"))
    out = _select_documents(
        "Summarise Sprntly_vs_Productboard_Comparison.docx for me", refs
    )
    assert [r.id for r in out] == ["id-0"]


def test_select_documents_matches_source_name():
    from app.ask_runner import _select_documents

    refs = _refs(("random_filename.txt", "Q3 NPS survey", "2026-08-02"))
    out = _select_documents("What did the Q3 NPS survey say?", refs)
    assert [r.id for r in out] == ["id-0"]


def test_selection_never_reaches_documents_outside_the_rendered_index(isolated_settings):
    """250 files; an exact-title match seeded among the oldest 50 (so it ranks
    outside the newest MAX_INDEX_ENTRIES=200 after truncation). Its body must
    NOT load and it must carry no manifest entry — truncation happens BEFORE
    selection, never after."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    # 200 newest files (ranks 1-200 after sort).
    for i in range(200):
        _seed_file(
            db, f"newest-{i}", src, filename=f"newest-{i}.txt",
            uploaded_at=f"2026-08-{2 + (i % 27):02d}T00:00:00+00:00",
        )
    # The exact-title match, seeded with an old timestamp so it ranks outside
    # the newest 200.
    _seed_file(
        db, "buried-match", src,
        filename="Sprntly_vs_Productboard_Comparison.docx",
        extracted_text="the buried body", uploaded_at="2020-01-01T00:00:00+00:00",
    )
    # 49 more old files so the buried match is genuinely among the oldest 50.
    for i in range(49):
        _seed_file(
            db, f"old-{i}", src, filename=f"old-{i}.txt",
            uploaded_at=f"2020-02-{(i % 27) + 1:02d}T00:00:00+00:00",
        )

    block, manifest = document_grounding(
        _CID, "What does the Sprntly_vs_Productboard_Comparison document say?"
    )

    assert "the buried body" not in block
    assert all(m["file_id"] != "buried-match" for m in manifest)
    assert "Sprntly_vs_Productboard_Comparison.docx" not in block


def test_select_documents_returns_empty_for_unrelated_question():
    from app.ask_runner import _select_documents

    refs = _refs(("Sprntly_vs_Productboard_Comparison.docx", "Competitive research", "2026-08-02"))
    assert _select_documents("What's the weather like?", refs) == []


def test_select_documents_ignores_short_tokens():
    from app.ask_runner import _select_documents

    refs = _refs(("vs_q3_report.txt", "Competitive research", "2026-08-02"))
    assert _select_documents("compare vs q3 numbers please", refs) == []


def test_document_grounding_omits_contents_heading_when_nothing_selected(isolated_settings):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx")

    block, _ = document_grounding(_CID, "What's the weather like?")
    assert "## Contents loaded for this question" not in block


# ═══════════════════════════ Serialization / manifest ══════════════════════


def test_manifest_entry_shape_and_file_id(isolated_settings):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    # Deliberately NOT the original "a.txt": a one-character stem is a
    # substring of almost any question ("a" is inside "unrelated"), so that
    # fixture silently selected the document and could not express the
    # nothing-selected shape this test is about. The substring arm's
    # behaviour is unchanged here — only the fixture stopped hiding it.
    _seed_file(db, "f1", src, filename="quarterly_metrics.txt")

    _, manifest = document_grounding(_CID, "unrelated")
    assert len(manifest) == 1
    # `scope`, `match` and `rank` joined when selection became two-staged:
    # they are what distinguishes "never selected" from "selected by name"
    # from "selected by topic at rank N", which are indistinguishable in the
    # answer but mean opposite things about whether selection is working.
    assert set(manifest[0].keys()) == {
        "file_id", "filename", "source_name", "uploaded_at", "loaded",
        "scope", "match", "rank",
    }
    assert manifest[0]["file_id"] == "f1"
    assert manifest[0]["scope"] == "workspace"
    # Nothing selected this document, so there is no match reason and no rank.
    assert manifest[0]["loaded"] is False
    assert manifest[0]["match"] is None
    assert manifest[0]["rank"] is None


def test_manifest_loaded_flag_true_only_for_rendered_bodies(isolated_settings):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="body")
    _seed_file(db, "f2", src, filename="unrelated.txt", extracted_text="other")

    _, manifest = document_grounding(_CID, "About Sprntly_vs_Productboard_Comparison")
    by_id = {m["file_id"]: m for m in manifest}
    assert by_id["f1"]["loaded"] is True
    assert by_id["f2"]["loaded"] is False


def test_block_contains_no_file_uuid(isolated_settings):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "a-very-unique-uuid-1234", src, filename="a.txt")

    block, _ = document_grounding(_CID, "unrelated")
    assert "a-very-unique-uuid-1234" not in block


def test_compose_ask_answer_payload_carries_documents(isolated_settings, fake_llm):
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="a.txt")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    _, manifest = ask_runner.document_grounding(_CID, "unrelated")
    payload = ask_runner.compose_ask_answer("asurion", "unrelated", enterprise_id=_CID)

    assert payload["documents"] == manifest


def test_answer_single_shot_payload_carries_documents(isolated_settings, monkeypatch):
    import app.qa_agent as qa
    from app import ask_runner
    from app.qa_agent import RouteDecision, _answer_single_shot

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="a.txt")

    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _Result(
            {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9,
             "unanswered": ""}
        ),
    )
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    _, manifest = ask_runner.document_grounding(_CID, "unrelated")
    decision = RouteDecision(skill_id="roadmap", confidence=1.0, source="slash")
    out = _answer_single_shot(decision, _CID, "unrelated", [])

    assert out["documents"] == manifest


# ═══════════════════════════ Edge cases ═════════════════════════════════════


def test_document_budget_caps_loaded_bodies(isolated_settings):
    from app.ask_runner import MAX_SELECTED_DOCUMENTS, document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    for i in range(12):
        _seed_file(
            db, f"f{i}", src, filename=f"named_doc_{i}.txt",
            extracted_text="x" * 20_000,
            uploaded_at=f"2026-08-{(i % 27) + 1:02d}T00:00:00+00:00",
        )

    question = " ".join(f"named_doc_{i}" for i in range(5))
    block, manifest = document_grounding(_CID, question)

    loaded = [m for m in manifest if m["loaded"]]
    assert len(loaded) <= MAX_SELECTED_DOCUMENTS
    assert len(block) <= 24000 + 3000 + 2000


def test_truncated_body_carries_visible_truncation_marker(isolated_settings):
    import re

    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(
        db, "f1", src, filename="huge_document.txt", extracted_text="y" * 50_000,
    )

    block, _ = document_grounding(_CID, "About huge_document")
    assert re.search(
        r"\[Truncated — showing the first \d+ of \d+ characters of this document\.\]",
        block,
    )


def test_index_truncates_visibly_at_max_entries(isolated_settings):
    from app.ask_runner import MAX_INDEX_ENTRIES, document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    for i in range(250):
        _seed_file(
            db, f"f{i}", src, filename=f"doc-{i}.txt",
            uploaded_at=f"2026-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T00:00:00+00:00",
        )

    block, manifest = document_grounding(_CID, "unrelated")

    assert block.count("- doc-") == MAX_INDEX_ENTRIES
    # The marker now says PARTIAL in as many words, because above the cap the
    # EXISTENCE contract changes, not just the display: the index stops being
    # the complete inventory, so absence from it stops being evidence of
    # absence. The prompt's existence rule keys off this exact word.
    assert "PARTIAL" in block
    # Wording no longer says "most recently uploaded": the index also carries
    # documents that live in a connected system, which nobody uploaded. The
    # count and the may-still-exist clause are what the existence rule needs,
    # and both are unchanged.
    assert (
        f"it shows {MAX_INDEX_ENTRIES} of 250 documents in this workspace"
        in block
    )
    assert "may still exist" in block
    assert len(manifest) == MAX_INDEX_ENTRIES


def test_document_grounding_empty_for_no_enterprise_id(isolated_settings):
    from app.ask_runner import document_grounding

    assert document_grounding(None, "anything") == ("", [])


def test_document_grounding_empty_for_company_with_no_files(isolated_settings):
    from app.ask_runner import document_grounding

    assert document_grounding("co-with-nothing", "anything") == ("", [])


def test_compose_ask_answer_unchanged_when_no_uploaded_documents(
    isolated_settings, fake_llm, monkeypatch
):
    """Working-tree invariant, not a git revision (shallow clones): a company
    with zero uploads must compose byte-identically to the SAME run with
    `document_grounding` monkeypatched to ("", []) — proving the addition is
    a no-op on this path, in-process."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-empty-docs")
    real_call = fake_llm["calls"][0]

    monkeypatch.setattr(
        ask_runner, "document_grounding", lambda eid, q, *a, **kw: ("", [])
    )
    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-empty-docs")
    patched_call = fake_llm["calls"][1]

    assert real_call["system"] == patched_call["system"]
    assert (
        real_call["kwargs"]["user_cacheable_prefix"]
        == patched_call["kwargs"]["user_cacheable_prefix"]
        is None
    )


def test_answer_single_shot_unchanged_when_no_uploaded_documents(
    isolated_settings, monkeypatch
):
    import app.qa_agent as qa
    from app.qa_agent import RouteDecision, _answer_single_shot

    captured = []
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: captured.append(k) or _Result(
            {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9,
             "unanswered": ""}
        ),
    )
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)
    decision = RouteDecision(skill_id="roadmap", confidence=1.0, source="slash")

    _answer_single_shot(decision, "co-empty-docs-2", "what next?", [])
    real_prefix = captured[0]["user_cacheable_prefix"]

    monkeypatch.setattr(
        qa, "document_grounding", lambda eid, q, *a, **kw: ("", [])
    )
    _answer_single_shot(decision, "co-empty-docs-2", "what next?", [])
    patched_prefix = captured[1]["user_cacheable_prefix"]

    assert real_prefix == patched_prefix is None


def test_generate_one_sync_composition_unchanged(isolated_settings, fake_llm, monkeypatch):
    from app import ask_runner
    from app.prompts import ASK_CACHE_VERSION

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner._generate_one_sync("asurion", "probe question")
    real_call = fake_llm["calls"][0]

    monkeypatch.setattr(
        ask_runner, "document_grounding", lambda eid, q, *a, **kw: ("", [])
    )
    ask_runner._generate_one_sync("asurion", "probe question")
    patched_call = fake_llm["calls"][1]

    assert real_call["system"] == patched_call["system"]
    assert (
        real_call["kwargs"]["user_cacheable_prefix"]
        == patched_call["kwargs"]["user_cacheable_prefix"]
    )
    assert ASK_CACHE_VERSION == 5


# ═══════════════════════════ Error handling ═════════════════════════════════


def test_list_company_files_returns_empty_on_read_error(isolated_settings, monkeypatch, caplog):
    from app.document_sources import list_company_files

    def _boom(self, cols="*", count=None):
        raise RuntimeError("read failed")

    monkeypatch.setattr(_Query, "select", _boom)

    with caplog.at_level("WARNING"):
        out = list_company_files(_CID)

    assert out == []
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert _CID in msg


def test_get_file_text_returns_none_on_read_error(isolated_settings, monkeypatch, caplog):
    from app.document_sources import get_file_text

    def _boom(self, cols="*", count=None):
        raise RuntimeError("read failed")

    monkeypatch.setattr(_Query, "select", _boom)

    with caplog.at_level("WARNING"):
        out = get_file_text(_CID, "some-file")

    assert out is None
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert _CID in msg and "some-file" in msg


def test_document_grounding_degrades_to_empty_when_reads_raise(isolated_settings, monkeypatch):
    from app import ask_runner

    monkeypatch.setattr(
        ask_runner, "list_company_files",
        lambda company_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert ask_runner.document_grounding(_CID, "anything") == ("", [])


def test_get_file_text_unknown_id_returns_none(isolated_settings):
    from app.document_sources import get_file_text

    db = isolated_settings["supabase"]
    _seed_source(db, "src-a")

    assert get_file_text(_CID, "does-not-exist") is None


# ═══════════════════════════ Tenant isolation ═══════════════════════════════


def test_list_company_files_tenant_isolation(isolated_settings):
    from app.document_sources import list_company_files

    db = isolated_settings["supabase"]
    src_b = _seed_source(db, "src-b", company_id=_OTHER_CID)
    _seed_file(db, "fb1", src_b, company_id=_OTHER_CID, filename="b-only.txt")

    assert list_company_files(_CID) == []
    assert len(list_company_files(_OTHER_CID)) == 1


def test_get_file_text_tenant_isolation(isolated_settings):
    from app.document_sources import get_file_text

    db = isolated_settings["supabase"]
    src_b = _seed_source(db, "src-b", company_id=_OTHER_CID)
    _seed_file(db, "fb1", src_b, company_id=_OTHER_CID, extracted_text="other tenant body")

    assert get_file_text(_CID, "fb1") is None
    assert get_file_text(_OTHER_CID, "fb1") == "other tenant body"


# ═══════════════════ Conversation-scoped attachments (chat) ════════════════
# A user attaches a document to a chat turn; the file lives in
# `conversation_turns.attachments`, a table `list_company_files` can never see
# by construction (it reads `document_source_file`). `document_grounding` now
# ALSO folds the active conversation's attachments into the index (as
# "attached to this conversation" entries) and manifest — ownership-checked
# (company AND user, mirroring `routes.ask._load_history`'s per-user guard),
# never by conversation_id equality alone.

def _seed_conversation_with_attachment(
    tenant, *, title="chat", attachment_name="Notes.docx",
    attachment_content="body text", question="look at the attached file",
):
    """A REAL conversation + turn via the conversations API (not a hand-built
    row), owned by `tenant` (a `tenant_client.make(...)` namespace). Returns
    (conversation_id, turn_id)."""
    conv = tenant.client.post("/v1/conversations", json={"title": title}).json()
    turn = tenant.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={
            "role": "user", "content": question,
            "attachments": [{"name": attachment_name, "content": attachment_content}],
        },
    ).json()
    return conv["id"], turn["id"]


def test_document_grounding_includes_conversation_attachment_in_index(
    tenant_client, isolated_settings
):
    """T4 — the real `ask_runner.document_grounding(...)` against a seeded
    `conversation_turns` row: the attachment appears in the index bearing
    'attached to this conversation' and carries no `source:` token. RED
    today (pre-fix `document_grounding` takes two arguments)."""
    from app import ask_runner

    t = tenant_client.make(slug="acme-conv-doc")
    conv_id, _ = _seed_conversation_with_attachment(
        t, attachment_name="Sprntly_vs_Productboard_Comparison.docx",
        attachment_content="the real attached body",
    )

    token = ask_runner.set_active_conversation(conv_id, t.user_id)
    try:
        block, manifest = ask_runner.document_grounding(
            t.company_id, "unrelated question", conversation_id=conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    line = next(
        l for l in block.splitlines()
        if "Sprntly_vs_Productboard_Comparison.docx" in l
    )
    assert "attached to this conversation" in line
    assert "source:" not in line
    assert any(
        m["filename"] == "Sprntly_vs_Productboard_Comparison.docx" for m in manifest
    )


def test_document_grounding_conversation_scoping_excludes_other_conversations(
    tenant_client, isolated_settings
):
    """T5 — an attachment from a DIFFERENT conversation of the SAME owner must
    not appear; document_grounding scopes strictly to the passed
    conversation_id, not to "anything this user owns"."""
    from app import ask_runner

    t = tenant_client.make(slug="acme-conv-scope")
    _seed_conversation_with_attachment(
        t, attachment_name="other_conversation.docx", attachment_content="OTHER BODY",
    )
    this_conv_id, _ = _seed_conversation_with_attachment(
        t, title="this one", attachment_name="this_conversation.docx",
        attachment_content="THIS BODY",
    )

    token = ask_runner.set_active_conversation(this_conv_id, t.user_id)
    try:
        block, manifest = ask_runner.document_grounding(
            t.company_id, "unrelated question", conversation_id=this_conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    assert "other_conversation.docx" not in block
    assert all(m.get("filename") != "other_conversation.docx" for m in manifest)
    assert "this_conversation.docx" in block


def test_document_grounding_degrades_to_empty_when_conversation_read_raises(
    tenant_client, isolated_settings, monkeypatch
):
    """T6 — a read failure ISOLATED to the conversation-attachments path
    (uploaded-document reads still succeed normally, finding none) must
    degrade the SAME way every other read here does: no exception escapes,
    the attachment is simply absent."""
    from app import ask_runner

    t = tenant_client.make(slug="acme-conv-fail")
    conv_id, _ = _seed_conversation_with_attachment(t)

    real_select = _Query.select

    def _boom(self, cols="*", count=None):
        if self.table in ("conversations", "conversation_turns"):
            raise RuntimeError("boom")
        return real_select(self, cols, count)

    monkeypatch.setattr(_Query, "select", _boom)

    token = ask_runner.set_active_conversation(conv_id, t.user_id)
    try:
        result = ask_runner.document_grounding(
            t.company_id, "unrelated", conversation_id=conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    assert result == ("", [])


def test_document_grounding_conversation_attachment_manifest_synthetic_id(
    tenant_client, isolated_settings
):
    """T7 — B7's synthetic id: f"turn:{turn_id}:attachment:{index}"
    (`TurnAttachment` has no id field of its own)."""
    from app import ask_runner

    t = tenant_client.make(slug="acme-conv-manifest")
    conv = t.client.post("/v1/conversations", json={"title": "c"}).json()
    conv_id = conv["id"]
    turn = t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={
            "role": "user", "content": "q",
            "attachments": [
                {"name": "first.docx", "content": "first body"},
                {"name": "second.docx", "content": "second body"},
            ],
        },
    ).json()
    turn_id = turn["id"]

    token = ask_runner.set_active_conversation(conv_id, t.user_id)
    try:
        _, manifest = ask_runner.document_grounding(
            t.company_id, "unrelated", conversation_id=conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    ids = {m["file_id"] for m in manifest}
    assert f"turn:{turn_id}:attachment:0" in ids
    assert f"turn:{turn_id}:attachment:1" in ids


def test_document_grounding_tenancy_check_blocks_foreign_company_conversation(
    tenant_client, isolated_settings
):
    """T9 (B) — TENANCY / IDOR, distinct from T5. A REAL conversation id
    belonging to a DIFFERENT COMPANY must not leak its attachments, even when
    a legitimate same-company caller is active — proves the ownership QUERY
    itself rejects it, not merely an absent calling-user context."""
    from app import ask_runner

    company_a = tenant_client.make(slug="acme-tenancy-a")
    company_b = tenant_client.make(slug="acme-tenancy-b")
    foreign_conv_id, _ = _seed_conversation_with_attachment(
        company_b, attachment_name="company_b_secret.docx",
        attachment_content="COMPANY B PRIVATE BODY",
    )

    token = ask_runner.set_active_conversation(foreign_conv_id, company_a.user_id)
    try:
        block, manifest = ask_runner.document_grounding(
            company_a.company_id, "unrelated question",
            conversation_id=foreign_conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    assert "company_b_secret.docx" not in block
    assert "COMPANY B PRIVATE BODY" not in block
    assert all(m.get("filename") != "company_b_secret.docx" for m in manifest)


def test_document_grounding_tenancy_check_blocks_different_user_same_company(
    tenant_client, isolated_settings
):
    """T9's other branch — a REAL conversation belonging to a DIFFERENT USER
    inside the SAME company must not leak either. A company-only ownership
    check would PASS this (same tenant); B4 requires per-user scoping."""
    from app import ask_runner

    a = tenant_client.make(slug="acme-tenancy-users")
    b = tenant_client.make(slug="acme-tenancy-users", user_id="user-b-tenancy")
    assert a.company_id == b.company_id
    b_conv_id, _ = _seed_conversation_with_attachment(
        b, attachment_name="teammate_private.docx",
        attachment_content="TEAMMATE PRIVATE BODY",
    )

    token = ask_runner.set_active_conversation(b_conv_id, a.user_id)
    try:
        block, manifest = ask_runner.document_grounding(
            a.company_id, "unrelated question", conversation_id=b_conv_id,
        )
    finally:
        ask_runner.reset_active_conversation(token)

    assert "teammate_private.docx" not in block
    assert "TEAMMATE PRIVATE BODY" not in block
    assert all(m.get("filename") != "teammate_private.docx" for m in manifest)


def test_document_grounding_conversation_attachment_no_context_no_leak(
    tenant_client, isolated_settings
):
    """B1/B4 — passing conversation_id alone, with NO active calling-user
    context, must behave exactly as if conversation_id had never been passed
    — proves the check isn't satisfiable by conversation_id equality alone,
    even for an otherwise-legitimately-owned conversation."""
    from app import ask_runner

    t = tenant_client.make(slug="acme-conv-no-context")
    conv_id, _ = _seed_conversation_with_attachment(
        t, attachment_name="no_context.docx",
    )

    # No set_active_conversation call at all — the contextvar is unset.
    block, manifest = ask_runner.document_grounding(
        t.company_id, "unrelated question", conversation_id=conv_id,
    )

    assert "no_context.docx" not in block
    assert all(m.get("filename") != "no_context.docx" for m in manifest)


def test_run_sync_sets_and_resets_active_conversation_around_answer(
    isolated_settings, monkeypatch
):
    """B5 wiring: `ask_job_runner._run_sync` sets the request-scoped
    conversation context immediately before `qa_agent.answer(...)` and ALWAYS
    clears it afterward — even when the answer call raises. Proven against
    the set/reset contract itself (called, in order, with the right args,
    reset always reached) rather than a full round trip through a background
    thread, which would prove nothing more about ordering than this does."""
    import asyncio

    import pytest

    from app import ask_job_runner, ask_runner
    from app.db import start_ask_job

    db = isolated_settings["supabase"]
    _seed_company(db, "co-ctx")
    ask_id = start_ask_job(company_id="co-ctx", dataset="ds", question="q")
    calls: list = []
    monkeypatch.setattr(
        ask_runner, "set_active_conversation",
        lambda cid, uid: calls.append(("set", cid, uid)) or "TOKEN",
    )
    monkeypatch.setattr(
        ask_runner, "reset_active_conversation",
        lambda token: calls.append(("reset", token)),
    )

    def _boom(**kwargs):
        calls.append(("answer_called",))
        raise RuntimeError("boom")

    monkeypatch.setattr(ask_job_runner.qa_agent, "answer", _boom)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(RuntimeError):
            ask_job_runner._run_sync(
                ask_id, "co-ctx", "q", "ds", [], None, None, loop,
                conversation_id=77, user_id="user-x",
            )
    finally:
        loop.close()

    assert calls[0] == ("set", 77, "user-x")
    assert calls[1] == ("answer_called",)
    assert calls[2] == ("reset", "TOKEN")


# ═══════════════════════════ Wiring ═════════════════════════════════════════


def test_compose_ask_answer_documents_in_cacheable_prefix_not_user(
    isolated_settings, fake_llm
):
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="the body text")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Sprntly_vs_Productboard_Comparison", enterprise_id=_CID,
    )

    call = fake_llm["calls"][0]
    assert "the body text" in call["kwargs"]["user_cacheable_prefix"]
    assert "the body text" not in call["user"]


def test_compose_ask_answer_documents_ordered_after_facts_and_after_corpus(
    isolated_settings, fake_llm
):
    """The cacheable prefix orders `facts` -> corpus -> `docs_block`: the
    corpus (the largest, most stable block) precedes the per-question
    document index/bodies, so the shared prefix survives a change in which
    documents a given question selects. Was
    `..._ordered_after_facts_before_corpus`, pinning the OPPOSITE order —
    every miss on the corpus block paid a full re-prefill on every ask."""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": _CID, "slug": "slug-co-docs-2", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-docs-2", "company_id": _CID, "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="THE DOCUMENT BODY")

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("THE CORPUS BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Sprntly_vs_Productboard_Comparison", enterprise_id=_CID,
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix.index(WORKSPACE_CONFIG_HEADER) < prefix.index("THE CORPUS BODY")
    assert prefix.index("THE CORPUS BODY") < prefix.index("THE DOCUMENT BODY")


def test_two_different_questions_share_the_whole_corpus_prefix(
    isolated_settings, fake_llm
):
    """Two asks in the same dataset with DIFFERENT questions (which select
    different documents, so `docs_block`'s per-question load markers and
    bodies differ) still share a common prefix that covers the entire corpus
    block — the point of the reorder. On the unfixed (docs-before-corpus)
    ordering, the first differing byte falls inside `docs_block`, BEFORE the
    corpus even starts, and this fails. (AC3)"""
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-two-q")
    _seed_file(db, "f-alpha", src, filename="Alpha_Report.docx",
               extracted_text="ALPHA BODY")
    _seed_file(db, "f-beta", src, filename="Beta_Report.docx",
               extracted_text="BETA BODY")

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    corpus_text = "THE SHARED CORPUS BODY " * 50
    (ds / "a.md").write_text(corpus_text)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "Tell me about Alpha_Report", enterprise_id=_CID,
    )
    ask_runner.compose_ask_answer(
        "asurion", "Tell me about Beta_Report", enterprise_id=_CID,
    )

    prefix_a = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    prefix_b = fake_llm["calls"][1]["kwargs"]["user_cacheable_prefix"]
    # Sanity: the two asks really did load different documents, so
    # `docs_block` genuinely differs between them.
    assert prefix_a != prefix_b

    common_len = 0
    for a, b in zip(prefix_a, prefix_b):
        if a != b:
            break
        common_len += 1

    assert corpus_text in prefix_a[:common_len]


def test_compose_ask_answer_appends_documents_addendum_only_when_block_present(
    isolated_settings, fake_llm
):
    from app import ask_runner
    from app.prompts import ASK_SYSTEM_DOCUMENTS_ADDENDUM

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-with-no-docs-3")
    assert ASK_SYSTEM_DOCUMENTS_ADDENDUM not in fake_llm["calls"][0]["system"]

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-b", company_id="co-with-docs-3")
    _seed_file(db, "fx", src, company_id="co-with-docs-3", filename="a.txt")
    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-with-docs-3")
    assert ASK_SYSTEM_DOCUMENTS_ADDENDUM in fake_llm["calls"][1]["system"]


def test_compose_ask_answer_prd_branch_carries_documents(isolated_settings, fake_llm):
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="PRD-BRANCH DOCUMENT BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Sprntly_vs_Productboard_Comparison", enterprise_id=_CID,
        prd_context="=== CURRENT PRD CONTEXT ===\nSome PRD body.",
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert "PRD-BRANCH DOCUMENT BODY" in prefix


def test_compose_ask_answer_kg_branch_carries_documents(isolated_settings, fake_llm):
    from unittest.mock import patch

    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="KG-BRANCH DOCUMENT BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    with patch.object(
        ask_runner, "_retrieve_kg_bundle",
        return_value={"signals": [], "themes": [], "kg_refs": [], "empty": False},
    ), patch(
        "app.graph.retrieval.render_context_section", return_value="KG SECTION",
    ):
        ask_runner.compose_ask_answer(
            "asurion", "About Sprntly_vs_Productboard_Comparison", enterprise_id=_CID,
        )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert "KG-BRANCH DOCUMENT BODY" in prefix


def test_decision_log_factors_carry_document_counts_only(isolated_settings, fake_llm):
    import json as jsonmod

    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="Sprntly_vs_Productboard_Comparison.docx",
               extracted_text="a body nobody should see in factors")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Sprntly_vs_Productboard_Comparison", enterprise_id=_CID,
    )

    rows = db.table("agent_decision_log").select("*").execute().data

    def _factors(row):
        f = row["factors"]
        return jsonmod.loads(f) if isinstance(f, str) else f

    # This path now writes TWO rows: its own `answer` row, and the shared
    # `document_selection` row that document grounding writes from the callee
    # both ask paths go through — the skill-routed path wrote nothing at all
    # before, which is why a wrong topical selection there left no record.
    # The count is asserted per decision_type rather than in total, so this
    # keeps catching a stray extra write instead of being loosened to "some
    # rows".
    by_type = {}
    for row in rows:
        by_type.setdefault(row["decision_type"], []).append(row)
    assert sorted(by_type) == ["answer", "document_selection"]
    assert len(by_type["answer"]) == 1
    assert len(by_type["document_selection"]) == 1

    factors = _factors(by_type["answer"][0])
    assert factors["documents"] == 1
    assert factors["documents_loaded"] == 1
    assert isinstance(factors["documents"], int)
    assert isinstance(factors["documents_loaded"], int)

    # The actual invariant this test exists for, and it must hold for EVERY
    # row the path writes: counts and enums only. A decision log that quoted a
    # filename or a line of a document would put document content into an
    # audit table that is not scoped like the documents are.
    for row in rows:
        blob = jsonmod.dumps(_factors(row))
        assert "Sprntly_vs_Productboard_Comparison.docx" not in blob
        assert "a body nobody should see in factors" not in blob


def test_answer_single_shot_addendum_order_after_company_facts(isolated_settings, monkeypatch):
    import app.qa_agent as qa
    from app.qa_agent import RouteDecision, _answer_single_shot

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-order-1", "slug": "slug-co-order-1", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-order-1", "company_id": "co-order-1", "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    src = _seed_source(db, "src-order-1", company_id="co-order-1")
    _seed_file(db, "f1", src, company_id="co-order-1", filename="a.txt")

    captured = {}
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: captured.update(k) or _Result(
            {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9,
             "unanswered": ""}
        ),
    )
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id="roadmap", confidence=1.0, source="slash")
    _answer_single_shot(decision, "co-order-1", "what next?", [])

    system = captured["system"]
    from app.prompts import ASK_SYSTEM_COMPANY_FACTS_ADDENDUM, ASK_SYSTEM_DOCUMENTS_ADDENDUM

    assert system.index(ASK_SYSTEM_COMPANY_FACTS_ADDENDUM) < system.index(
        ASK_SYSTEM_DOCUMENTS_ADDENDUM
    )


def test_ask_get_returns_documents_key(tenant_client, isolated_settings):
    from app import db as db_mod

    t = tenant_client.make(slug="acme-docs")
    ask_id = db_mod.start_ask_job(company_id=t.company_id, dataset="acme-docs", question="q?")
    db_mod.complete_ask_job(ask_id, {
        "answer": "done", "key_points": [], "citations": [], "confidence": 1.0,
        "unanswered": "",
        "documents": [
            {"file_id": "f1", "filename": "a.txt", "source_name": "s", "uploaded_at": None,
             "loaded": False},
        ],
    })
    resp = t.client.get(f"/v1/ask/{ask_id}")
    assert resp.status_code == 200
    assert resp.json()["documents"] == [
        {"file_id": "f1", "filename": "a.txt", "source_name": "s", "uploaded_at": None,
         "loaded": False},
    ]

    ask_id_2 = db_mod.start_ask_job(company_id=t.company_id, dataset="acme-docs", question="q2?")
    db_mod.complete_ask_job(ask_id_2, {
        "answer": "done", "key_points": [], "citations": [], "confidence": 1.0,
        "unanswered": "",
    })
    resp2 = t.client.get(f"/v1/ask/{ask_id_2}")
    assert resp2.json()["documents"] == []


def test_ask_get_still_strips_citations_and_keeps_documents(tenant_client, isolated_settings):
    """Mirrors the real worker: citations are stripped BEFORE the payload is
    persisted (app.ask_job_runner._strip_citations, identical to
    app.routes.ask._strip_citations), not at GET time — so this seeds the
    job the same way the worker would, proving `documents` survives that
    stripping step untouched (AC21)."""
    from app import db as db_mod
    from app.routes.ask import _strip_citations

    t = tenant_client.make(slug="acme-docs-2")
    ask_id = db_mod.start_ask_job(company_id=t.company_id, dataset="acme-docs-2", question="q?")
    payload = _strip_citations({
        "answer": "done", "key_points": [], "citations": [{"source": "a", "evidence": "x"}],
        "confidence": 1.0, "unanswered": "",
        "documents": [
            {"file_id": "f1", "filename": "a.txt", "source_name": "s", "uploaded_at": None,
             "loaded": True},
        ],
    })
    db_mod.complete_ask_job(ask_id, payload)
    resp = t.client.get(f"/v1/ask/{ask_id}")
    body = resp.json()
    assert body["citations"] == []
    assert body["documents"]


def test_no_existing_signature_changed():
    from app.ask_runner import company_facts_block, compose_ask_answer
    from app.document_sources import (
        get_document_source,
        list_document_sources,
        list_source_files,
    )
    from app.qa_agent import _answer_single_shot, answer

    sig = inspect.signature(compose_ask_answer)
    assert set(sig.parameters) >= {
        "dataset", "question", "enterprise_id", "prd_context", "history",
        "on_delta",
    }
    # Every pre-fix keyword still binds with its pre-fix default, and every
    # pre-fix call shape (enumerated via `grep -rn 'compose_ask_answer'
    # backend/`) still binds unchanged — `history` is new and additive, never
    # required.
    assert sig.parameters["enterprise_id"].default is None
    assert sig.parameters["prd_context"].default == ""
    assert sig.parameters["on_delta"].default is None
    assert sig.parameters["history"].default is None
    sig.bind("asurion", "q?")
    sig.bind("asurion", "q?", enterprise_id="co-1")
    sig.bind("asurion", "q?", enterprise_id="co-1", prd_context="PRD block")
    sig.bind("asurion", "q?", enterprise_id="co-1", on_delta=lambda *_: None)
    sig.bind("asurion", "q?", enterprise_id="co-1", history=[{"role": "user", "content": "x"}])
    sig = inspect.signature(company_facts_block)
    assert list(sig.parameters) == ["enterprise_id"]
    sig = inspect.signature(list_document_sources)
    assert list(sig.parameters) == ["company_id"]
    sig = inspect.signature(list_source_files)
    assert list(sig.parameters) == ["company_id", "source_id"]
    sig = inspect.signature(get_document_source)
    assert list(sig.parameters) == ["company_id", "source_id"]
    sig = inspect.signature(answer)
    assert "enterprise_id" in sig.parameters and "question" in sig.parameters
    sig = inspect.signature(_answer_single_shot)
    assert set(sig.parameters) >= {
        "decision", "enterprise_id", "question", "history", "prd_context",
        "on_delta", "skill_spec", "on_phase",
    }


# ══════════════ Topical selection — finding a document by what it is about ══
#
# The incident these cover: selection used to require the question to nearly
# spell the filename (stem-token overlap at a fixed ratio). A user who asked
# about the TOPIC of a document they had uploaded was told to ask again using
# the exact filename. Selection is now two stages — documents the question
# NAMES, then documents the question is ABOUT, ranked by the catalog's hybrid
# lexical+semantic search — with no ratio and no score floor anywhere.

_CANDIDATES_FN = "document_find_candidates"


@pytest.fixture
def catalog_candidates():
    """Stub the hybrid-rank SQL function and hand back a setter.

    `document_find_candidates` keeps its ranking AND its tenant filter inside
    the Postgres function body; the fake client has no SQL engine behind
    `rpc()`. So these tests fix the function's RESULT and assert what the
    reader does with it — which providers it will select, how it orders them,
    how it accounts for them. The ranking itself is exercised against real
    Postgres, and whether the ranking picks the right document for a real
    question is a live-verification question, not a unit-test one.
    """
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)
    FakeSupabaseClient.rpc_calls.clear()

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_CANDIDATES_FN] = rows

    yield _set
    # Class-level state on the fake: leaving a stub behind would silently feed
    # candidates to every later test in the session.
    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)
    FakeSupabaseClient.rpc_calls.clear()


def _seed_catalog_row(
    db, *, provider, external_id, title, company_id=_CID, source_name="Uploads",
    summary="", topics=(), doc_date="2026-08-02T10:00:00+00:00",
    conversation_id=None, user_id=None,
):
    _seed_company(db, company_id)
    db.table("document_catalog").insert({
        "company_id": company_id,
        "provider": provider,
        "external_id": external_id,
        "title": title,
        "source_name": source_name,
        "content_hash": f"hash-{external_id}",
        "summary": summary,
        "topics": list(topics),
        "doc_date": doc_date,
        "conversation_id": conversation_id,
        "user_id": user_id,
    }).execute()


def _candidate(
    *, provider, external_id, title, score=0.04, source_name="Uploads",
    summary="", topics=(), doc_date="2026-08-02T10:00:00+00:00",
    conversation_id=None,
):
    """One row shaped exactly like `document_find_candidates` returns."""
    return {
        "id": f"cat-{external_id}",
        "provider": provider,
        "external_id": external_id,
        "title": title,
        "source_name": source_name,
        "summary": summary,
        "topics": list(topics),
        "url": None,
        "doc_date": doc_date,
        "conversation_id": conversation_id,
        "score": score,
    }


# T1 — the reported incident.
def test_topic_question_loads_the_document_it_is_about(
    isolated_settings, catalog_candidates
):
    """The user's verbatim question, against the document it is about.

    The file is "Sprntly_vs_Productboard_Comparison.docx"; he wrote "product
    board" as two words and never named the file. Stem-token overlap scored
    1/3 against a 0.8 requirement, so nothing loaded and the answer told him
    to come back with the exact filename. The document is an ordinary
    workspace upload, so it is in scope for body resolution.

    Whether the composed answer then READS well is live-verification's
    question — a stubbed model cannot judge content fidelity. What is
    mechanically checkable, and is the whole defect, is that the body loads.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _, file_id, text = _seed_incident_fixture(db)
    _seed_catalog_row(
        db, provider="uploads", external_id=file_id,
        title="Sprntly_vs_Productboard_Comparison.docx",
        source_name="Competitive research",
        summary="Compares Sprntly and Productboard on PRD-to-prototype speed.",
        topics=["competitive comparison", "productboard", "prd tooling"],
    )
    catalog_candidates([_candidate(
        provider="uploads", external_id=file_id,
        title="Sprntly_vs_Productboard_Comparison.docx",
        source_name="Competitive research",
        summary="Compares Sprntly and Productboard on PRD-to-prototype speed.",
    )])

    block, manifest = document_grounding(
        _CID, "give me a summary of the product board vs sprntly discussion"
    )

    entry = next(m for m in manifest if m["file_id"] == file_id)
    assert entry["loaded"] is True, (
        "the document the question is about did not load — this is the "
        "reported defect"
    )
    assert text[:80] in block


# T8 — the exclusion is GONE. Replaces the previous
# `test_only_providers_whose_bodies_resolve_are_selectable`, which asserted
# the opposite and was correct while it stood: Drive and Confluence had no
# body reader, so ranking one and then failing to read it would have told the
# user their document was present but unreadable — verbatim the complaint this
# whole line of work exists to close. They have readers now, so the exclusion
# is deleted rather than weakened, and this is the test that proves it.
def test_every_catalogued_provider_is_selectable(
    isolated_settings, catalog_candidates
):
    """All four providers reach selection, and no Python-side provider filter
    survives anywhere between the rank and the choice.

    Asserted at BOTH levels on purpose. `_topical_candidates` is where the old
    predicate lived, so its output is checked directly — a reinstated filter
    would show up there even if the end-to-end block happened to look right.
    """
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-1", name="Competitive research")
    _seed_file(
        db, "file-1", src, filename="Pricing_Notes.docx",
        extracted_text="Usage-based pricing lands in Q3." * 10,
    )
    _seed_catalog_row(
        db, provider="uploads", external_id="file-1", title="Pricing_Notes.docx",
        summary="Usage-based pricing replaces seat pricing in Q3.",
    )

    question = "what is happening with enterprise billing"
    catalog_candidates([
        _candidate(provider="confluence", external_id="page-99",
                   title="Enterprise billing", source_name="Finance space",
                   score=0.09),
        _candidate(provider="google_drive", external_id="drive-77",
                   title="Billing model 2026", source_name="Finance drive",
                   score=0.08),
        _candidate(provider="uploads", external_id="file-1",
                   title="Pricing_Notes.docx", score=0.02),
        _candidate(provider="chat_attachment", external_id="turn:1:attachment:0",
                   title="billing.pdf", score=0.01),
    ])

    candidates = ask_runner._topical_candidates(
        _CID, question, question_embedding=None,
        conversation_id=None, user_id=None, exclude_external_ids=set(),
    )
    assert [c["provider"] for c in candidates] == [
        "confluence", "google_drive", "uploads", "chat_attachment"
    ], "a provider filter is still discarding rows the rank returned"

    # And the constant that used to hold the exclusion is gone outright, so it
    # cannot be reintroduced by re-adding a single call site.
    assert not hasattr(ask_runner, "SELECTABLE_PROVIDERS")


# T2 — topical selection over WORKSPACE uploads.
def test_workspace_upload_loads_from_a_question_that_never_names_it(
    isolated_settings, catalog_candidates
):
    """A workspace upload, a question with no filename in it at all.

    Deliberately not a chat attachment: an attachment is hardcoded loaded and
    reaches the model through history-folding, so an attachment fixture would
    pass this test without either selection stage running — proving nothing
    about the mechanism under change.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a", name="Research")
    body = "Churn concentrates in month two of the annual plan. " * 30
    _seed_file(db, "f1", src, filename="Q3_retention_deep_dive.docx",
               extracted_text=body)
    _seed_catalog_row(
        db, provider="uploads", external_id="f1",
        title="Q3_retention_deep_dive.docx", source_name="Research",
        summary="Where and when customers churn on the annual plan.",
        topics=["churn", "retention", "annual plan"],
    )
    catalog_candidates([_candidate(
        provider="uploads", external_id="f1",
        title="Q3_retention_deep_dive.docx",
        summary="Where and when customers churn on the annual plan.",
    )])

    block, manifest = document_grounding(_CID, "why are customers leaving us?")

    entry = next(m for m in manifest if m["file_id"] == "f1")
    assert entry["loaded"] is True
    assert entry["match"] == "topic"
    assert entry["rank"] == 1
    assert "Churn concentrates in month two" in block


# T3 — the no-floor consequence, stated honestly.
def test_documents_may_load_for_an_irrelevant_question_and_the_prompt_says_ignore(
    isolated_settings, catalog_candidates
):
    """With no score floor, Stage T fills its slots whenever the catalog is
    non-empty — including for questions no document is relevant to.

    That is accepted, not a bug: a wrong load costs bounded budget, a wrong
    denial is the incident this change exists to fix. What keeps the ANSWER
    honest is the prompt, not a hidden threshold — so the contract asserted
    here is "an irrelevant document may load, AND the model is told to ignore
    what doesn't bear on the question", which is the pair that has to hold
    together.
    """
    from app.ask_runner import document_grounding
    from app.prompts import ASK_SYSTEM_DOCUMENTS_ADDENDUM

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a", name="Ops")
    _seed_file(db, "f1", src, filename="office_move_logistics.docx",
               extracted_text="The new floor plan seats 40." * 10)
    _seed_catalog_row(
        db, provider="uploads", external_id="f1",
        title="office_move_logistics.docx", source_name="Ops",
        summary="Seating and logistics for the office move.",
    )
    catalog_candidates([_candidate(
        provider="uploads", external_id="f1",
        title="office_move_logistics.docx",
        summary="Seating and logistics for the office move.",
    )])

    _, manifest = document_grounding(_CID, "what is our API rate limit?")

    # It loads. There is no floor that would have stopped it, by design.
    assert next(m for m in manifest if m["file_id"] == "f1")["loaded"] is True
    # And the instruction that makes that safe is present and explicit.
    assert "IGNORE the ones that don't" in ASK_SYSTEM_DOCUMENTS_ADDENDUM


# T4 — conflict: both documents load, with their dates, into the prompt.
def test_two_conflicting_documents_both_reach_the_prompt_with_their_dates(
    isolated_settings, catalog_candidates
):
    """Mechanically checkable half of the conflict contract.

    Whether the model then NAMES the disagreement instead of silently picking
    one is real-model behaviour and belongs to live verification. What can be
    proven here is that it has what it needs to: both bodies, and both dates.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a", name="Pricing")
    _seed_file(db, "old", src, filename="pricing_2025.docx",
               extracted_text="Enterprise pricing is per seat." * 10,
               uploaded_at="2025-06-01T00:00:00+00:00")
    _seed_file(db, "new", src, filename="pricing_2026.docx",
               extracted_text="Enterprise pricing is usage-based." * 10,
               uploaded_at="2026-07-01T00:00:00+00:00")
    for ext, title, date in (
        ("old", "pricing_2025.docx", "2025-06-01T00:00:00+00:00"),
        ("new", "pricing_2026.docx", "2026-07-01T00:00:00+00:00"),
    ):
        _seed_catalog_row(db, provider="uploads", external_id=ext, title=title,
                          source_name="Pricing", summary="How enterprise is priced.",
                          doc_date=date)
    catalog_candidates([
        _candidate(provider="uploads", external_id="new", title="pricing_2026.docx",
                   doc_date="2026-07-01T00:00:00+00:00", score=0.05),
        _candidate(provider="uploads", external_id="old", title="pricing_2025.docx",
                   doc_date="2025-06-01T00:00:00+00:00", score=0.04),
    ])

    block, manifest = document_grounding(_CID, "how do we price enterprise?")

    loaded = {m["file_id"] for m in manifest if m["loaded"]}
    assert loaded == {"old", "new"}
    assert "Enterprise pricing is per seat." in block
    assert "Enterprise pricing is usage-based." in block
    # Dates are what let the model reason about which supersedes which.
    assert "2025-06-01" in block and "2026-07-01" in block


# T5 — Stage N still works.
def test_naming_the_file_still_loads_it_without_any_catalog(
    isolated_settings, catalog_candidates
):
    """Stage N is unchanged and does not depend on the catalog at all: naming
    a document must keep working when the catalog is empty, unreachable, or
    has never been populated for this tenant."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _, file_id, text = _seed_incident_fixture(db)
    catalog_candidates([])

    _, manifest = document_grounding(
        _CID, "What does the Sprntly_vs_Productboard_Comparison document say?"
    )

    entry = next(m for m in manifest if m["file_id"] == file_id)
    assert entry["loaded"] is True
    assert entry["match"] == "named"
    # Named matches carry no rank — they did not come from the ranking.
    assert entry["rank"] is None


# T6 — conversation scope outranks workspace scope on equal match.
def test_conversation_scoped_document_outranks_workspace_on_equal_score(
    isolated_settings, catalog_candidates
):
    """The SQL orders by fused score alone, so this precedence is applied in
    Python and asserted here rather than assumed from the query."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    catalog_candidates([
        # Workspace row arrives FIRST from the ranking; equal score.
        _candidate(provider="uploads", external_id="workspace-doc",
                   title="workspace.docx", score=0.05),
        _candidate(provider="chat_attachment", external_id="turn:9:attachment:0",
                   title="attached.docx", score=0.05, conversation_id=42),
    ])

    ordered = ask_runner._topical_candidates(
        _CID, "pricing", question_embedding=None,
        conversation_id=42, user_id="user-x", exclude_external_ids=set(),
    )

    assert [c["external_id"] for c in ordered] == [
        "turn:9:attachment:0", "workspace-doc",
    ]


def test_a_better_workspace_match_still_beats_a_conversation_document(
    isolated_settings, catalog_candidates
):
    """The tie-break is a TIE-break: session scope wins when the match is
    equal, and must not let a weakly-matching attachment displace a document
    that genuinely answers the question."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    catalog_candidates([
        _candidate(provider="uploads", external_id="workspace-doc",
                   title="workspace.docx", score=0.09),
        _candidate(provider="chat_attachment", external_id="turn:9:attachment:0",
                   title="attached.docx", score=0.01, conversation_id=42),
    ])

    ordered = ask_runner._topical_candidates(
        _CID, "pricing", question_embedding=None,
        conversation_id=42, user_id="user-x", exclude_external_ids=set(),
    )

    assert [c["external_id"] for c in ordered] == [
        "workspace-doc", "turn:9:attachment:0",
    ]


# T7 — embedding unavailable degrades to lexical-only, and it is recorded.
def test_missing_embedding_degrades_to_lexical_and_is_recorded(
    isolated_settings, monkeypatch
):
    """A zero vector is worse than no vector — in cosine kNN it ranks
    arbitrarily — so the accessor drops it. The point of this test is that the
    CALLER still surfaces that the degradation happened: the system keeps
    answering, slightly worse, and without this nothing in the record says
    why.
    """
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)

    # Exactly what embed_texts returns with no API key configured.
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts", lambda texts, **kw: [[0.0] * 1536]
    )

    vec, degraded = ask_runner._question_embedding(_CID, "anything")

    assert vec is None
    assert degraded is True


def test_a_usable_embedding_is_not_reported_as_degraded(isolated_settings, monkeypatch):
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts", lambda texts, **kw: [[0.02] * 1536]
    )

    vec, degraded = ask_runner._question_embedding(_CID, "anything")

    assert vec is not None and degraded is False


def test_degraded_embedding_is_written_to_the_decision_log(
    isolated_settings, fake_llm, monkeypatch
):
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="notes.txt", extracted_text="body")
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts", lambda texts, **kw: [[0.0] * 1536]
    )
    logged = {}
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: logged.update(kw),
    )

    ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert logged["factors"]["retrieval_embedding_degraded"] is True


# The accessor above is correct and its result is thrown away one call
# later: `question_embedding=None` means "compute it yourself" to
# `retrieve_context`, not "there is none". These pin the fix: the degraded
# flag `_question_embedding` already produces now rides across the
# `retrieve_context` boundary (`skip_semantic`) instead of collapsing back to
# a self-embed on a doomed zero vector.
#
# NOTE: `isolated_settings` never sets `OPENAI_API_KEY`, so `settings.
# openai_api_key` is already unset ("") for every test below that doesn't
# explicitly configure one — the real no-key `embed_texts` fallback runs
# unmocked, exactly as it would in production with no key configured.


def test_no_key_never_runs_theme_knn(isolated_settings, fake_llm):
    """With no key, a direct-path ask must reach `find_candidates` zero times
    for theme kNN — no call with a zero vector, no call with any vector.
    RED before the fix: `question_embedding=None` reached `retrieve_context`,
    which self-embedded, got a zero vector from the no-key fallback, and
    shipped it to `find_candidates` anyway."""
    from unittest.mock import patch

    from app import ask_runner
    from app.graph.facade import GraphFacade

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: calls.append(vec) or [],
    ):
        ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert calls == [], f"find_candidates called for theme kNN with no key: {calls}"


def test_no_key_issues_exactly_one_embedding_call(isolated_settings, fake_llm, monkeypatch):
    """`embed_texts` is called at most once per direct-path ask — the shared
    ContextVar / `_resolve_question_embedding` machinery exists specifically
    to keep this at exactly one. RED before the fix: `retrieve_context`
    re-embedded a second time because `None` collapsed back to "compute your
    own"."""
    from app import ask_runner
    import app.graph.embeddings as embeddings_mod

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    calls: list = []
    real_embed_texts = embeddings_mod.embed_texts

    def _counting_embed(texts, **kw):
        calls.append(texts)
        return real_embed_texts(texts, **kw)

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _counting_embed)

    ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert len(calls) == 1, f"expected exactly one embed_texts call, got {len(calls)}"


def test_no_key_still_returns_an_answer(isolated_settings, fake_llm):
    """With no key, `compose_ask_answer` still returns a well-formed payload —
    the guard degrades, it never raises and never yields an empty answer."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "grounded on lexical channels only", "key_points": ["k"],
        "citations": [], "confidence": 0.6, "unanswered": "",
    }

    payload = ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert payload["answer"] == "grounded on lexical channels only"
    assert payload["key_points"] == ["k"]


def test_no_key_bundle_is_recent_signals_not_empty_and_not_raised(
    isolated_settings, fake_llm
):
    """The documented degradation shape holds: with no key, theme kNN never
    runs, but a KG with recent non-stale signals still yields a non-empty
    bundle (the recent-signals fallback) — not an exception, not an empty
    answer. `retrieve_context`'s docstring binds this: "never raises on a
    partial-KG read — degrades to an emptier bundle and logs."""
    from datetime import datetime, timezone

    from app import ask_runner
    from app.graph.facade import GraphFacade
    from app.graph.types import Signal

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    GraphFacade().write_signal(
        _CID,
        Signal(
            enterprise_id=_CID,
            source_type="analytics",
            kind="metric_shift",
            content="recent-only signal",
            valid_at=datetime.now(timezone.utc),
        ),
    )

    ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    user = fake_llm["calls"][0]["user"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in user
    assert "recent-only signal" in user


def test_embed_texts_raising_does_not_break_the_ask(isolated_settings, fake_llm, monkeypatch):
    """The embedder raising (a real HTTP failure, not just a missing key)
    leaves the answer intact — resilience must hold on this path exactly as
    it does on the no-key path."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts",
        lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    payload = ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert payload["answer"] == "x"


def test_decision_log_degraded_flag_matches_behaviour(
    isolated_settings, fake_llm, monkeypatch
):
    """When `retrieval_embedding_degraded` is True in the decision log, no
    semantic channel ran for EITHER consumer — not just document selection.
    This is the exact gap the ticket closes: the flag previously read as "we
    degraded to lexical-only" while the KG silently ran kNN on a zero
    vector."""
    from unittest.mock import patch

    from app import ask_runner
    from app.graph.facade import GraphFacade

    db = isolated_settings["supabase"]
    _seed_company(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    logged = {}
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: logged.update(kw),
    )
    calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: calls.append(vec) or [],
    ):
        ask_runner.compose_ask_answer("asurion", "anything", enterprise_id=_CID)

    assert logged["factors"]["retrieval_embedding_degraded"] is True
    assert calls == [], (
        "retrieval_embedding_degraded=True but theme kNN ran anyway — "
        "the audit row would be lying"
    )


def test_accessor_pinned_tests_unchanged():
    """CLOSED-WORLD TRAP guard: this diff must not touch `_question_embedding`
    or its three pinned tests. Confirms they still exist under their exact
    names in this module — a rename or deletion here would be a silent
    regression this suite otherwise wouldn't catch. (The "unedited" half of
    this AC is enforced by code review against the diff, not a runtime
    assertion.)"""
    import tests.test_ask_document_retrieval as _mod

    for name in (
        "test_missing_embedding_degrades_to_lexical_and_is_recorded",
        "test_a_usable_embedding_is_not_reported_as_degraded",
        "test_degraded_embedding_is_written_to_the_decision_log",
    ):
        assert hasattr(_mod, name), f"pinned test {name} is missing"


# T8 — catalog read failure degrades Stage T only.
def test_catalog_failure_leaves_stage_n_and_the_answer_intact(
    isolated_settings, fake_llm, monkeypatch
):
    """A catalog outage costs topical selection and nothing else. Stage N
    keeps working off `list_company_files`, the index still renders, the
    answer is still produced — and the deleted ratio is NOT resurrected as a
    fallback, because the fallback for "cannot rank" is "do not rank", not
    "guess from the filename".
    """
    from app import ask_runner

    db = isolated_settings["supabase"]
    _, file_id, text = _seed_incident_fixture(db)
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    def _boom(*a, **k):
        raise RuntimeError("catalog is down")

    monkeypatch.setattr(ask_runner, "find_catalog_candidates", _boom)
    monkeypatch.setattr(ask_runner, "list_catalog_documents", _boom)

    # Naming the document still loads it.
    block, manifest = ask_runner.document_grounding(
        _CID, "What does the Sprntly_vs_Productboard_Comparison document say?"
    )
    assert next(m for m in manifest if m["file_id"] == file_id)["loaded"] is True
    assert text[:60] in block

    # And a topical question simply loads nothing, rather than erroring.
    _, topical_manifest = ask_runner.document_grounding(
        _CID, "give me a summary of the product board vs sprntly discussion"
    )
    assert all(not m["loaded"] for m in topical_manifest)

    # The answer is still produced.
    payload = ask_runner.compose_ask_answer(
        "asurion", "anything at all", enterprise_id=_CID
    )
    assert payload["answer"] == "x"


# T9 — the index line carries every field selection is judged on.
def test_index_line_carries_summary_topics_date_scope_and_loaded_marker(
    isolated_settings, catalog_candidates
):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a", name="Research")
    _seed_file(db, "f1", src, filename="retention.docx", extracted_text="body",
               uploaded_at="2026-08-02T10:00:00+00:00")
    _seed_catalog_row(
        db, provider="uploads", external_id="f1", title="retention.docx",
        source_name="Research",
        summary="Where and when customers churn on the annual plan.",
        topics=["churn", "retention"],
    )
    catalog_candidates([])

    block, _ = document_grounding(_CID, "unrelated question")

    line = next(l for l in block.splitlines() if l.startswith("- retention.docx"))
    assert "retention.docx" in line                      # filename
    assert "source: Research" in line                    # source + scope tag
    assert "2026-08-02" in line                          # date
    assert "Where and when customers churn" in line      # summary
    assert "Topics: churn, retention." in line           # topics
    assert "[not loaded for this question]" in line      # loaded-marker


def test_loaded_marker_flips_for_a_selected_document(
    isolated_settings, catalog_candidates
):
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _, file_id, _ = _seed_incident_fixture(db)
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "What does the Sprntly_vs_Productboard_Comparison document say?"
    )

    line = next(
        l for l in block.splitlines()
        if l.startswith("- Sprntly_vs_Productboard_Comparison.docx")
    )
    assert "[loaded for this question]" in line


# T10 — the partial-index contract, both directions.
def test_a_complete_index_still_permits_an_absence_claim(isolated_settings):
    """The other half of the partial-index rule, and the one that is easy to
    lose: below the cap the index IS the complete inventory, so it must not be
    marked partial — otherwise the model is forbidden from ever saying a
    document does not exist, which was the original incident's failure mode
    pointing the other way."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="only_one.txt")

    block, _ = document_grounding(_CID, "unrelated")

    assert "PARTIAL" not in block
    assert "may still exist" not in block


# T11 — one embedding per ask, shared by both consumers.
def test_the_question_is_embedded_once_and_shared_by_both_consumers(
    isolated_settings, fake_llm, monkeypatch
):
    """Document selection runs BEFORE knowledge-graph retrieval, and on
    PRD-grounded asks KG retrieval never runs at all — so neither consumer can
    produce the vector for the other. It is computed once at the top of the
    ask and threaded into both; this test is what stops that regressing into
    two embedding calls per question.
    """
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db)
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="notes.txt", extracted_text="body")
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    calls = []

    def _embed(texts, **kw):
        calls.append(texts)
        return [[0.03] * 1536 for _ in texts]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _embed)

    seen = {}
    real_grounding = ask_runner.document_grounding

    # `**kw` rather than a spelled-out signature: this spy exists to observe
    # the EMBEDDING argument, and every other parameter grounding grows (a
    # conversation id, now a history) is passed straight through. Naming them
    # here made the spy a second copy of the signature that had to be kept in
    # step with the real one, and it fell behind.
    def _spy_grounding(eid, q, conversation_id=None, *, question_embedding=None, **kw):
        seen["documents"] = question_embedding
        return real_grounding(
            eid, q, conversation_id, question_embedding=question_embedding, **kw
        )

    monkeypatch.setattr(ask_runner, "document_grounding", _spy_grounding)

    def _spy_retrieve(facade, eid, q, **kw):
        seen["kg"] = kw.get("question_embedding")
        return {"empty": True}

    monkeypatch.setattr("app.graph.retrieval.retrieve_context", _spy_retrieve)

    ask_runner.compose_ask_answer("asurion", "how do we price?", enterprise_id=_CID)

    assert len(calls) == 1, f"expected one embedding call per ask, got {len(calls)}"
    assert seen["documents"] is not None
    assert seen["documents"] == seen["kg"], (
        "both consumers must receive the SAME vector, not two of their own"
    )


def test_retrieve_context_still_embeds_for_callers_that_pass_nothing(
    isolated_settings, monkeypatch
):
    """The sharing is opt-in: every other caller of `retrieve_context` keeps
    the self-contained behaviour it relies on."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    db = isolated_settings["supabase"]
    _seed_company(db)
    calls = []

    def _embed(texts, **kw):
        calls.append(texts)
        return [[0.03] * 1536 for _ in texts]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _embed)

    retrieve_context(GraphFacade(), _CID, "a question")

    assert len(calls) == 1


# T12 — the prompt clauses this change depends on, as a property.
def test_addendum_carries_the_conflict_and_ignore_irrelevant_clauses():
    """Both clauses are load-bearing for a no-floor selection design: without
    ignore-irrelevant, generous loading turns into padded answers; without the
    conflict clause, two disagreeing documents are silently resolved to
    whichever the model read first."""
    from app.prompts import ASK_SYSTEM_DOCUMENTS_ADDENDUM as a

    # Ignore-irrelevant.
    assert "IGNORE the ones that don't" in a
    assert "never pad an answer" in a
    # Conflict.
    assert "CONFLICTING claims" in a
    assert "name both documents" in a
    assert "Never silently answer from one side of a conflict." in a
    # A summary is a routing hint, not loaded content.
    assert "ROUTING HINT" in a
    # The partial-index existence rule.
    assert "PARTIAL" in a


# ══════════ Connected-source documents — findable without naming them ═══════
#
# Measured on staging before this landed: "what were the most recent product
# release notes?" carried eight documents into the prompt, every one of them
# an upload, and not a single Confluence page — and the model, correctly
# following its instructions, offered to check Confluence if asked directly.
# Asking it to "check Confluence for release notes in the SD space" then
# returned real wiki content. Routing metadata was null both times, so no
# interceptor was involved: the naming distinction WAS the defect.
#
# These cover the mechanism that closes it. Whether the composed answer then
# reads well is live-verification's question — a stubbed model cannot judge
# content fidelity — but whether the body reaches the prompt at all is
# mechanically checkable, and that is the whole failure.

_CONFLUENCE_PAGE_ID = "page-sd-4471"
_DRIVE_FILE_ID = "drive-file-9922"


class _FakeConfluenceFetch:
    def __init__(self, page=None, *, session=object(), raises=False):
        self.page = page
        self.session = session
        self.raises = raises
        self.pages_fetched = []
        self.sessions_opened = 0

    def open_session(self, enterprise_id):
        self.sessions_opened += 1
        return self.session

    def get_page(self, session, page_id):
        self.pages_fetched.append(page_id)
        if self.raises:
            raise RuntimeError("auth expired")
        return self.page


@pytest.fixture
def confluence_pages(monkeypatch):
    """Stub the live wiki read. Bodies are fetched at ANSWER time by design
    (nothing is cached), so the seam under test is the fetch itself."""
    from app.connectors import confluence_fetch

    def _install(**kwargs):
        fake = _FakeConfluenceFetch(**kwargs)
        monkeypatch.setattr(confluence_fetch, "open_session", fake.open_session)
        monkeypatch.setattr(confluence_fetch, "get_page", fake.get_page)
        return fake

    return _install


def _seed_drive_corpus_file(db, data_dir, *, file_id, label, slug, name, text):
    """A synced Drive file: its corpus markdown, plus the provenance row that
    records WHERE that markdown landed."""
    from app import document_bodies
    from app.datasets import dataset_path

    target = dataset_path(slug) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(_CID, file_id),
        "enterprise_id": _CID,
        "source_type": "google_drive",
        "label": label,
        "config": {
            "file_id": file_id, "md_dataset": slug, "md_file": name,
        },
        "status": "active",
    }).execute()


# T1 — RED-first. A Confluence page, found by topic, never named.
def test_topic_question_loads_a_confluence_page_without_naming_confluence(
    isolated_settings, catalog_candidates, confluence_pages
):
    """The staging question, verbatim, against a wiki page it is about.

    The question names neither Confluence nor the page. Before body
    resolution existed the page was catalogued and summarised but could not be
    selected, so the prompt carried nothing from the wiki and the answer said
    to go and ask about Confluence specifically.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes for the August product release.",
        topics=["release notes", "shipping"],
    )
    catalog_candidates([_candidate(
        provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes for the August product release.",
    )])
    fake = confluence_pages(page={
        "id": _CONFLUENCE_PAGE_ID,
        "text": "August release ships on the 14th. ChoisBits is shurting down.",
    })

    block, manifest = document_grounding(
        _CID, "what were the most recent product release notes?"
    )

    entry = next(
        m for m in manifest if m["file_id"] == f"confluence:{_CONFLUENCE_PAGE_ID}"
    )
    assert entry["loaded"] is True, (
        "a wiki page the question is about did not load — this is the defect"
    )
    assert "August release ships on the 14th" in block
    assert fake.pages_fetched == [_CONFLUENCE_PAGE_ID]
    # Never named in the question, and the question is what selection saw.
    assert "confluence" not in "what were the most recent product release notes?"


# T2 — RED-first. A Drive file, found by topic, never named.
def test_topic_question_loads_a_drive_file_without_naming_it(
    isolated_settings, catalog_candidates
):
    """Drive's half of the same defect. The body comes from the corpus
    markdown its own sync already wrote — read back via the path that sync
    recorded, because the name is not reconstructible."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _seed_drive_corpus_file(
        db, data_dir, file_id=_DRIVE_FILE_ID, label="Billing model 2026",
        slug="acme", name="billing_model_2026.md",
        text="Enterprise billing moves to usage-based in Q1.",
    )
    _seed_catalog_row(
        db, provider="google_drive", external_id=_DRIVE_FILE_ID,
        title="Billing model 2026", source_name="Google Drive",
        summary="Enterprise billing moves from seats to usage in Q1.",
        topics=["billing", "usage-based pricing"],
    )
    catalog_candidates([_candidate(
        provider="google_drive", external_id=_DRIVE_FILE_ID,
        title="Billing model 2026", source_name="Google Drive",
        summary="Enterprise billing moves from seats to usage in Q1.",
    )])

    block, manifest = document_grounding(
        _CID, "how is enterprise billing changing next year"
    )

    entry = next(
        m for m in manifest if m["file_id"] == f"google_drive:{_DRIVE_FILE_ID}"
    )
    assert entry["loaded"] is True
    assert "Enterprise billing moves to usage-based in Q1." in block


# T5 — AC5. A fetch failure is a degradation, never a denial.
def test_confluence_fetch_failure_degrades_to_summary_only(
    isolated_settings, catalog_candidates, confluence_pages
):
    """The page stays in the Index with its summary, the model is told the
    contents could not be loaded AND why, and nothing anywhere reads as "this
    document does not exist" — which is the incident this work exists to
    close, and the one shape that must never come back."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes for the August product release.",
        topics=["release notes"],
    )
    catalog_candidates([_candidate(
        provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes for the August product release.",
    )])
    confluence_pages(raises=True)

    block, manifest = document_grounding(
        _CID, "what were the most recent product release notes?"
    )

    # Still in the index, still summarised — existence is not in doubt.
    assert "Release notes — August" in block
    assert "Ship dates and fixes for the August product release." in block
    assert "Confluence: SD" in block
    # And explicitly marked as present-but-unloaded, with a reason.
    assert "could not be loaded for this question" in block
    assert "this document exists" in block

    # `loaded` is about the BODY reaching the prompt, so it is False — the
    # manifest must not record an intention as a fact.
    entry = next(
        m for m in manifest if m["file_id"] == f"confluence:{_CONFLUENCE_PAGE_ID}"
    )
    assert entry["loaded"] is False
    assert entry["match"] == "topic"

    # Nothing in the block may read as absence.
    lowered = block.lower()
    for forbidden in (
        "does not exist", "no such document", "not in any connected source",
        "has not been uploaded",
    ):
        assert forbidden not in lowered


def test_an_unfetchable_page_is_not_quoted_as_empty_content(
    isolated_settings, catalog_candidates, confluence_pages
):
    """The distinction AC9 exists for, at block level: a page that could not
    be fetched contributes NO body section at all. Rendering it with an empty
    body would tell the model it had read a page with nothing in it."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes.",
    )
    catalog_candidates([_candidate(
        provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
    )])
    confluence_pages(page=None)

    block, _ = document_grounding(_CID, "what shipped recently")

    assert "## Contents loaded for this question" not in block


# AC6 — the fetch count is bounded by the selection cap that already exists.
def test_confluence_fetches_are_bounded_by_the_selection_cap(
    isolated_settings, catalog_candidates, confluence_pages
):
    """Ten ranked pages, three slots. The worst case for added latency on an
    ask is therefore three page fetches, sharing one session."""
    from app.ask_runner import MAX_SELECTED_DOCUMENTS, document_grounding

    db = isolated_settings["supabase"]
    rows = []
    for i in range(10):
        page_id = f"page-{i}"
        _seed_catalog_row(
            db, provider="confluence", external_id=page_id,
            title=f"Wiki page {i}", source_name="SD",
            summary=f"Notes about topic {i}.",
        )
        rows.append(_candidate(
            provider="confluence", external_id=page_id,
            title=f"Wiki page {i}", source_name="SD", score=1.0 - i / 100,
        ))
    catalog_candidates(rows)
    fake = confluence_pages(page={"id": "x", "text": "body text"})

    document_grounding(_CID, "tell me about the wiki")

    assert len(fake.pages_fetched) == MAX_SELECTED_DOCUMENTS
    assert fake.sessions_opened == 1


# AC4 — no caching. Two asks, two fetches.
def test_a_second_ask_fetches_the_page_again(
    isolated_settings, catalog_candidates, confluence_pages
):
    """Bodies are read live every time. A wiki page can change between two
    questions in one conversation, and the cache that was considered — the
    conversation turn row — does not exist while an answer is being composed
    and would replay a body clamped smaller than a live fetch delivers."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD", summary="Ship dates.",
    )
    catalog_candidates([_candidate(
        provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
    )])
    fake = confluence_pages(page={"id": _CONFLUENCE_PAGE_ID, "text": "first read"})

    document_grounding(_CID, "what shipped recently")
    document_grounding(_CID, "what shipped recently")

    assert fake.pages_fetched == [_CONFLUENCE_PAGE_ID, _CONFLUENCE_PAGE_ID]


def test_a_connected_document_is_indexed_even_when_nothing_selects_it(
    isolated_settings, catalog_candidates, confluence_pages
):
    """AC5's precondition, asserted on its own: the page is in the Index
    because it is in the wiki, not because this question happened to pick it.
    Without this, "stays in the index" has nothing to stay in."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Release notes — August", source_name="SD",
        summary="Ship dates and fixes.",
    )
    catalog_candidates([])
    fake = confluence_pages(page={"id": _CONFLUENCE_PAGE_ID, "text": "body"})

    block, manifest = document_grounding(_CID, "something else entirely")

    line = next(
        line for line in block.splitlines()
        if line.startswith("- Release notes — August")
    )
    assert "Confluence: SD" in line
    assert "[not loaded for this question]" in line
    # Not selected means not fetched: the index costs no network.
    assert fake.pages_fetched == []
    assert len(manifest) == 1


def test_naming_a_connected_document_selects_it_through_stage_n(
    isolated_settings, catalog_candidates, confluence_pages
):
    """Stage N extended over the catalog. Naming a wiki page is as
    unambiguous a request as naming an uploaded file, and no ranking should
    have to be involved for it to land."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Pricing rollout plan", source_name="SD", summary="Rollout steps.",
    )
    # Stage T contributes nothing, so a pass here is Stage N's alone.
    catalog_candidates([])
    confluence_pages(page={"id": _CONFLUENCE_PAGE_ID, "text": "Step one: notify."})

    block, manifest = document_grounding(
        _CID, "summarise the Pricing rollout plan for me"
    )

    entry = next(
        m for m in manifest if m["file_id"] == f"confluence:{_CONFLUENCE_PAGE_ID}"
    )
    assert entry["match"] == "named"
    assert entry["loaded"] is True
    assert "Step one: notify." in block


def test_uploads_and_connected_documents_share_one_index_cap(
    isolated_settings, catalog_candidates
):
    """Connecting a wiki must not grow the worst-case prompt. Uploads fill the
    cap first and the overflow is declared PARTIAL, rather than the index
    quietly getting a second allowance."""
    from app.ask_runner import MAX_INDEX_ENTRIES, document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    for i in range(MAX_INDEX_ENTRIES):
        _seed_file(
            db, f"f{i}", src, filename=f"doc-{i}.txt",
            uploaded_at=f"2026-01-{(i % 27) + 1:02d}T00:00:00+00:00",
        )
    _seed_catalog_row(
        db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
        title="Overflowed wiki page", source_name="SD", summary="Anything.",
    )
    catalog_candidates([])

    block, manifest = document_grounding(_CID, "unrelated")

    assert block.count("\n- ") == MAX_INDEX_ENTRIES
    assert "Overflowed wiki page" not in block
    assert "PARTIAL" in block
    assert len(manifest) == MAX_INDEX_ENTRIES


# ══════════ Topic ranking — the semantic channel, and what a topic match may
#             be claimed for ══════════════════════════════════════════════════
#
# The incident these cover: topical ranking returned the NEWEST document
# whatever was asked, and labelled it a topic match. Two independent causes.
#
# The lexical channel could not see filenames — Postgres reads
# "Sprntly_vs_Productboard_Comparison.docx" as a single `host` token — so it
# ranked on summary and topics alone, and a document whose summariser had not
# run contributed nothing and was undiscoverable. And the semantic channel
# never ran on the path most traffic takes, because
# `qa_agent._answer_single_shot` calls `document_grounding` positionally and
# left `question_embedding` at None. With both channels flat every candidate
# tied and the SQL's last-resort `updated_at desc` picked the winner.
#
# The ranking itself is SQL and is verified against real Postgres (this repo
# has no Postgres in either CI lane and standing one up is a shared-workflow
# change). What is covered here is everything on the Python side of that
# boundary: that the embedding reaches the RPC on both paths, that it is
# computed once, and that nothing is presented as a topic match unless the
# fused rank actually returned it.


def _decision_rows(db, decision_type):
    import json as jsonmod

    out = []
    for row in db.table("agent_decision_log").select("*").execute().data:
        if row["decision_type"] != decision_type:
            continue
        factors = row["factors"]
        if isinstance(factors, str):
            factors = jsonmod.loads(factors)
        out.append(factors)
    return out


# T3 — a summary-less document is not dressed up as a topic match.
def test_zero_ranked_candidates_produce_no_topic_match(
    isolated_settings, catalog_candidates
):
    """A document whose summariser never ran carries summary='' and topics={},
    so before the title fix neither channel could rank it and the RPC returned
    nothing. Returning nothing must mean nothing: no `match: "topic"` anywhere,
    no falling back to "the most recent document" and calling that a topic
    hit — which is exactly what the incident looked like from the outside.

    The log has to say WHICH nothing it was, too: the catalog held a document
    here, so this is a ranking miss, not an empty workspace."""
    from app.ask_runner import TOPICAL_SEARCHED_NO_MATCH, document_grounding

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(
        db, "f-summariless", src,
        filename="Sprntly_vs_Productboard_Comparison.docx",
        extracted_text="Body text that never got summarised.",
        uploaded_at="2026-08-03T09:00:00+00:00",
    )
    _seed_catalog_row(
        db, provider="uploads", external_id="f-summariless",
        title="Sprntly_vs_Productboard_Comparison.docx",
        summary="", topics=(),
    )
    # Both channels empty — what the RPC returns for a row it cannot rank.
    catalog_candidates([])

    _, manifest = document_grounding(_CID, "how do we compare on roadmapping")

    assert [m["match"] for m in manifest] == [None]
    assert [m["rank"] for m in manifest] == [None]
    assert [m["loaded"] for m in manifest] == [False]

    factors = _decision_rows(db, "document_selection")
    assert len(factors) == 1
    assert factors[0]["documents_topical"] == 0
    assert factors[0]["catalog_size"] == 1
    assert factors[0]["topical_candidates"] == 0
    assert factors[0]["topical_outcome"] == TOPICAL_SEARCHED_NO_MATCH


# T6 — the two kinds of "no documents came back".
def test_empty_catalog_is_distinguishable_from_searched_and_no_match(
    isolated_settings, catalog_candidates
):
    """"This workspace has nothing" and "we searched and ranked nothing" read
    identically in an answer and mean opposite things: the first is an ingest
    failure, the second a ranking one. They are separated in the log, from
    `catalog_docs`, which grounding already holds — no second query."""
    from app.ask_runner import (
        TOPICAL_CATALOG_EMPTY,
        TOPICAL_SEARCHED_NO_MATCH,
        document_grounding,
    )

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    # An upload with NO catalog row: the index has a document, the catalog is
    # empty, so there was nothing for the ranking to search.
    _seed_file(db, "f-uncatalogued", src, filename="notes.txt",
               extracted_text="body")
    catalog_candidates([])

    document_grounding(_CID, "anything at all")
    first = _decision_rows(db, "document_selection")
    assert len(first) == 1
    assert first[0]["catalog_size"] == 0
    assert first[0]["topical_outcome"] == TOPICAL_CATALOG_EMPTY

    # Now catalogue it. Same question, same empty RPC result — but this time
    # the ranking really did look at something and come back with nothing.
    _seed_catalog_row(
        db, provider="uploads", external_id="f-uncatalogued",
        title="notes.txt", summary="Some notes.",
    )
    document_grounding(_CID, "anything at all")
    second = _decision_rows(db, "document_selection")
    assert len(second) == 2
    assert second[1]["catalog_size"] == 1
    assert second[1]["topical_outcome"] == TOPICAL_SEARCHED_NO_MATCH


def test_workspace_with_no_documents_at_all_is_recorded_not_silent(
    isolated_settings, catalog_candidates
):
    """The earliest exit — no uploads, no attachments, nothing connected — is
    logged too. Grounding returns ("", []) there, and without a row the case
    is invisible in exactly the way that let this incident run for a day."""
    from app.ask_runner import TOPICAL_NO_INDEX, document_grounding

    db = isolated_settings["supabase"]
    _seed_company(db)
    catalog_candidates([])

    assert document_grounding(_CID, "anything") == ("", [])

    factors = _decision_rows(db, "document_selection")
    assert len(factors) == 1
    assert factors[0]["topical_outcome"] == TOPICAL_NO_INDEX
    assert factors[0]["documents"] == 0


# T4 — the semantic channel reaches the skill path, without touching qa_agent.
def test_skill_path_grounding_receives_the_embedding_via_contextvar(
    isolated_settings, catalog_candidates
):
    """`qa_agent._answer_single_shot` calls `document_grounding(enterprise_id,
    question)` positionally and is deliberately not edited, so the embedding
    cannot arrive as an argument. It arrives on the request-scoped ContextVar
    the Ask worker sets, the same route `conversation_id` already travels.

    Asserted at the RPC boundary — what `document_find_candidates` was
    actually called with — because that is the thing that was wrong in
    production: the call was reaching Postgres with p_embedding => null."""
    from tests._fake_supabase import FakeSupabaseClient

    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="notes.txt", extracted_text="body")
    _seed_catalog_row(db, provider="uploads", external_id="f1", title="notes.txt",
                      summary="Some notes.")
    catalog_candidates([])
    vector = [0.25] * 1536

    token = ask_runner.set_active_question_embedding(vector, False)
    try:
        # Positional, exactly as qa_agent._answer_single_shot calls it.
        ask_runner.document_grounding(_CID, "what did we decide about pricing")
    finally:
        ask_runner.reset_active_question_embedding(token)

    calls = [c for c in FakeSupabaseClient.rpc_calls if c[0] == _CANDIDATES_FN]
    assert len(calls) == 1
    assert calls[0][1]["p_embedding"] == vector


def test_grounding_without_a_carried_embedding_still_ranks_lexically(
    isolated_settings, catalog_candidates
):
    """No ContextVar set and no argument is still legitimate — it means the
    caller genuinely had no vector. The RPC is called with a null embedding and
    the lexical channel carries the ranking: degraded, not dead."""
    from tests._fake_supabase import FakeSupabaseClient

    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="notes.txt", extracted_text="body")
    _seed_catalog_row(db, provider="uploads", external_id="f1", title="notes.txt",
                      summary="Some notes.")
    catalog_candidates([])

    ask_runner.document_grounding(_CID, "what did we decide about pricing")

    calls = [c for c in FakeSupabaseClient.rpc_calls if c[0] == _CANDIDATES_FN]
    assert len(calls) == 1
    assert calls[0][1]["p_embedding"] is None
    assert _decision_rows(db, "document_selection")[0]["semantic_channel"] is False


# T4/AC5 — one embedding per ask, however many consumers there are.
def test_one_embedding_per_ask_shared_by_every_consumer(
    isolated_settings, fake_llm, catalog_candidates, monkeypatch
):
    """The worker embeds once and publishes; `compose_ask_answer` reuses that
    instead of computing its own. Two consumers, one embeddings call — if this
    regresses, every ask silently pays twice."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-a")
    _seed_file(db, "f1", src, filename="notes.txt", extracted_text="body")
    catalog_candidates([])
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    calls: list[str] = []

    def _counting_embed(texts, **kwargs):
        calls.append(texts[0])
        return [[0.5] * 1536]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _counting_embed)

    # What ask_job_runner._run_sync does: embed once, publish, and let every
    # downstream consumer read it off the context.
    embedding, degraded = ask_runner._question_embedding(_CID, "a question")
    token = ask_runner.set_active_question_embedding(embedding, degraded)
    try:
        ask_runner.compose_ask_answer("asurion", "a question", enterprise_id=_CID)
        ask_runner.document_grounding(_CID, "a question")
    finally:
        ask_runner.reset_active_question_embedding(token)

    assert len(calls) == 1


def test_ask_worker_scopes_the_embedding_lazily_and_always_clears_it(
    isolated_settings, monkeypatch
):
    """The worker's setter must be paired with a reset in a `finally`, exactly
    as the conversation pair is: a vector left on the context outlives its own
    request and would be read by whatever reuses that context next.

    The worker SCOPES that slot without filling it (`ask_runner._EMBED_PENDING`):
    the vector is computed by the first consumer that actually needs one and
    memoised back, so the ask still pays for at most one embedding but pays for
    none at all when no consumer needs a vector. The reset discipline is
    unchanged — `ContextVar.reset` unwinds whatever ended up in the slot,
    resolved or not."""
    from app import ask_job_runner, ask_runner

    seen: dict = {}
    calls: list = []

    def _embed(eid, q):
        calls.append(q)
        return ([0.75] * 1536, False)

    monkeypatch.setattr(ask_runner, "_question_embedding", _embed)

    def _boom(**kwargs):
        # Scoped, but nothing has needed a vector yet — nothing embedded.
        seen["pending"] = (
            ask_runner._active_question_embedding.get()
            is ask_runner._EMBED_PENDING
        )
        seen["calls_before"] = len(calls)
        # The first consumer to need one resolves it...
        seen["first"] = ask_runner._resolve_question_embedding(_CID, "a question")
        # ...and a second consumer reuses it rather than embedding again.
        seen["second"] = ask_runner._resolve_question_embedding(_CID, "a question")
        seen["calls_after"] = len(calls)
        raise RuntimeError("answer failed")

    monkeypatch.setattr(ask_job_runner.qa_agent, "answer", _boom)

    with pytest.raises(RuntimeError):
        ask_job_runner._run_sync(
            1, _CID, "a question", "asurion", [], None, None, None,
        )

    # Scoped but unresolved when the answer call began: no embedding paid for.
    assert seen["pending"] is True
    assert seen["calls_before"] == 0
    # First consumer resolves, second reuses — ONE call across both.
    assert seen["first"] == ([0.75] * 1536, False)
    assert seen["second"] == ([0.75] * 1536, False)
    assert seen["calls_after"] == 1
    # ...and gone once the call has returned, even though it raised.
    assert ask_runner._carried_question_embedding() is None
    assert ask_runner._active_question_embedding.get() is None


def test_ask_that_needs_no_vector_embeds_nothing(isolated_settings, monkeypatch):
    """A workspace with no documents returns from `document_grounding` before
    Stage T, which is the only consumer that reads the vector. The old eager
    embed paid a full round trip for a vector nothing then read (2.8s on the
    trace that motivated this); scoping it lazily must drop that to zero."""
    from app import ask_job_runner, ask_runner

    calls: list = []

    def _embed(eid, q):
        calls.append(q)
        return ([0.75] * 1536, False)

    monkeypatch.setattr(ask_runner, "_question_embedding", _embed)
    monkeypatch.setattr(ask_job_runner.qa_agent, "answer", lambda **kw: {"answer": "x"})

    ask_job_runner._run_sync(1, _CID, "a question", "asurion", [], None, None, None)

    assert calls == []


# ══════════════════ Cache-prefix ordering: content preservation ════════════


def test_prefix_content_set_is_unchanged_only_reordered(isolated_settings, fake_llm):
    """For one ask, the reorder changes ORDER only — every block that was in
    the prefix before is still in it, none added, dropped, or truncated.
    (AC4)"""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": _CID, "slug": "slug-co-set", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-set", "company_id": _CID, "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    src = _seed_source(db, "src-set")
    _seed_file(db, "f-set", src, filename="Set_Report.docx",
               extracted_text="SET DOCUMENT BODY")

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("SET CORPUS BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "About Set_Report", enterprise_id=_CID)

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    blocks = prefix.split("\n\n---\n\n")

    # Exactly the three blocks this ask produces — none merged or dropped by
    # the reorder. Order is asserted separately (see the ordered-after-facts
    # test above); this checks the SET.
    assert len(blocks) == 3
    assert sum(WORKSPACE_CONFIG_HEADER in b for b in blocks) == 1
    assert sum(b.count("SET CORPUS BODY") == 1 for b in blocks) == 1
    assert sum("SET DOCUMENT BODY" in b for b in blocks) == 1


def test_no_documents_prefix_byte_identical_to_prefix_fix(isolated_settings, fake_llm):
    """When `docs_block` is empty, the join drops the empty part, so the
    prefix is byte-identical whether `docs_block` would have sat before or
    after the corpus — order is unobservable. (AC5)"""
    from app import ask_runner
    from app.ask_runner import company_facts_block

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": _CID, "slug": "slug-co-nodocs", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-nodocs", "company_id": _CID, "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("NO DOCS CORPUS BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id=_CID)

    facts = company_facts_block(_CID)
    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix == (
        f"{facts}\n\n---\n\nSource material:\n\n"
        "<<< SOURCE: a >>>\nNO DOCS CORPUS BODY\n<<< END SOURCE >>>"
    )


def test_no_facts_prefix_starts_with_corpus(isolated_settings, fake_llm):
    """When `facts` is empty, the prefix begins with the corpus and
    `docs_block` still trails it. (AC6)"""
    from app import ask_runner

    # A `companies` row with a BLANK display name (the FK the document rows
    # need) and no `products` row at all — `company_facts_block` returns ""
    # for exactly this shape (no company_name, no product_name, no website
    # → no lines → ""), keeping `facts` genuinely empty rather than merely
    # unseeded.
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-nofacts", "slug": "slug-co-nofacts", "display_name": ""}
    ).execute()
    db.table("document_source").insert(
        {"id": "src-nofacts", "company_id": "co-nofacts", "name": "x", "description": ""}
    ).execute()
    db.table("document_source_file").insert(
        {
            "id": "f-nofacts", "source_id": "src-nofacts", "company_id": "co-nofacts",
            "filename": "Nofacts_Report.docx", "extracted_text": "NOFACTS DOCUMENT BODY",
            "uploaded_at": "2026-08-02T00:00:00+00:00",
        }
    ).execute()
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("NOFACTS CORPUS BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Nofacts_Report", enterprise_id="co-nofacts",
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix.startswith("Source material:")
    assert prefix.index("NOFACTS CORPUS BODY") < prefix.index("NOFACTS DOCUMENT BODY")


def test_empty_corpus_and_no_documents_prefix_is_none(isolated_settings, fake_llm):
    """All parts empty → `user_cacheable_prefix is None`, matching the
    existing behaviour this ticket does not change (also pinned in
    `test_chat_kg_retrieval.py`)."""
    from app import ask_runner

    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-all-empty-x")

    assert fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"] is None


def test_single_block_prefix_has_no_delimiter(isolated_settings, fake_llm):
    """Exactly one non-empty part (facts only, here) → no stray
    `\\n\\n---\\n\\n` delimiter in the prefix."""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": _CID, "slug": "slug-co-single", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-single", "company_id": _CID, "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id=_CID)

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert "\n\n---\n\n" not in prefix
    assert WORKSPACE_CONFIG_HEADER in prefix


# ══════════════════ Cache-prefix ordering: observability (meta_out) ════════


def test_cache_counters_recorded_on_the_answer_decision_row(isolated_settings, monkeypatch):
    """The `answer` decision-log row carries both cache counters as
    integers. (AC7)"""
    import json as jsonmod

    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db, "co-cache-1")

    def _fake_call_json(**kwargs):
        meta_out = kwargs.get("meta_out")
        if meta_out is not None:
            meta_out.update({
                "cache_read_input_tokens": 4200,
                "cache_creation_input_tokens": 0,
                "input_tokens": 4500,
            })
        return {
            "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
            "unanswered": "",
        }

    monkeypatch.setattr(ask_runner, "call_json", _fake_call_json)

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-cache-1")

    rows = (
        db.table("agent_decision_log").select("*")
        .eq("decision_type", "answer").execute().data
    )
    assert len(rows) == 1
    factors = rows[0]["factors"]
    factors = jsonmod.loads(factors) if isinstance(factors, str) else factors
    assert factors["cache_read_input_tokens"] == 4200
    assert factors["cache_creation_input_tokens"] == 0
    assert factors["input_tokens"] == 4500
    assert isinstance(factors["cache_read_input_tokens"], int)
    assert isinstance(factors["cache_creation_input_tokens"], int)
    assert isinstance(factors["input_tokens"], int)


def test_cache_counters_default_to_zero_when_absent(isolated_settings, fake_llm):
    """The stock `fake_llm` fixture never populates `meta_out` — a provider
    that reports no usage still yields integer `0`, never `None` and never a
    missing key. (AC7)"""
    import json as jsonmod

    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db, "co-cache-2")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-cache-2")

    rows = db.table("agent_decision_log").select("*").execute().data
    answer_rows = [r for r in rows if r["decision_type"] == "answer"]
    assert len(answer_rows) == 1
    factors = answer_rows[0]["factors"]
    factors = jsonmod.loads(factors) if isinstance(factors, str) else factors
    assert factors["cache_read_input_tokens"] == 0
    assert factors["cache_creation_input_tokens"] == 0
    assert factors["input_tokens"] == 0
    assert factors["cache_read_input_tokens"] is not None


def test_missing_meta_out_does_not_break_the_answer(isolated_settings, fake_llm):
    """The counters are best-effort — a provider that returns no usage object
    still yields a complete answer payload."""
    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db, "co-meta-missing")
    fake_llm["payload"] = {
        "answer": "the answer text", "key_points": ["a"], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }

    payload = ask_runner.compose_ask_answer(
        "asurion", "q?", enterprise_id="co-meta-missing",
    )

    assert payload["answer"] == "the answer text"
    assert payload["key_points"] == ["a"]
    assert "documents" in payload


def test_cache_read_tokens_non_zero_on_second_call(isolated_settings, monkeypatch):
    """Unit-level with a fake that echoes usage: a second call within the
    cache window reports non-zero `cache_read_input_tokens`. The REAL proof
    is the live base-vs-head run (LV1) — this only proves the wiring.
    (AC8)"""
    import json as jsonmod

    from app import ask_runner

    db = isolated_settings["supabase"]
    _seed_company(db, "co-cache-second")

    responses = [
        {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 5000},
        {"cache_read_input_tokens": 4800, "cache_creation_input_tokens": 0},
    ]

    def _fake_call_json(**kwargs):
        usage = responses.pop(0)
        meta_out = kwargs.get("meta_out")
        if meta_out is not None:
            meta_out.update({**usage, "input_tokens": 5000})
        return {
            "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
            "unanswered": "",
        }

    monkeypatch.setattr(ask_runner, "call_json", _fake_call_json)

    ask_runner.compose_ask_answer("asurion", "first q", enterprise_id="co-cache-second")
    ask_runner.compose_ask_answer("asurion", "second q", enterprise_id="co-cache-second")

    rows = (
        db.table("agent_decision_log").select("*")
        .eq("decision_type", "answer").execute().data
    )
    assert len(rows) == 2

    def _factors(row):
        f = row["factors"]
        return jsonmod.loads(f) if isinstance(f, str) else f

    reads = sorted(_factors(r)["cache_read_input_tokens"] for r in rows)
    assert reads == [0, 4800]


def test_no_document_or_corpus_text_in_decision_factors(isolated_settings, fake_llm):
    """The added cache/usage factors are integers only; no factor value on
    any row this ask writes contains a filename, title, summary, corpus
    text, or PRD text. (AC11)"""
    import json as jsonmod

    from app import ask_runner

    db = isolated_settings["supabase"]
    src = _seed_source(db, "src-prop", company_id="co-prop")
    _seed_file(db, "f-prop", src, company_id="co-prop",
               filename="Confidential_Roadmap.docx",
               extracted_text="SECRET CORPUS-ADJACENT TEXT nobody should log")
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("SECRET CORPUS BODY nobody should log either")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Confidential_Roadmap", enterprise_id="co-prop",
    )

    rows = db.table("agent_decision_log").select("*").execute().data
    assert rows
    for row in rows:
        f = row["factors"]
        factors = jsonmod.loads(f) if isinstance(f, str) else f
        blob = jsonmod.dumps(factors)
        assert "Confidential_Roadmap.docx" not in blob
        assert "SECRET CORPUS-ADJACENT TEXT" not in blob
        assert "SECRET CORPUS BODY" not in blob

    answer_rows = [r for r in rows if r["decision_type"] == "answer"]
    factors = answer_rows[0]["factors"]
    factors = jsonmod.loads(factors) if isinstance(factors, str) else factors
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens"):
        assert isinstance(factors[key], int)


# ══════════════ Cache-prefix ordering: contract (non-breakage) ═════════════


def test_cache_control_breakpoint_placement_unchanged():
    """`cache_control: ephemeral` placement in `llm._build_base_kwargs` is
    unchanged by this ticket — still exactly one breakpoint, on the whole
    cacheable-prefix block, before the uncached `user` turn. This ticket only
    reorders what's INSIDE `user_cacheable_prefix`; it never touches
    `llm.py`. (AC9)"""
    from app import llm

    kwargs = llm._build_base_kwargs(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="a short system prompt",
        user="the question",
        user_cacheable_prefix="the cacheable prefix text",
    )

    content = kwargs["messages"][0]["content"]
    cache_blocks = [b for b in content if "cache_control" in b]
    assert len(cache_blocks) == 1
    block = cache_blocks[0]
    assert block["type"] == "text"
    assert block["text"] == "the cacheable prefix text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert content[0] is block
    assert content[1] == {"type": "text", "text": "the question"}


def test_prefix_call_sites_still_bind():
    """Every enumerated `user_cacheable_prefix` caller still `py_compile`s
    and still passes the same keyword — this ticket changes the ORDER of the
    string each site passes, never the signature or the call shape. (AC10)"""
    import py_compile

    from app import ask_runner, qa_agent

    py_compile.compile(ask_runner.__file__, doraise=True)
    py_compile.compile(qa_agent.__file__, doraise=True)

    import inspect

    assert inspect.getsource(ask_runner).count("user_cacheable_prefix=") >= 2
    assert "user_cacheable_prefix=" in inspect.getsource(qa_agent)


# ── Stage P: the documents the PLANNER named ─────────────────────────────────

def test_a_planned_document_is_loaded(isolated_settings):
    """The planner can name a catalog document and grounding loads it, without
    the question containing the title (Stage N cannot match it) and without the
    fused rank having to surface it."""
    from app import ask_runner
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="wiki-42",
        title="Pricing Principles", summary="How we price.",
    )

    token = ask_runner.set_active_planned_documents(["wiki-42"])
    try:
        _, manifest = document_grounding(_CID, "what does it say about pricing?")
    finally:
        ask_runner.reset_active_planned_documents(token)

    assert any(m.get("file_id") == "confluence:wiki-42" for m in manifest), manifest


def test_a_planned_document_is_marked_topic_not_named(isolated_settings):
    """THE safety property, and the reason Stage P exists in this shape.

    `document_referent` was written because an earlier attempt at model-picked
    documents pinned a Confluence page onto "what's our pricing strategy?" and
    the model answered AS that page. Its rule is that a FALSE REFERENT IS WORSE
    THAN NO REFERENT.

    "named" asserts the USER asked for this document. "topic" says it was
    selected automatically — the honest claim for a model's pick, and the one
    the answer prompt already tells the model it may ignore (rule 6). So a wrong
    planner pick costs prompt budget, exactly like a wrong Stage T pick, instead
    of hijacking the answer's voice. If this ever flips to "named", that guard
    is gone."""
    from app import ask_runner
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="wiki-99",
        title="Q3 Pricing", summary="Pricing for Q3.",
    )

    token = ask_runner.set_active_planned_documents(["wiki-99"])
    try:
        _, manifest = document_grounding(_CID, "what's our pricing strategy?")
    finally:
        ask_runner.reset_active_planned_documents(token)

    picked = [m for m in manifest if m.get("file_id") == "confluence:wiki-99"]
    assert picked, manifest
    assert picked[0]["match"] == "topic", (
        "a planner-picked document must never be presented as one the USER "
        "named — see app/document_referent.py"
    )


def test_a_planned_id_this_company_cannot_see_is_ignored(isolated_settings):
    """Tenant scoping does not depend on the planner being well behaved: an id
    that is not in THIS caller's catalog selects nothing."""
    from app import ask_runner
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="theirs-1",
        title="Someone Else's Doc", company_id="co-other-tenant",
    )

    token = ask_runner.set_active_planned_documents(["theirs-1"])
    try:
        _, manifest = document_grounding(_CID, "what does it say?")
    finally:
        ask_runner.reset_active_planned_documents(token)

    assert all(m.get("file_id") != "confluence:theirs-1" for m in manifest), manifest


def test_stage_n_still_wins_over_a_planned_document(isolated_settings):
    """A title the user SPELLED OUT is an unambiguous request. A model's opinion
    must never displace it, which is why Stage P runs after Stage N."""
    from app import ask_runner
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="named-doc",
        title="Billing Runbook", summary="Billing.",
    )
    _seed_catalog_row(
        db, provider="confluence", external_id="planned-doc",
        title="Something Else", summary="Other.",
    )

    token = ask_runner.set_active_planned_documents(["planned-doc"])
    try:
        _, manifest = document_grounding(_CID, "what does the Billing Runbook say?")
    finally:
        ask_runner.reset_active_planned_documents(token)

    named = [m for m in manifest if m.get("file_id") == "confluence:named-doc"]
    assert named, manifest
    assert named[0]["match"] == "named"

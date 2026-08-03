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
    _seed_file(db, "f1", src, filename="a.txt")

    _, manifest = document_grounding(_CID, "unrelated")
    assert len(manifest) == 1
    assert set(manifest[0].keys()) == {
        "file_id", "filename", "source_name", "uploaded_at", "loaded",
    }
    assert manifest[0]["file_id"] == "f1"


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
    assert (
        f"[This list shows the {MAX_INDEX_ENTRIES} most recently uploaded of "
        "250 documents.]" in block
    )
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

    monkeypatch.setattr(ask_runner, "document_grounding", lambda eid, q: ("", []))
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

    monkeypatch.setattr(qa, "document_grounding", lambda eid, q: ("", []))
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

    monkeypatch.setattr(ask_runner, "document_grounding", lambda eid, q: ("", []))
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


def test_compose_ask_answer_documents_ordered_after_facts_before_corpus(
    isolated_settings, fake_llm
):
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
    assert prefix.index(WORKSPACE_CONFIG_HEADER) < prefix.index("THE DOCUMENT BODY")
    assert prefix.index("THE DOCUMENT BODY") < prefix.index("THE CORPUS BODY")


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
    assert len(rows) == 1
    factors = rows[0]["factors"]
    if isinstance(factors, str):
        factors = jsonmod.loads(factors)
    assert factors["documents"] == 1
    assert factors["documents_loaded"] == 1
    assert isinstance(factors["documents"], int)
    assert isinstance(factors["documents_loaded"], int)
    assert "Sprntly_vs_Productboard_Comparison.docx" not in jsonmod.dumps(factors)
    assert "a body nobody should see in factors" not in jsonmod.dumps(factors)


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
        "dataset", "question", "enterprise_id", "prd_context", "on_delta",
    }
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

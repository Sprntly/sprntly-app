"""The artifacts a chat produced are IN that chat's prompt.

The reported failure: with a report open in the side panel, "summarize the
report in just one paragraph" was answered from a corpus file covering a
different month, and the follow-up ("how many themes are in the report?")
counted that file's themes as the report's. A PRD tab, an evidence tab and a
ticket-set tab each send an id and each has a builder; a report or a document
has neither, so it was never in the prompt at all.

Pinned here:
- a report the thread produced is grounded, body and all
- so is a document
- THE THREAD IS THE BOUNDARY — another conversation's report never appears
- THE PANEL WINS — what the reader has open is rendered first, in full
- the others still ride along, bounded, so either can be asked about
- a thread with nothing produces no block, so an ordinary ask is unchanged
- a read that fails degrades to '' rather than taking the answer down with it
"""
from __future__ import annotations

import pytest

from app import thread_context


def _report(rid: int, title: str, body: str, created: str = "2026-08-25T10:00:00Z"):
    return {
        "id": rid, "title": title, "html": f"<h1>{title}</h1><p>{body}</p>",
        "skill": "voice-of-customer-report", "question": "what are customers saying?",
        "created_at": created, "conversation_id": 7,
    }


def _document(did: int, title: str, body: str, created: str = "2026-08-24T10:00:00Z"):
    return {
        "id": did, "title": title, "body_html": f"<p>{body}</p>",
        "created_at": created, "conversation_id": 7,
    }


@pytest.fixture
def sources(monkeypatch):
    """Swap both thread reads for in-memory rows, keyed by conversation."""
    state: dict = {"reports": [], "documents": [], "seen": []}

    def fake_reports(conversation_id, company_id, limit=4):
        state["seen"].append(("reports", conversation_id, company_id))
        return [r for r in state["reports"] if r["conversation_id"] == conversation_id]

    def fake_documents(company_id, conversation_id, limit=3):
        state["seen"].append(("documents", conversation_id, company_id))
        return [d for d in state["documents"] if d["conversation_id"] == conversation_id]

    import app.db.reports as reports_db
    import app.db.custom_artifacts as documents_db

    monkeypatch.setattr(
        reports_db, "reports_with_bodies_for_conversation", fake_reports, raising=False
    )
    monkeypatch.setattr(
        documents_db, "documents_with_bodies_for_conversation", fake_documents,
        raising=False,
    )
    return state


def test_a_report_the_thread_made_is_in_the_prompt(sources):
    sources["reports"] = [_report(1, "Voice of Customer · August", "Theme 1 is latency.")]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "ARTIFACTS IN THIS CHAT" in block
    assert "Voice of Customer · August" in block
    # The BODY, not just the title — answering "summarize the report" needs the
    # words, and a title is what the old answer effectively had.
    assert "Theme 1 is latency." in block


def test_a_document_the_thread_wrote_is_in_the_prompt(sources):
    sources["documents"] = [_document(4, "Launch checklist", "Step one is the beta gate.")]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "Launch checklist" in block
    assert "Step one is the beta gate." in block


def test_another_conversation_never_leaks_in(sources):
    """The thread is the boundary. A report from the chat next door is not what
    'the report' means to someone typing in this one."""
    mine = _report(1, "Mine", "This thread's finding.")
    theirs = _report(2, "Theirs", "Another thread's finding.")
    theirs["conversation_id"] = 99
    sources["reports"] = [mine, theirs]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "Mine" in block
    assert "Theirs" not in block
    assert "Another thread's finding." not in block


def test_the_open_panel_is_rendered_first(sources):
    """What the reader is looking at is what 'it' refers to — even when a newer
    artifact exists in the same thread."""
    sources["reports"] = [
        _report(1, "Newest report", "Newer body.", created="2026-08-25T12:00:00Z"),
        _report(2, "The one on screen", "Focused body.", created="2026-08-20T12:00:00Z"),
    ]

    block = thread_context.build_thread_artifact_context(
        "acme", 7, focus={"kind": "report", "id": 2}
    )

    assert block.index("The one on screen") < block.index("Newest report")


def test_without_a_focus_the_newest_leads(sources):
    sources["reports"] = [
        _report(1, "Older", "Older body.", created="2026-08-01T00:00:00Z"),
        _report(2, "Newer", "Newer body.", created="2026-08-25T00:00:00Z"),
    ]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert block.index("Newer") < block.index("Older")


def test_the_others_ride_along_so_either_can_be_asked_about(sources):
    """Prioritising the open one must not hide the rest — 'what did the other
    report say?' is a fair question about the same thread."""
    sources["reports"] = [_report(1, "Competitive intelligence", "Rival shipped SSO.")]
    sources["documents"] = [_document(2, "Launch checklist", "Beta gate first.")]

    block = thread_context.build_thread_artifact_context(
        "acme", 7, focus={"kind": "document", "id": 2}
    )

    assert "Launch checklist" in block and "Beta gate first." in block
    assert "Competitive intelligence" in block and "Rival shipped SSO." in block


def test_a_focus_from_another_thread_reorders_nothing(sources):
    """A stale pointer (the thread the reader just left) must not fetch, and
    must not blank the block either."""
    sources["reports"] = [_report(1, "Mine", "This thread's finding.")]

    block = thread_context.build_thread_artifact_context(
        "acme", 7, focus={"kind": "report", "id": 4242}
    )

    assert "Mine" in block


def test_a_thread_with_no_artifacts_composes_no_block(sources):
    assert thread_context.build_thread_artifact_context("acme", 7) == ""


def test_missing_ids_compose_no_block(sources):
    assert thread_context.build_thread_artifact_context(None, 7) == ""
    assert thread_context.build_thread_artifact_context("acme", None) == ""
    # And neither one touched the database.
    assert sources["seen"] == []


def test_a_failing_read_degrades_instead_of_breaking_the_answer(monkeypatch):
    """Grounding is best-effort by contract: a report table that errors costs
    the answer its grounding, never the answer itself."""
    import app.db.reports as reports_db
    import app.db.custom_artifacts as documents_db

    def boom(*a, **k):
        raise RuntimeError("supabase is having a day")

    monkeypatch.setattr(
        reports_db, "reports_with_bodies_for_conversation", boom, raising=False
    )
    monkeypatch.setattr(
        documents_db, "documents_with_bodies_for_conversation", boom, raising=False
    )

    assert thread_context.build_thread_artifact_context("acme", 7) == ""


def test_one_source_failing_does_not_cost_the_other(monkeypatch, sources):
    """A documents table that errors must not lose the report it was going to
    be grounded beside."""
    import app.db.custom_artifacts as documents_db

    def boom(*a, **k):
        raise RuntimeError("documents are down")

    sources["reports"] = [_report(1, "Voice of Customer", "Latency is the theme.")]
    monkeypatch.setattr(
        documents_db, "documents_with_bodies_for_conversation", boom, raising=False
    )

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "Latency is the theme." in block


def test_a_long_report_is_truncated_not_dropped(sources):
    """The cap protects the prompt; losing the document entirely would defeat
    the point of the block."""
    body = "x" * (thread_context._FOCUS_CAP + 5_000)
    sources["reports"] = [_report(1, "Enormous", body)]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "Enormous" in block
    assert "[… truncated]" in block
    assert len(block) < thread_context._FOCUS_CAP + 3_000


def test_artifacts_past_the_cap_are_named_but_not_described(sources):
    """A tail the prompt cannot hold is still SAID to exist — a model that
    cannot see it must not imply the list it can see is everything."""
    sources["reports"] = [
        _report(i, f"Report {i}", f"Body {i}", created=f"2026-08-{20 - i:02d}T00:00:00Z")
        for i in range(1, 7)
    ]

    block = thread_context.build_thread_artifact_context("acme", 7)

    assert "Also in this chat, not included above" in block
    assert "do not describe what they say" in block

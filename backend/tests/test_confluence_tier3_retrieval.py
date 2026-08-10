"""Regression tests proving a Confluence document that is CATALOGUED but was
never KG-extracted (an old, out-of-window page) is answerable through the
read path: `ask_runner._topical_candidates` surfaces it by topic with no
recency filter and no `kg_signal` join, and
`document_bodies.BodyResolver.resolve_confluence` fetches its live body with
no KG signal required.

None of these tests seed a `kg_signal` row for the page under test — that
absence IS the fixture. The read path holds no reference to `kg_signal`
anywhere on this route (see `ask_runner._topical_candidates` and
`document_catalog.find_candidates`), so a catalogued-but-never-extracted page
is eligible on exactly the same terms as an extracted one.

Seam-level only, per the evidence tier for this ticket: no live Confluence
connection exists in the current rig, so `confluence_fetch` is monkeypatched
at the module boundary the way `test_document_bodies.py` and
`test_ask_document_retrieval.py` already do — same fixture shape, kept local
to this file rather than imported, matching how those two files (and
`test_document_referent.py`) each already carry their own copy rather than
sharing one across modules.
"""
from __future__ import annotations

import pytest

_CID = "co-tier3-confluence"


class _FakeConfluenceFetch:
    """Stands in for `connectors.confluence_fetch` at the seam."""

    def __init__(self, page=None, *, session=object(), raises=False):
        self.page = page
        self.session = session
        self.raises = raises
        self.pages_fetched = []

    def open_session(self, enterprise_id):
        return self.session

    def get_page(self, session, page_id):
        self.pages_fetched.append(page_id)
        if self.raises:
            raise RuntimeError("token expired")
        return self.page


@pytest.fixture
def confluence_pages(monkeypatch):
    from app.connectors import confluence_fetch

    def _install(**kwargs):
        fake = _FakeConfluenceFetch(**kwargs)
        monkeypatch.setattr(confluence_fetch, "open_session", fake.open_session)
        monkeypatch.setattr(confluence_fetch, "get_page", fake.get_page)
        return fake

    return _install


def _catalog_row(
    *, external_id="page-old-1", title="Legacy pricing memo",
    source_name="Legacy space", score=0.05,
):
    """One row shaped exactly like `document_find_candidates` returns for a
    catalogued Confluence page — an old `doc_date`, no similarity floor
    applied, and (the point of this fixture) no `kg_signal` anywhere in the
    shape: the RPC's result never carries one, matching a Tier-3
    (catalogued-but-never-KG-extracted) page."""
    return {
        "provider": "confluence",
        "external_id": external_id,
        "title": title,
        "source_name": source_name,
        "summary": "",
        "topics": [],
        "doc_date": "2023-01-01T00:00:00+00:00",
        "conversation_id": None,
        "score": score,
    }


# ─────────── AC1: topical selection surfaces the unextracted row ───────────


def test_topic_selection_returns_catalogued_confluence_row_without_signal(
    monkeypatch,
):
    """Age and the absence of a KG signal do not filter the row out —
    `_topical_candidates` hands back whatever the catalog RPC returned,
    unfiltered."""
    from app import ask_runner

    row = _catalog_row()
    monkeypatch.setattr(
        ask_runner, "find_catalog_candidates", lambda *a, **k: [row]
    )

    candidates = ask_runner._topical_candidates(
        _CID, "what did the legacy pricing memo say",
        question_embedding=None, conversation_id=None, user_id=None,
        exclude_external_ids=set(),
    )

    assert [c["external_id"] for c in candidates] == ["page-old-1"]
    assert candidates[0]["provider"] == "confluence"


# ─────────── AC2/AC3: body resolution needs no signal, survives past 4k ────


def test_resolve_confluence_returns_body_without_kg_signal(confluence_pages):
    """No KG signal is consulted or required anywhere on this path — the
    resolver reads straight from `confluence_fetch.get_page`."""
    from app import document_bodies

    confluence_pages(page={"id": "page-old-1", "text": "the wiki page body"})

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("page-old-1")

    assert resolved.resolved
    assert resolved.text == "the wiki page body"


def test_resolve_confluence_body_survives_past_4000(confluence_pages):
    """The puller's `_TEXT_CHARS = 4000` extraction-slice cap lives only in
    the ingest path and never touches this resolve path. Plant a fact at
    char 5,000 — inside the 4,001-6,000 window that survives the puller's
    slice yet stays within `confluence_fetch.PAGE_BODY_CHARS = 6000` — and
    confirm the resolver hands it back whole."""
    from app import document_bodies

    fact = "The renewal discount floor is locked at 12 percent."
    body = ("x" * 5000) + fact
    assert len(body) <= 6000, "stub must stay under the live-fetch cap"

    confluence_pages(page={"id": "page-old-1", "text": body})

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("page-old-1")

    assert fact in resolved.text
    assert len(resolved.text) == len(body)


# ─────────── AC4: combined proof — decoupled from any KG signal ────────────


def test_resolve_confluence_decouples_catalog_from_signal(
    monkeypatch, confluence_pages,
):
    """One flow: `_topical_candidates` surfaces a catalogued-but-unextracted
    Confluence row, and `BodyResolver.resolve_confluence` resolves its body —
    the end-to-end read seam works with zero KG signal. No `kg_signal` row is
    seeded or referenced anywhere in this test; that absence is the point."""
    from app import ask_runner, document_bodies

    row = _catalog_row(
        external_id="page-old-2", title="Old onboarding runbook",
    )
    monkeypatch.setattr(
        ask_runner, "find_catalog_candidates", lambda *a, **k: [row]
    )
    confluence_pages(page={"id": "page-old-2", "text": "runbook body text"})

    candidates = ask_runner._topical_candidates(
        _CID, "what does the old onboarding runbook say",
        question_embedding=None, conversation_id=None, user_id=None,
        exclude_external_ids=set(),
    )
    assert candidates and candidates[0]["external_id"] == "page-old-2"

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence(
        candidates[0]["external_id"]
    )

    assert resolved.resolved
    assert resolved.text == "runbook body text"


# ─────────── Edge case: unreachable is a stated reason, not an absence ─────


def test_resolve_confluence_unreachable_is_a_stated_nonanswer(confluence_pages):
    """No Confluence session for this workspace must land on `text=None` with
    a reason — never an exception, and never read as absence of the page."""
    from app import document_bodies

    confluence_pages(session=None)

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("page-old-1")

    assert resolved.text is None
    assert resolved.reason
    assert not resolved.resolved

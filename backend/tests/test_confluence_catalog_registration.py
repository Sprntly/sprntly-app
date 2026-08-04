"""Confluence → document catalog registration, and the truncation trap.

WHERE this registration happens is the whole test. The puller fetches page
bodies in full, then slices each to `_TEXT_CHARS` (4,000) for the EXTRACTION
leg, which is all the KG needs. The catalog needs the opposite: the FULL body
— not to store it (the catalog stores no bodies at all) but because the
summary and, critically, the CONTENT HASH are both taken from it.

"Register during record processing" has two readings and the natural one is
wrong. Registering from `kg_ingest/runner.py`'s generic loop — or from inside
`_to_record` but after the slice — only ever sees text already cut to 4,000
characters. The summary would then describe only a long spec's opening, and
the hash would be blind to every edit past the 4k mark, so an updated page
would re-register as a no-op and its catalog entry would be frozen at the
first version forever. Nothing else in the system reports either fault: the
row exists and the summary reads perfectly well.

`test_the_content_hash_is_taken_over_the_full_body` is the test that tells
those two implementations apart.
"""
from __future__ import annotations

import pytest

from app.kg_ingest.pullers import confluence


class _Ctx:
    """Minimal stand-in for ConfluenceContext (the puller only reads these)."""

    company_id = "co-conf"
    site_url = "https://acme.atlassian.net/wiki"
    access_token = "t"
    cloud_id = "c"
    base = "https://api.atlassian.com/ex/confluence/c/wiki"
    space_ids: list[str] = []
    space_keys: dict[str, str] = {}


_SPACE = {"id": "s1", "key": "ENG", "name": "Engineering"}


def _page(body_text: str, *, page_id="page-1", title="Search ranking spec"):
    return {
        "id": page_id,
        "title": title,
        "body": {"storage": {"value": f"<p>{body_text}</p>",
                             "representation": "storage"}},
        "_links": {"webui": "/spaces/ENG/pages/1"},
        "version": {"number": 3, "createdAt": "2026-07-30T00:00:00Z"},
        "status": "current",
    }


@pytest.fixture
def registered(monkeypatch):
    """Capture every register_document call the puller makes."""
    calls: list[dict] = []

    def _register(company_id, **kw):
        calls.append({"company_id": company_id, **kw})

    monkeypatch.setattr(
        confluence.document_catalog, "register_document", _register
    )
    return calls


# ───────────────── T6a: the truncation trap (AC11b, load-bearing) ─────────


def test_the_content_hash_is_taken_over_the_full_body(registered):
    """T6a (AC11b), the primary test.

    Two revisions of a page identical in their first 4,000 characters and
    different only after must NOT collide on content_hash. They would collide
    if the hash were taken over the extraction slice — and because an
    unchanged hash is a deliberate no-op, the edited page would never
    re-summarise. Its catalog entry would describe version one forever, and
    nothing would report it.
    """
    shared = "Identical opening. " * 300
    assert len(shared) > confluence._TEXT_CHARS, "fixture too short to matter"

    confluence._to_record(_Ctx(), _SPACE, "page", _page(shared + "ENDING A"))
    confluence._to_record(
        _Ctx(), _SPACE, "page", _page(shared + "ENDING B", page_id="page-2")
    )
    assert registered[0]["content_hash"] != registered[1]["content_hash"], (
        "two revisions differing only past the 4,000-char slice hashed "
        "identically — the hash is being taken over truncated text, so an "
        "edited page would never re-summarise"
    )


def test_an_edit_past_the_truncation_point_regenerates_the_summary(
    isolated_settings, monkeypatch
):
    """T6a's consequence, end to end with the REAL register_document: an edit
    the extraction slice cannot see must still produce a new summary."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-conf", "slug": "co-conf", "display_name": "C"}
    ).execute()
    calls = []
    monkeypatch.setattr(document_catalog, "llm_call", lambda **k: calls.append(k) or type(
        "R", (), {"output": {"summary": "Ranking uses reciprocal rank fusion.",
                             "topics": ["ranking"]}})())
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])

    shared = "Identical opening. " * 300
    confluence._to_record(_Ctx(), _SPACE, "page", _page(shared + "ENDING A"))
    assert len(calls) == 1

    # Same page id, edited only past the 4k mark.
    confluence._to_record(_Ctx(), _SPACE, "page", _page(shared + "ENDING B"))
    assert len(calls) == 2, "the edited revision did not re-summarise"

    assert len(document_catalog.list_documents("co-conf")) == 1


def test_the_summariser_receives_the_untruncated_body(registered):
    """AC11b: the summary is built from the whole page, not its opening — and
    the extraction leg keeps its own cap, which is correct and unchanged."""
    long_body = "Ranking uses reciprocal rank fusion. " * 300  # ~11k chars
    assert len(long_body) > confluence._TEXT_CHARS * 2, "fixture too short"

    record = confluence._to_record(_Ctx(), _SPACE, "page", _page(long_body))

    assert len(registered) == 1
    summarised = registered[0]["get_text"]()
    assert len(summarised) > confluence._TEXT_CHARS, (
        f"the summariser got post-slice text ({len(summarised)} chars) "
        "instead of the full _text_from_body result"
    )
    assert len(summarised) == pytest.approx(len(long_body), rel=0.02)

    # RawRecord.text — the KG's copy — stays capped.
    assert len(record.text) <= confluence._TEXT_CHARS


# ───────────────────────── T6: the Confluence writer ──────────────────────


def test_confluence_registers_provider_scope_and_page_metadata(registered):
    """T6/AC11: company-scoped, keyed on the Confluence page id, carrying the
    space name and the permalink."""
    confluence._to_record(_Ctx(), _SPACE, "page", _page("Short body."))

    assert len(registered) == 1
    call = registered[0]
    assert call["company_id"] == "co-conf"
    assert call["provider"] == "confluence"
    assert call["external_id"] == "page-1"
    assert call["title"] == "Search ranking spec"
    assert call["source_name"] == "Engineering"
    assert call["url"] == "https://acme.atlassian.net/wiki/spaces/ENG/pages/1"
    assert call["doc_date"] == "2026-07-30T00:00:00Z"
    assert call.get("conversation_id") is None


def test_a_skipped_record_is_not_catalogued(registered):
    """A listing entry with neither title nor body yields no RawRecord, and
    must leave no catalog row either."""
    empty = {"id": "page-9", "title": "  ", "body": {}, "version": {}}
    assert confluence._to_record(_Ctx(), _SPACE, "page", empty) is None
    assert registered == []

    assert confluence._to_record(_Ctx(), _SPACE, "page", {"title": "x"}) is None
    assert registered == []


def test_registration_failure_does_not_break_the_pull(monkeypatch):
    """T7/AC12: at THIS call site "log and continue" is right — a sync that
    succeeds today must still succeed if cataloguing fails. (Drive is the one
    exception; see test_drive_catalog_registration.py.)"""
    def _boom(company_id, **kw):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(
        confluence.document_catalog, "register_document", _boom
    )
    record = confluence._to_record(_Ctx(), _SPACE, "page", _page("Body."))
    assert record is not None
    assert record.external_id == "page-1"
    assert record.text.startswith("Body.")


def test_no_page_body_is_stored_at_rest(registered):
    """The catalog is a pointer, not a copy: Sprntly keeps a summary and a URL
    for a wiki page, never a duplicate of the page itself. Connecting a wiki
    must not hand us a copy of it — which also means there is no
    stored-body retention question to answer.

    Note this is asserted about a page whose body is far past the extraction
    slice, so it cannot pass by accident on a short fixture."""
    long_body = "Ranking uses reciprocal rank fusion. " * 300
    confluence._to_record(_Ctx(), _SPACE, "page", _page(long_body))

    call = registered[0]
    assert call.get("body_text") is None
    # ...while the summary and hash still come from the whole page.
    assert len(call["get_text"]()) > confluence._TEXT_CHARS
    assert call["content_hash"]

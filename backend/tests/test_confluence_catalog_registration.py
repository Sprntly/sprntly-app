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

from datetime import datetime, timedelta, timezone

import pytest

from app.kg_ingest.pullers import confluence


def _iso_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _stub_pull(monkeypatch, pages):
    """Wire the puller's fetch seams so pull() walks one space returning
    `pages`. Leaves register_document alone so the `registered` fixture (or a
    test's own patch) captures what the walk catalogues."""
    monkeypatch.setattr(confluence, "sync_context", lambda cid: _Ctx())
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_SPACE])

    def fake_api_get(token, url, params=None, *, what="read"):
        if url.endswith("/api/v2/pages"):
            return {"results": list(pages)}
        return {}

    monkeypatch.setattr(confluence, "api_get", fake_api_get)


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


# ────────────── Decoupling: catalog coverage vs extraction coverage ──────────


def test_old_page_is_catalogued_but_not_yielded(registered, monkeypatch):
    """AC5/AC6/AC7, the load-bearing decouple: a space holding one in-window and
    one out-of-window page must catalog BOTH (findable forever) yet yield only
    the in-window one for KG extraction.

    This is what unblocks answering from an old page that was never extracted:
    the catalog row exists regardless of age; the graph only carries the recent
    facts."""
    recent = _page("Recent decision.", page_id="recent")
    recent["version"]["createdAt"] = _iso_ago(15)
    old = _page("Old decision.", page_id="old")
    old["version"]["createdAt"] = "2020-01-01T00:00:00Z"
    _stub_pull(monkeypatch, [recent, old])

    recs = list(confluence.pull("co-conf"))

    # BOTH pages are catalogued — coverage does not depend on the window.
    assert {c["external_id"] for c in registered} == {"recent", "old"}
    # Only the in-window page is yielded for extraction.
    assert [r.external_id for r in recs] == ["recent"]


def test_registration_uses_version_modified_date(registered):
    """AC5: the catalog doc_date is the page's MODIFIED date —
    version.createdAt — falling back to item.createdAt only when the version
    carries none (the same value the window keys off, so catalog and window can
    never disagree about a page's age)."""
    page = _page("Body.", page_id="p-mod")
    page["version"]["createdAt"] = "2026-05-01T00:00:00Z"
    page["createdAt"] = "2020-01-01T00:00:00Z"        # the CREATED date — ignored
    confluence._to_record(_Ctx(), _SPACE, "page", page)
    assert registered[-1]["doc_date"] == "2026-05-01T00:00:00Z"

    # version present but without createdAt → fall back to the item's createdAt.
    fallback = _page("Body.", page_id="p-fallback")
    fallback["version"] = {"number": 1}
    fallback["createdAt"] = "2026-06-01T00:00:00Z"
    confluence._to_record(_Ctx(), _SPACE, "page", fallback)
    assert registered[-1]["doc_date"] == "2026-06-01T00:00:00Z"


def test_registration_failure_still_continues_pull(monkeypatch, caplog):
    """AC15 through pull(): a catalog write that raises for one page logs a
    WARNING naming the page id and the pull keeps going — the in-window record
    is still yielded. (Extends test_registration_failure_does_not_break_the_pull,
    which pins the same guarantee at the _to_record seam.)"""
    import logging as _logging

    def _boom(company_id, **kw):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(confluence.document_catalog, "register_document", _boom)
    page = _page("Body.", page_id="still-here")
    page["version"]["createdAt"] = _iso_ago(10)
    _stub_pull(monkeypatch, [page])

    with caplog.at_level(_logging.WARNING, logger="app.kg_ingest.pullers.confluence"):
        recs = list(confluence.pull("co-conf"))

    assert [r.external_id for r in recs] == ["still-here"]
    warnings = [
        r for r in caplog.records
        if r.levelno == _logging.WARNING and "still-here" in r.getMessage()
    ]
    assert len(warnings) == 1


# ───────── the space id on the row, and why it is the id and not the key ────
#
# A catalog row for Confluence is keyed on a PAGE id. The selection the admin
# saves is a list of SPACE ids. Until `container_id` there was no stored field
# joining the two, so unticking a space could not remove its pages and they
# stayed catalogued — offered to the model as documents the workspace has,
# rankable, assertable as the subject of a question, and then failing their
# body fetch with "the contents could not be loaded".


def test_the_space_id_is_stored_as_the_container(registered):
    """The join key, written at registration. `source_name` beside it is the
    space's DISPLAY NAME and joins to nothing the picker stores."""
    confluence._to_record(_Ctx(), _SPACE, "page", _page("Short body."))

    assert registered[0]["container_id"] == "s1"
    assert registered[0]["source_name"] == "Engineering", (
        "the display name is still what a reader sees — the container is an "
        "additional field, not a replacement for it"
    )


def test_the_container_survives_a_space_rename(registered):
    """WHY THE ID AND NOT THE KEY, as a test rather than a comment.

    The other two candidate joins both live on the row already and both break
    here: `source_name` is the display name, and the space KEY can be read out
    of the page URL. Confluence lets an admin change both — a rename rewrites
    the name, and a space-key change rewrites every page URL — while the space
    id is immutable. A join through either would silently stop matching, and
    the failure is invisible: the deselection returns 200 and quietly removes
    nothing.
    """
    renamed = {"id": "s1", "key": "PLATFORM", "name": "Platform Engineering"}
    confluence._to_record(_Ctx(), _SPACE, "page", _page("Body.", page_id="a"))
    confluence._to_record(_Ctx(), renamed, "page", _page("Body.", page_id="b"))

    assert registered[0]["container_id"] == registered[1]["container_id"] == "s1"
    # The two fields a naive join would have used DID move underneath it.
    assert registered[0]["source_name"] != registered[1]["source_name"]


def test_a_space_with_no_id_registers_without_a_container(registered):
    """A listing entry missing its id must not write the empty string as a
    container — `""` is a value that IN can match, so a later deregistration
    naming it would sweep up every page that had one missing."""
    confluence._to_record(
        _Ctx(), {"key": "ENG", "name": "Engineering"}, "page", _page("Body.")
    )

    assert registered[0]["container_id"] is None


def test_an_unchanged_page_still_acquires_a_missing_container(
    isolated_settings, monkeypatch
):
    """THE LAZY REPAIR, and the reason the change needs no data migration.

    `register_document` short-circuits on an unchanged content hash — no
    re-summarise, no re-embed, no row write. Every page catalogued before
    `container_id` existed is in exactly that state and would keep a NULL
    container forever, because a page nobody edits never re-registers. The
    deselection cleanup would then be inert for precisely the tenants that
    already have a catalog worth cleaning.

    So the short-circuit stamps a missing container on its way out. The
    Confluence pull walks and catalogues EVERY page in a selected space, not
    just recent ones, so one sweep fills them in."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-conf", "slug": "co-conf", "display_name": "C"}
    ).execute()
    calls: list = []
    monkeypatch.setattr(
        document_catalog, "llm_call",
        lambda **k: calls.append(k) or type(
            "R", (), {"output": {"summary": "A summary.", "topics": ["t"]}}
        )(),
    )
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])

    # A row exactly as a pre-`container_id` pull left it.
    confluence._to_record(
        _Ctx(), {"key": "ENG", "name": "Engineering"}, "page", _page("Body.")
    )
    rows = db.table("document_catalog").select("*").execute().data
    assert len(rows) == 1 and rows[0]["container_id"] is None
    assert len(calls) == 1

    # The next pull, byte-identical page, now knowing the space id.
    confluence._to_record(_Ctx(), _SPACE, "page", _page("Body."))

    rows = db.table("document_catalog").select("*").execute().data
    assert len(rows) == 1, "the repair inserted a second row"
    assert rows[0]["container_id"] == "s1", (
        "an unchanged page kept a NULL container — every row written before "
        "this column existed would stay invisible to the deselection cleanup"
    )
    assert len(calls) == 1, (
        "the repair re-summarised an unchanged page — it must cost no model "
        "call, or a pull over a large space becomes an LLM bill"
    )


def test_the_repair_does_not_look_like_an_edit(isolated_settings, monkeypatch):
    """Stamping a container must not move `updated_at`.

    Recency on this table means "the document changed", and a repair is not a
    change. #1119's audit found a stale row precisely BY its lagging
    `updated_at`; a repair that touched it would have hidden that row, and
    would make a quiet, fully-connected source indistinguishable from one that
    had just re-synced."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-conf", "slug": "co-conf", "display_name": "C"}
    ).execute()
    monkeypatch.setattr(
        document_catalog, "llm_call",
        lambda **k: type("R", (), {"output": {"summary": "A summary.",
                                              "topics": ["t"]}})(),
    )
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])

    confluence._to_record(
        _Ctx(), {"key": "ENG", "name": "Engineering"}, "page", _page("Body.")
    )
    before = db.table("document_catalog").select("*").execute().data[0]
    db.table("document_catalog").update(
        {"updated_at": "2026-01-01T00:00:00+00:00"}
    ).eq("id", before["id"]).execute()

    confluence._to_record(_Ctx(), _SPACE, "page", _page("Body."))

    after = db.table("document_catalog").select("*").execute().data[0]
    assert after["container_id"] == "s1"
    assert after["updated_at"] == "2026-01-01T00:00:00+00:00", (
        "the container repair touched updated_at — a repair is not an edit, "
        "and anything reading recency to spot a stale row is now blind to it"
    )


def test_a_writer_with_no_container_never_blanks_a_stored_one(
    isolated_settings, monkeypatch
):
    """The upsert must omit the column rather than send NULL for it.

    Writers that know no container (uploads, chat attachments, and Drive
    today) call `register_document` without one. If that sent an explicit
    NULL, any such writer touching a row would erase a container another
    writer had set — and the row would drop out of the deselection cleanup
    with nothing to indicate it had."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-conf", "slug": "co-conf", "display_name": "C"}
    ).execute()
    monkeypatch.setattr(
        document_catalog, "llm_call",
        lambda **k: type("R", (), {"output": {"summary": "A summary.",
                                              "topics": ["t"]}})(),
    )
    monkeypatch.setattr(document_catalog, "embed_texts",
                        lambda texts, **k: [[0.1] * 1536])

    confluence._to_record(_Ctx(), _SPACE, "page", _page("Body."))
    assert db.table("document_catalog").select("*").execute().data[0][
        "container_id"
    ] == "s1"

    # A CHANGED body, re-registered by a writer that declares no container —
    # the full upsert path, not the short-circuit.
    document_catalog.register_document(
        "co-conf",
        provider=document_catalog.PROVIDER_CONFLUENCE,
        external_id="page-1",
        title="Search ranking spec",
        content_hash="a-different-hash",
        get_text=lambda: "New body.",
    )

    assert db.table("document_catalog").select("*").execute().data[0][
        "container_id"
    ] == "s1", "a container-less re-registration blanked the stored space id"

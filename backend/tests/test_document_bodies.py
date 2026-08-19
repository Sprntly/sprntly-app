"""Body resolution for documents that do not live in `document_source_file`.

The defect these close: a Confluence page or a Drive file could be
catalogued, summarised, embedded and ranked, and its text still could not be
located. Retrieval therefore required the user to already know where the
document lived — ask "what do the release notes say" and get nothing, ask
"check Confluence for release notes" and get real content. Same shape as
needing to spell a filename, one layer up.

Drive is the harder half and the reason a path has to be STORED rather than
recomputed: `ingest_file` writes to `md_filename(name)`, which slugifies, and
a collision appends `.1.md`. Two Drive files whose display names normalise to
one markdown name are indistinguishable after the fact — which is exactly the
case `test_two_drive_files_that_collide_resolve_to_their_own_text` fixes in
place, and exactly the case the backfill refuses to guess at.
"""
from __future__ import annotations

import json

import pytest

from app import document_bodies
from app.datasets import dataset_path

_CID = "co-doc-bodies"


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert({
            "id": company_id, "slug": f"slug-{company_id}",
            "display_name": company_id,
        }).execute()


def _seed_drive_source(db, file_id, *, label, company_id=_CID, config=None):
    """One Drive file's `kg_source` provenance row, as the extractor writes it."""
    _seed_company(db, company_id)
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(company_id, file_id),
        "enterprise_id": company_id,
        "source_type": "google_drive",
        "label": label,
        "config": {"file_id": file_id, **(config or {})},
        "status": "active",
    }).execute()


def _write_corpus_file(data_dir, slug, name, text):
    target = dataset_path(slug) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


# ────────────────────────── markdown_location ──────────────────────────


def test_markdown_location_keeps_the_basename_not_the_absolute_path():
    """The absolute path embeds `settings.data_path`. Storing it would strand
    every recorded location at once the first time that volume is re-mounted
    or re-pathed — and nothing is gained, because `ingest_file` always writes
    directly under `dataset_path(slug)`."""
    location = document_bodies.markdown_location(
        "acme", "/var/data/acme/q3_roadmap.1.md"
    )
    assert location == {"md_dataset": "acme", "md_file": "q3_roadmap.1.md"}


def test_markdown_location_is_empty_when_either_half_is_unknown():
    """`{}` rather than blank strings, so a caller merging this into a config
    leaves an unknown location UNSET. A stored empty string would later read
    as "we looked and there was nothing", which is a different claim."""
    assert document_bodies.markdown_location("", "/var/data/acme/x.md") == {}
    assert document_bodies.markdown_location("acme", "") == {}


# ─────────────────── T3: the collision that makes storage necessary ──────


def test_two_drive_files_that_collide_resolve_to_their_own_text(isolated_settings):
    """AC7/T3. Two distinct Drive files whose display names normalise to ONE
    markdown filename. The second is written as the `.1.md` variant.

    This is the case that cannot be reconstructed — from the catalog row alone
    both files derive the SAME candidate path, so whichever is asked for, one
    of them would be served the other's text under its own name. Storing the
    path the write actually returned is what makes them distinguishable.
    """
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _write_corpus_file(data_dir, "acme", "q3_roadmap.md", "FIRST file contents")
    _write_corpus_file(data_dir, "acme", "q3_roadmap.1.md", "SECOND file contents")

    _seed_drive_source(
        db, "drive-first", label="Q3 Roadmap",
        config={"md_dataset": "acme", "md_file": "q3_roadmap.md"},
    )
    _seed_drive_source(
        db, "drive-second", label="Q3 roadmap",
        config={"md_dataset": "acme", "md_file": "q3_roadmap.1.md"},
    )

    first = document_bodies.resolve_drive_body(_CID, "drive-first")
    second = document_bodies.resolve_drive_body(_CID, "drive-second")

    assert first.text == "FIRST file contents"
    assert second.text == "SECOND file contents"
    # Named explicitly: serving one document's text under the other's name is
    # the failure this whole mechanism exists to prevent, and it would still
    # produce two successful reads.
    assert first.text != second.text


# ───────────────── T6: None is not empty (AC9) ──────────────────────────


def test_unrecorded_drive_path_resolves_to_none_with_a_reason(isolated_settings):
    """A file synced before the location was recorded. `None`, not ""."""
    db = isolated_settings["supabase"]
    _seed_drive_source(db, "drive-old", label="Legacy doc")

    resolved = document_bodies.resolve_drive_body(_CID, "drive-old")

    assert resolved.text is None
    assert resolved.resolved is False
    assert resolved.reason


def test_deleted_drive_file_resolves_to_none_not_empty(isolated_settings):
    """The path is recorded and the file is gone. Still None."""
    db = isolated_settings["supabase"]
    _seed_drive_source(
        db, "drive-gone", label="Deleted doc",
        config={"md_dataset": "acme", "md_file": "vanished.md"},
    )

    resolved = document_bodies.resolve_drive_body(_CID, "drive-gone")

    assert resolved.text is None
    assert resolved.reason


def test_empty_drive_file_resolves_to_empty_string_not_none(isolated_settings):
    """AC9's other half, and the one that is easy to get wrong: a document
    that IS readable and happens to have no text must not be reported the same
    way as one that could not be read. `if not text` collapses them; the
    caller has to branch on `is None`."""
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _write_corpus_file(data_dir, "acme", "blank.md", "")
    _seed_drive_source(
        db, "drive-blank", label="Blank doc",
        config={"md_dataset": "acme", "md_file": "blank.md"},
    )

    resolved = document_bodies.resolve_drive_body(_CID, "drive-blank")

    assert resolved.text == ""
    assert resolved.resolved is True
    assert resolved.text is not None


def test_a_stored_path_may_not_escape_the_dataset_directory(isolated_settings):
    """The stored value is read out of a JSON blob and joined onto a path, so
    anything that is not a plain basename is refused rather than followed."""
    db = isolated_settings["supabase"]
    _seed_drive_source(
        db, "drive-evil", label="Traversal",
        config={"md_dataset": "acme", "md_file": "../../etc/passwd"},
    )

    assert document_bodies.drive_markdown_path(_CID, "drive-evil") is None
    assert document_bodies.resolve_drive_body(_CID, "drive-evil").text is None


# ────────────────────── T7: backfill (AC10) ─────────────────────────────


def _connect_drive(monkeypatch, slug="acme"):
    """A Drive connection whose config names the dataset the corpus lives in —
    where the backfill learns which directory to look in."""
    from app import db

    monkeypatch.setattr(
        db, "get_connection",
        lambda company_id, provider: {
            "config_json": json.dumps({"dataset": slug})
        },
    )


def test_backfill_records_an_unambiguous_path(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _connect_drive(monkeypatch)
    _write_corpus_file(data_dir, "acme", "q3_roadmap.md", "roadmap text")
    _seed_drive_source(db, "drive-1", label="Q3 Roadmap")

    counts = document_bodies.backfill_drive_markdown_paths(_CID)

    assert counts["updated"] == 1
    assert document_bodies.resolve_drive_body(_CID, "drive-1").text == "roadmap text"


def test_backfill_leaves_a_collision_unset_rather_than_guessing(
    isolated_settings, monkeypatch
):
    """AC10's load-bearing half. Once `.1.md` exists, nothing recorded which
    file took which variant. A guess silently serves another document's text
    under this document's name — the user cannot even tell it happened, which
    makes it worse than having no text at all."""
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _connect_drive(monkeypatch)
    _write_corpus_file(data_dir, "acme", "q3_roadmap.md", "one of them")
    _write_corpus_file(data_dir, "acme", "q3_roadmap.1.md", "the other one")
    _seed_drive_source(db, "drive-a", label="Q3 Roadmap")
    _seed_drive_source(db, "drive-b", label="q3 roadmap")

    counts = document_bodies.backfill_drive_markdown_paths(_CID)

    assert counts["updated"] == 0
    assert counts["ambiguous"] == 2
    for file_id in ("drive-a", "drive-b"):
        assert document_bodies.resolve_drive_body(_CID, file_id).text is None


def test_backfill_is_idempotent(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _connect_drive(monkeypatch)
    _write_corpus_file(data_dir, "acme", "q3_roadmap.md", "roadmap text")
    _seed_drive_source(db, "drive-1", label="Q3 Roadmap")

    first = document_bodies.backfill_drive_markdown_paths(_CID)
    second = document_bodies.backfill_drive_markdown_paths(_CID)

    assert first["updated"] == 1
    assert second["updated"] == 0
    assert second["already_set"] == 1
    assert document_bodies.resolve_drive_body(_CID, "drive-1").text == "roadmap text"


def test_backfill_dry_run_writes_nothing(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    _connect_drive(monkeypatch)
    _write_corpus_file(data_dir, "acme", "q3_roadmap.md", "roadmap text")
    _seed_drive_source(db, "drive-1", label="Q3 Roadmap")

    counts = document_bodies.backfill_drive_markdown_paths(_CID, apply=False)

    assert counts["updated"] == 1
    assert document_bodies.resolve_drive_body(_CID, "drive-1").text is None


# ──────────────────── Confluence resolution (AC3, AC4, AC5) ─────────────


class _FakeConfluence:
    """Stands in for `connectors.confluence_fetch`."""

    def __init__(self, page=None, *, session=object(), raises=False):
        self.page = page
        self.session = session
        self.raises = raises
        self.sessions_opened = 0
        self.pages_fetched = []

    def open_session(self, enterprise_id):
        self.sessions_opened += 1
        return self.session

    def get_page(self, session, page_id):
        self.pages_fetched.append(page_id)
        if self.raises:
            raise RuntimeError("token expired")
        return self.page


@pytest.fixture
def fake_confluence(monkeypatch):
    from app.connectors import confluence_fetch

    def _install(**kwargs):
        fake = _FakeConfluence(**kwargs)
        monkeypatch.setattr(confluence_fetch, "open_session", fake.open_session)
        monkeypatch.setattr(confluence_fetch, "get_page", fake.get_page)
        return fake

    return _install


def test_confluence_body_resolves_through_open_session_and_get_page(
    fake_confluence,
):
    """AC3: the page id IS the catalog row's external_id, and the two calls
    are the ones the chat lookup path already uses."""
    fake = fake_confluence(page={"id": "page-42", "text": "Cyberdyne is shurting down"})

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("page-42")

    assert resolved.text == "Cyberdyne is shurting down"
    assert fake.pages_fetched == ["page-42"]


def test_confluence_session_is_opened_once_per_ask(fake_confluence):
    """AC6: worst case is MAX_SELECTED_DOCUMENTS page fetches, and they share
    one session — three token exchanges to read three pages would be two more
    than the work needs."""
    fake = fake_confluence(page={"id": "p", "text": "body"})
    resolver = document_bodies.BodyResolver(_CID)

    for page_id in ("p1", "p2", "p3"):
        resolver.resolve_confluence(page_id)

    assert fake.sessions_opened == 1
    assert fake.pages_fetched == ["p1", "p2", "p3"]


def test_confluence_bodies_are_never_cached_across_asks(fake_confluence):
    """AC4. Fetch live, every time. A wiki page can change between two
    questions in one conversation, and the cache that was considered — the
    conversation turn row — does not exist while the answer is being composed
    and would replay a body clamped SMALLER than a live fetch delivers."""
    fake = fake_confluence(page={"id": "p1", "text": "body"})

    document_bodies.BodyResolver(_CID).resolve_confluence("p1")
    document_bodies.BodyResolver(_CID).resolve_confluence("p1")

    assert fake.pages_fetched == ["p1", "p1"]


def test_confluence_fetch_failure_is_a_stated_reason_not_an_exception(
    fake_confluence,
):
    fake = fake_confluence(raises=True)

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("p1")

    assert resolved.text is None
    assert resolved.reason
    assert fake.pages_fetched == ["p1"]


def test_confluence_unreadable_page_does_not_read_as_absence(fake_confluence):
    """AC5, at the unit level. `get_page` returns None for a page the account
    cannot read. The reason must describe REACHABILITY, never existence."""
    fake_confluence(page=None)

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("p1")

    assert resolved.text is None
    lowered = resolved.reason.lower()
    assert "could not be read" in lowered
    for forbidden in ("does not exist", "no such", "not found", "never uploaded"):
        assert forbidden not in lowered


def test_no_confluence_connection_is_a_stated_reason(fake_confluence):
    fake_confluence(session=None)

    resolved = document_bodies.BodyResolver(_CID).resolve_confluence("p1")

    assert resolved.text is None
    assert "confluence" in resolved.reason.lower()


def test_an_unknown_provider_degrades_instead_of_raising():
    """A source can be catalogued long before anything knows how to read it.
    That must degrade the way every other unreadable document does."""
    resolved = document_bodies.BodyResolver(_CID).resolve("notion", "abc")

    assert resolved.text is None
    assert resolved.reason

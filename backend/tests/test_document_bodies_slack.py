"""Body resolution for the Slack `document_catalog` provider.

Slack channels are catalogued (see `test_kg_ingest_slack_extract.py`'s
registration suite), so `BodyResolver.resolve("slack", channel_id)` must be
able to read one back — the step that turns a ranked, catalogued channel into
something the model can actually quote. Unlike Confluence and Drive, there is
no network call: `resolve_slack_body` reads the channel's own section out of
`slack_channels.md`, the same corpus file `connectors.slack_sync.sync_slack`
already writes and keeps current on every sync.

Also carries the AC13 regression proof that adding the Slack branch left the
Drive and Confluence branches, and the unknown-provider fallback, byte-for-
byte unchanged.
"""
from __future__ import annotations

import pytest

from app import document_bodies, document_catalog
from app.connectors.slack_sync import channel_messages_to_markdown
from app.datasets import dataset_path

_CID = "co-doc-bodies-slack"


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert({
            "id": company_id, "slug": f"slug-{company_id}", "display_name": company_id,
        }).execute()


@pytest.fixture
def catalog(isolated_settings, monkeypatch):
    """`document_catalog` with its outbound LLM/embedding calls stubbed — the
    same idiom `test_document_catalog.py`'s `catalog` fixture uses — so these
    tests exercise the REAL `register_document` upsert without a real
    network round-trip. `_CID` is pre-seeded as a company."""
    from app import document_catalog as mod

    monkeypatch.setattr(mod, "llm_call", lambda **k: type(
        "R", (), {"output": {"summary": "s", "topics": ["t"]}})())
    monkeypatch.setattr(
        mod, "embed_texts", lambda texts, **k: [[0.1] * 1536 for _ in texts]
    )
    _seed_company(isolated_settings["supabase"])
    return isolated_settings


def _write_corpus(data_dir, slug, text):  # noqa: ARG001 — data_dir kept for readability at call sites
    path = dataset_path(slug) / "slack_channels.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _register(company_id, channel_id, channel_name, text):
    document_catalog.register_document(
        company_id,
        provider=document_catalog.PROVIDER_SLACK,
        external_id=channel_id,
        title=f"#{channel_name}",
        source_name="Slack",
        content_hash=document_catalog.content_hash_for(text),
        get_text=lambda: text,
    )


def _msg(user, text, ts):
    return {"user": user, "text": text, "ts": ts}


# ═══════════════════════ Retrieval — round trip (AC10) ═════════════════════


def test_resolve_slack_body_returns_the_channel_section(catalog):
    md = channel_messages_to_markdown(
        "general", "", "",
        [_msg("U1", "Customers keep asking for SSO.", "1700000000.000001")],
        {}, {"U1": "Alice"},
    )
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md)
    _register(_CID, "C1", "general", md)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert resolved.text is not None
    assert "Customers keep asking for SSO." in resolved.text
    assert resolved.reason == ""


def test_resolve_slack_body_does_not_bleed_into_the_next_channel(catalog):
    md_a = channel_messages_to_markdown(
        "general", "", "", [_msg("U1", "general content", "1700000000.000001")], {}, {}
    )
    md_b = channel_messages_to_markdown(
        "random", "", "", [_msg("U1", "random content", "1700000001.000001")], {}, {}
    )
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md_a + "\n" + md_b)
    _register(_CID, "C1", "general", md_a)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert "general content" in resolved.text
    assert "random content" not in resolved.text


def test_resolve_slack_body_not_truncated_by_a_heading_shaped_message(catalog):
    """Mirrors the trap `remove_channels_from_corpus` already guards against
    (`slack_sync.py:715-722`): a message whose own TEXT happens to look like
    a markdown heading must not be mistaken for the next channel's heading
    and truncate the section early."""
    md_a = channel_messages_to_markdown(
        "eng", "", "",
        [_msg("U1", "## deploy notes", "1700000000.000001"),
         _msg("U1", "still eng content after", "1700000001.000001")],
        {}, {},
    )
    md_b = channel_messages_to_markdown(
        "random", "", "", [_msg("U1", "random content", "1700000002.000001")], {}, {}
    )
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md_a + "\n" + md_b)
    _register(_CID, "C1", "eng", md_a)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert "## deploy notes" in resolved.text
    assert "still eng content after" in resolved.text
    assert "random content" not in resolved.text


def test_resolve_slack_body_matches_channel_name_case_insensitively(catalog):
    md = channel_messages_to_markdown(
        "support", "", "", [_msg("U1", "help please", "1700000000.000001")], {}, {}
    )
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md)
    # The catalog row's title carries the ORIGINAL display case; the corpus
    # heading Slack channel names normalise to lowercase — the two must
    # still match.
    _register(_CID, "C1", "Support", md)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert "help please" in resolved.text


# ═══════════════════════ Retrieval — not found (AC11) ═══════════════════════


def test_resolve_slack_body_missing_file_returns_none_with_reason(catalog):
    _register(_CID, "C1", "general", "## #general\n\nhi\n")

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert resolved.text is None
    assert resolved.reason


def test_resolve_slack_body_missing_section_returns_a_different_reason(catalog):
    """AC11's second half: "no file at all" and "a file with no section for
    this channel" are different facts and must carry different reasons."""
    md_other = channel_messages_to_markdown(
        "random", "", "", [_msg("U1", "random content", "1700000000.000001")], {}, {}
    )
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md_other)
    _register(_CID, "C1", "general", "## #general\n\nhi\n")
    section_missing = document_bodies.resolve_slack_body(_CID, "C1")

    other_cid = "co-doc-bodies-slack-2"
    _seed_company(catalog["supabase"], other_cid)
    _register(other_cid, "C2", "general", "## #general\n\nhi\n")
    file_missing = document_bodies.resolve_slack_body(other_cid, "C2")

    assert section_missing.text is None
    assert file_missing.text is None
    assert section_missing.reason and file_missing.reason
    assert section_missing.reason != file_missing.reason


def test_resolve_slack_body_empty_channel_returns_empty_string_not_none(catalog):
    """AC11's other half, and the one that is easy to get wrong: a channel
    that synced with zero messages carries only the placeholder sentence
    `channel_messages_to_markdown` writes — read, genuinely empty — never the
    same outcome as a channel that could not be read at all."""
    md = channel_messages_to_markdown("quiet", "", "", [], {}, {})
    _write_corpus(catalog["data_dir"], f"slug-{_CID}", md)
    _register(_CID, "C1", "quiet", md)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert resolved.text == ""
    assert resolved.resolved is True
    assert resolved.text is not None


# ═══════════════════════ Retrieval — multi-dataset (AC12) ══════════════════


def test_resolve_slack_body_finds_a_non_default_dataset(catalog, monkeypatch):
    """Corpus lives under a workspace dataset, not the company's default
    slug — resolution must sweep `company_dataset_slugs`, the same helper
    `purge_channels_from_synced_data` already sweeps, rather than only
    checking the default."""
    from app.connectors import slack_sync

    monkeypatch.setattr(
        slack_sync, "company_dataset_slugs", lambda _cid: ["workspace-eng"]
    )
    md = channel_messages_to_markdown(
        "general", "", "", [_msg("U1", "hi from workspace", "1700000000.000001")], {}, {}
    )
    _write_corpus(catalog["data_dir"], "workspace-eng", md)
    _register(_CID, "C1", "general", md)

    resolved = document_bodies.resolve_slack_body(_CID, "C1")

    assert "hi from workspace" in resolved.text


# ═══════════════════════ Regression / non-breakage (AC13) ══════════════════


def test_body_resolver_drive_branch_unchanged(isolated_settings):
    """Adding the Slack branch to `BodyResolver.resolve` must leave Drive's
    resolution identical to the pre-change build."""
    db = isolated_settings["supabase"]
    data_dir = isolated_settings["data_dir"]
    cid = "co-drive-regress"
    _seed_company(db, cid)
    target = dataset_path("acme") / "q3_roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("roadmap text", encoding="utf-8")
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(cid, "drive-1"),
        "enterprise_id": cid,
        "source_type": "google_drive",
        "label": "Q3 Roadmap",
        "config": {
            "file_id": "drive-1", "md_dataset": "acme", "md_file": "q3_roadmap.md",
        },
        "status": "active",
    }).execute()

    resolved = document_bodies.BodyResolver(cid).resolve("google_drive", "drive-1")

    assert resolved.text == "roadmap text"


def test_body_resolver_confluence_branch_unchanged(monkeypatch):
    """Same proof for Confluence — the live-fetch branch is untouched."""
    from app.connectors import confluence_fetch

    monkeypatch.setattr(confluence_fetch, "open_session", lambda enterprise_id: object())
    monkeypatch.setattr(
        confluence_fetch, "get_page",
        lambda session, page_id: {"id": page_id, "text": "wiki body"},
    )

    resolved = document_bodies.BodyResolver("co-confluence-regress").resolve(
        "confluence", "page-1"
    )

    assert resolved.text == "wiki body"


def test_body_resolver_unknown_provider_still_states_a_reason():
    """A source can be catalogued long before anything here knows how to
    read it (unchanged from before Slack existed as a provider)."""
    resolved = document_bodies.BodyResolver("co-unknown-regress").resolve(
        "notion", "abc"
    )

    assert resolved.text is None
    assert resolved.reason

"""Real-source provenance for brief chips (app/brief_sources).

Apurva ruling 2026-07-27: cards may only show sources the company actually
has. The extractor infers signal source_types from document CONTENT, so a
plain uploaded PDF can mint `analytics` signals for a company with no
analytics source — and those inferred types were surfacing as "Analytics"
chips. These tests pin the resolution and the display filter.
"""
from __future__ import annotations

from app.brief_sources import (
    DOCUMENTS_SOURCE,
    allowed_source_types,
    display_source_types,
    source_label,
)


def _seed_company(db, cid="ent-A", slug="acme"):
    if not db.table("companies").select("id").eq("id", cid).execute().data:
        db.table("companies").insert(
            {"id": cid, "slug": slug, "display_name": cid.title()}
        ).execute()
    return cid


def test_no_connections_no_categories_allows_only_pm_manual(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db)
    allowed = allowed_source_types("ent-A", "acme")
    assert allowed == {"pm_manual"}


def test_active_connector_makes_its_source_type_real(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db)
    from app.db.connections import upsert_connection

    def _conn(provider, status):
        upsert_connection(company_id="ent-A", provider=provider,
                          token_encrypted="tok", scopes="", status=status)

    _conn("amplitude", "active")
    _conn("slack", "active")
    # An inactive connection contributes nothing.
    _conn("stripe", "disconnected")

    allowed = allowed_source_types("ent-A", "acme")
    assert "analytics" in allowed          # amplitude
    assert "communication" in allowed      # slack
    assert "revenue" not in allowed        # stripe is not active


def test_categorized_upload_makes_its_source_type_real(isolated_settings, tmp_path, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db)
    from app import datasets

    monkeypatch.setattr(datasets, "dataset_path", lambda slug: tmp_path / slug)
    (tmp_path / "acme").mkdir(parents=True, exist_ok=True)
    datasets.set_file_categories("acme", ["feedback.csv"], "voice")

    allowed = allowed_source_types("ent-A", "acme")
    assert "customer_voice" in allowed
    assert "analytics" not in allowed


def test_research_upload_alone_makes_customer_voice_real(
    isolated_settings, tmp_path, monkeypatch
):
    """A research readout dropped into the Research shelf is real evidence with
    no connector at all — that shelf ships with only a coming-soon Marvin, so
    the upload path is the whole feature."""
    db = isolated_settings["supabase"]
    _seed_company(db)
    from app import datasets

    monkeypatch.setattr(datasets, "dataset_path", lambda slug: tmp_path / slug)
    (tmp_path / "acme").mkdir(parents=True, exist_ok=True)
    datasets.set_file_categories("acme", ["interviews.docx"], "research")

    allowed = allowed_source_types("ent-A", "acme")
    assert "customer_voice" in allowed
    assert "analytics" not in allowed


def test_display_filter_drops_unreal_and_falls_back_to_documents():
    allowed = {"customer_voice", "pm_manual"}
    # Mixed: only the real channel survives.
    assert display_source_types({"analytics", "customer_voice"}, allowed) == [
        "customer_voice"
    ]
    # Nothing real: honest documents fallback, never an empty chip row.
    assert display_source_types({"analytics", "revenue"}, allowed) == [
        DOCUMENTS_SOURCE
    ]
    # Fail-open: allowed=None means no filtering.
    assert display_source_types({"analytics"}, None) == ["analytics"]


def test_source_labels_are_human():
    assert source_label("customer_voice") == "Customer voice"
    assert source_label(DOCUMENTS_SOURCE) == "Uploaded documents"
    assert source_label("unknown_thing") == "Unknown Thing"


def test_payload_and_cards_respect_real_sources():
    """The compose payload lists only real sources (with documents fallback in
    evidence tags), and cards_to_insights ENFORCES the chip list server-side
    regardless of what the model emitted."""
    from app.synthesis.top_insights_skill import cards_to_insights, to_signal_payload
    from app.synthesis.convergence import ThemeConvergence

    tc = ThemeConvergence(theme_id="t1", theme_label="Latency")
    tc.signal_count = 3
    tc.source_types = {"analytics", "customer_voice"}
    tc.evidence = [
        {"source_type": "analytics", "kind": "metric", "content": "20s paints"},
        {"source_type": "customer_voice", "kind": "ticket", "content": "slow"},
    ]

    text = to_signal_payload(
        [tc], recipient="A", company_scale=None,
        allowed_sources={"customer_voice", "pm_manual"})
    assert "sources: ['customer_voice']" in text
    assert "[documents/metric]" in text          # analytics tag rewritten
    assert "[customer_voice/ticket]" in text     # real channel kept

    cards = [{"type": "reliability", "title": "t", "body": "b",
              "sources": ["Analytics", "Support tickets"], "finding_id": "t1"}]
    insights = [{"theme_id": "t1", "title": "t", "subtitle": "s",
                 "recommendation": "r", "tag": "something_broken"}]
    out = cards_to_insights(
        cards, insights,
        display_sources_by_theme={"t1": ["customer_voice"]})
    assert out[0]["_card"]["sources"] == ["Customer voice"]

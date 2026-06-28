"""Model tiering: the deep-reasoning work (KG extraction + weekly-brief
synthesis) runs on DEEP_MODEL (opus); the PRD composes off that already-analysed
material and stays on the default (sonnet).

Locks the decision so a future refactor that drops a `model=` override (silently
falling back to DEFAULT_MODEL) is caught here, and guards the telemetry pricing
row that est_cost_usd fails closed on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult
from app.llm import DEEP_MODEL, DEFAULT_MODEL


def _llm_result(output, model=DEFAULT_MODEL):
    return LLMResult(
        output=output, model=model, prompt_version="test",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_theme_with_signals(facade, ent, label, specs):
    """specs: list of (source_type, kind, props, age_days). Returns the theme."""
    from app.graph.types import Entity, Relationship, Signal
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    for st, kind, props, age in specs:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=f"{label} {kind} {age}", properties=props,
                     valid_at=now - timedelta(days=age))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return theme


def _seed_company(db, cid):
    if not db.table("companies").select("id").eq("id", cid).execute().data:
        db.table("companies").insert(
            {"id": cid, "slug": f"slug-{cid}", "display_name": cid.title()}
        ).execute()
    return cid


# ─────────────────────── DEEP_MODEL call sites ───────────────────────

def test_deep_model_is_opus_and_distinct_from_default():
    # Opus tier standardised on 4.7 (single opus version across the codebase).
    assert DEEP_MODEL == "claude-opus-4-7"
    assert DEFAULT_MODEL == "claude-sonnet-4-6"
    assert DEEP_MODEL != DEFAULT_MODEL


def test_kg_extraction_stays_on_default(facade):
    """KG extraction is schema-bound extraction that LOOPS per doc/batch — opus
    would compound cost for marginal lift, so it stays on the default (sonnet)."""
    from app.graph import extractor

    captured = {}

    def _spy(**kw):
        captured.update(kw)
        return _llm_result({"signals": [], "themes": []})

    with patch.object(extractor, "llm_call", side_effect=_spy), \
         patch.object(extractor, "embed_texts",
                      side_effect=lambda texts, **k: [[0.1] * 4 for _ in texts]):
        extractor.extract_document(facade, "ent-A", doc_name="doc1", text="...")

    assert captured["purpose"] == "extract_document"
    assert captured.get("model") is None   # → gateway uses DEFAULT_MODEL (sonnet)


def test_weekly_brief_synthesis_uses_deep_model(facade, isolated_settings):
    """compose_weekly_brief is the weekly brief → opus."""
    from app.synthesis import agent as synth

    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ])
    ranked = {
        "summary_headline": "h",
        "insights": [{
            "theme_id": theme.id, "tag": "something_broken", "title": "t",
            "subtitle": "s", "recommendation": "do it",
            "metrics": [{"label": "ARR", "value": "$1M"}],
            "convergence": [{"source": "revenue", "signal": "x",
                             "strength": "Strong"}],
            "confidence": 0.8, "is_headline": True, "reasoning": "top.",
        }],
    }

    captured = {}

    def _spy(**kw):
        captured.update(kw)
        return _llm_result(ranked)

    with patch.object(synth, "llm_call", side_effect=_spy):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert captured["purpose"] == "compose_weekly_brief"
    assert captured["model"] == DEEP_MODEL


def test_backlog_sequencing_stays_on_default(facade, isolated_settings):
    """Backlog sequencing ranks the lower-stakes 'rest' (ranks 4+) — a ranking
    task, not open-ended synthesis, so it stays on the default (sonnet)."""
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only",
                                 [("revenue", "deal_blocker", {}, 0)])

    captured = {}

    def _spy(**kw):
        captured.update(kw)
        return _llm_result({"items": [{"theme_id": t.id, "tag": "something_new",
                                       "reasoning": "r"}]})

    with patch.object(bl, "llm_call", side_effect=_spy):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    assert captured["purpose"] == "sequence_backlog"
    assert captured.get("model") is None   # → gateway uses DEFAULT_MODEL (sonnet)


def test_agent_chat_uses_default_model():
    """The agentic chat route is an interactive tool-dispatch loop (MEDIUM
    routing, many turns) → default (sonnet), not opus."""
    from app.routes import agent_chat

    assert agent_chat._MODEL == DEFAULT_MODEL
    assert "opus" not in agent_chat._MODEL


# ─────────────────────── pricing (fails closed) ───────────────────────

def test_deep_model_is_priced():
    """est_cost_usd fails closed; DEEP_MODEL must have a pricing row or every
    KG/brief call would raise on cost accounting."""
    from app.llm_telemetry import MODEL_PRICING, RunUsage

    assert DEEP_MODEL in MODEL_PRICING
    usage = RunUsage(input_tokens=1000, output_tokens=1000)
    assert usage.est_cost_usd(DEEP_MODEL) > 0


# ─────────────────────── PRD stays on the default (sonnet) ───────────────────────

def test_prd_author_not_in_heavy_skills():
    """PRD generation stays on sonnet: prd-author must NOT route to the heavy
    (opus) model in the interactive ask path — only the genuinely heavy skill
    remains."""
    from app.qa_agent import HEAVY_MODEL, HEAVY_SKILLS

    assert "prd-author" not in HEAVY_SKILLS
    assert "competitive-intelligence-review" in HEAVY_SKILLS
    assert HEAVY_MODEL in __import__("app.llm_telemetry",
                                     fromlist=["MODEL_PRICING"]).MODEL_PRICING

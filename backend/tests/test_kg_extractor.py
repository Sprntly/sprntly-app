"""Tests for app.graph.extractor.extract_document — the source_type_default
coercion and provenance_extra stamping used by connector-category uploads."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.gateway import LLMResult


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _llm_result(items: list[dict]) -> LLMResult:
    return LLMResult(
        output={"signals": items}, model="m", prompt_version=ex.PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _item(content: str, source_type: str) -> dict:
    return {"kind": "feature_request", "content": content,
            "source_type": source_type, "theme": "Search",
            "relationship": "REQUESTS", "confidence": 0.9}


def _extract(facade, items, **kwargs) -> None:
    with patch.object(ex, "llm_call", return_value=_llm_result(items)), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="calls.md",
                            text="doc body", **kwargs)


def _signals(facade):
    # query via edges is overkill here; read the signal store directly through
    # the facade's public per-id fetch by re-deriving the content-keyed ids.
    import uuid
    ids = [str(uuid.uuid5(ex._NS, f"ent-x|{c}"))
           for c in ("seeded fact", "revenue fact", "weird fact")]
    return facade.get_signals("ent-x", ids)


def test_source_type_default_coerces_seeded_and_unknown_types(facade):
    """With source_type_default set, seeded types (pm_manual/verbal_claim/
    agent_inferred) and out-of-vocabulary types are re-stamped; an evidence
    type the LLM picked on merit is kept."""
    _extract(facade, [
        _item("seeded fact", "pm_manual"),
        _item("revenue fact", "revenue"),
        _item("weird fact", "meeting_notes"),   # not in SIGNAL_SOURCE_TYPES
    ], source_type_default="customer_voice")

    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["seeded fact"].source_type == "customer_voice"
    assert by_content["weird fact"].source_type == "customer_voice"
    assert by_content["revenue fact"].source_type == "revenue"


def test_no_default_keeps_llm_source_types(facade):
    """Without source_type_default (every pre-existing caller), the LLM's
    choice lands verbatim — no behavior change."""
    _extract(facade, [_item("seeded fact", "pm_manual")])
    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["seeded fact"].source_type == "pm_manual"


def test_force_source_type_overrides_even_merited_evidence_types(facade):
    """force_source_type PINS every signal, unlike source_type_default which
    keeps an evidence type the LLM picked on merit.

    Used where the DOCUMENT CLASS decides evidentiary weight and a model-picked
    connected type would be an integrity problem — the roadmap path, whose
    quoted metrics must never count as connected evidence in the brief gate."""
    _extract(facade, [
        _item("seeded fact", "pm_manual"),
        _item("revenue fact", "revenue"),      # merited — still overridden
        _item("weird fact", "meeting_notes"),  # out of vocabulary
    ], force_source_type="pm_manual")

    by_content = {s.content: s for s in _signals(facade).values()}
    assert {s.source_type for s in by_content.values()} == {"pm_manual"}


def test_force_source_type_takes_precedence_over_default(facade):
    """When both are passed, the pin wins."""
    _extract(facade, [_item("revenue fact", "revenue")],
             source_type_default="customer_voice", force_source_type="pm_manual")
    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["revenue fact"].source_type == "pm_manual"


def test_provenance_extra_merged_with_origin(facade):
    """provenance_extra rides into every signal's provenance next to origin."""
    _extract(facade, [_item("seeded fact", "pm_manual")],
             origin="connector",
             provenance_extra={"channel": "upload", "category": "voice"})
    sig = next(iter(_signals(facade).values()))
    assert sig.provenance["origin"] == "connector"
    assert sig.provenance["channel"] == "upload"
    assert sig.provenance["category"] == "voice"

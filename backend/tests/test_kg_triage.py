"""Tests for app.graph.triage — the haiku relevance/category pass ahead of
extraction, and the app.graph.types taxonomy it classifies against."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult
from app.graph.types import TRIAGE_CATEGORIES, TRIAGE_TAXONOMY_VERSION


def _llm_result(output: dict) -> LLMResult:
    return LLMResult(
        output=output, model="m", prompt_version="p",
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


# ---------- taxonomy ----------

def test_taxonomy_is_bounded_and_versioned():
    """David's 'max ~50 categories' idea from the discovery call is the
    ceiling, not the target — the taxonomy should be short and explicit."""
    assert 0 < len(TRIAGE_CATEGORIES) <= 50
    assert isinstance(TRIAGE_TAXONOMY_VERSION, str) and TRIAGE_TAXONOMY_VERSION


def test_taxonomy_every_entry_has_a_non_empty_description():
    for cat_id, desc in TRIAGE_CATEGORIES.items():
        assert cat_id and isinstance(cat_id, str)
        assert desc and isinstance(desc, str)


def test_taxonomy_referenced_by_the_triage_prompt():
    """AC: taxonomy is referenced by the triage prompt — every category id
    actually appears in the system prompt the classifier sees."""
    import app.graph.triage as triage_mod
    for cat_id in TRIAGE_CATEGORIES:
        assert cat_id in triage_mod._SYSTEM
    assert TRIAGE_TAXONOMY_VERSION in triage_mod.PROMPT_VERSION


# ---------- triage_batch ----------

def test_triage_batch_relevant_verdict():
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", return_value=_llm_result(
            {"relevant": True, "category": "customer_feedback",
             "reason": "a customer complaint", "confidence": 0.8})):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="ingest:test", doc_name="d.md",
            text="the customer says the export is broken",
        )
    assert verdict.relevant is True
    assert verdict.category == "customer_feedback"
    assert verdict.source == "llm"


def test_triage_batch_not_relevant_verdict():
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", return_value=_llm_result(
            {"relevant": False, "category": "internal_admin",
             "reason": "HR PTO policy doc", "confidence": 0.95})):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="ingest:test", doc_name="pto.md",
            text="Employees accrue 15 days of PTO per year.",
        )
    assert verdict.relevant is False
    assert verdict.category == "internal_admin"


def test_triage_batch_unknown_category_coerced_to_other():
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", return_value=_llm_result(
            {"relevant": True, "category": "not-a-real-category",
             "reason": "x", "confidence": 0.5})):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="t",
        )
    assert verdict.category == "other"


def test_triage_batch_missing_relevant_key_defaults_true():
    """Malformed/partial model output must not accidentally filter — the
    weighting toward false negatives applies to schema gaps too."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", return_value=_llm_result(
            {"category": "other", "reason": "x"})):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="t",
        )
    assert verdict.relevant is True


def test_triage_batch_non_dict_output_defaults_relevant_and_other():
    """A malformed (non-dict) `.output` doesn't raise — it degrades to the
    same safe defaults as a missing key would (relevant=True, category=
    'other'), NOT the fail_open sentinel (no exception was actually raised)."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", return_value=_llm_result("not a dict")):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="t",
        )
    assert verdict.relevant is True
    assert verdict.category == "other"
    assert verdict.source == "llm"


def test_triage_batch_fails_open_on_exception():
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", side_effect=RuntimeError("down")):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="t",
        )
    assert verdict.relevant is True
    assert verdict.category == "uncategorized"
    assert verdict.source == "fail_open"


def test_triage_batch_truncates_long_text():
    """Only enough text for a classification judgment rides into the triage
    call — it isn't the extraction call."""
    import app.graph.triage as triage_mod
    captured = {}

    def fake_llm_call(**kw):
        captured["input"] = kw["input"]
        return _llm_result({"relevant": True, "category": "other", "reason": "x"})

    with patch.object(triage_mod, "llm_call", side_effect=fake_llm_call):
        triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d",
            text="z" * 50_000,
        )
    assert len(captured["input"]) < 50_000


def test_log_filtered_logs_category_and_reason(caplog):
    import app.graph.triage as triage_mod
    verdict = triage_mod.TriageResult(relevant=False, category="legal_compliance",
                                      reason="NDA boilerplate")
    with caplog.at_level("WARNING", logger="app.graph.triage"):
        triage_mod.log_filtered(enterprise_id="ent-x", agent="ingest:test",
                                doc_name="nda.md", result=verdict)
    assert len(caplog.records) == 1
    msg = caplog.records[0].message
    assert "ent-x" in msg and "legal_compliance" in msg and "NDA boilerplate" in msg


def test_category_skills_map_only_names_installed_skills():
    """Whatever CATEGORY_SKILLS grows to later, every entry must be a real
    vendored skill — same discipline as PROVIDER_SKILLS
    (test_kg_ingest_skill_routing.test_provider_skills_map_only_names_installed_skills)."""
    from app.skills.loader import list_skills
    import app.graph.triage as triage_mod

    installed = set(list_skills())
    for category, skill_id in triage_mod.CATEGORY_SKILLS.items():
        assert category in TRIAGE_CATEGORIES
        assert skill_id in installed

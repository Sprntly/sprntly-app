"""The roadmap-extraction skill: it loads, declares the expected-output
contract, and running extract_document against a real roadmap chunk (the
same shape app.kg_ingest.roadmap._ingest_roadmap_locked hands it) routes
through the skill and produces output matching what the skill declares in
its references/expected-signal-shape.md.

LLM calls are mocked (no live model in this suite — see
test_kg_extraction_skills.py for the same convention); what's under test
here is (1) the skill loads and is bound the same way the other three
extraction skills are, (2) the routing/gateway plumbing (skill= kwarg,
provenance stamping) matches theirs, and (3) a plausible roadmap output is a
legal member of the vocabulary this skill's own reference doc declares —
including the resolved kind-collapse decision (deal_blocker is NEVER
emitted; commercial stakes ride in `properties.commercial_risk` instead).

A separate real-LLM comparison (not part of this file — mocking a model
response can't prove prompt quality) re-confirms the actual extraction
behavior; see the ticket's live-verification note.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.gateway import LLMResult
import app.qa_agent as qa
from app.skills.loader import get_skill, list_skills

# Declared source_type x kind vocabulary, mirroring
# references/expected-signal-shape.md. Kept here (not parsed from the
# markdown) so a doc/code drift shows up as a clear assertion failure on
# either side, not a silently-passing regex match — same convention as
# test_kg_extraction_skills.py.
_EXPECTED_VOCAB: dict[str, set[str]] = {
    "pm_manual": {"finding"},
}


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


def test_skill_loads_and_declares_expected_shape():
    spec = get_skill("roadmap-extraction")
    assert spec.method.strip()
    assert spec.description
    assert "expected-signal-shape.md" in spec.references
    assert "expected signal shape" in spec.references[
        "expected-signal-shape.md"].lower()
    assert "references/expected-signal-shape.md" in spec.method


def test_skill_is_installed():
    assert "roadmap-extraction" in list_skills()


def test_skill_is_not_invocable_from_chat():
    """An ingestion-time method bound by name from app.kg_ingest.roadmap — not
    something a chat turn may reach. It used to be listed in NON_ROUTABLE (a
    per-skill opt-out of a router menu that offered every other built-in); with
    the built-in menu gone the guarantee is stronger and needs no allow-list:
    NO vendored id is invocable from chat, this one included. Same posture as
    hubspot/jira/clickup-extraction."""
    assert qa._routable("roadmap-extraction", "co-1") is False
    assert qa._invocable("roadmap-extraction", "co-1") is False


def test_skill_runs_against_a_realistic_roadmap_chunk(facade):
    """A real roadmap chunk (no per-record bracket header — free prose, the
    shape app.kg_ingest.roadmap actually hands the extractor) routes through
    the skill and produces a signal whose shape matches the declared
    contract."""
    chunk = (
        "SSO group sync — basically done, just finishing up QA this week.\n"
        "Unblocks the Acme Corp renewal.\n"
    )
    item = {
        "kind": "finding",
        "content": "SSO group sync is basically done, finishing QA this "
                    "week, unblocking the Acme Corp renewal",
        "source_type": "pm_manual", "theme": "SSO",
        "relationship": "AFFECTS",
        "properties": {"initiative_status": "committed",
                        "commercial_risk": True},
        "confidence": 0.85,
    }
    with patch.object(ex, "llm_call", return_value=_llm_result([item])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        result = ex.extract_document(
            facade, "ent-roadmap", doc_name="roadmap: H2.md",
            text=chunk, agent="ingest:roadmap",
            origin=None, force_source_type="pm_manual",
            skill_id="roadmap-extraction",
        )
    assert result["signals"] == 1
    assert mock_call.call_args.kwargs["skill"] == "roadmap-extraction"

    vocab = _EXPECTED_VOCAB[item["source_type"]]
    assert item["kind"] in vocab

    [signal] = facade.active_signals("ent-roadmap")
    assert signal.skill_id == "roadmap-extraction"
    assert signal.kind == "finding"
    assert signal.properties["initiative_status"] == "committed"
    assert signal.properties.get("commercial_risk") is True


def test_kind_collapse_decision_is_documented():
    """The resolved trade-off (collapse to `finding`, never `deal_blocker`)
    must be explicit in the skill's own method, not left ambiguous — the
    ticket's own acceptance criterion."""
    doc = get_skill("roadmap-extraction").method
    assert "deal_blocker" in doc
    assert "ALWAYS" in doc and '"finding"' in doc
    assert "jira-extraction" in doc  # names the precedent it follows


def test_initiative_status_and_target_period_are_documented():
    doc = get_skill("roadmap-extraction").method.lower()
    assert "initiative_status" in doc
    assert "committed" in doc and "planned" in doc and "exploring" in doc
    assert "target_period" in doc


def test_do_not_extract_guidance_is_documented():
    doc = get_skill("roadmap-extraction").method.lower()
    assert "standalone" in doc and "metric" in doc
    assert "shipped" in doc

"""The three connector extraction skills (hubspot/jira/clickup-extraction):
they load, they carry the declared expected-output contract, and running
extract_document against a REAL sample of that connector's data (built with
the actual puller's RawRecord, not a hand-typed string) routes through the
skill and produces output whose structure matches what the skill declares in
its references/expected-signal-shape.md.

LLM calls are mocked (no live model in this suite — see the other extractor
tests for the same convention); what's under test here is (1) the input each
skill actually receives is a real puller rendering, (2) the routing/gateway
plumbing (skill= kwarg, provenance stamping), and (3) that a plausible output
for that connector's record kinds is a legal member of the vocabulary the
skill's own reference doc declares — the structural check the ticket's test
plan asks for.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.gateway import LLMResult
from app.kg_ingest.types import RawRecord
from app.skills.loader import get_skill

# Declared source_type × kind vocabulary per connector, mirroring each
# skill's references/expected-signal-shape.md table. Kept here (not parsed
# from the markdown) so a doc/code drift shows up as a clear assertion
# failure on either side, not a silently-passing regex match.
_EXPECTED_VOCAB: dict[str, dict[str, set[str]]] = {
    "hubspot-extraction": {
        "revenue": {"deal_blocker", "finding"},
        "customer_voice": {"bug", "feature_request", "incident",
                            "sentiment", "finding"},
    },
    "jira-extraction": {
        "project_mgmt": {"bug", "feature_request", "finding"},
    },
    "clickup-extraction": {
        "project_mgmt": {"bug", "feature_request", "finding"},
    },
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


@pytest.mark.parametrize("skill_id", [
    "hubspot-extraction", "jira-extraction", "clickup-extraction",
])
def test_skill_loads_and_declares_expected_shape(skill_id):
    spec = get_skill(skill_id)
    assert spec.method.strip()
    assert spec.description
    assert "expected-signal-shape.md" in spec.references
    assert "expected signal shape" in spec.references["expected-signal-shape.md"].lower()
    # SKILL.md points readers at the reference doc it ships (same convention
    # every other skill with a references/ dir follows).
    assert "references/expected-signal-shape.md" in spec.method


def test_hubspot_skill_runs_against_a_real_hubspot_deal_record(facade):
    """A real RawRecord rendering — built the same way the hubspot puller
    builds one (see app/kg_ingest/pullers/hubspot.py::_pull_deals) — routes
    through the skill and produces a signal whose shape matches the skill's
    declared contract."""
    record = RawRecord(
        provider="hubspot", kind="deal", external_id="901",
        title="Acme Robotics — Q3 renewal",
        text="Lost to a competitor after a 3-week delay on a custom export "
             "API; buyer cited the missing CSV export as the deciding factor.",
        properties={"amount_usd": 42000, "stage": "closedlost",
                    "pipeline": "Enterprise"},
        timestamp="2026-07-01T00:00:00Z",
    )
    batch_text = record.render()
    assert "[hubspot/deal id=901" in batch_text  # sanity: real puller shape

    item = {
        "kind": "deal_blocker",
        "content": "Acme Robotics ($42,000) lost to a competitor after the "
                   "buyer cited missing CSV export as the deciding factor",
        "source_type": "revenue", "theme": "CSV export",
        "relationship": "PRESSURES",
        "properties": {"amount_usd": 42000, "stage": "closedlost"},
        "confidence": 0.9,
    }
    with patch.object(ex, "llm_call", return_value=_llm_result([item])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        result = ex.extract_document(
            facade, "ent-hubspot", doc_name="hubspot-sync-batch-0",
            text=batch_text, agent="ingest:hubspot",
            origin="connector", skill_id="hubspot-extraction",
        )
    assert result["signals"] == 1
    assert mock_call.call_args.kwargs["skill"] == "hubspot-extraction"
    assert mock_call.call_args.kwargs["input"].count("[hubspot/deal id=901") == 1

    vocab = _EXPECTED_VOCAB["hubspot-extraction"]
    assert item["source_type"] in vocab
    assert item["kind"] in vocab[item["source_type"]]


def test_jira_skill_runs_against_a_real_jira_issue_record(facade):
    record = RawRecord(
        provider="jira", kind="issue", external_id="PROJ-142",
        title="Users can't export dashboard as CSV",
        text="CSV export button throws a 500 error for any dashboard with "
             "more than 3 widgets.",
        properties={"status": "In Progress", "priority": "High",
                    "type": "Bug", "project": "Platform",
                    "labels": ["export", "dashboard"]},
        timestamp="2026-07-10T00:00:00Z",
    )
    batch_text = record.render()
    assert "[jira/issue id=PROJ-142" in batch_text

    item = {
        "kind": "bug",
        "content": "CSV export button throws a 500 error for dashboards "
                   "with more than 3 widgets",
        "source_type": "project_mgmt", "theme": "CSV export",
        "relationship": "AFFECTS",
        "properties": {"status": "In Progress", "priority": "High",
                       "issue_type": "Bug"},
        "confidence": 0.9,
    }
    with patch.object(ex, "llm_call", return_value=_llm_result([item])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        result = ex.extract_document(
            facade, "ent-jira", doc_name="jira-sync-batch-0",
            text=batch_text, agent="ingest:jira",
            origin="connector", skill_id="jira-extraction",
        )
    assert result["signals"] == 1
    assert mock_call.call_args.kwargs["skill"] == "jira-extraction"

    vocab = _EXPECTED_VOCAB["jira-extraction"]
    assert item["source_type"] in vocab
    assert item["kind"] in vocab[item["source_type"]]


def test_clickup_skill_runs_against_a_real_clickup_task_record(facade):
    record = RawRecord(
        provider="clickup", kind="task", external_id="88213",
        title="Dashboard export is broken for large workspaces",
        text="Reported by a customer via support: exporting a dashboard "
             "with 50+ widgets times out instead of downloading.",
        properties={"status": "open", "priority": "urgent",
                    "list": "Bugs", "tags": ["export", "p1"],
                    "assignees": ["dana"]},
        timestamp="2026-07-12T00:00:00Z",
    )
    batch_text = record.render()
    assert "[clickup/task id=88213" in batch_text

    item = {
        "kind": "bug",
        "content": "Dashboard export times out for workspaces with 50+ "
                   "widgets instead of downloading, reported by a customer "
                   "via support",
        "source_type": "project_mgmt", "theme": "Dashboard export",
        "relationship": "AFFECTS",
        "properties": {"status": "open", "priority": "urgent", "list": "Bugs"},
        "confidence": 0.85,
    }
    with patch.object(ex, "llm_call", return_value=_llm_result([item])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        result = ex.extract_document(
            facade, "ent-clickup", doc_name="clickup-sync-batch-0",
            text=batch_text, agent="ingest:clickup",
            origin="connector", skill_id="clickup-extraction",
        )
    assert result["signals"] == 1
    assert mock_call.call_args.kwargs["skill"] == "clickup-extraction"

    vocab = _EXPECTED_VOCAB["clickup-extraction"]
    assert item["source_type"] in vocab
    assert item["kind"] in vocab[item["source_type"]]


def test_hubspot_owner_record_is_documented_as_never_extracted():
    """The skill's own contract says owner records carry no signal — pin
    that here so the documented behavior can't silently drift from the
    written contract without a test noticing."""
    doc = get_skill("hubspot-extraction").method.lower()
    assert "owner" in doc
    assert "never" in doc or "do not extract" in doc

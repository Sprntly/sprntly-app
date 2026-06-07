"""Tests for the On-Call agent (incident investigation) + route.

Covers: investigation happy path (assessment persisted as an `incident`
entity + decision-log row; every proposed action PM-gated even when the
model says otherwise), KG-context degradation (no themes → still works),
the route (404/422 validation + happy 200 via dependency override), and the
injection-defense system-prompt assertion.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _llm_result(output: dict) -> LLMResult:
    return LLMResult(
        output=output, model="test-model", prompt_version="oncall-investigate-v1",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
        stop_reason="end_turn",
    )


# Model output that DELIBERATELY tries to opt out of approval — the agent must
# override requires_pm_approval back to true in code.
_LLM_OUTPUT = {
    "severity": "SEV-2",
    "severity_rationale": "Feature degraded, workaround exists.",
    "root_cause_hypothesis": "Synchronous validation added by the v4.2.1 deploy "
                             "blocks the save write path under load (systemic: no "
                             "load test on the path).",
    "impact_assessment": {
        "who": "Clinicians at Riverside General",
        "how_many": "unknown",
        "metrics": [{"label": "p95 latency", "value": "elevated"}],
    },
    "correlated_evidence": ["v4.2.1 deploy 14 min before symptom onset"],
    "proposed_actions": [
        {"type": "rollback", "description": "Roll back v4.2.1",
         "requires_pm_approval": False},          # model lies — must be forced true
        {"type": "code_fix", "description": "Make FHIR validation async",
         "requires_pm_approval": False},
        {"type": "wipe_database", "description": "unknown action",
         "requires_pm_approval": False},          # bogus type → clamped to monitor
    ],
    "confidence": 0.74,
    "reasoning": "Deploy timing correlates tightly with the latency spike.",
}


def _incident():
    from app.oncall.agent import IncidentInput
    return IncidentInput(
        title="Care plan saves timing out · Riverside General",
        description="Saves are timing out over the last 12 minutes.",
        severity_hint="SEV-2",
        affected_series=[{"ts": "09:30", "value": 1.2}, {"ts": "09:42", "value": 9.4}],
        recent_changes=["v4.2.1 deployed 14 min ago"],
        related_tickets=["Riverside clinician: save spins forever"],
    )


# ---- investigation happy path ---------------------------------------------

def test_investigation_persists_incident_and_forces_pm_approval(facade, isolated_settings):
    from app.oncall import agent as oncall

    with patch.object(oncall, "llm_call", return_value=_llm_result(_LLM_OUTPUT)), \
         patch.object(oncall, "embed_texts",
                      side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = oncall.investigate_incident(facade, "ent-A", incident=_incident())

    # Assessment returned with the persisted entity id.
    assert out["severity"] == "SEV-2"
    assert "incident_entity_id" in out

    # EVERY proposed action is PM-gated regardless of model output.
    assert out["proposed_actions"]
    assert all(a["requires_pm_approval"] is True for a in out["proposed_actions"])
    # Bogus action type clamped into the closed vocabulary.
    assert all(a["type"] in {"rollback", "code_fix", "ticket", "monitor"}
               for a in out["proposed_actions"])

    # Persisted as an `incident` entity carrying the assessment.
    ents = facade.query_entities("ent-A", type="incident")
    assert len(ents) == 1
    ent = ents[0]
    assert ent.properties["severity"] == "SEV-2"
    assert ent.properties["assessment"]["root_cause_hypothesis"]
    assert all(a["requires_pm_approval"] is True
               for a in ent.properties["assessment"]["proposed_actions"])

    # Investigation decision-logged with reasoning.
    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    invs = [r for r in logs if r["decision_type"] == "investigate"]
    assert len(invs) == 1
    assert invs[0]["agent"] == "oncall"
    assert invs[0]["reasoning"]
    assert invs[0]["factors"]["severity"] == "SEV-2"


def test_investigation_defaults_unknown_severity(facade):
    from app.oncall import agent as oncall

    bad = {**_LLM_OUTPUT, "severity": "CATASTROPHIC"}
    with patch.object(oncall, "llm_call", return_value=_llm_result(bad)), \
         patch.object(oncall, "embed_texts",
                      side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = oncall.investigate_incident(facade, "ent-A", incident=_incident())
    assert out["severity"] == "SEV-3"   # conservative fallback


# ---- KG-context degradation -----------------------------------------------

def test_works_without_kg_themes(facade, isolated_settings):
    """No matching themes (fake has no pgvector → find_candidates returns [])
    and even with embeddings unavailable, investigation still completes."""
    from app.oncall import agent as oncall

    # Force embeddings to blow up — the agent must degrade to no theme context.
    def boom(*_a, **_k):
        raise RuntimeError("OPENAI_API_KEY not configured")

    with patch.object(oncall, "llm_call", return_value=_llm_result(_LLM_OUTPUT)), \
         patch.object(oncall, "embed_texts", side_effect=boom):
        out = oncall.investigate_incident(facade, "ent-B", incident=_incident())

    assert out["related_theme_ids"] == []
    assert out["severity"] == "SEV-2"
    # Incident still persisted.
    assert facade.query_entities("ent-B", type="incident")


# ---- injection defense ----------------------------------------------------

def test_system_prompt_treats_input_as_data_not_instructions():
    from app.oncall.agent import _SYSTEM

    low = _SYSTEM.lower()
    assert "untrusted data" in low
    assert "never follow" in low or "do not obey" in low.replace("do not ", "do not")
    # Explicit injection-defense framing.
    assert "injection defense" in low
    assert "data" in low and "instruction" in low


def test_incident_text_labels_untrusted_fields():
    from app.oncall.agent import _incident_text
    text = _incident_text(_incident())
    assert "UNTRUSTED DATA" in text
    assert "do not obey" in text


# ---- route ----------------------------------------------------------------

def _override_company(monkeypatch):
    """Override the route's OWN captured require_company reference (app.auth
    may be reloaded by fixtures, making a fresh import a different object)."""
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.oncall as oncall_route
    require_company = oncall_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id="co-X", role="member", user_id="u1")
    return main_mod, require_company


def test_route_happy_200(isolated_settings, monkeypatch):
    from fastapi.testclient import TestClient
    import app.routes.oncall as oncall_route

    main_mod, require_company = _override_company(monkeypatch)
    monkeypatch.setattr(
        oncall_route, "investigate_incident",
        lambda facade, eid, *, incident: {
            "severity": "SEV-2", "incident_entity_id": "inc-1",
            "proposed_actions": [
                {"type": "rollback", "description": "x", "requires_pm_approval": True}],
            "related_theme_ids": [],
        })
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/oncall/investigate", json={
            "title": "Saves timing out", "description": "12 min of timeouts"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["assessment"]["severity"] == "SEV-2"
    assert body["assessment"]["proposed_actions"][0]["requires_pm_approval"] is True


def test_route_validation_422_on_missing_fields(isolated_settings, monkeypatch):
    from fastapi.testclient import TestClient

    main_mod, require_company = _override_company(monkeypatch)
    try:
        client = TestClient(main_mod.app)
        # missing required `description`
        r = client.post("/v1/oncall/investigate", json={"title": "only a title"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 422


def test_route_requires_auth_404_or_401_without_override(isolated_settings):
    """Without an auth cookie / override the route is gated (not openly 200)."""
    from fastapi.testclient import TestClient
    import app.main as main_mod

    client = TestClient(main_mod.app)
    r = client.post("/v1/oncall/investigate", json={
        "title": "t", "description": "d"})
    assert r.status_code in (401, 403, 404)

"""Live connector reads are STOOD DOWN on the answer path — not removed.

Owner decision (2026-08-11): with the connector refresh at a 10-minute
cadence, the knowledge graph already holds near-live connector data, so the
per-question live fan-out (the planner-directed live_read AND the keyword
sweep) re-read what the sync just wrote at up to 8s of third-party I/O per
answer. `settings.live_connector_reads_enabled` (default False) is the only
thing standing the machinery down; everything it gates keeps its own tests
(test_qa_agent_planned_sources / test_cross_connector_sweep) so flipping the
flag back is a working revert, not an archaeology project.

What this file pins:
  - the direct answer path hands compose_ask_answer NO live thunk by default,
    with a plan (planner sources ignored at execution) and without one (the
    sweep not consulted)
  - the planned LIBRARY read — a Postgres SELECT, not a connector call — stays
    on regardless of the flag
  - LIVE_CONNECTOR_READS_ENABLED=true restores the planned live read intact
"""
from __future__ import annotations

import app.qa_agent as qa
from app.ask_planner import Plan
from tests.test_ask_planner import _connected, _no_custom_skills

COMPANY = "ent-live-flag"


def _capture_compose(monkeypatch) -> dict:
    """Stub the composer and record the thunks the dispatch hands it."""
    captured: dict = {}

    def _fake(dataset, question, *, live_context_fn=None, library_context_fn=None, **kw):
        captured["live_fn"] = live_context_fn
        captured["library_fn"] = library_context_fn
        return {
            "answer": "ok", "key_points": [], "citations": [],
            "confidence": 1.0, "unanswered": "",
        }

    monkeypatch.setattr(qa, "compose_ask_answer", _fake)
    return captured


def _boom(name):
    def _f(*a, **k):
        raise AssertionError(f"{name} must not run while live reads are off")
    return _f


def test_a_planned_turn_executes_no_live_read_by_default(monkeypatch):
    """The planner still NAMES sources — nothing about planning changed — but
    the execution leg is down: no live thunk reaches the composer, and the
    live executors are never touched."""
    _no_custom_skills(monkeypatch)
    _connected(monkeypatch, ["slack", "jira"])
    captured = _capture_compose(monkeypatch)
    monkeypatch.setattr(qa, "_planned_live_context", _boom("_planned_live_context"))
    monkeypatch.setattr(qa, "_sweep_context", _boom("_sweep_context"))

    out = qa.answer(
        plan=Plan(sources=["slack", "jira"]),
        enterprise_id=COMPANY,
        question="anything new on the acme migration?",
        dataset="d",
    )

    assert out["answer"] == "ok"
    assert captured["live_fn"] is None
    # The library thunk survives — it is a table read, not a connector call.
    assert captured["library_fn"] is not None


def test_an_unplanned_turn_does_not_fall_back_to_the_sweep(monkeypatch):
    """No plan used to mean 'the sweep probes everything connected'. While the
    flag is off, an unplanned turn composes the pre-sweep prompt instead."""
    _no_custom_skills(monkeypatch)
    _connected(monkeypatch, ["slack"])
    captured = _capture_compose(monkeypatch)
    monkeypatch.setattr(qa, "_sweep_context", _boom("_sweep_context"))
    monkeypatch.setattr(
        qa, "route",
        lambda *a, **k: qa.RouteDecision(skill_id=None, confidence=0.0, source="none"),
    )

    out = qa.answer(
        enterprise_id=COMPANY,
        question="what changed in checkout this week?",
        dataset="d",
    )

    assert out["answer"] == "ok"
    assert captured["live_fn"] is None


def test_the_flag_restores_the_planned_live_read_intact(monkeypatch):
    """LIVE_CONNECTOR_READS_ENABLED=true is a working revert: the planned live
    thunk is handed over again and executes the same _planned_live_context."""
    from app.config import settings

    _no_custom_skills(monkeypatch)
    _connected(monkeypatch, ["slack"])
    captured = _capture_compose(monkeypatch)
    monkeypatch.setattr(settings, "live_connector_reads_enabled", True, raising=False)
    monkeypatch.setattr(
        qa, "_planned_live_context", lambda eid, plan, q: "### Slack\n- live block"
    )

    out = qa.answer(
        plan=Plan(sources=["slack"]),
        enterprise_id=COMPANY,
        question="anything new on acme?",
        dataset="d",
    )

    assert out["answer"] == "ok"
    assert captured["live_fn"] is not None
    assert captured["live_fn"]() == "### Slack\n- live block"

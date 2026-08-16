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
  - a planned turn still hands over a thunk, but in LOCAL-ONLY mode: the
    networked fan-out is down while the legs served from our own tables (the
    call index, synced PR rows) keep running — see below for why
  - an unplanned turn does not fall back to the keyword sweep, which has no
    local half to preserve
  - the planned LIBRARY read — a Postgres SELECT, not a connector call — stays
    on regardless of the flag
  - LIVE_CONNECTOR_READS_ENABLED=true restores the full live read intact

LOCAL LEGS ARE NOT LIVE READS (2026-08-15). Standing them down with the live
ones took the already-synced call index off the answer path, so "how many
customer calls did I have each week" was answered from the KG's ~3-day signal
horizon and rendered zeros for every earlier week, while `call_index` held 522
calls going back to 2023. `_LOCAL_LEGS` are Postgres SELECTs against tables
this same connector sync fills; the flag's stated cost is third-party I/O,
which they do not incur.
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


def test_a_planned_turn_reads_local_legs_but_not_the_network(monkeypatch):
    """The planner still NAMES sources — nothing about planning changed — and
    the thunk still runs, in LOCAL-ONLY mode: our own tables are read, the
    networked fan-out is not. Standing the local legs down too is what hid an
    indexed call history behind a 3-day KG horizon."""
    _no_custom_skills(monkeypatch)
    _connected(monkeypatch, ["slack", "jira"])
    captured = _capture_compose(monkeypatch)
    seen: dict = {}

    def _planned(eid, plan, q, *, local_only=False):
        seen["local_only"] = local_only
        return "### Recorded calls (indexed)\n- 2026-08-13 · Maverik"

    monkeypatch.setattr(qa, "_planned_live_context", _planned)
    monkeypatch.setattr(qa, "_sweep_context", _boom("_sweep_context"))

    out = qa.answer(
        plan=Plan(sources=["slack", "jira"]),
        enterprise_id=COMPANY,
        question="anything new on the acme migration?",
        dataset="d",
    )

    assert out["answer"] == "ok"
    assert captured["live_fn"] is not None
    assert "Maverik" in captured["live_fn"]()
    # ...and it ran with the network half switched off.
    assert seen["local_only"] is True
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
    seen: dict = {}

    def _planned(eid, plan, q, *, local_only=False):
        seen["local_only"] = local_only
        return "### Slack\n- live block"

    monkeypatch.setattr(qa, "_planned_live_context", _planned)

    out = qa.answer(
        plan=Plan(sources=["slack"]),
        enterprise_id=COMPANY,
        question="anything new on acme?",
        dataset="d",
    )

    assert out["answer"] == "ok"
    assert captured["live_fn"] is not None
    assert captured["live_fn"]() == "### Slack\n- live block"
    # The whole point of the flag: the network half is back on.
    assert seen["local_only"] is False

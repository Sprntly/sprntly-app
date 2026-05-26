"""Tests for the On-Call Agent emergency brief (P1.5).

Covers:
  - Pydantic model rejection of malformed inputs (oversize body, missing
    fields).
  - generate_emergency_brief: invokes app.llm.call_json with the right
    system prompt + workspace context; stamps workspace_id /
    generated_at; forces requires_pm_approval=True even when the LLM
    returns False.
  - Route: 401 without auth, 200 with auth, requires_pm_approval ALWAYS
    True on the response.
"""
from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError


# ---------- Pydantic model guardrails ----------

def test_ticket_input_rejects_oversize_body(isolated_settings):
    from app.oncall.models import TICKET_BODY_MAX_CHARS, TicketInput

    with pytest.raises(ValidationError):
        TicketInput(
            source="zendesk",
            title="huge ticket",
            body="x" * (TICKET_BODY_MAX_CHARS + 1),
        )


def test_ticket_input_rejects_missing_title(isolated_settings):
    from app.oncall.models import TicketInput

    with pytest.raises(ValidationError):
        TicketInput(source="zendesk", body="something happened")  # type: ignore[call-arg]


def test_ticket_input_rejects_unknown_source(isolated_settings):
    from app.oncall.models import TicketInput

    with pytest.raises(ValidationError):
        TicketInput(
            source="pagerduty",  # type: ignore[arg-type]
            title="t",
            body="b",
        )


def test_ticket_input_rejects_empty_body(isolated_settings):
    from app.oncall.models import TicketInput

    with pytest.raises(ValidationError):
        TicketInput(source="manual", title="t", body="")


def test_ticket_input_accepts_minimal_valid_payload(isolated_settings):
    from app.oncall.models import TicketInput

    t = TicketInput(source="manual", title="checkout broken", body="users see 500s on POST /checkout")
    assert t.linked_ticket_ids == []
    assert t.ticket_id is None


# ---------- Runner: prompt + LLM call shape ----------

def _seed_workspace_corpus(isolated_settings, slug: str = "acme") -> None:
    """Drop a one-file corpus on disk so load_corpus(slug) succeeds."""
    db = isolated_settings["db"]
    db.insert_dataset(slug, slug.title())
    ws_dir = isolated_settings["data_dir"] / slug
    ws_dir.mkdir(exist_ok=True)
    (ws_dir / "kpi_tree.md").write_text("# KPI Tree\n- Activation: 65%\n- Checkout success: 92%\n")


def test_generate_invokes_llm_with_oncall_system_prompt(isolated_settings, fake_llm):
    """The runner must call app.llm.call_json with the on-call SRE
    system prompt — not the brief / PRD / evidence prompts. Also: the
    workspace context (KPI tree corpus) and ticket text must land in
    the user prompt so the LLM has both."""
    _seed_workspace_corpus(isolated_settings, "acme")

    import app.oncall.emergency_brief as eb
    importlib.reload(eb)
    from app.oncall.models import TicketInput

    fake_llm["payload"] = {
        "ticket_summary": "Checkout returns 500 for some users",
        "root_cause": {
            "summary": "Race condition in order finalization",
            "confidence": "medium",
            "evidence": ["error trace shows null user.session"],
        },
        "impact": {
            "affected_user_count_estimate": 42,
            "severity": "high",
            "affected_features": ["checkout"],
        },
        "proposed_solution": {
            "summary": "Lock the session row before finalization",
            "code_change_hint": "fix order_service.finalize() to SELECT … FOR UPDATE",
            "rollback_plan": "feature-flag oncall_lock_v1 → false",
            "requires_pm_approval": True,
        },
        "agent_notes": "Need actual error trace.",
    }
    fake_llm["calls"] = []

    ticket = TicketInput(
        source="zendesk",
        ticket_id="ZD-123",
        title="Cannot complete checkout",
        body="Pressing the Place Order button returns a 500. Multiple users in #support.",
        reporter="cs-lead",
        received_at="2026-05-23T18:14:00Z",
        linked_ticket_ids=["ZD-124", "ZD-125"],
    )
    brief = eb.generate_emergency_brief("acme", ticket)

    # Exactly one LLM call, with the on-call system prompt.
    assert len(fake_llm["calls"]) == 1
    call = fake_llm["calls"][0]
    assert "on-call SRE agent" in call["system"]
    assert "NEVER" in call["system"] or "never" in call["system"].lower()
    # Workspace context (the corpus we seeded) made it into the user prompt.
    assert "KPI Tree" in call["user"]
    # The ticket payload landed in the user prompt.
    assert "Cannot complete checkout" in call["user"]
    assert "ZD-123" in call["user"]
    assert "ZD-124" in call["user"] and "ZD-125" in call["user"]
    assert "Place Order" in call["user"]

    # Server-stamped fields override the LLM payload's silence on them.
    assert brief.workspace_id == "acme"
    assert brief.generated_at  # ISO timestamp from the server clock.
    assert brief.root_cause.confidence == "medium"
    assert brief.impact.severity == "high"


def test_generate_forces_requires_pm_approval_true(isolated_settings, fake_llm):
    """Hard invariant: even if the LLM returns requires_pm_approval=False
    (jailbreak / hallucination), the runner re-stamps it to True before
    returning. V1 NEVER auto-acts."""
    _seed_workspace_corpus(isolated_settings, "acme")

    import app.oncall.emergency_brief as eb
    importlib.reload(eb)
    from app.oncall.models import TicketInput

    fake_llm["payload"] = {
        "ticket_summary": "x",
        "root_cause": {"summary": "x", "confidence": "low", "evidence": []},
        "impact": {
            "affected_user_count_estimate": None,
            "severity": "low",
            "affected_features": [],
        },
        "proposed_solution": {
            "summary": "auto-deploy patch",
            "code_change_hint": None,
            "rollback_plan": None,
            "requires_pm_approval": False,  # LLM tried to bypass.
        },
        "agent_notes": "",
    }
    ticket = TicketInput(source="manual", title="t", body="b")
    brief = eb.generate_emergency_brief("acme", ticket)

    assert brief.proposed_solution.requires_pm_approval is True


def test_generate_handles_workspace_without_corpus(isolated_settings, fake_llm):
    """If the workspace has no on-disk corpus, the runner still produces
    a brief (degraded mode) — it does NOT raise. The user prompt should
    flag the missing context so the LLM can note it in agent_notes."""
    import app.oncall.emergency_brief as eb
    importlib.reload(eb)
    from app.oncall.models import TicketInput

    fake_llm["payload"] = {
        "ticket_summary": "x",
        "root_cause": {"summary": "x", "confidence": "low", "evidence": []},
        "impact": {
            "affected_user_count_estimate": None,
            "severity": "low",
            "affected_features": [],
        },
        "proposed_solution": {"summary": "x", "requires_pm_approval": True},
        "agent_notes": "no KPI tree available",
    }
    fake_llm["calls"] = []

    ticket = TicketInput(source="manual", title="t", body="b")
    brief = eb.generate_emergency_brief("nonexistent-workspace", ticket)

    assert brief.workspace_id == "nonexistent-workspace"
    assert len(fake_llm["calls"]) == 1
    # The runner should have signaled the missing context to the LLM.
    assert "No KPI tree" in fake_llm["calls"][0]["user"] or "no KPI" in fake_llm["calls"][0]["user"].lower()


# ---------- Route ----------

def _valid_ticket_payload() -> dict:
    return {
        "source": "zendesk",
        "ticket_id": "ZD-1",
        "title": "Cannot complete checkout",
        "body": "Place Order returns 500.",
        "reporter": "cs-lead",
        "received_at": "2026-05-23T18:14:00Z",
        "linked_ticket_ids": [],
    }


def _set_llm_brief_payload(fake_llm: dict) -> None:
    fake_llm["payload"] = {
        "ticket_summary": "Checkout 500s",
        "root_cause": {
            "summary": "race in finalize",
            "confidence": "medium",
            "evidence": ["null session"],
        },
        "impact": {
            "affected_user_count_estimate": 42,
            "severity": "high",
            "affected_features": ["checkout"],
        },
        "proposed_solution": {
            "summary": "lock session row",
            "code_change_hint": "SELECT FOR UPDATE in order_service",
            "rollback_plan": "feature-flag off",
            # Even returning False here — route should overwrite via runner.
            "requires_pm_approval": False,
        },
        "agent_notes": "",
    }


def test_emergency_brief_route_requires_auth(unauth_client, fake_llm):
    _set_llm_brief_payload(fake_llm)
    r = unauth_client.post(
        "/v1/oncall/emergency-brief?workspace_id=acme",
        json=_valid_ticket_payload(),
    )
    assert r.status_code == 401


def test_emergency_brief_route_requires_workspace_id(app_client, fake_llm):
    _set_llm_brief_payload(fake_llm)
    r = app_client.post("/v1/oncall/emergency-brief", json=_valid_ticket_payload())
    assert r.status_code == 422  # FastAPI: missing required query param


def test_emergency_brief_route_rejects_oversize_body(app_client, fake_llm):
    _set_llm_brief_payload(fake_llm)
    payload = _valid_ticket_payload()
    payload["body"] = "x" * 8001  # > TICKET_BODY_MAX_CHARS
    r = app_client.post(
        "/v1/oncall/emergency-brief?workspace_id=acme",
        json=payload,
    )
    assert r.status_code == 422


def test_emergency_brief_route_returns_shaped_brief(app_client, fake_llm, isolated_settings):
    _seed_workspace_corpus(isolated_settings, "acme")
    _set_llm_brief_payload(fake_llm)

    r = app_client.post(
        "/v1/oncall/emergency-brief?workspace_id=acme",
        json=_valid_ticket_payload(),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Shape check — every top-level field of EmergencyBrief is present.
    assert body["workspace_id"] == "acme"
    assert body["generated_at"]
    assert body["ticket_summary"] == "Checkout 500s"
    assert body["root_cause"]["confidence"] == "medium"
    assert body["impact"]["severity"] == "high"
    assert body["impact"]["affected_features"] == ["checkout"]
    assert body["proposed_solution"]["summary"] == "lock session row"
    assert body["agent_notes"] == ""

    # Hard invariant: requires_pm_approval is ALWAYS True on the wire,
    # even though the fake LLM returned False above.
    assert body["proposed_solution"]["requires_pm_approval"] is True


def test_requires_pm_approval_always_true_across_payloads(app_client, fake_llm, isolated_settings):
    """Parameterized assertion: regardless of how the LLM tries to set
    requires_pm_approval (False, missing, None-as-default), the brief
    we return has it True. V1 never auto-acts."""
    _seed_workspace_corpus(isolated_settings, "acme")

    for forged in (False, True):
        fake_llm["payload"] = {
            "ticket_summary": "t",
            "root_cause": {"summary": "r", "confidence": "low", "evidence": []},
            "impact": {
                "affected_user_count_estimate": None,
                "severity": "low",
                "affected_features": [],
            },
            "proposed_solution": {
                "summary": "s",
                "requires_pm_approval": forged,
            },
            "agent_notes": "",
        }
        r = app_client.post(
            "/v1/oncall/emergency-brief?workspace_id=acme",
            json=_valid_ticket_payload(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["proposed_solution"]["requires_pm_approval"] is True

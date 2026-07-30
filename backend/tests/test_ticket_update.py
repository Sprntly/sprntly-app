"""Ticket-update skill — intent direction, target resolution, propose-only writes.

No network/LLM/DB: the ticket reader, the PRD reader and the tool loop are
patched in the app.ticket_update namespace.
"""
from __future__ import annotations

import app.ticket_update as tu
from app.skill_router import is_jira_lookup, is_ticket_update


# ── intent detection ─────────────────────────────────────────────────────────

def test_is_ticket_update_positive():
    for q in [
        "update the ticket details with the PRD",
        "update the ticket with the prd",
        "rewrite the ticket description from the PRD",
        "update PROJ-142 from the PRD",
        "sync the ticket with the spec",
        "refresh the story details based on the product requirements doc",
        "flesh out the ticket using the PRD",
        "align the issue with the requirements doc",
    ]:
        assert is_ticket_update(q), q


def test_is_ticket_update_direction_decides():
    """The same words, both ways round. Only the one whose TARGET is the ticket
    belongs here — the reverse edits the document and is the PRD-edit flow's."""
    assert is_ticket_update("update the ticket details with the PRD")
    for q in [
        "update the PRD with the ticket details",
        "update the PRD with what's in PROJ-142",
        "rewrite the spec from the ticket",
    ]:
        assert not is_ticket_update(q), q


def test_is_ticket_update_negative():
    for q in [
        # No source document — a plain field write, the Jira lookup's job.
        "update PROJ-1 to done",
        "move the ticket to in review",
        # Creation, not a rewrite of something that exists.
        "create tickets from the PRD",
        "generate tickets from the spec",
        # Reads.
        "what's in the PRD for onboarding?",
        "show me the ticket details",
        # No link word: two objects, not a rewrite.
        "update the ticket and the PRD",
        # No ticket anywhere.
        "update the roadmap with the PRD",
    ]:
        assert not is_ticket_update(q), q


def test_is_ticket_update_followup_needs_a_tracker_thread():
    """"update it with the PRD" names no ticket — only the thread can supply
    one, so it fires inside a tracker thread and nowhere else."""
    thread = [
        {"role": "user", "content": "get me the ticket about checkout"},
        {"role": "assistant", "content": "KAN-14 — Checkout rewrite (In Progress)"},
    ]
    assert is_ticket_update("update it with the PRD", thread)
    assert not is_ticket_update("update it with the PRD", [])
    assert not is_ticket_update("update it with the PRD")


def test_ticket_update_is_checked_before_the_jira_lookup():
    """The bug this path fixes: is_jira_lookup ALSO claims this sentence (a write
    verb on a PM noun), and the skill it routes to cannot read a PRD. Both
    matching is fine — qa_agent checks is_ticket_update first — but if the
    ordering is ever reversed this test says what breaks."""
    q = "update the ticket details with the PRD"
    assert is_ticket_update(q)
    assert is_jira_lookup(q)


# ── propose: validation + staging ────────────────────────────────────────────

_TICKET = {
    "id": "prd-42-abc123",
    "title": "Checkout rewrite",
    "description": "Old description",
    "acceptance_criteria": ["User can pay", "Errors are shown"],
    "status": "Backlog",
}


def _propose(inp, *, ticket=_TICKET, jira_session=None, proposal=None):
    proposal = proposal if proposal is not None else {}
    orig = tu._load_ticket
    tu._load_ticket = lambda cid, key: (ticket if key == _TICKET["id"] else None)
    try:
        text = tu._dispatch_propose(
            inp, company_id="acme", jira_session=jira_session, proposal=proposal
        )
    finally:
        tu._load_ticket = orig
    return text, proposal


def test_propose_stages_a_sprntly_rewrite():
    text, proposal = _propose({
        "target": "sprntly",
        "ticket_key": "prd-42-abc123",
        "description": "New description grounded in the PRD",
        "acceptance_criteria": ["User can pay with a saved card"],
    })
    assert "waiting for the user's confirmation" in text
    assert proposal["target"] == "sprntly"
    assert proposal["ticket_key"] == "prd-42-abc123"
    assert proposal["title"] == "Checkout rewrite"
    assert proposal["description"] == "New description grounded in the PRD"
    assert proposal["acceptance_criteria"] == ["User can pay with a saved card"]
    assert any("2 → 1 item(s)" in line for line in proposal["preview"])


def test_propose_carries_existing_criteria_forward_when_not_rewritten():
    """PUT /v1/tickets/{key}/description REPLACES the criteria with whatever it
    is sent, so "leave them alone" must be resolved to the ticket's CURRENT list
    here — an omitted list would reach the endpoint as [] and blank them."""
    _, proposal = _propose({
        "target": "sprntly",
        "ticket_key": "prd-42-abc123",
        "description": "New description only",
    })
    assert proposal["acceptance_criteria"] == ["User can pay", "Errors are shown"]
    assert any("unchanged (2 item(s))" in line for line in proposal["preview"])


def test_propose_rejects_an_unknown_ticket_without_staging():
    text, proposal = _propose({
        "target": "sprntly",
        "ticket_key": "prd-42-nope",
        "description": "New description",
    })
    assert "no Sprntly ticket found" in text
    # Nothing staged → no confirm card → no button that 404s on click.
    assert proposal == {}


def test_propose_rejects_bad_input_without_staging():
    for inp in [
        {"target": "elsewhere", "ticket_key": "prd-42-abc123", "description": "x"},
        {"target": "sprntly", "ticket_key": "", "description": "x"},
        {"target": "sprntly", "ticket_key": "prd-42-abc123", "description": "   "},
    ]:
        text, proposal = _propose(inp)
        assert text.startswith("(propose_ticket_update:"), inp
        assert proposal == {}, inp


def test_propose_jira_folds_criteria_into_the_description():
    """Jira has no separate acceptance-criteria field. Dropping them silently
    would lose content the user just approved, so they fold into the body."""
    class _Session:
        pass

    import app.connectors.jira_fetch as jf

    orig = jf.get_issue
    jf.get_issue = lambda s, k: {"summary": "Checkout", "description": "Old"}
    try:
        _, proposal = _propose(
            {
                "target": "jira",
                "ticket_key": "PROJ-142",
                "description": "New body",
                "acceptance_criteria": ["Pays with saved card"],
            },
            jira_session=_Session(),
        )
    finally:
        jf.get_issue = orig

    assert proposal["target"] == "jira"
    assert "Acceptance criteria:" in proposal["description"]
    assert "- Pays with saved card" in proposal["description"]
    # Always null for Jira — the confirm card must not send a criteria write.
    assert proposal["acceptance_criteria"] is None


def test_propose_jira_without_a_connection_is_refused():
    text, proposal = _propose(
        {"target": "jira", "ticket_key": "PROJ-142", "description": "New body"},
        jira_session=None,
    )
    assert "Jira is not connected" in text
    assert proposal == {}


# ── answer: payload shape + the no-Jira-needed contract ──────────────────────

def _no_jira_session(monkeypatch):
    import app.connectors.jira_fetch as jf

    monkeypatch.setattr(jf, "open_session", lambda cid: None)


def test_answer_works_without_jira_when_a_prd_is_open(monkeypatch):
    """The reported failure mode's other half: jira_lookup bails outright when
    Jira isn't connected. A Sprntly ticket needs no connector, so this path must
    still run — with the Jira tools simply absent."""
    _no_jira_session(monkeypatch)
    seen: dict = {}

    def _loop(**kwargs):
        seen.update(kwargs)
        return "Proposed an update to prd-42-abc123."

    monkeypatch.setattr(tu, "run_tool_loop", _loop)
    monkeypatch.setattr(tu, "_log", lambda *a, **k: None)

    out = tu.answer(enterprise_id="acme", question="update the ticket with the PRD", prd_id=42)

    assert out["_skill_source"] == "ticket-update"
    tool_names = {t["name"] for t in seen["tools"]}
    assert "propose_ticket_update" in tool_names
    assert "get_prd" in tool_names
    assert not {"jira_search", "jira_get_issue"} & tool_names


def test_answer_offers_jira_tools_when_connected(monkeypatch):
    import app.connectors.jira_fetch as jf

    monkeypatch.setattr(jf, "open_session", lambda cid: object())
    seen: dict = {}

    def _loop(**kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(tu, "run_tool_loop", _loop)
    monkeypatch.setattr(tu, "_log", lambda *a, **k: None)

    tu.answer(enterprise_id="acme", question="update PROJ-1 from the PRD", prd_id=42)

    tool_names = {t["name"] for t in seen["tools"]}
    assert {"jira_search", "jira_get_issue"} <= tool_names


def test_answer_carries_the_proposal_out_on_the_payload(monkeypatch):
    _no_jira_session(monkeypatch)

    def _loop(**kwargs):
        # The real loop reaches the propose tool through dispatch; do the same.
        kwargs["dispatch"]("propose_ticket_update", {
            "target": "sprntly",
            "ticket_key": _TICKET["id"],
            "description": "New description",
        })
        return "I've proposed an update to prd-42-abc123 — confirm to apply it."

    monkeypatch.setattr(tu, "run_tool_loop", _loop)
    monkeypatch.setattr(tu, "_log", lambda *a, **k: None)
    monkeypatch.setattr(tu, "_load_ticket", lambda cid, key: _TICKET)

    out = tu.answer(enterprise_id="acme", question="update the ticket with the PRD", prd_id=42)

    change = out["_pending_ticket_change"]
    assert change["ticket_key"] == "prd-42-abc123"
    assert change["description"] == "New description"


def test_answer_without_a_prd_or_jira_asks_for_one(monkeypatch):
    _no_jira_session(monkeypatch)
    called = {"loop": False}
    monkeypatch.setattr(
        tu, "run_tool_loop", lambda **k: called.__setitem__("loop", True) or ""
    )

    out = tu.answer(enterprise_id="acme", question="update the ticket with the PRD")

    assert not called["loop"]  # no LLM spend on a request that cannot be served
    assert "_pending_ticket_change" not in out
    assert "open the PRD" in out["answer"]


def test_answer_never_raises_when_the_loop_fails(monkeypatch):
    _no_jira_session(monkeypatch)

    def _boom(**kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(tu, "run_tool_loop", _boom)

    out = tu.answer(enterprise_id="acme", question="update the ticket with the PRD", prd_id=42)

    assert "_pending_ticket_change" not in out
    assert out["answer"]

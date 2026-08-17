"""POST /v1/tickets/assign-plan + app.ticket_assign — chat ticket assignment.

The chat's `assign_tickets` action: "assign the auth ticket to Dave" resolves
against the PRD's generated tickets and the team roster, returning explicit
(ticket, member) pairs plus popup questions for everything the request left
open. PLAN ONLY — the writes go through PUT /v1/tickets/{key}/fields from the
client, so nothing here mutates.

Contract under test:
  - model proposes, Python disposes: an assignment or question naming a ticket
    key or user id that does not exist is DROPPED and surfaced in `note`,
    never passed to the client as something clickable
  - question options filter to the real roster/ticket list and BACKFILL to the
    full list rather than rendering an empty card
  - deleted tickets are not offered (same rule as ticket_update's target list)
  - no tickets / no members short-circuit with an honest note and NO LLM call
  - fail-open: gateway error → empty plan + note, never a 500 in the chat
  - route: tenant-scoped (a foreign prd_id reads as "no tickets"), 422 on a
    missing instruction

LLM work is mocked at the gateway seam (app.ticket_assign.llm_call).
"""
from __future__ import annotations

import uuid

from app import ticket_assign
from app.graph.gateway import LLMResult


def _llm_result(output) -> LLMResult:
    return LLMResult(
        output=output, model="m", prompt_version="ticket-assign-v1",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1, stop_reason="end_turn",
    )


def _seed_tickets(company_id: str, prd_id: int, stories: list[dict]) -> None:
    from app.db.client import require_client

    require_client().table("prd_tickets").insert({
        "company_id": company_id,
        "prd_id": prd_id,
        "content_hash": "h",
        "stories": stories,
        "status": "ready",
    }).execute()


def _seed_member(company_id: str, user_id: str, name: str, email: str) -> None:
    from app.db.client import require_client

    c = require_client()
    c.table("company_members").insert({
        "id": uuid.uuid4().hex,
        "company_id": company_id,
        "user_id": user_id,
        "role": "member",
    }).execute()
    c.table("profiles").insert({
        "id": user_id,
        "email": email,
        "full_name": name,
    }).execute()


def test_explicit_pair_rides_out_with_the_full_assignee(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow"},
        {"id": "s2", "title": "Settings page"},
    ])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    monkeypatch.setattr(ticket_assign, "llm_call", lambda **kw: _llm_result({
        "assignments": [{"ticket_key": "prd-7-s1", "user_id": "u-dave"}],
        "questions": [],
        "note": "",
    }))
    resp = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign the login ticket to Dave"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["questions"] == []
    assert len(body["assignments"]) == 1
    a = body["assignments"][0]
    assert a["ticket_key"] == "prd-7-s1"
    assert a["ticket_title"] == "Login flow"
    # The full TicketAssignee shape PUT /fields stores — the client writes this
    # verbatim, so every field the drawer's picker sends must be here.
    assert a["assignee"] == {
        "user_id": "u-dave", "display_name": "Dave Okafor",
        "email": "dave@acme.com", "role": "member", "avatar_url": None,
    }


def test_invented_ids_are_dropped_into_the_note(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [{"id": "s1", "title": "Login flow"}])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    monkeypatch.setattr(ticket_assign, "llm_call", lambda **kw: _llm_result({
        "assignments": [{"ticket_key": "prd-7-invented", "user_id": "u-dave"}],
        "questions": [{"prompt": "Who gets it?", "user_id": "u-nobody"}],
        "note": "",
    }))
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign things"},
    ).json()
    assert body["assignments"] == []
    assert body["questions"] == []
    assert "didn't match" in body["note"]


def test_question_options_filter_and_backfill(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow"},
        {"id": "s2", "title": "Settings page"},
    ])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    _seed_member(t.company_id, "u-priya", "Priya Nair", "priya@acme.com")
    monkeypatch.setattr(ticket_assign, "llm_call", lambda **kw: _llm_result({
        "assignments": [],
        "questions": [
            # Named options: one real, one invented — only the real one stays.
            {
                "header": "Assignee", "prompt": "Who should “Login flow” go to?",
                "ticket_key": "prd-7-s1",
                "option_user_ids": ["u-dave", "u-ghost"],
            },
            # No options at all → the full roster backfills, never an empty card.
            {
                "header": "Assignee", "prompt": "Who should “Settings page” go to?",
                "ticket_key": "prd-7-s2",
            },
        ],
        "note": "",
    }))
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign these to the team"},
    ).json()
    q1, q2 = body["questions"]
    assert q1["fixed"] == {"kind": "ticket", "ticket_key": "prd-7-s1", "ticket_title": "Login flow"}
    assert [o["value"] for o in q1["options"]] == ["u-dave"]
    # Member options carry the writable assignee record — one click, one PUT.
    assert q1["options"][0]["assignee"]["user_id"] == "u-dave"
    # The backfill is the WHOLE roster — the two seeded members plus the
    # company's own owner user from the tenant fixture.
    assert sorted(o["value"] for o in q2["options"]) == sorted(
        ["u-dave", "u-priya", t.user_id]
    )


def test_member_fixed_question_offers_tickets(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow"},
        {"id": "s2", "title": "Settings page"},
    ])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    monkeypatch.setattr(ticket_assign, "llm_call", lambda **kw: _llm_result({
        "assignments": [],
        "questions": [{
            "header": "Which ticket", "prompt": "Which ticket should Dave get?",
            "user_id": "u-dave",
            "option_ticket_keys": ["prd-7-s2"],
        }],
        "note": "",
    }))
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "give one to Dave"},
    ).json()
    (q,) = body["questions"]
    assert q["fixed"]["kind"] == "member"
    assert q["fixed"]["assignee"]["user_id"] == "u-dave"
    assert [o["value"] for o in q["options"]] == ["prd-7-s2"]
    assert q["options"][0]["label"] == "Settings page"


def test_a_several_tickets_ask_rides_out_as_one_multi_question(
    tenant_client, monkeypatch
):
    """"Assign 2 tickets to fortune" — reported with a screenshot: the popup
    listed the tickets but only one could be clicked. A person-fixed question
    the model marks `multi` keeps the flag, so the card renders as
    tick-several-confirm-once; a ticket-fixed question NEVER carries it (a
    ticket has exactly one assignee), whatever the model says."""
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow"},
        {"id": "s2", "title": "Settings page"},
    ])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    monkeypatch.setattr(ticket_assign, "llm_call", lambda **kw: _llm_result({
        "assignments": [],
        "questions": [
            {
                "header": "Which tickets", "prompt": "Which 2 tickets should Dave get?",
                "user_id": "u-dave",
                "option_ticket_keys": ["prd-7-s1", "prd-7-s2"],
                "multi": True,
            },
            {
                "header": "Assignee", "prompt": "Who should “Login flow” go to?",
                "ticket_key": "prd-7-s1",
                "option_user_ids": ["u-dave"],
                "multi": True,  # the model drifted — must not survive
            },
        ],
        "note": "",
    }))
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign 2 tickets to Dave"},
    ).json()
    member_q, ticket_q = body["questions"]
    assert member_q["fixed"]["kind"] == "member"
    assert member_q["multi"] is True
    assert [o["value"] for o in member_q["options"]] == ["prd-7-s1", "prd-7-s2"]
    assert ticket_q["fixed"]["kind"] == "ticket"
    assert "multi" not in ticket_q


def test_deleted_tickets_are_not_offered(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow"},
        {"id": "s2", "title": "Settings page"},
    ])
    from app.db.ticket_lifecycle import set_lifecycle

    set_lifecycle(t.company_id, "prd-7-s2", "deleted")
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"assignments": [], "questions": [], "note": ""})

    monkeypatch.setattr(ticket_assign, "llm_call", _capture)
    t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign these"},
    )
    assert "prd-7-s1" in seen["input"]
    assert "prd-7-s2" not in seen["input"]


def test_category_rule_rides_in_the_prompt(tenant_client, monkeypatch):
    """"assign all backend related tickets to myself" must be classified by
    the model, never bounced back as a select-which-ones-are-backend quiz
    (live session, 2026-08-14). The rule lives ONLY in `_SYSTEM`, so this
    guards its load-bearing phrases the way the deleted-tickets test guards
    the ticket list: a prompt rewrite that drops the category rule fails here
    before a user rediscovers the quiz."""
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [{"id": "s1", "title": "Login flow"}])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({
            "assignments": [{"ticket_key": "prd-7-s1", "user_id": "u-dave"}],
            "questions": [],
            "note": "“Login flow” is the only backend-ish ticket; the rest are process work.",
        })

    monkeypatch.setattr(ticket_assign, "llm_call", _capture)
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign all backend related tickets to Dave"},
    ).json()
    # The rule's three anchors: category selection is the model's call, the
    # verdict lands in `note`, and the user is never handed the sorting.
    assert "TOPIC or CATEGORY" in seen["system"]
    assert "Never ask the user to sort tickets into the category" in seen["system"]
    assert seen["prompt_version"] == ticket_assign.PLAN_PROMPT_VERSION == "ticket-assign-v2"
    # And a category verdict rides out as plain assignments plus the note.
    assert [a["ticket_key"] for a in body["assignments"]] == ["prd-7-s1"]
    assert body["questions"] == []
    assert "only backend-ish" in body["note"]


def test_the_requester_rides_the_prompt_so_me_resolves(tenant_client, monkeypatch):
    """"Assign the frontend tickets to me" answered "no session identity is
    provided" (screenshot-reported) — the plan call never knew who was asking.
    The authenticated caller — company owner or INVITED member alike, it is
    whoever's JWT made the request — now rides the input as an explicit
    REQUESTER line, and the ticket lines carry content (summary + area), not
    bare titles, so descriptive phrases have something to match against."""
    t = tenant_client.make(slug="acme", user_id="u-invited")
    _seed_tickets(t.company_id, 7, [
        {"id": "s1", "title": "Login flow", "what": "Retry the SPF check",
         "route": "backend", "labels": ["email"], "prd_section": "Delivery"},
    ])
    # The caller's PROFILE details (the tenant fixture already seeded their
    # membership + a bare profile — whoever's JWT it is, owner or invitee,
    # the roster read finds them; this just gives the row a name to assert).
    from app.db.client import require_client

    require_client().table("profiles").update({
        "email": "fortune@acme.com", "full_name": "Fortune Tede",
    }).eq("id", "u-invited").execute()
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"assignments": [], "questions": [], "note": ""})

    monkeypatch.setattr(ticket_assign, "llm_call", _capture)
    t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign the frontend tickets to me"},
    )
    # The requester block names the caller — id, name, email.
    assert "REQUESTER" in seen["input"]
    assert "u-invited — Fortune Tede — fortune@acme.com" in seen["input"]
    # The ticket line carries what the ticket IS, not just its title.
    assert "Retry the SPF check" in seen["input"]
    assert "backend, email, Delivery" in seen["input"]
    # And the rule the model follows lives in the system prompt.
    assert '"me" / "myself"' in seen["system"]


def test_no_tickets_short_circuits_without_an_llm_call(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    called = {"n": 0}

    def _boom(**kw):
        called["n"] += 1
        raise AssertionError("must not be called")

    monkeypatch.setattr(ticket_assign, "llm_call", _boom)
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 99, "instruction": "assign these to Dave"},
    ).json()
    assert called["n"] == 0
    assert body["assignments"] == [] and body["questions"] == []
    assert "no tickets" in body["note"].lower()


def test_foreign_prd_reads_as_no_tickets(tenant_client, monkeypatch):
    """Tenancy: get_tickets is company-scoped, so another company's prd_id
    yields the same answer as a nonexistent one — no existence leak."""
    other = tenant_client.make(slug="rival")
    _seed_tickets(other.company_id, 7, [{"id": "s1", "title": "Secret work"}])
    t = tenant_client.make(slug="acme", user_id="acme-user")
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")
    monkeypatch.setattr(
        ticket_assign, "llm_call",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    body = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign these to Dave"},
    ).json()
    assert body["assignments"] == [] and body["questions"] == []
    assert "no tickets" in body["note"].lower()


def test_fails_open_on_gateway_error(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_tickets(t.company_id, 7, [{"id": "s1", "title": "Login flow"}])
    _seed_member(t.company_id, "u-dave", "Dave Okafor", "dave@acme.com")

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(ticket_assign, "llm_call", _boom)
    resp = t.client.post(
        "/v1/tickets/assign-plan",
        json={"prd_id": 7, "instruction": "assign the login ticket to Dave"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignments"] == [] and body["questions"] == []
    assert body["note"]


def test_missing_instruction_is_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = t.client.post("/v1/tickets/assign-plan", json={"prd_id": 7, "instruction": ""})
    assert resp.status_code == 422

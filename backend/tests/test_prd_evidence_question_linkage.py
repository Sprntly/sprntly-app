"""Originating-question/conversation linkage for PRDs and Evidence docs.

Mirrors db/reports.py's `question`/`ask_id` precedent (additive, nullable, no
backfill) plus the Evidence half of the conversation<->artifact binding that
already existed for PRDs (`conversations.prd_id` /
db.conversations.bind_conversation_to_prd) — extended here to
`conversations.evidence_id` / bind_conversation_to_evidence rather than adding
a redundant `evidences.conversation_id` column.

Covered:
- generate-from-task stamps `question` on the PRD row.
- generate_task_evidence stamps `question` on the Evidence row AND binds the
  commanding conversation via conversations.evidence_id (mirrors the PRD's
  bind_conversation_to_prd, same tenancy rules).
- GET /v1/conversations/by-evidence/{evidence_id}: happy path, empty (not
  404) when the caller has no bound conversation, tenant-scoped.
- Old-style rows (question/ask_id NULL, no bound conversation) still render
  via GET /v1/prd/{id} and GET /v1/evidence/{id} without error.
"""
from __future__ import annotations

import asyncio

from app.db.client import require_client


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _new_conversation(company_id, user_id):
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "generate prd",
        "query": "generate a PRD for dark mode on mobile",
        "agent_type": "ask",
    }
    resp = require_client().table("conversations").insert(row).execute()
    return resp.data[0]["id"]


def _conversation(conv_id):
    return (
        require_client().table("conversations").select("*")
        .eq("id", conv_id).execute().data[0]
    )


def _prd_row(prd_id):
    return require_client().table("prds").select("*").eq("id", prd_id).execute().data[0]


def _evidence_row(evidence_id):
    return require_client().table("evidences").select("*").eq("id", evidence_id).execute().data[0]


# ── PRD: question stamped at generation time ─────────────────────────────────

def test_generate_from_task_stamps_the_originating_question(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile"},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]
    assert _prd_row(prd_id)["question"] == "dark mode on mobile"


def test_get_prd_route_surfaces_the_question(tenant_client, isolated_settings):
    """The GET route's `select("*")` carries `question` through with no route
    change needed — pinned here so a future column rename/removal is caught."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    prd_id = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    ).json()["prd_id"]

    row = t.client.get(f"/v1/prd/{prd_id}").json()
    assert row["question"] == "dark mode on mobile"


def test_a_brief_insight_prd_has_no_question(tenant_client, isolated_settings):
    """Only the chat-task path has a genuine originating question — a brief
    insight PRD (no chat command behind it) leaves it NULL, not invented."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1,
        variant="v3",
    )
    assert _prd_row(prd_id)["question"] is None
    # Old-style row: renders fine via the route, question simply absent/None.
    row = t.client.get(f"/v1/prd/{prd_id}").json()
    assert row.get("question") is None


# ── Evidence: question + conversation binding at generation time ────────────

def test_generate_task_evidence_stamps_question_and_binds_conversation(
    tenant_client, isolated_settings, monkeypatch
):
    import app.graph.retrieval as retrieval
    from app import evidence_kg
    from app.graph.gateway import LLMResult

    t = tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    trail = {
        "insight": {"title": "dark mode"},
        "theme_id": None,
        "hypothesis": None,
        "signals": [{"signal_id": "s1", "content": "users ask for dark mode",
                     "kind": "feedback", "source_type": "zendesk",
                     "provenance": {"source": "ticket-1"}, "confidence": 0.9,
                     "rank": 1.0}],
        "kg_refs": ["s1"],
        "empty": False,
    }
    monkeypatch.setattr(retrieval, "task_evidence_trail", lambda f, e, tx: trail)
    monkeypatch.setattr(evidence_kg, "llm_call", lambda **kwargs: LLMResult(
        output="<html><style></style><body>evidence</body></html>",
        model="claude-sonnet-4-6", prompt_version="x+evidence-brief@abc123",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
        stop_reason="end_turn",
    ))

    insight = {"title": "Dark mode", "summary": "s", "query": "dark mode on mobile"}
    _run(evidence_kg.generate_task_evidence(
        brief_id, insight, "chat:abc123",
        question="dark mode on mobile",
        conversation_id=conv_id,
        company_id=t.company_id,
        user_id=t.user_id,
    ))

    rows = (
        require_client().table("evidences").select("*")
        .eq("brief_id", brief_id).execute().data
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["question"] == "dark mode on mobile"
    assert row["status"] == "ready"

    # Bound by the CALL, before generation finished — mirrors the PRD's
    # bind_conversation_to_prd, called eagerly in routes/prd.py.
    assert _conversation(conv_id)["evidence_id"] == row["id"]


def test_generate_task_evidence_without_conversation_id_is_unchanged(
    tenant_client, isolated_settings, monkeypatch
):
    """conversation_id is optional — omitting it must not break generation or
    stamp any binding."""
    import app.graph.retrieval as retrieval
    from app import evidence_kg
    from app.graph.gateway import LLMResult

    t = tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")

    trail = {
        "insight": {"title": "dark mode"}, "theme_id": None, "hypothesis": None,
        "signals": [{"signal_id": "s1", "content": "x", "kind": "feedback",
                     "source_type": "zendesk", "provenance": {}, "confidence": 0.9,
                     "rank": 1.0}],
        "kg_refs": ["s1"], "empty": False,
    }
    monkeypatch.setattr(retrieval, "task_evidence_trail", lambda f, e, tx: trail)
    monkeypatch.setattr(evidence_kg, "llm_call", lambda **kwargs: LLMResult(
        output="<html><style></style><body>evidence</body></html>",
        model="claude-sonnet-4-6", prompt_version="x+evidence-brief@abc123",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
        stop_reason="end_turn",
    ))

    insight = {"title": "Dark mode", "summary": "s", "query": "dark mode"}
    _run(evidence_kg.generate_task_evidence(brief_id, insight, "chat:xyz"))

    row = require_client().table("evidences").select("*").eq("brief_id", brief_id).execute().data[0]
    assert row["question"] is None
    assert row["status"] == "ready"


def test_evidence_binding_ignores_a_conversation_owned_by_another_user(
    tenant_client, isolated_settings, monkeypatch
):
    """Same tenancy posture as bind_conversation_to_prd: a conversation id
    belonging to someone else is silently ignored, never bound."""
    import app.graph.retrieval as retrieval
    from app import evidence_kg
    from app.graph.gateway import LLMResult

    owner = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="acme", user_id="user-someone-else")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")
    victim = _new_conversation(owner.company_id, owner.user_id)

    monkeypatch.setattr(retrieval, "task_evidence_trail", lambda f, e, tx: {
        "insight": {"title": "x"}, "theme_id": None, "hypothesis": None,
        "signals": [{"signal_id": "s1", "content": "x", "kind": "feedback",
                     "source_type": "zendesk", "provenance": {}, "confidence": 0.9,
                     "rank": 1.0}],
        "kg_refs": ["s1"], "empty": False,
    })
    monkeypatch.setattr(evidence_kg, "llm_call", lambda **kwargs: LLMResult(
        output="<html><style></style><body>evidence</body></html>",
        model="claude-sonnet-4-6", prompt_version="x+evidence-brief@abc123",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
        stop_reason="end_turn",
    ))

    insight = {"title": "x", "summary": "s", "query": "x"}
    _run(evidence_kg.generate_task_evidence(
        brief_id, insight, "chat:victim",
        conversation_id=victim, company_id=other.company_id, user_id=other.user_id,
    ))
    assert _conversation(victim)["evidence_id"] is None


def test_an_old_style_evidence_row_renders_without_the_context_block(
    tenant_client, isolated_settings
):
    """A row generated before this shipped (NULL question, no bound
    conversation) renders fine — graceful, not broken."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1,
        variant="v3",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="<html></html>")

    assert _evidence_row(evidence_id)["question"] is None
    resp = t.client.get(f"/v1/evidence/{evidence_id}")
    assert resp.status_code == 200
    assert resp.json().get("question") is None


# ── GET /v1/conversations/by-evidence/{evidence_id} ──────────────────────────

def test_by_evidence_returns_conversation_with_turns(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1,
        variant="v3", question="dark mode on mobile",
    )
    conv_id = _new_conversation(t.company_id, t.user_id)
    require_client().table("conversations").update(
        {"evidence_id": evidence_id}
    ).eq("id", conv_id).execute()
    t.client.post(f"/v1/conversations/{conv_id}/turns",
                  json={"role": "user", "content": "dark mode on mobile"})

    resp = t.client.get(f"/v1/conversations/by-evidence/{evidence_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conversation"]["id"] == conv_id
    assert [turn["content"] for turn in data["turns"]] == ["dark mode on mobile"]


def test_by_evidence_empty_when_no_conversation(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/conversations/by-evidence/999")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"conversation": None, "turns": []}


def test_by_evidence_is_tenant_scoped(tenant_client, isolated_settings):
    a = tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="company-a")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1,
        variant="v3",
    )
    conv_id = _new_conversation(a.company_id, a.user_id)
    require_client().table("conversations").update(
        {"evidence_id": evidence_id}
    ).eq("id", conv_id).execute()

    b = tenant_client.make(slug="company-b")
    resp = b.client.get(f"/v1/conversations/by-evidence/{evidence_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation"] is None

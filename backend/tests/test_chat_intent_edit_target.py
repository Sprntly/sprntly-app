"""An edit can find the report to act on — from the panel, or from the thread.

Reported: "the system does not have a good understanding of what a report is,
and it cannot edit a report based on a prompt. Tried making reference to a
report several times and it did not get it." The reference was never the
problem. Two things were.

FIRST, THE PLANNER WAS NEVER TOLD. `plan_for_answer` has always accepted
`open_artifact`, and `chat_intent` never passed it — so the prompt was
rendered with no "Active tab: report #45 … is open beside this chat" line,
ever. `edit_artifact`'s own rule says "Choose this only when that line names a
report or a document", which made the action unreachable by construction:
every "add a risks section to that report" planned as `answer` and came back
as the rewritten section printed into the chat, report untouched.

SECOND, THE TARGET CAME ONLY FROM THE PANEL. `chat_intent` downgrades
`edit_artifact` to `answer` when it has no target, and the only source of one
was `_resolve_open_artifact` — the side panel. With the panel closed, or
showing a list with nothing picked, there was nothing to act on even once the
planner could see it. A report generated in this conversation belongs to it
whether or not the panel happens to be showing it.

Pinned here on the ROUTE, which is where both resolvers live.
"""
from __future__ import annotations

import app.routes.chat as chat_route
from app.db.client import require_client


def _conversation(company_id: str, user_id: str) -> int:
    return require_client().table("conversations").insert({
        "company_id": company_id, "user_id": user_id,
        "title": "chat", "query": "chat", "agent_type": "ask",
    }).execute().data[0]["id"]


def _report(company_id: str, conversation_id: int, title: str, created: str) -> int:
    return require_client().table("reports").insert({
        "company_id": company_id, "conversation_id": conversation_id,
        "skill": "voice-of-customer-report", "title": title,
        "html": "<h1>Body</h1>", "question": "what are customers saying?",
        "created_at": created,
    }).execute().data[0]["id"]


def _capture(monkeypatch) -> dict:
    """Record what the route hands the resolver."""
    seen: dict = {}

    def _resolve(enterprise_id, message, history=None, *, prd_id=None,
                 prd_title=None, has_attachments=False, open_artifact=None,
                 thread_artifact=None):
        seen.update(open_artifact=open_artifact, thread_artifact=thread_artifact)
        return {
            "intent": "answer", "confidence": 0.9, "task": None,
            "instruction": None, "reason": "stub", "source": "llm",
        }

    monkeypatch.setattr(chat_route, "resolve_chat_intent", _resolve)
    return seen


def test_a_report_this_thread_made_is_an_edit_target(tenant_client, monkeypatch):
    """The panel is showing nothing; the thread's own report is the referent."""
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.company_id, t.user_id)
    rid = _report(t.company_id, conv, "Voice of Customer · August",
                  "2026-08-25T10:00:00Z")
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={
        "message": "add a risks section to that report", "conversation_id": conv,
    })

    assert seen["open_artifact"] is None
    assert seen["thread_artifact"] == {
        "kind": "report", "id": rid, "title": "Voice of Customer · August",
        # The prompt must not claim a closed panel is open — this is what makes
        # the planner's line say "was produced in this conversation" instead.
        "origin": "thread",
    }


def test_the_panel_wins_when_it_is_showing_one(tenant_client, monkeypatch):
    """The thread fallback is a fallback. What the reader has open is the
    stronger statement of what they mean, and it is not second-guessed."""
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.company_id, t.user_id)
    older = _report(t.company_id, conv, "The one on screen", "2026-08-01T00:00:00Z")
    _report(t.company_id, conv, "Newer report", "2026-08-25T00:00:00Z")
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={
        "message": "rewrite the summary for an exec",
        "conversation_id": conv,
        "open_artifact": {"kind": "report", "id": older},
    })

    assert seen["open_artifact"]["id"] == older
    # Not resolved at all when the panel answered — one referent, not two.
    assert seen["thread_artifact"] is None


def test_the_newest_wins_among_several(tenant_client, monkeypatch):
    """Same resolution `project_prd_edit_target` uses for PRDs: with more than
    one, the newest is what a follow-up means."""
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.company_id, t.user_id)
    _report(t.company_id, conv, "Older", "2026-08-01T00:00:00Z")
    newest = _report(t.company_id, conv, "Newest", "2026-08-25T00:00:00Z")
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={
        "message": "cut the appendix from that report", "conversation_id": conv,
    })

    assert seen["thread_artifact"]["id"] == newest


def test_another_conversation_is_never_the_target(tenant_client, monkeypatch):
    """The thread is the boundary here exactly as it is for grounding: a report
    from the chat next door is not what 'that report' means in this one."""
    t = tenant_client.make(slug="acme")
    mine = _conversation(t.company_id, t.user_id)
    theirs = _conversation(t.company_id, t.user_id)
    _report(t.company_id, theirs, "Their report", "2026-08-25T00:00:00Z")
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={
        "message": "add a risks section to that report", "conversation_id": mine,
    })

    assert seen["thread_artifact"] is None


def test_a_thread_with_no_documents_has_no_target(tenant_client, monkeypatch):
    """No referent is the honest answer, and `answer` is the recoverable
    landing — it can ask which document they mean."""
    t = tenant_client.make(slug="acme")
    conv = _conversation(t.company_id, t.user_id)
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={
        "message": "add a risks section to that report", "conversation_id": conv,
    })

    assert seen["thread_artifact"] is None


def test_no_conversation_resolves_nothing(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    seen = _capture(monkeypatch)

    t.client.post("/v1/chat/intent", json={"message": "edit that report"})

    assert seen["thread_artifact"] is None


def test_a_foreign_tenants_report_is_not_reachable(tenant_client, monkeypatch):
    """Both reads are company-scoped, so a conversation id that resolves for
    someone else returns nothing here rather than their document."""
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="initech")
    theirs = _conversation(b.company_id, b.user_id)
    _report(b.company_id, theirs, "Initech confidential", "2026-08-25T00:00:00Z")
    seen = _capture(monkeypatch)

    # `a` names `b`'s conversation id outright.
    a.client.post("/v1/chat/intent", json={
        "message": "edit that report", "conversation_id": theirs,
    })

    assert seen.get("thread_artifact") is None

"""Realtime fan-out on the SHARED turn-write route (`POST /v1/conversations/
{id}/turns`, `routes/conversations.py::add_turn`) — the fast-follow closing
the plain-Q&A gap: this route is what BOTH main chat and the individual
project chat's own plain-ask flow persist through client-side
(`web/.../projects/useProjectConversation.ts`'s `chatPersistence`).

A `turn.created` broadcast fires ONLY when the written conversation is an
INDIVIDUAL PROJECT chat (`kind == "individual" and project_id is not None`).
The regression guard (`test_main_chat_turn_never_publishes`) is the
load-bearing safety test for touching this shared/hot route: main chat (and
any other non-project conversation) must stay byte-identical — no publish,
no added query, no behavior change.
"""
from __future__ import annotations

import app.routes.conversations as conversations_route
from app.db.workspaces import ensure_default_workspace


def _seed_project(t, *, name: str = "Realtime add_turn project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    return project["id"]


def _individual_conversation_id(project_id: int, user_id: str) -> int:
    from app.db import conversations as conversations_db

    return conversations_db.create_individual_project_chat(project_id, user_id)["id"]


def test_add_turn_publishes_turn_created_for_individual_project_conversation(tenant_client, monkeypatch):
    """The owner uid is the conversation's OWN `user_id` — never a
    keyword-argument mismatch or the acting request identity (there's only
    one identity here, but the assertion pins the SOURCE the topic reads
    from: `conversation["user_id"]`, not a request-scoped variable)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        conversations_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    resp = t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "user", "content": "what's the status of the export review?"},
    )
    assert resp.status_code == 200, resp.text

    events = [p for p in published if p[1] == "turn.created"]
    assert len(events) == 1, published
    topic, _event, payload = events[0]
    assert topic == f"project:{project_id}:user:{t.user_id}"
    assert topic != f"project:{project_id}"  # never the group channel
    assert set(payload) == {"id", "role", "content", "created_at"}
    assert payload["role"] == "user"
    assert payload["content"] == "what's the status of the export review?"


def test_add_turn_publishes_for_the_assistant_side_too(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        conversations_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    resp = t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "assistant", "content": "Still on track for Friday."},
    )
    assert resp.status_code == 200, resp.text

    events = [p for p in published if p[1] == "turn.created"]
    assert len(events) == 1, published
    assert events[0][2]["role"] == "assistant"


def test_main_chat_turn_never_publishes(tenant_client, monkeypatch):
    """REGRESSION GUARD (load-bearing): a plain main-chat conversation
    (`project_id IS NULL`) must NEVER publish — `add_turn` is a shared/hot
    route, and this is the safety net proving main chat stays byte-identical."""
    t = tenant_client.make(slug="acme")

    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        conversations_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    conv = t.client.post("/v1/conversations", json={"title": "Main chat"}).json()
    resp = t.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "what happened last week?"},
    )
    assert resp.status_code == 200, resp.text

    assert published == [], "a non-project conversation must never publish turn.created"


def test_add_turn_publish_failure_does_not_break_the_write(tenant_client, monkeypatch):
    """Best-effort (AD-P22): a raising `publish_broadcast` must never fail or
    roll back the already-written turn — `publish_broadcast` itself never
    raises in production, but the gate's own wrapping is what's under test
    here."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    def _boom(topic, event, payload):
        raise RuntimeError("simulated realtime failure")

    monkeypatch.setattr(conversations_route, "publish_broadcast", _boom)

    resp = t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "user", "content": "still persisted despite the publish hiccup"},
    )
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]
    assert turns[0]["content"] == "still persisted despite the publish hiccup"


def test_add_turn_never_publishes_whitespace_only_content(tenant_client, monkeypatch):
    """Defense-in-depth: a whitespace-only body still passes `TurnIn`'s
    `min_length=1` validation (so the row is still written, unchanged) but
    must never be broadcast — mirrors the client's own
    `parseRealtimeTurnPayload` blank-content guard, closing the gap from the
    OTHER side so a stray blank write can never reach a live thread as a
    phantom bubble, however it was produced."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        conversations_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    resp = t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "assistant", "content": "   "},
    )
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]
    assert turns[0]["content"] == "   ", "the row is still persisted, unchanged"
    assert published == [], "a blank-content turn must never publish"


def test_non_project_individual_conversation_never_publishes(tenant_client, monkeypatch):
    """A `kind='individual'` row with NO `project_id` (an ordinary main-chat
    conversation — every main-chat row defaults to `kind='individual'` too)
    must not publish either: the gate requires BOTH `kind == 'individual'`
    AND `project_id is not None`."""
    t = tenant_client.make(slug="acme")

    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        conversations_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    conv = t.client.post("/v1/conversations", json={"title": "Ordinary chat"}).json()
    resp = t.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "no project attached"},
    )
    assert resp.status_code == 200, resp.text
    assert published == []

"""`add_turn`'s idempotent-upsert branch (`routes/conversations.py`) — closes
the project individual-chat double-write: a completed ask's assistant reply
can legitimately be persisted twice through this SAME route (a second
mount/tab resuming the same conversation-scoped ask — see
`useProjectConversation.ts`'s resume effect), and a same-`client_message_id`
retry must collapse to ONE row via the existing idempotent writer
(`db/conversations.py::post_owned_individual_assistant_turn`).

The regression guard (`test_main_chat_never_uses_the_upsert_branch`,
`test_no_client_message_id_inserts_as_before`) is load-bearing: `add_turn` is
the SHARED write path for main chat too, and this branch must engage ONLY for
`kind='individual' AND project_id IS NOT NULL AND role='assistant' AND
client_message_id present` — every other shape takes the ORIGINAL insert
path, unchanged.
"""
from __future__ import annotations

from app.db.workspaces import ensure_default_workspace


def _seed_project(t, *, name: str = "Idempotent add_turn project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    return project["id"]


def _individual_conversation_id(project_id: int, user_id: str) -> int:
    from app.db import conversations as conversations_db

    return conversations_db.create_individual_project_chat(project_id, user_id)["id"]


def test_same_client_message_id_collapses_to_one_row(tenant_client):
    """Two `add_turn` POSTs for the SAME assistant reply, same key — the
    double-submit this ticket closes."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    body = {
        "role": "assistant",
        "content": "I've reviewed the PRD — looks good to ship.",
        "client_message_id": "ask-2044-reply",
    }
    resp1 = t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)
    resp2 = t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text
    # The SAME row both times (upsert, not a second insert).
    assert resp1.json()["id"] == resp2.json()["id"]

    turns = t.client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]
    assistant_turns = [
        turn for turn in turns
        if turn["role"] == "assistant" and turn["content"] == body["content"]
    ]
    assert len(assistant_turns) == 1, turns


def test_a_genuinely_distinct_second_turn_is_not_collapsed(tenant_client):
    """A SECOND, real assistant turn with its OWN client_message_id must still
    insert — the upsert only collapses a REPEAT of the same key, never a
    legitimately distinct message."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "assistant", "content": "First answer.", "client_message_id": "ask-1-reply"},
    )
    t.client.post(
        f"/v1/conversations/{conv_id}/turns",
        json={"role": "assistant", "content": "Second, different answer.", "client_message_id": "ask-2-reply"},
    )

    turns = t.client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]
    contents = [turn["content"] for turn in turns if turn["role"] == "assistant"]
    assert contents == ["First answer.", "Second, different answer."]


def test_no_client_message_id_inserts_as_before(tenant_client):
    """REGRESSION GUARD: an assistant turn on an individual project
    conversation with NO `client_message_id` takes the ORIGINAL insert path —
    two such POSTs (the pre-fix shape) still insert TWO rows, byte-identical
    to before this ticket. The upsert branch never engages without a key."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    body = {"role": "assistant", "content": "Same content, no dedup key."}
    t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)
    t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)

    turns = t.client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]
    matching = [turn for turn in turns if turn["content"] == body["content"]]
    assert len(matching) == 2, "no client_message_id must insert unchanged, exactly as before"


def test_main_chat_never_uses_the_upsert_branch(tenant_client):
    """REGRESSION GUARD (load-bearing): main chat (a plain, non-project
    conversation) must insert as before even when a client_message_id IS
    supplied — the upsert branch is gated on `kind='individual' AND
    project_id IS NOT NULL`, which main chat never satisfies."""
    t = tenant_client.make(slug="acme")
    conv = t.client.post("/v1/conversations", json={"title": "Main chat"}).json()

    body = {"role": "assistant", "content": "A main-chat reply.", "client_message_id": "should-be-ignored"}
    resp1 = t.client.post(f"/v1/conversations/{conv['id']}/turns", json=body)
    resp2 = t.client.post(f"/v1/conversations/{conv['id']}/turns", json=body)
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text
    # Two separate inserts — the client_message_id is silently ignored outside
    # the individual-project-conversation gate.
    assert resp1.json()["id"] != resp2.json()["id"]

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    matching = [turn for turn in turns if turn["content"] == body["content"]]
    assert len(matching) == 2


def test_user_role_never_uses_the_upsert_branch(tenant_client):
    """REGRESSION GUARD: the upsert branch is `role == 'assistant'` only — a
    USER turn with a client_message_id (the pre-existing project-chat send
    identity, unrelated to this ticket) must keep inserting exactly as
    before, never routed through the assistant-only writer."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id = _individual_conversation_id(project_id, t.user_id)

    body = {"role": "user", "content": "what's next?", "client_message_id": "user-key-1"}
    resp1 = t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)
    resp2 = t.client.post(f"/v1/conversations/{conv_id}/turns", json=body)
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text
    assert resp1.json()["id"] != resp2.json()["id"]

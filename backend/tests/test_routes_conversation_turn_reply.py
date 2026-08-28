"""The structured reply on a turn — persistence round-trip.

`content` is a string, so everything an answer showed beyond prose was lost
the moment the turn was saved. The reported failure: "show me the PRDs I
created" rendered twelve clickable rows live and, reopened from Chat history,
rendered only the sentence announcing them — "click one to open it" above
empty space. `conversation_turns.reply` (jsonb, live since the group-chat
reply migration) is where the rows survive; this is the main chat's half of
that wiring.

Covered:
- an assistant turn's `reply` round-trips verbatim, `artifact_list` included
- a turn saved WITHOUT one keeps the legacy shape (null), so every row
  written before this restores exactly as it did
- `content` is untouched either way — it stays the fallback the restore path
  reads the answer text from
- a USER turn never stores one: there is no structured reply to a question
- an oversized payload is refused (422), not truncated into half a thread
"""
from __future__ import annotations


def _conv(client, title="Chat"):
    resp = client.post("/v1/conversations", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()


CARDS = [
    {
        "id": 3827, "type": "prd", "title": "Checkout margin fix",
        "created_at": "2026-08-24T16:12:00Z",
        "open": {"prd_id": 3827}, "source": {"conversation_id": 91},
    },
    {
        "id": 3828, "type": "prd", "title": "AI SOC collaboration",
        "created_at": "2026-08-23T09:00:00Z",
        "open": {"prd_id": 3828}, "source": {"conversation_id": 92},
    },
]


def test_assistant_reply_round_trips_with_its_cards(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "assistant",
        "content": "Here are your most recent PRDs — click one to open it with its chat.",
        "reply": {
            "answer": "Here are your most recent PRDs — click one to open it with its chat.",
            "citations": [],
            "artifact_list": CARDS,
        },
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert len(turns) == 1
    # The rows come back exactly as they went in — the restore path hands them
    # straight to the same card renderer the live turn used.
    assert turns[0]["reply"]["artifact_list"] == CARDS
    # …and the prose is still on `content`, which is what every row written
    # before this column existed restores from.
    assert turns[0]["content"].startswith("Here are your most recent PRDs")


def test_turn_without_a_reply_keeps_the_legacy_shape(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "assistant", "content": "Onboarding friction is the top theme."},
    )
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns[0]["reply"] is None
    assert turns[0]["content"] == "Onboarding friction is the top theme."


def test_a_user_turn_never_stores_a_structured_reply(tenant_client):
    """There is no structured reply to a question — a client sending one is
    describing a turn that does not exist, and the field is dropped rather
    than stored."""
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user",
        "content": "show me the prds that i created",
        "reply": {"answer": "not mine to write", "artifact_list": CARDS},
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns[0]["role"] == "user"
    assert turns[0]["reply"] is None


def test_an_oversized_reply_is_refused(tenant_client):
    """Rejected outright, never truncated: half a payload restores as half a
    thread, which is worse than restoring from `content`."""
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "assistant",
        "content": "here you go",
        "reply": {"answer": "x" * 70_000},
    })
    assert resp.status_code == 422, resp.text

    # And nothing was written — a refused payload must not leave a half-turn.
    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns == []

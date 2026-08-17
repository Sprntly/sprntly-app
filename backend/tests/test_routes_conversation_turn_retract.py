"""Retracting a conversation's last user turn — DELETE /v1/conversations/{id}/turns/{turn_id}.

This exists for exactly one client flow: editing a question that was stopped
before it answered. The user turn is persisted the moment a message is sent, so
re-sending an edited version has to take the original row back out; otherwise
the live thread shows one question and the same thread reopened from history
shows two, the second unanswered.

It is the ONLY endpoint that removes something from a conversation's record, so
the tests here are mostly about what it REFUSES:

- the last turn only, so history can never be punched full of holes and a user
  turn can never be orphaned from the assistant turn that answered it
- user turns only, so nobody can edit out what the product said
- the owner only, with the same 404-not-403 rule as every other route here
"""
from __future__ import annotations


def _create(client, *, title="chat"):
    resp = client.post("/v1/conversations", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _add(client, conv_id, role, content):
    resp = client.post(
        f"/v1/conversations/{conv_id}/turns", json={"role": role, "content": content}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _contents(client, conv_id):
    return [t["content"] for t in client.get(f"/v1/conversations/{conv_id}/turns").json()["turns"]]


def test_retracts_the_last_user_turn(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _add(a.client, conv["id"], "user", "first question")
    _add(a.client, conv["id"], "assistant", "first answer")
    stopped = _add(a.client, conv["id"], "user", "waht is the audit form")

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/{stopped['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert _contents(a.client, conv["id"]) == ["first question", "first answer"]


def test_rolls_the_list_preview_back_to_the_previous_user_turn(tenant_client):
    # The chat-history row must stop advertising a message that no longer
    # exists — `add_turn` set the preview from the turn being retracted.
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _add(a.client, conv["id"], "user", "first question")
    _add(a.client, conv["id"], "assistant", "first answer")
    stopped = _add(a.client, conv["id"], "user", "waht is the audit form")

    a.client.delete(f"/v1/conversations/{conv['id']}/turns/{stopped['id']}")
    listed = a.client.get("/v1/conversations").json()["conversations"]
    row = next(c for c in listed if c["id"] == conv["id"])
    assert row["preview"] == "first question"


def test_preview_empties_when_nothing_is_left(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    only = _add(a.client, conv["id"], "user", "the only question")

    a.client.delete(f"/v1/conversations/{conv['id']}/turns/{only['id']}")
    listed = a.client.get("/v1/conversations").json()["conversations"]
    row = next(c for c in listed if c["id"] == conv["id"])
    assert row["preview"] == ""
    assert _contents(a.client, conv["id"]) == []


def test_refuses_a_turn_that_is_not_the_last(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    first = _add(a.client, conv["id"], "user", "first question")
    _add(a.client, conv["id"], "assistant", "first answer")

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/{first['id']}")
    assert resp.status_code == 409, resp.text
    assert _contents(a.client, conv["id"]) == ["first question", "first answer"]


def test_refuses_an_assistant_turn(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _add(a.client, conv["id"], "user", "question")
    answer = _add(a.client, conv["id"], "assistant", "answer")

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/{answer['id']}")
    assert resp.status_code == 409, resp.text
    assert _contents(a.client, conv["id"]) == ["question", "answer"]


def test_unknown_turn_id_is_a_conflict_not_a_deletion(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _add(a.client, conv["id"], "user", "question")

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/999999")
    assert resp.status_code == 409, resp.text
    assert _contents(a.client, conv["id"]) == ["question"]


def test_a_teammate_gets_404_and_the_turn_survives(tenant_client):
    # Same 404-not-403 rule as the rest of this router: a foreign caller must
    # not be able to tell "exists but not yours" from "doesn't exist".
    a = tenant_client.make(slug="acme", user_id="user-a")
    b = tenant_client.make(slug="acme", user_id="user-b")
    assert a.company_id == b.company_id
    conv = _create(a.client)
    turn = _add(a.client, conv["id"], "user", "private question")

    resp = b.client.delete(f"/v1/conversations/{conv['id']}/turns/{turn['id']}")
    assert resp.status_code == 404, resp.text
    assert _contents(a.client, conv["id"]) == ["private question"]


def test_another_companys_conversation_is_404(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    other = tenant_client.make(slug="globex", user_id="user-z")
    assert a.company_id != other.company_id
    conv = _create(a.client)
    turn = _add(a.client, conv["id"], "user", "private question")

    resp = other.client.delete(f"/v1/conversations/{conv['id']}/turns/{turn['id']}")
    assert resp.status_code == 404, resp.text
    assert _contents(a.client, conv["id"]) == ["private question"]

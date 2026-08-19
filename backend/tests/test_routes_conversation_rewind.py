"""Rewinding a conversation — DELETE /v1/conversations/{id}/turns/{turn_id}.

Deletes that turn and every turn after it. Two client flows need it: editing a
past prompt, and retrying one. Both replace what came after the question being
re-asked, and the record has to follow the screen — otherwise the same
conversation reopened from history shows the old question, its old answer, AND
the new pair.

This is the only endpoint that removes anything from a conversation, so most of
what is worth testing is what it REFUSES:

- the anchor must be a USER turn, so an assistant turn can never be deleted
  while the question it answered survives
- it cuts a SUFFIX, never a hole, so whatever remains is a coherent prefix of
  the conversation that actually happened
- owner only, with the same 404-not-403 rule as every other route here
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


def _seed(client, conv_id):
    """A three-exchange conversation; returns the three user turns."""
    q1 = _add(client, conv_id, "user", "first question")
    _add(client, conv_id, "assistant", "first answer")
    q2 = _add(client, conv_id, "user", "second question")
    _add(client, conv_id, "assistant", "second answer")
    q3 = _add(client, conv_id, "user", "third question")
    return q1, q2, q3


def test_rewinds_to_a_past_user_turn_dropping_everything_after(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _q1, q2, _q3 = _seed(a.client, conv["id"])

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/{q2['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "removed": 3}
    # Everything from the second question on is gone; the first exchange stands.
    assert _contents(a.client, conv["id"]) == ["first question", "first answer"]


def test_rewinding_to_the_last_turn_removes_only_that_turn(tenant_client):
    # The degenerate case — retracting a question that was stopped before it
    # answered, which is the flow this endpoint was first written for.
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _add(a.client, conv["id"], "user", "first question")
    _add(a.client, conv["id"], "assistant", "first answer")
    stopped = _add(a.client, conv["id"], "user", "waht is the audit form")

    resp = a.client.delete(f"/v1/conversations/{conv['id']}/turns/{stopped['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    assert _contents(a.client, conv["id"]) == ["first question", "first answer"]


def test_every_surviving_question_keeps_its_answer(tenant_client):
    # The suffix-only property, stated as the thing it protects: a rewind can
    # never leave a user turn orphaned from the assistant turn that answered it.
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _q1, _q2, q3 = _seed(a.client, conv["id"])

    a.client.delete(f"/v1/conversations/{conv['id']}/turns/{q3['id']}")
    turns = a.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    roles = [t["role"] for t in turns]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_rolls_the_list_preview_back_to_the_last_surviving_question(tenant_client):
    # The chat-history row must stop advertising a message that no longer
    # exists — `add_turn` set the preview from a turn being rewound away.
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    _q1, q2, _q3 = _seed(a.client, conv["id"])

    a.client.delete(f"/v1/conversations/{conv['id']}/turns/{q2['id']}")
    listed = a.client.get("/v1/conversations").json()["conversations"]
    row = next(c for c in listed if c["id"] == conv["id"])
    assert row["preview"] == "first question"


def test_preview_empties_when_the_rewind_takes_everything(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client)
    first = _add(a.client, conv["id"], "user", "the only question")
    _add(a.client, conv["id"], "assistant", "the only answer")

    a.client.delete(f"/v1/conversations/{conv['id']}/turns/{first['id']}")
    listed = a.client.get("/v1/conversations").json()["conversations"]
    row = next(c for c in listed if c["id"] == conv["id"])
    assert row["preview"] == ""
    assert _contents(a.client, conv["id"]) == []


def test_refuses_to_rewind_to_an_assistant_turn(tenant_client):
    # You rewind to a QUESTION, never into the middle of an answer — that is
    # what stops the product's own words being edited out from under it.
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


def test_a_turn_from_another_conversation_is_a_conflict(tenant_client):
    # Same owner, wrong thread: the id exists but rewinding THIS conversation to
    # it is meaningless, and it must not touch either one.
    a = tenant_client.make(slug="acme", user_id="user-a")
    one = _create(a.client, title="one")
    two = _create(a.client, title="two")
    _add(a.client, one["id"], "user", "in one")
    elsewhere = _add(a.client, two["id"], "user", "in two")

    resp = a.client.delete(f"/v1/conversations/{one['id']}/turns/{elsewhere['id']}")
    assert resp.status_code == 409, resp.text
    assert _contents(a.client, one["id"]) == ["in one"]
    assert _contents(a.client, two["id"]) == ["in two"]


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

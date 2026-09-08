"""An empty conversation never reaches the frontend.

Owner rule, 2026-09-08: a conversation with nothing said in it must not be
returned anywhere. They exist because some surfaces mint a durable row when a
chat is OPENED rather than when it is used — a project's individual chat is
created by `POST /v1/projects/{id}/individual` on mount, so that the turns it
may later produce have a `conversation_id` to thread into. Those rows carry
the caller's `user_id` and the active workspace, so they matched
`GET /v1/conversations` and turned up in Chat history as untitled "ASK"
entries: rows the reader never created, cannot recognise, and cannot open into
anything.

The filter is on READ, not on creation, because the row is load-bearing where
it is made (the individual-chat memory-promotion hook only fires for a turn
that already has a conversation_id). What "empty" means is therefore precise:

  - no `conversation_turns` rows, AND
  - nothing on the conversation row itself.

That second half is not decoration. Threads written before turns existed as
the durable store carry their content in `title`/`query`/`reply` and have no
turn rows — a turns-only test would erase real history from every
long-standing account the day it shipped, which is the regression this file
exists to make impossible.
"""
from __future__ import annotations


def _titles(client) -> list[str]:
    resp = client.get("/v1/conversations")
    assert resp.status_code == 200, resp.text
    return [c.get("title") or "" for c in resp.json()["conversations"]]


def _ids(client) -> list[int]:
    resp = client.get("/v1/conversations")
    assert resp.status_code == 200, resp.text
    return [c["id"] for c in resp.json()["conversations"]]


def test_a_project_individual_chat_is_not_listed_until_it_is_used(tenant_client):
    """The exact reported case: opening a project chat put an untitled row in
    Chat history before a word was said in it."""
    client = tenant_client.make(slug="acme", user_id="user-a").client
    project = client.post("/v1/projects", json={"name": "Quote flow"}).json()

    conv = client.post(f"/v1/projects/{project['id']}/individual").json()
    assert conv["id"], "the row is still created — only its listing changes"

    assert conv["id"] not in _ids(client)


def test_the_same_chat_appears_once_it_has_a_turn(tenant_client):
    """The filter is about emptiness, not about project chats. The moment the
    conversation holds something, it lists like any other."""
    client = tenant_client.make(slug="acme", user_id="user-a").client
    project = client.post("/v1/projects", json={"name": "Quote flow"}).json()
    conv = client.post(f"/v1/projects/{project['id']}/individual").json()

    added = client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "What did we decide about pricing?"},
    )
    assert added.status_code == 200, added.text

    assert conv["id"] in _ids(client)


def test_a_legacy_row_with_no_turns_is_still_listed(tenant_client):
    """The half that stops this becoming a data-loss bug.

    `POST /v1/conversations` still accepts `title`/`query`/`reply`, and older
    threads live entirely on the row with no turn rows behind them. Emptiness
    has to mean "nothing was said", not "no turns table entry".
    """
    client = tenant_client.make(slug="acme", user_id="user-a").client
    resp = client.post(
        "/v1/conversations",
        json={"title": "How do we price the add-on?", "query": "price the add-on"},
    )
    assert resp.status_code == 200, resp.text

    assert "How do we price the add-on?" in _titles(client)


def test_an_untitled_row_carrying_only_a_reply_still_lists(tenant_client):
    """Any one of the three content fields is enough — a row is not empty
    because the field that happens to be filled is not the one checked first."""
    client = tenant_client.make(slug="acme", user_id="user-a").client
    conv = client.post("/v1/conversations", json={"title": "t"}).json()
    patched = client.patch(
        f"/v1/conversations/{conv['id']}",
        json={"title": "", "reply": "Because the tier above already covers it."},
    )
    assert patched.status_code == 200, patched.text

    assert conv["id"] in _ids(client)


def test_listing_survives_a_workspace_with_nothing_in_it(tenant_client):
    """No rows at all must not become an error on the way through the new
    second read — the turns query is skipped rather than asked for an empty
    id list."""
    client = tenant_client.make(slug="acme", user_id="user-a").client
    resp = client.get("/v1/conversations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversations"] == []

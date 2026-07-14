"""Per-user chat privacy on app.routes.conversations.

Chats (regular AND PRD-anchored) are PER-USER: every conversation is stamped
with the creating member's user_id, and only that member can list, read,
update, or delete it. Teammates in the same workspace never see each other's
chats — only artifacts (PRDs, prototypes, evidence) are workspace-shared.

Legacy rows written before stamping existed (user_id IS NULL) cannot be
attributed to an owner, so they are hidden from everyone — strict per-user
privacy beats resurfacing chats whose author is unknown.

Covered:
- create stamps the caller's user_id on the row
- list returns only the caller's conversations (teammate's are hidden)
- by-prd returns the CALLER'S conversation for the PRD, never a teammate's
- update / delete / turns 404 for a teammate (and don't mutate the row)
- legacy user_id-NULL rows are hidden from every member
"""
from __future__ import annotations


def _create(client, *, title, prd_id=None):
    body = {"title": title}
    if prd_id is not None:
        body["prd_id"] = prd_id
    resp = client.post("/v1/conversations", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _two_members(tenant_client):
    """Two authed clients for DIFFERENT users in the SAME company."""
    a = tenant_client.make(slug="acme", user_id="user-a")
    b = tenant_client.make(slug="acme", user_id="user-b")
    assert a.company_id == b.company_id
    return a, b


def test_create_stamps_the_callers_user_id(tenant_client):
    a = tenant_client.make(slug="acme", user_id="user-a")
    conv = _create(a.client, title="mine")
    assert conv["user_id"] == "user-a"


def test_list_shows_only_the_callers_conversations(tenant_client):
    a, b = _two_members(tenant_client)
    _create(a.client, title="A's private chat")
    _create(b.client, title="B's private chat")

    a_titles = [c["title"] for c in a.client.get("/v1/conversations").json()["conversations"]]
    b_titles = [c["title"] for c in b.client.get("/v1/conversations").json()["conversations"]]
    assert a_titles == ["A's private chat"]
    assert b_titles == ["B's private chat"]


def test_by_prd_is_per_user(tenant_client):
    a, b = _two_members(tenant_client)
    conv = _create(a.client, title="A's PRD chat", prd_id=5)
    a.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "private question"},
    )

    # The owner rehydrates their own chat…
    got = a.client.get("/v1/conversations/by-prd/5").json()
    assert got["conversation"]["id"] == conv["id"]
    assert [t["content"] for t in got["turns"]] == ["private question"]

    # …a teammate opening the same PRD gets an empty slate, not A's chat.
    assert b.client.get("/v1/conversations/by-prd/5").json() == {
        "conversation": None,
        "turns": [],
    }


def test_update_delete_and_turns_are_owner_only(tenant_client):
    a, b = _two_members(tenant_client)
    conv = _create(a.client, title="A's chat")
    cid = conv["id"]

    # A teammate can't read, append to, rename, or delete it.
    assert b.client.get(f"/v1/conversations/{cid}/turns").status_code == 404
    assert (
        b.client.post(
            f"/v1/conversations/{cid}/turns",
            json={"role": "user", "content": "intrusion"},
        ).status_code
        == 404
    )
    assert (
        b.client.patch(f"/v1/conversations/{cid}", json={"title": "hijacked"}).status_code
        == 404
    )
    assert b.client.delete(f"/v1/conversations/{cid}").status_code == 404

    # The row is untouched and still fully usable by its owner.
    mine = a.client.get("/v1/conversations").json()["conversations"]
    assert [c["title"] for c in mine] == ["A's chat"]
    assert a.client.get(f"/v1/conversations/{cid}/turns").json()["turns"] == []
    assert a.client.delete(f"/v1/conversations/{cid}").status_code == 200


def test_ask_history_never_replays_a_teammates_conversation(tenant_client):
    """ask.py loads prior turns for follow-ups; a teammate's conversation_id
    must load NOTHING — otherwise their private chat leaks into the model
    context (the cross-user read that survived the original per-user commit)."""
    from app.routes.ask import _load_history

    a, b = _two_members(tenant_client)
    conv = _create(a.client, title="A's chat")
    a.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "A's private question"},
    )

    owner_turns = _load_history(conv["id"], a.company_id, a.user_id)
    assert [t["content"] for t in owner_turns] == ["A's private question"]

    assert _load_history(conv["id"], b.company_id, b.user_id) == []


def test_legacy_unowned_rows_are_hidden_from_all_members(tenant_client):
    from app.db.client import require_client

    a, b = _two_members(tenant_client)
    # A pre-stamping row: user_id NULL (written before chats were per-user).
    inserted = require_client().table("conversations").insert(
        {"company_id": a.company_id, "user_id": None, "title": "legacy shared chat"}
    ).execute()
    legacy_id = inserted.data[0]["id"]

    for member in (a, b):
        titles = [
            c["title"]
            for c in member.client.get("/v1/conversations").json()["conversations"]
        ]
        assert "legacy shared chat" not in titles
        assert member.client.get(f"/v1/conversations/{legacy_id}/turns").status_code == 404

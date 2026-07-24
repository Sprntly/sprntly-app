"""Tests for the PRD-scoped conversation lookup on app.routes.conversations.

A PRD's chat is persisted as a normal conversation, now tagged with the PRD it's
about (`prd_id`). Reopening a PRD tab looks the conversation up by that id
(GET /v1/conversations/by-prd/{prd_id}) and rehydrates its turns — so a user's
earlier questions survive across sessions/devices, not just the localStorage tab.

Covered:
- create carries prd_id through to the row
- by-prd returns the conversation + its turns, oldest-first
- by-prd returns an empty (not 404) shape for a PRD with no conversation
- by-prd is tenant-scoped: another company's PRD conversation is not visible
- by-prd returns the MOST RECENT conversation when a PRD has several
- PATCH back-patches prd_id (command flows create the conversation BEFORE the
  async generate returns the prd_id, so it's first stored null), making the
  conversation findable by-prd afterwards
"""
from __future__ import annotations


def _create(client, *, title, prd_id=None):
    body = {"title": title}
    if prd_id is not None:
        body["prd_id"] = prd_id
    resp = client.post("/v1/conversations", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_persists_prd_id(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _create(t.client, title="PRD chat", prd_id=5)
    assert conv["prd_id"] == 5


def test_by_prd_returns_conversation_with_turns_oldest_first(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _create(t.client, title="PRD chat", prd_id=5)
    cid = conv["id"]
    assert t.client.post(f"/v1/conversations/{cid}/turns",
                         json={"role": "user", "content": "How does auth work?"}).status_code == 200
    assert t.client.post(f"/v1/conversations/{cid}/turns",
                         json={"role": "assistant", "content": "It uses OAuth."}).status_code == 200

    resp = t.client.get("/v1/conversations/by-prd/5")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conversation"]["id"] == cid
    contents = [turn["content"] for turn in data["turns"]]
    assert contents == ["How does auth work?", "It uses OAuth."]


def test_by_prd_empty_when_no_conversation(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/conversations/by-prd/999")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"conversation": None, "turns": []}


def test_by_prd_is_tenant_scoped(tenant_client):
    a = tenant_client.make(slug="company-a")
    _create(a.client, title="A's PRD chat", prd_id=7)
    b = tenant_client.make(slug="company-b")
    # Company B must not see company A's conversation for the same prd_id.
    resp = b.client.get("/v1/conversations/by-prd/7")
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation"] is None


def test_patch_back_patches_prd_id(tenant_client):
    # The command-flow race: a PRD chat's conversation is created from the seed
    # turn BEFORE the async generate returns the prd_id, so it starts null. PATCH
    # must be able to set it afterwards, and the row must then be findable by-prd.
    t = tenant_client.make(slug="acme")
    conv = _create(t.client, title="PRD chat")  # no prd_id at create → null
    assert conv.get("prd_id") is None
    cid = conv["id"]

    resp = t.client.patch(f"/v1/conversations/{cid}", json={"prd_id": 88})
    assert resp.status_code == 200, resp.text

    # The by-prd lookup now resolves the conversation the reopen-from-history path
    # relies on.
    found = t.client.get("/v1/conversations/by-prd/88").json()["conversation"]
    assert found is not None and found["id"] == cid


def test_by_prd_returns_most_recent(tenant_client):
    t = tenant_client.make(slug="acme")
    first = _create(t.client, title="first", prd_id=5)
    second = _create(t.client, title="second", prd_id=5)
    # Touch `second` so its updated_at is the newest (add a turn).
    t.client.post(f"/v1/conversations/{second['id']}/turns",
                  json={"role": "user", "content": "later"})
    resp = t.client.get("/v1/conversations/by-prd/5")
    assert resp.status_code == 200, resp.text
    got = resp.json()["conversation"]["id"]
    assert got == second["id"] and got != first["id"]

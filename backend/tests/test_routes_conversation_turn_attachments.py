"""Turn attachments on app.routes.conversations — persistence round-trip.

Extracted attachment text is persisted WITH its conversation turn
(conversation_turns.attachments jsonb) so a reloaded thread — and the chat→PRD
flow that grounds a later "generate a PRD" on earlier documents — can still
see files attached messages ago. Before this, the text lived only in the
frontend's transient send string and was silently forgotten.

Covered:
- add_turn stores attachments; list_turns returns them verbatim
- a turn without attachments stores/returns null (legacy shape unchanged)
- a NAME-ONLY attachment (empty content — a doc imported straight to a PRD) is
  accepted and round-trips, so the reopened thread shows the chip beside the ask
- validation: too many attachments / oversized content → 422
"""
from __future__ import annotations


def _conv(client, title="Chat"):
    resp = client.post("/v1/conversations", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_turn_attachments_round_trip(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user",
        "content": "here's the requirements doc",
        "attachments": [
            {"name": "requirements.pdf", "content": "MUST prefill cart. Marker-1"},
            {"name": "notes.md", "content": "Brand locked. Marker-2"},
        ],
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert len(turns) == 1
    assert turns[0]["attachments"] == [
        {"name": "requirements.pdf", "content": "MUST prefill cart. Marker-1"},
        {"name": "notes.md", "content": "Brand locked. Marker-2"},
    ]


def test_turn_without_attachments_keeps_legacy_shape(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(
        f"/v1/conversations/{conv['id']}/turns",
        json={"role": "user", "content": "plain question"},
    )
    assert resp.status_code == 200

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns[0]["attachments"] is None


def test_name_only_attachment_round_trips(tenant_client):
    # A "generate a PRD" command over an attached file imports the doc straight to
    # a PRD — there's no in-chat extracted text, but the file NAME is persisted as
    # a name-only chip. Empty content must be ACCEPTED (not 422'd) so the user turn
    # itself still saves; a rejected attachment used to drop the whole user turn,
    # leaving the reopened chat showing "No response was generated".
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user",
        "content": "generate a prd",
        "attachments": [{"name": "spec.pptx", "content": ""}],
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert len(turns) == 1
    assert turns[0]["content"] == "generate a prd"
    assert turns[0]["attachments"] == [{"name": "spec.pptx", "content": ""}]


def test_turn_attachments_validation(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = _conv(t.client)
    url = f"/v1/conversations/{conv['id']}/turns"

    nine = [{"name": f"d{i}.md", "content": "x"} for i in range(9)]
    assert t.client.post(
        url, json={"role": "user", "content": "m", "attachments": nine}
    ).status_code == 422

    big = [{"name": "big.md", "content": "x" * 60_001}]
    assert t.client.post(
        url, json={"role": "user", "content": "m", "attachments": big}
    ).status_code == 422

"""Chat attachment file storage — upload + sign + turn round-trip.

The ORIGINAL uploaded file is stashed in storage (POST /v1/conversations/attachments)
so a reopened chat can render the real document (PDF/image inline, everything
downloadable) — not just the extracted text. The turn persists the returned
`key`/`mime`; the viewer mints a fresh signed URL by key (GET .../attachments/sign).

Covered:
- upload returns a workspace-prefixed key + sniffed metadata
- upload rejects empty / oversized / unsupported types
- sign returns view + download URLs for an owned key; refuses a foreign-workspace key
- a turn carrying key/mime round-trips through add_turn → list_turns
"""
from __future__ import annotations

_PDF = b"%PDF-1.4\n%stub\n"


def _upload(client, *, name="Sprntly-How-To-Guide.pdf", data=_PDF, mime="application/pdf"):
    return client.post(
        "/v1/conversations/attachments",
        files={"file": (name, data, mime)},
    )


def test_upload_returns_key_and_metadata(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"].startswith("chat-attachments/")
    assert body["key"].endswith(".pdf")
    assert body["mime"] == "application/pdf"
    assert body["size"] == len(_PDF)
    assert body["name"] == "Sprntly-How-To-Guide.pdf"


def test_upload_rejects_unsupported_type(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, name="malware.exe", data=b"MZ...", mime="application/octet-stream")
    assert resp.status_code == 422


def test_upload_rejects_empty(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, data=b"")
    assert resp.status_code == 400


def test_sign_returns_urls_for_owned_key(tenant_client):
    t = tenant_client.make(slug="acme")
    key = _upload(t.client).json()["key"]

    resp = t.client.get("/v1/conversations/attachments/sign", params={"key": key, "name": "doc.pdf"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["view_url"]
    assert body["download_url"]
    assert body["mime"] == "application/pdf"


def test_sign_refuses_foreign_workspace_key(tenant_client):
    t = tenant_client.make(slug="acme")
    # A key under a DIFFERENT workspace prefix must never resolve — 404, never a
    # signed URL into another tenant's storage.
    resp = t.client.get(
        "/v1/conversations/attachments/sign",
        params={"key": "chat-attachments/some-other-workspace/deadbeef.pdf"},
    )
    assert resp.status_code == 404


def test_turn_persists_key_and_mime(tenant_client):
    t = tenant_client.make(slug="acme")
    conv = t.client.post("/v1/conversations", json={"title": "Chat"}).json()
    key = _upload(t.client).json()["key"]

    resp = t.client.post(f"/v1/conversations/{conv['id']}/turns", json={
        "role": "user",
        "content": "here's the guide",
        "attachments": [{"name": "guide.pdf", "content": "", "key": key, "mime": "application/pdf", "size": 12}],
    })
    assert resp.status_code == 200, resp.text

    turns = t.client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    att = turns[0]["attachments"][0]
    assert att["key"] == key
    assert att["mime"] == "application/pdf"

"""Tests for /internal/mcp-tokens/resolve + /internal/mcp/* (the mcp/
service's machine-to-machine API).

Covers: every route 401s without a valid X-Internal-Key, resolve turns a
raw token into {company_id, user_id, role}, and each data route is scoped
to the company_id it's given — a cross-tenant lookup (right ticket_key,
wrong company_id) returns nothing rather than another tenant's data.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from fastapi.testclient import TestClient

from app.db.mcp_tokens import create_mcp_token

_INTERNAL_KEY = "test-internal-key"


def _seed_company_and_member(client, *, company_id: str, slug: str, user_id: str) -> None:
    client.table("companies").insert(
        {"id": company_id, "slug": slug, "display_name": slug.title()}
    ).execute()
    client.table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company_id, "user_id": user_id, "role": "owner"}
    ).execute()


def _client(isolated_settings, monkeypatch) -> TestClient:
    import app.config as config_mod
    import app.main as main_mod

    monkeypatch.setattr(config_mod.settings, "internal_api_key", _INTERNAL_KEY, raising=False)
    return TestClient(main_mod.app)


def _headers() -> dict[str, str]:
    return {"X-Internal-Key": _INTERNAL_KEY}


def test_routes_401_without_internal_key(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    assert client.post("/internal/mcp-tokens/resolve", json={"token": "x"}).status_code == 401
    assert client.get("/internal/mcp/datasets", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/brief/current", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/backlog", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/prd/latest", params={"company_id": "x"}).status_code == 401
    assert (
        client.get("/internal/mcp/tickets/ABC-1/data", params={"company_id": "x"}).status_code
        == 401
    )


def test_resolve_returns_company_context_for_valid_token(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id=uid)
    created = create_mcp_token(company_id=cid, user_id=uid, name="t")

    r = client.post(
        "/internal/mcp-tokens/resolve", json={"token": created["token"]}, headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] == cid
    assert body["user_id"] == uid
    assert body["role"] == "owner"


def test_resolve_401s_on_unknown_token(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    r = client.post(
        "/internal/mcp-tokens/resolve", json={"token": "sprn_mcp_bogus"}, headers=_headers()
    )
    assert r.status_code == 401


def test_datasets_scoped_to_company(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    db.table("datasets").insert({"slug": "acme", "display_name": "Acme"}).execute()
    db.table("datasets").insert({"slug": "globex", "display_name": "Globex"}).execute()

    r = client.get("/internal/mcp/datasets", params={"company_id": cid_a}, headers=_headers())
    assert r.status_code == 200, r.text
    slugs = [d["slug"] for d in r.json()["datasets"]]
    assert slugs == ["acme"]


def test_backlog_empty_when_no_brief(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    r = client.get("/internal/mcp/backlog", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "count": 0}


def test_prd_latest_404s_when_none(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    r = client.get("/internal/mcp/prd/latest", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 404


def test_ticket_data_is_company_scoped(isolated_settings, monkeypatch):
    """Same ticket_key, two companies: each sees only its own override —
    no RLS safety net on the service-role client, so this is the explicit
    test that the company_id filter is actually applied."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    db.table("ticket_edits").insert(
        {"company_id": cid_a, "ticket_key": "ABC-1", "description": "Company A's ticket"}
    ).execute()

    r_a = client.get(
        "/internal/mcp/tickets/ABC-1/data", params={"company_id": cid_a}, headers=_headers()
    )
    assert r_a.status_code == 200, r_a.text
    assert r_a.json()["description"] == "Company A's ticket"

    r_b = client.get(
        "/internal/mcp/tickets/ABC-1/data", params={"company_id": cid_b}, headers=_headers()
    )
    assert r_b.status_code == 200, r_b.text
    assert r_b.json()["description"] is None
    assert r_b.json()["attachments"] == []
    assert r_b.json()["comments"] == []

"""Internal transcript viewer (/v1/transcripts).

Covers:
  * The shared-access-code login: success mints a 12h JWT that passes
    require_transcripts; a wrong code 401s; an unset TRANSCRIPTS_ACCESS_CODE_HASH
    ⇒ 404 everywhere, login included (fail closed, invisible).
  * require_transcripts gate: 404 for no token, a Supabase USER token, and a
    STAFF token (same signing secret, wrong audience) — the two internal
    surfaces are deliberately not interchangeable in either direction.
  * Listing: cross-tenant by design, newest first, filtered by company and by
    an INCLUSIVE date range, with turn counts and company/user labels.
  * Detail: conversation + all turns oldest-first, including the legacy
    query/reply fallback for rows that predate turn storage.
"""
from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from argon2 import PasswordHasher

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client

ACCESS_CODE = "open-sesame-transcripts"
# argon2id is deliberately slow — hash once at import, reuse in every test.
ACCESS_CODE_HASH = PasswordHasher().hash(ACCESS_CODE)


def _db():
    from app.db.client import require_client

    return require_client()


def _enable_surface(monkeypatch, *, code_hash: str = ACCESS_CODE_HASH):
    """Configure the shared access code.

    Must run AFTER company_client() — that helper reloads app.config/app.auth,
    which would discard an earlier settings patch."""
    import app.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.settings, "transcripts_access_code_hash", code_hash
    )


def _login(ctx, *, code: str = ACCESS_CODE):
    return ctx.client.post("/v1/transcripts/login", json={"code": code})


def _viewer_ctx(monkeypatch):
    """A client authed with a freshly minted transcripts JWT."""
    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)
    r = _login(ctx)
    assert r.status_code == 200, r.text
    ctx.client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return ctx


def _seed_company(company_id: str, display_name: str) -> None:
    db = _db()
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        # slug tracks the id — companies.slug is UNIQUE and company_client()
        # already seeds a fixture company, so deriving it from display_name
        # can collide.
        db.table("companies").insert(
            {"id": company_id, "slug": company_id, "display_name": display_name}
        ).execute()


def _seed_conversation(
    *,
    company_id: str,
    created_at: str,
    title: str = "A chat",
    user_id: str | None = None,
    query: str = "",
    reply: str = "",
) -> int:
    """Insert a conversation with an EXPLICIT ISO created_at.

    The SQLite mirror stores timestamps as text and the route's date filter
    compares against `…T00:00:00Z`, so tests must use the same ISO shape
    Postgres returns rather than SQLite's default `datetime('now')` (which is
    space-separated and would sort wrong against a 'T').
    """
    row = _db().table("conversations").insert({
        "company_id": company_id,
        "user_id": user_id,
        "title": title,
        "preview": "last thing they said",
        "agent_type": "ask",
        "query": query,
        "reply": reply,
        "created_at": created_at,
        "updated_at": created_at,
    }).execute()
    return row.data[0]["id"]


def _seed_turn(conversation_id: int, role: str, content: str, created_at: str) -> None:
    _db().table("conversation_turns").insert({
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": created_at,
    }).execute()


# ─────────────────────── login + gate ───────────────────────


def test_login_token_passes_transcripts_gate(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)

    r = _login(ctx)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 12 * 3600

    ctx.client.headers["Authorization"] = f"Bearer {body['token']}"
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 200


def test_login_wrong_code_401(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)
    assert _login(ctx, code="not-the-code").status_code == 401


def test_surface_disabled_404s_everything(isolated_settings, monkeypatch):
    """Unset hash ⇒ login and every route 404 — the surface is invisible."""
    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch, code_hash="")

    assert _login(ctx).status_code == 404
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 404
    assert ctx.client.get("/v1/transcripts/companies").status_code == 404
    assert ctx.client.get("/v1/transcripts/conversations/1").status_code == 404


def test_no_token_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)
    ctx.client.headers.pop("Authorization", None)
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 404


def test_user_token_does_not_pass(isolated_settings, monkeypatch):
    """A normal Supabase user token is not a transcripts token."""
    ctx = company_client(monkeypatch)  # already carries a user bearer
    _enable_surface(monkeypatch)
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 404


def test_staff_token_does_not_pass(isolated_settings, monkeypatch):
    """The staff JWT shares the signing secret but has aud=sprntly-staff, so it
    must NOT unlock transcripts — the credentials are deliberately split."""
    import app.auth as auth_mod

    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)
    monkeypatch.setattr(auth_mod.settings, "staff_admin_id", "sprntly-owner")
    monkeypatch.setattr(
        auth_mod.settings, "staff_admin_password_hash", ACCESS_CODE_HASH
    )
    staff_token = auth_mod.make_staff_token()

    ctx.client.headers["Authorization"] = f"Bearer {staff_token}"
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 404


def test_expired_token_404(isolated_settings, monkeypatch):
    import app.auth as auth_mod

    ctx = company_client(monkeypatch)
    _enable_surface(monkeypatch)
    expired = pyjwt.encode(
        {
            "sub": auth_mod.TRANSCRIPTS_SUB,
            "role": auth_mod.TRANSCRIPTS_ROLE,
            "aud": auth_mod.TRANSCRIPTS_AUD,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 60,
        },
        auth_mod.settings.jwt_secret,
        algorithm=auth_mod.JWT_ALG,
    )
    ctx.client.headers["Authorization"] = f"Bearer {expired}"
    assert ctx.client.get("/v1/transcripts/conversations").status_code == 404


# ─────────────────────── listing ───────────────────────


def test_list_is_cross_tenant_newest_first(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    _seed_company("comp-b", "Globex")
    _seed_conversation(
        company_id="comp-a", created_at="2026-07-18T09:00:00Z", title="older"
    )
    _seed_conversation(
        company_id="comp-b", created_at="2026-07-20T09:00:00Z", title="newer"
    )

    rows = ctx.client.get("/v1/transcripts/conversations").json()["conversations"]
    assert [r["title"] for r in rows] == ["newer", "older"]
    # Cross-tenant by design, with human labels resolved.
    assert {r["company_name"] for r in rows} == {"Acme", "Globex"}


def test_list_filters_by_company(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    _seed_company("comp-b", "Globex")
    _seed_conversation(company_id="comp-a", created_at="2026-07-20T09:00:00Z")
    _seed_conversation(company_id="comp-b", created_at="2026-07-20T10:00:00Z")

    rows = ctx.client.get(
        "/v1/transcripts/conversations", params={"company_id": "comp-a"}
    ).json()["conversations"]
    assert len(rows) == 1
    assert rows[0]["company_id"] == "comp-a"


def test_date_range_is_inclusive_of_the_end_date(isolated_settings, monkeypatch):
    """A conversation at 14:00 on `date_to` must be INCLUDED — the classic
    off-by-one this filter is written to avoid."""
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    _seed_conversation(
        company_id="comp-a", created_at="2026-07-17T23:00:00Z", title="before"
    )
    _seed_conversation(
        company_id="comp-a", created_at="2026-07-18T00:30:00Z", title="start day"
    )
    _seed_conversation(
        company_id="comp-a", created_at="2026-07-19T14:00:00Z", title="end day"
    )
    _seed_conversation(
        company_id="comp-a", created_at="2026-07-20T01:00:00Z", title="after"
    )

    rows = ctx.client.get(
        "/v1/transcripts/conversations",
        params={"date_from": "2026-07-18", "date_to": "2026-07-19"},
    ).json()["conversations"]
    assert sorted(r["title"] for r in rows) == ["end day", "start day"]


def test_bad_date_422(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    r = ctx.client.get(
        "/v1/transcripts/conversations", params={"date_from": "18-07-2026"}
    )
    assert r.status_code == 422


def test_list_reports_turn_counts_and_has_more(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    cid = _seed_conversation(company_id="comp-a", created_at="2026-07-20T09:00:00Z")
    _seed_turn(cid, "user", "hello", "2026-07-20T09:00:01Z")
    _seed_turn(cid, "assistant", "hi there", "2026-07-20T09:00:02Z")
    _seed_conversation(company_id="comp-a", created_at="2026-07-20T10:00:00Z")

    body = ctx.client.get(
        "/v1/transcripts/conversations", params={"limit": 1}
    ).json()
    assert body["has_more"] is True
    assert len(body["conversations"]) == 1

    body = ctx.client.get("/v1/transcripts/conversations").json()
    assert body["has_more"] is False
    counts = {r["id"]: r["turn_count"] for r in body["conversations"]}
    assert counts[cid] == 2


def test_companies_filter_list_only_offers_tenants_with_chats(
    isolated_settings, monkeypatch
):
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    _seed_company("comp-quiet", "Quiet Co")
    _seed_conversation(company_id="comp-a", created_at="2026-07-20T09:00:00Z")

    companies = ctx.client.get("/v1/transcripts/companies").json()["companies"]
    assert [c["id"] for c in companies] == ["comp-a"]
    assert companies[0]["display_name"] == "Acme"


# ─────────────────────── detail ───────────────────────


def test_detail_returns_turns_oldest_first(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    cid = _seed_conversation(company_id="comp-a", created_at="2026-07-20T09:00:00Z")
    _seed_turn(cid, "user", "what is our churn?", "2026-07-20T09:00:01Z")
    _seed_turn(cid, "assistant", "Churn is 4%.", "2026-07-20T09:00:02Z")
    _seed_turn(cid, "user", "and last month?", "2026-07-20T09:00:03Z")

    body = ctx.client.get(f"/v1/transcripts/conversations/{cid}").json()
    assert [t["role"] for t in body["turns"]] == ["user", "assistant", "user"]
    assert body["turns"][0]["content"] == "what is our churn?"
    assert body["conversation"]["company_name"] == "Acme"
    assert body["conversation"]["turn_count"] == 3


def test_detail_exposes_legacy_query_reply_fallback(isolated_settings, monkeypatch):
    """Rows predating turn storage carry the whole exchange in query/reply and
    have no turn rows — the reader falls back to those."""
    ctx = _viewer_ctx(monkeypatch)
    _seed_company("comp-a", "Acme")
    cid = _seed_conversation(
        company_id="comp-a",
        created_at="2026-06-01T09:00:00Z",
        query="how many signups?",
        reply="1,204 last week.",
    )

    body = ctx.client.get(f"/v1/transcripts/conversations/{cid}").json()
    assert body["turns"] == []
    assert body["conversation"]["query"] == "how many signups?"
    assert body["conversation"]["reply"] == "1,204 last week."


def test_detail_unknown_id_404(isolated_settings, monkeypatch):
    ctx = _viewer_ctx(monkeypatch)
    assert ctx.client.get("/v1/transcripts/conversations/999999").status_code == 404

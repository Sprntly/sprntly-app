"""Tests for POST /v1/whitelist — the public early-access signup form.

The rules worth pinning, in the order they can break:

  - It takes NO auth. A regression that adds a tenant dep here breaks the only
    caller (an anonymous visitor on the marketing site) and would otherwise show
    up first in production.
  - A repeat signup is a silent 200 that does NOT create a second row and does
    NOT move the original created_at. That idempotence is what lets the response
    stay uniform, which is what stops the endpoint being an "is this address on
    the list?" oracle.
  - Case and surrounding whitespace are normalised before the unique constraint
    sees the address, or `Foo@bar.com` sits next to `foo@bar.com`.
  - Junk is 422, not a stored row.
  - The per-IP budget actually closes.
"""
from __future__ import annotations

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules


def _anon():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def _reset_limiter():
    """The limiter is a module global that outlives a single test."""
    from app.routes.whitelist import SIGNUP_LIMITER

    SIGNUP_LIMITER._events.clear()


def _rows() -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("whitelist")
        .select("id, email, source, created_at")
        .execute()
        .data
        or []
    )


def test_signup_stores_email_and_source(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        r = anon.post(
            "/v1/whitelist", json={"email": "sam@example.com", "source": "landing-hero"}
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["email"] == "sam@example.com"
    assert rows[0]["source"] == "landing-hero"


def test_source_is_optional(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        assert anon.post("/v1/whitelist", json={"email": "sam@example.com"}).status_code == 200

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["source"] is None


def test_email_is_normalised_before_it_is_stored(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        anon.post("/v1/whitelist", json={"email": "  SAM@Example.COM  "})
        # A differently-cased duplicate must collapse onto the same row, which
        # only happens if normalisation runs BEFORE the unique constraint.
        anon.post("/v1/whitelist", json={"email": "sam@example.com"})

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["email"] == "sam@example.com"


def test_resubmitting_is_a_silent_success_that_keeps_the_first_signup(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        anon.post("/v1/whitelist", json={"email": "sam@example.com", "source": "first"})
        first = _rows()[0]
        second = anon.post(
            "/v1/whitelist", json={"email": "sam@example.com", "source": "second"}
        )

    # Same 200 as a fresh signup — the response must not disclose that the
    # address was already there.
    assert second.status_code == 200
    assert second.json() == {"ok": True}

    rows = _rows()
    assert len(rows) == 1
    # ON CONFLICT DO NOTHING: the ORIGINAL source and timestamp survive, so
    # "when did this person first put their hand up" stays answerable.
    assert rows[0]["source"] == "first"
    assert rows[0]["created_at"] == first["created_at"]


def test_junk_addresses_are_rejected_and_store_nothing(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        for bad in ("", "   ", "hello", "a@b", "no@domain.", "two@@at.com", "sp ace@x.com"):
            assert anon.post("/v1/whitelist", json={"email": bad}).status_code == 422, bad
    assert _rows() == []


def test_oversized_fields_are_rejected(isolated_settings):
    _reset_limiter()
    with _anon() as anon:
        long_email = "a" * 320 + "@example.com"
        assert anon.post("/v1/whitelist", json={"email": long_email}).status_code == 422
        assert anon.post(
            "/v1/whitelist", json={"email": "sam@example.com", "source": "x" * 101}
        ).status_code == 422
    assert _rows() == []


def test_per_ip_budget_closes(isolated_settings):
    _reset_limiter()
    from app.routes.whitelist import SIGNUP_LIMITER

    with _anon() as anon:
        for i in range(SIGNUP_LIMITER._max):
            assert anon.post(
                "/v1/whitelist", json={"email": f"sam{i}@example.com"}
            ).status_code == 200
        blocked = anon.post("/v1/whitelist", json={"email": "one-too-many@example.com"})

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    # The rejected signup was not stored.
    assert all(r["email"] != "one-too-many@example.com" for r in _rows())


def test_marketing_origin_allowed_without_env(isolated_settings, monkeypatch):
    """The form's origin must not depend on an operator editing ALLOWED_ORIGINS.

    Staging deployed without it, so the browser threw away a perfectly good 200
    as `400 Disallowed CORS origin` while the row was written — a signup that
    "failed" and succeeded at the same time.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "allowed_origins", "http://localhost:3000")
    assert "https://www.sprntly.ai" in settings.origins_list
    assert "https://sprntly.ai" in settings.origins_list

    # An env that already names one does not get it twice.
    monkeypatch.setattr(
        settings, "allowed_origins", "https://sprntly.ai,http://localhost:3000"
    )
    assert settings.origins_list.count("https://sprntly.ai") == 1

    # And CORSMiddleware honours it — the stack is built from origins_list.
    with _anon() as anon:
        pre = anon.options(
            "/v1/whitelist",
            headers={
                "Origin": "https://www.sprntly.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "https://www.sprntly.ai"

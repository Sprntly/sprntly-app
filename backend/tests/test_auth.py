"""Tests for app.auth — audience-split login, tokens, and gate.

Covers:
  - Token mint+decode per audience, and cross-audience rejection.
  - require_session generic gate accepting either cookie.
  - require_app_session / require_demo_session enforcing one audience.
  - /v1/auth/login mints the right cookie per audience.
  - Cross-cookie copy-paste fails (audience claim is the firewall).
  - /v1/auth/me returns per-audience shape.
  - Legacy cookie (sprintly_session) is never accepted but is cleared.
"""
import time

import jwt
import pytest
from fastapi import HTTPException

from app import auth


# ─────────────────────── Token round-trip ───────────────────────


def test_make_token_includes_audience(isolated_settings):
    for aud in ("app", "demo"):
        token = auth._make_token(aud)
        decoded = auth._decode_token(token, aud)
        assert decoded["aud"] == aud
        assert decoded["scope"] == aud
        assert "iat" in decoded and "exp" in decoded
        assert decoded["exp"] > decoded["iat"]


def test_decode_token_rejects_wrong_audience(isolated_settings):
    """A token minted with aud=app must not decode as aud=demo."""
    token = auth._make_token("app")
    with pytest.raises(jwt.PyJWTError):
        auth._decode_token(token, "demo")


def test_decode_token_rejects_tampered_token(isolated_settings):
    """Replace the signature segment entirely so HMAC validation fails."""
    token = auth._make_token("app")
    head, payload, _sig = token.split(".")
    # Use a clearly-different but still base64-shaped sig.
    bogus_sig = "X" * 43
    tampered = ".".join([head, payload, bogus_sig])
    with pytest.raises(jwt.PyJWTError):
        auth._decode_token(tampered, "app")


# ─────────────────────── require_session (generic) ───────────────────────


def test_require_session_raises_when_no_cookies(isolated_settings):
    with pytest.raises(HTTPException) as exc:
        auth.require_session(None, None)
    assert exc.value.status_code == 401


def test_require_session_accepts_app_cookie(isolated_settings):
    token = auth._make_token("app")
    payload = auth.require_session(token, None)
    assert payload["aud"] == "app"


def test_require_session_accepts_demo_cookie(isolated_settings):
    token = auth._make_token("demo")
    payload = auth.require_session(None, token)
    assert payload["aud"] == "demo"


def test_require_session_rejects_swapped_cookie(isolated_settings):
    """An app token in the demo slot, or vice versa, must 401 — that's the firewall."""
    app_token = auth._make_token("app")
    demo_token = auth._make_token("demo")
    # demo cookie slot holding an app token: audience mismatch
    with pytest.raises(HTTPException):
        auth.require_session(None, app_token)
    with pytest.raises(HTTPException):
        auth.require_session(demo_token, None)


def test_require_session_rejects_expired_token(isolated_settings):
    now = int(time.time())
    expired = jwt.encode(
        {"iat": now - 7200, "exp": now - 3600, "aud": "app", "scope": "app"},
        isolated_settings["config"].settings.jwt_secret,
        algorithm=auth.JWT_ALG,
    )
    with pytest.raises(HTTPException) as exc:
        auth.require_session(expired, None)
    assert exc.value.status_code == 401


# ─────────────────────── audience-locked gates ───────────────────────


def test_require_app_session_accepts_app(isolated_settings):
    token = auth._make_token("app")
    payload = auth.require_app_session(token)
    assert payload["aud"] == "app"


def test_require_app_session_rejects_demo(isolated_settings):
    token = auth._make_token("demo")
    with pytest.raises(HTTPException):
        auth.require_app_session(token)


def test_require_demo_session_accepts_demo(isolated_settings):
    token = auth._make_token("demo")
    payload = auth.require_demo_session(token)
    assert payload["aud"] == "demo"


def test_require_demo_session_rejects_app(isolated_settings):
    token = auth._make_token("app")
    with pytest.raises(HTTPException):
        auth.require_demo_session(token)


# ─────────────────────── /v1/auth/login route ───────────────────────


def test_login_defaults_to_demo(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={"password": "test-pw"})
    assert resp.status_code == 200
    assert resp.json()["audience"] == "demo"
    assert auth.DEMO_COOKIE in resp.cookies or auth.DEMO_COOKIE in unauth_client.cookies
    assert auth.APP_COOKIE not in resp.cookies


def test_login_app_audience_sets_app_cookie(unauth_client):
    resp = unauth_client.post(
        "/v1/auth/login",
        json={"password": "test-pw", "audience": "app"},
    )
    assert resp.status_code == 200
    assert resp.json()["audience"] == "app"
    assert auth.APP_COOKIE in resp.cookies or auth.APP_COOKIE in unauth_client.cookies


def test_login_demo_audience_sets_demo_cookie(unauth_client):
    resp = unauth_client.post(
        "/v1/auth/login",
        json={"password": "test-pw", "audience": "demo"},
    )
    assert resp.status_code == 200
    assert auth.DEMO_COOKIE in resp.cookies or auth.DEMO_COOKIE in unauth_client.cookies


def test_login_rejects_unknown_audience(unauth_client):
    resp = unauth_client.post(
        "/v1/auth/login",
        json={"password": "test-pw", "audience": "admin"},
    )
    assert resp.status_code == 422  # pydantic validation


def test_login_with_wrong_password_returns_401(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={"password": "wrong-pw"})
    assert resp.status_code == 401


def test_login_missing_password_returns_422(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={})
    assert resp.status_code == 422


# ─────────────────────── independence between audiences ───────────────────────


def test_app_and_demo_sessions_are_independent(unauth_client):
    """Logging in to both audiences leaves both cookies live independently."""
    a = unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    d = unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "demo"})
    assert a.status_code == 200 and d.status_code == 200
    me = unauth_client.get("/v1/auth/me").json()
    assert me["app"] is not None
    assert me["demo"] is not None


def test_demo_login_does_not_grant_app_session(unauth_client):
    """The user-visible promise of the split: demo login does not log you into app."""
    unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "demo"})
    # Drop the demo cookie from the jar; we're checking the app cookie was never set.
    assert auth.APP_COOKIE not in unauth_client.cookies


# ─────────────────────── /v1/auth/me ───────────────────────


def test_me_returns_401_without_cookies(unauth_client):
    resp = unauth_client.get("/v1/auth/me")
    assert resp.status_code == 401


def test_me_shape_per_audience(unauth_client):
    unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    me = unauth_client.get("/v1/auth/me").json()
    assert me["app"] is not None
    assert "expires_at" in me["app"]
    assert me["demo"] is None


# ─────────────────────── /v1/auth/logout ───────────────────────


def test_logout_clears_both_cookies(unauth_client):
    unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    unauth_client.post("/v1/auth/login", json={"password": "test-pw", "audience": "demo"})
    resp = unauth_client.post("/v1/auth/logout")
    assert resp.status_code == 200
    # /me should now 401 (cookies are gone from the jar / are expired).
    me = unauth_client.get("/v1/auth/me")
    assert me.status_code == 401


# ─────────────────────── legacy cookie behavior ───────────────────────


def test_legacy_cookie_is_never_accepted(isolated_settings):
    """Old `sprintly_session` JWTs without an aud claim must 401."""
    now = int(time.time())
    no_aud = jwt.encode(
        {"iat": now, "exp": now + 3600, "scope": "demo"},  # no aud!
        isolated_settings["config"].settings.jwt_secret,
        algorithm=auth.JWT_ALG,
    )
    # Try the legacy token in both new cookie slots — neither validates.
    with pytest.raises(HTTPException):
        auth.require_session(no_aud, None)
    with pytest.raises(HTTPException):
        auth.require_session(None, no_aud)

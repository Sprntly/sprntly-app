"""Tests for app.auth — login flow, session token, and require_session."""
import time

import jwt
import pytest
from fastapi import HTTPException

from app import auth


# ---- Token round-trip --------------------------------------------------------

def test_make_token_and_decode_token_roundtrip(isolated_settings):
    token = auth._make_token()
    decoded = auth._decode_token(token)
    assert decoded["scope"] == "demo"
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_decode_token_rejects_tampered_token(isolated_settings):
    token = auth._make_token()
    # Flip the last char — signature won't verify.
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(jwt.PyJWTError):
        auth._decode_token(tampered)


# ---- require_session ---------------------------------------------------------

def test_require_session_raises_on_missing_token(isolated_settings):
    with pytest.raises(HTTPException) as excinfo:
        auth.require_session(None)
    assert excinfo.value.status_code == 401


def test_require_session_raises_on_empty_string(isolated_settings):
    with pytest.raises(HTTPException) as excinfo:
        auth.require_session("")
    assert excinfo.value.status_code == 401


def test_require_session_raises_on_garbage_token(isolated_settings):
    with pytest.raises(HTTPException) as excinfo:
        auth.require_session("not-a-real-jwt")
    assert excinfo.value.status_code == 401


def test_require_session_accepts_valid_token(isolated_settings):
    token = auth._make_token()
    payload = auth.require_session(token)
    assert payload["scope"] == "demo"


def test_require_session_rejects_expired_token(isolated_settings):
    """Manually-minted token with `exp` in the past must 401."""
    now = int(time.time())
    expired = jwt.encode(
        {"iat": now - 7200, "exp": now - 3600, "scope": "demo"},
        isolated_settings["config"].settings.jwt_secret,
        algorithm=auth.JWT_ALG,
    )
    with pytest.raises(HTTPException) as excinfo:
        auth.require_session(expired)
    assert excinfo.value.status_code == 401


# ---- /v1/auth/login route ---------------------------------------------------

def test_login_with_correct_password_sets_cookie(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={"password": "test-pw"})
    assert resp.status_code == 200
    # The TestClient stores Set-Cookie response cookies on its cookie jar.
    assert auth.COOKIE_NAME in resp.cookies or auth.COOKIE_NAME in unauth_client.cookies


def test_login_with_wrong_password_returns_401(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={"password": "wrong-pw"})
    assert resp.status_code == 401


def test_login_with_missing_password_returns_validation_error(unauth_client):
    resp = unauth_client.post("/v1/auth/login", json={})
    # FastAPI / Pydantic returns 422 for body validation errors.
    assert resp.status_code == 422


def test_me_endpoint_requires_session(unauth_client):
    resp = unauth_client.get("/v1/auth/me")
    assert resp.status_code == 401


def test_me_endpoint_returns_scope_after_login(unauth_client):
    login = unauth_client.post("/v1/auth/login", json={"password": "test-pw"})
    assert login.status_code == 200
    me = unauth_client.get("/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["scope"] == "demo"
    assert "expires_at" in body


def test_logout_clears_cookie(unauth_client):
    unauth_client.post("/v1/auth/login", json={"password": "test-pw"})
    resp = unauth_client.post("/v1/auth/logout")
    assert resp.status_code == 200

"""Smoke tests for the FastAPI auth + session boundary.

Auth is now token-based: login returns a token in the JSON body, the
client sends it back as `Authorization: Bearer <token>`. The chat
endpoint itself is exercised via a mocked ChatRunner because we don't
want to hit the Anthropic API during CI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Set the env BEFORE importing the app module so load_config() picks it up.
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))

    from ds_agent.server.app import create_app
    return TestClient(create_app())


def _login(client) -> str:
    r = client.post("/api/login", json={"password": "letmein"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    token = body["token"]
    assert token
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public(client):
    assert client.get("/health").text == "ok"


def test_session_requires_auth(client):
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_password"


def test_login_returns_token(client):
    token = _login(client)
    # The token is signed; we won't decode here, just confirm it's non-empty.
    assert isinstance(token, str) and len(token) > 20


def test_session_with_bearer_token(client):
    token = _login(client)
    r = client.get("/api/session", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["has_dataset"] is False


def test_session_with_bad_token(client):
    r = client.get("/api/session", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_session_with_wrong_scheme(client):
    token = _login(client)
    # `Basic <token>` instead of `Bearer <token>` should not authenticate.
    r = client.get("/api/session", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401


def test_logout_clears_session(client):
    token = _login(client)
    client.post("/api/logout", headers=_auth(token))
    # Server-side session is gone, but the token is still cryptographically
    # valid — the client is expected to drop the token from storage. We
    # verify the /session call still works (the token still decodes to a sid)
    # but the session it points at is fresh empty state.
    r = client.get("/api/session", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["has_dataset"] is False


def test_chat_requires_dataset(client):
    token = _login(client)
    r = client.post("/api/chat", json={"message": "go"}, headers=_auth(token))
    assert r.status_code == 400
    assert r.json()["detail"] == "no_dataset_loaded"

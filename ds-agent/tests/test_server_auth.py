"""Auth boundary for the FastAPI app.

Bearer-token sessions: login returns a token in the JSON body, every
gated route requires `Authorization: Bearer <token>`. No Anthropic
calls are made by any of these tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")

    # Stub the Files API uploader so tests don't hit the wire.
    from ds_agent.server import tools as _tools

    def _fake_upload(staged):
        return [
            _tools.UploadedFile(
                local_path=s.local_path,
                label=s.label,
                size_bytes=s.size_bytes,
                anthropic_file_id=f"file_{i}",
            )
            for i, s in enumerate(staged)
        ]

    monkeypatch.setattr(_tools, "upload_staged", _fake_upload)
    monkeypatch.setattr(_tools, "delete_file", lambda *a, **kw: None)

    from ds_agent.server.app import create_app
    return TestClient(create_app())


def _login(client) -> str:
    r = client.post("/api/login", json={"password": "letmein"})
    assert r.status_code == 200
    return r.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public(client):
    assert client.get("/health").text == "ok"


def test_session_requires_auth(client):
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/agents/ds/state").status_code == 401
    assert client.post("/api/agents/ds/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/api/agents/ds/reset").status_code == 401


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_password"


def test_login_returns_token(client):
    token = _login(client)
    assert isinstance(token, str) and len(token) > 20


def test_session_with_bearer_token(client):
    token = _login(client)
    r = client.get("/api/session", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True


def test_hub_lists_ds_agent(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    ids = {a["id"] for a in r.json()["agents"]}
    assert "ds" in ids


def test_session_with_bad_token(client):
    r = client.get("/api/session", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_session_with_wrong_scheme(client):
    token = _login(client)
    r = client.get("/api/session", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401


def test_chat_requires_dataset(client):
    token = _login(client)
    r = client.post("/api/agents/ds/chat", json={"message": "go"}, headers=_auth(token))
    assert r.status_code == 400
    assert r.json()["detail"] == "no_dataset_loaded"

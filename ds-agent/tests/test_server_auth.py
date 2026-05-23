"""Smoke tests for the FastAPI auth + session boundary.

The chat endpoint itself is exercised via a mocked ChatRunner because
we don't want to hit the Anthropic API during CI.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Set the env BEFORE importing the app module so load_config() picks it up.
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_COOKIE_SECURE", "0")  # plain HTTP in tests
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))

    from ds_agent.server.app import create_app
    return TestClient(create_app())


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


def test_login_and_session_roundtrip(client):
    r = client.post("/api/login", json={"password": "letmein"})
    assert r.status_code == 200
    r2 = client.get("/api/session")
    assert r2.status_code == 200
    assert r2.json()["authenticated"] is True
    assert r2.json()["has_dataset"] is False


def test_logout_clears_session(client):
    client.post("/api/login", json={"password": "letmein"})
    client.post("/api/logout")
    assert client.get("/api/session").status_code == 401


def test_chat_requires_dataset(client):
    client.post("/api/login", json={"password": "letmein"})
    r = client.post("/api/chat", json={"message": "go"})
    assert r.status_code == 400
    assert r.json()["detail"] == "no_dataset_loaded"

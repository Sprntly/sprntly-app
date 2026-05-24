"""Sample-dataset listing + load + reset endpoints.

The chat endpoint itself is mocked here; Anthropic is not called.
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

    from ds_agent.server import tools as _tools
    monkeypatch.setattr(_tools, "upload_csv", lambda *a, **kw: "file_test")
    monkeypatch.setattr(_tools, "delete_file", lambda *a, **kw: None)

    from ds_agent.server.app import create_app
    return TestClient(create_app())


def _login(client) -> dict[str, str]:
    r = client.post("/api/login", json={"password": "letmein"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_samples_listed(client):
    headers = _login(client)
    r = client.get("/api/samples", headers=headers)
    assert r.status_code == 200
    samples = r.json()["samples"]
    assert any(s["id"] == "saas_retention" for s in samples)


def test_load_sample_sets_dataset(client):
    headers = _login(client)
    r = client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    assert r.status_code == 200
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is True
    assert "saas" in (state["dataset_label"] or "").lower()


def test_unknown_sample_404s(client):
    headers = _login(client)
    r = client.post("/api/load-sample", json={"sample_id": "bogus"}, headers=headers)
    assert r.status_code == 404


def test_reset_clears_dataset(client):
    headers = _login(client)
    client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    client.post("/api/reset", headers=headers)
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is False


def test_upload_rejects_non_csv(client):
    headers = _login(client)
    r = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("not_a_csv.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "only_csv_supported"


def test_upload_csv_sets_dataset(client):
    headers = _login(client)
    r = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert r.status_code == 200
    state = client.get("/api/state", headers=headers).json()
    assert state["has_dataset"] is True
    assert state["dataset_label"] == "data.csv"

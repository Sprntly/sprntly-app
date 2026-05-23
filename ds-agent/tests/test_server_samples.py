"""Sample-dataset listing + load endpoints, plus tool execution against
a session that has a CSV loaded. ChatRunner is not invoked here — we
exercise the tool layer directly to avoid hitting the Anthropic API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    from ds_agent.server.app import create_app
    return TestClient(create_app())


def _login(client) -> dict[str, str]:
    """Returns Authorization headers for a fresh logged-in client."""
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
    assert state["goal_metric"] == "retention_30d"
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
    assert state["goal_metric"] is None


def test_tool_describe_dataset_on_loaded_sample(client):
    """Hit the tools layer directly to keep this test offline."""
    from ds_agent.server import tools
    from ds_agent.server.state import SessionState
    from pathlib import Path

    here = Path(__file__).parent.parent / "ds_agent" / "server" / "samples"
    s = SessionState(sid="t1")
    s.csv_path = here / "saas_retention.csv"
    s.dataset_label = "saas_retention.csv"

    out = tools.execute("describe_dataset", {}, s)
    assert "error" not in out
    assert out["row_count"] == 4000
    cols = {c["name"] for c in out["columns"]}
    assert "retention_30d" in cols
    assert "posts_first_week" in cols


def test_tool_set_goal_metric_rejects_unknown_column(client):
    from ds_agent.server import tools
    from ds_agent.server.state import SessionState
    from pathlib import Path

    here = Path(__file__).parent.parent / "ds_agent" / "server" / "samples"
    s = SessionState(sid="t2")
    s.csv_path = here / "saas_retention.csv"

    out = tools.execute("set_goal_metric", {"metric": "not_a_real_column"}, s)
    assert "error" in out
    assert "unknown_column" in out["error"]

"""Locks the /api/chat NDJSON streaming shape with the ChatRunner mocked.

We don't hit Anthropic in CI — instead we patch the runner with a stub
that yields a deterministic event sequence, and parse the NDJSON
response.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient


class _StubRunner:
    def stream_turn(self, session, message) -> Iterator[dict[str, Any]]:
        yield {"type": "text_delta", "text": "here's a quick look\n\n"}
        yield {
            "type": "code_start",
            "id": "srv_1",
            "code": "df.describe()",
        }
        yield {
            "type": "code_result",
            "id": "srv_1",
            "stdout": "row 1\nrow 2",
            "stderr": "",
            "return_code": 0,
            "file_ids": ["file_chart_abc"],
            "error_code": None,
        }
        yield {"type": "text_delta", "text": "## TL;DR\n- finding 1\n"}
        yield {"type": "done"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PASSWORD", "letmein")
    monkeypatch.setenv("AGENT_COOKIE_SECRET", "test-cookie-secret-min-32-chars-long")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")

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

    from ds_agent.server import app as _app_module
    monkeypatch.setattr(_app_module, "ChatRunner", lambda *a, **kw: _StubRunner())
    return TestClient(_app_module.create_app())


def _login(client) -> dict[str, str]:
    r = client.post("/api/login", json={"password": "letmein"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _read_ndjson(response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8")
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def test_chat_streams_ndjson(client):
    headers = _login(client)
    client.post("/api/agents/ds/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    with client.stream(
        "POST", "/api/agents/ds/chat", json={"message": "go"}, headers=headers
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = _read_ndjson(r)

    # Expected sequence: text_delta, code_start, code_result, text_delta, done
    types = [e["type"] for e in events]
    assert types == ["text_delta", "code_start", "code_result", "text_delta", "done"]

    code_start = events[1]
    code_result = events[2]
    assert code_start["id"] == "srv_1"
    assert code_start["code"] == "df.describe()"
    assert code_result["id"] == "srv_1"
    assert code_result["stdout"].startswith("row 1")
    assert code_result["return_code"] == 0
    assert code_result["file_ids"] == ["file_chart_abc"]
    assert code_result["error_code"] is None


def test_chat_empty_message_400s(client):
    headers = _login(client)
    client.post("/api/agents/ds/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    r = client.post("/api/agents/ds/chat", json={"message": "  "}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "empty_message"


def test_chat_requires_dataset(client):
    headers = _login(client)
    r = client.post("/api/agents/ds/chat", json={"message": "go"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "no_dataset_loaded"

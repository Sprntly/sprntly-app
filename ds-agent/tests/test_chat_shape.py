"""Locks the /api/chat response shape with the ChatRunner mocked.

We don't hit Anthropic in CI — instead we patch `_runner()` in app.py
to return a stub that returns one text chunk + one code-execution
bundle, and assert the JSON shape the UI depends on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ds_agent.server.chat import CodeExecution, TurnResult


class _StubRunner:
    def turn(self, session, message):
        return TurnResult(
            assistant_text="here's a quick look",
            code_executions=[
                CodeExecution(
                    code="df.describe()",
                    stdout="row 1\nrow 2",
                    stderr="",
                    return_code=0,
                    file_ids=["file_chart_abc"],
                )
            ],
        )


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

    # `app.py` imports ChatRunner by name at module load and the lazy
    # `_runner()` closure captures it — so we must patch the name on
    # the `app` module, not on `chat`.
    from ds_agent.server import app as _app_module
    monkeypatch.setattr(_app_module, "ChatRunner", lambda *a, **kw: _StubRunner())
    return TestClient(_app_module.create_app())


def _login(client) -> dict[str, str]:
    r = client.post("/api/login", json={"password": "letmein"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_chat_returns_assistant_and_code_executions(client):
    headers = _login(client)
    client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    r = client.post("/api/chat", json={"message": "go"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["assistant"] == "here's a quick look"
    assert isinstance(body["code_executions"], list) and len(body["code_executions"]) == 1
    ce = body["code_executions"][0]
    assert ce["code"] == "df.describe()"
    assert ce["stdout"].startswith("row 1")
    assert ce["return_code"] == 0
    assert ce["file_ids"] == ["file_chart_abc"]
    assert ce.get("error_code") is None


def test_chat_empty_message_400s(client):
    headers = _login(client)
    client.post("/api/load-sample", json={"sample_id": "saas_retention"}, headers=headers)
    r = client.post("/api/chat", json={"message": "  "}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "empty_message"

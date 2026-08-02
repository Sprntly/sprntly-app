"""POST /v1/prd/classify-command — tier-2 LLM fallback for the chat command
decision (does this message ask us to CREATE a PRD, and for what task?).

The client calls this only when a message NAMES a PRD but the regex tier
(web BriefChat.isPrdCommand) didn't match — novel phrasings like "let's get a
PRD going for checkout". Contract under test:

  - the haiku verdict is passed through {is_prd_command, task, confidence}
  - the gateway call binds the acting company as enterprise_id
  - fail-open: ANY gateway error → not-a-command (the chat send must complete)
  - input validation: empty / oversized text → 422, no LLM call

LLM work is mocked at the gateway seam (app.prd_command.llm_call), same as
tests/test_prd_input_questions.py.
"""
from __future__ import annotations

from app import prd_command
from app.graph.gateway import LLMResult


def _llm_result(output) -> LLMResult:
    return LLMResult(
        output=output, model="claude-haiku-4-5", prompt_version="prd-command-classify-v1",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1, stop_reason="end_turn",
    )


def test_classify_passes_through_verdict(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_command, "llm_call", lambda **kw: _llm_result({
        "is_prd_command": True, "task": "checkout revamp", "confidence": 0.92,
    }))
    resp = t.client.post(
        "/v1/prd/classify-command",
        json={"text": "let's get a PRD going for the checkout revamp"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "is_prd_command": True, "task": "checkout revamp", "confidence": 0.92,
    }


def test_classify_binds_acting_company_and_sends_message(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"is_prd_command": False, "confidence": 0.8})

    monkeypatch.setattr(prd_command, "llm_call", _capture)
    resp = t.client.post("/v1/prd/classify-command", json={"text": "prd thoughts?"})
    assert resp.status_code == 200
    assert seen["enterprise_id"] == t.company_id
    assert "prd thoughts?" in seen["input"]
    assert seen["json_schema"] is prd_command._SCHEMA


def test_classify_normalizes_blank_task_to_null(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_command, "llm_call", lambda **kw: _llm_result({
        "is_prd_command": True, "task": "   ", "confidence": 0.9,
    }))
    resp = t.client.post("/v1/prd/classify-command", json={"text": "make us a prd"})
    assert resp.json()["task"] is None


def test_classify_fails_open_on_gateway_error(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(prd_command, "llm_call", _boom)
    resp = t.client.post("/v1/prd/classify-command", json={"text": "prd for checkout"})
    assert resp.status_code == 200
    assert resp.json() == {"is_prd_command": False, "task": None, "confidence": 0.0}


def test_classify_rejects_empty_and_oversized_text(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    called = []
    monkeypatch.setattr(prd_command, "llm_call", lambda **kw: called.append(1))

    assert t.client.post("/v1/prd/classify-command", json={"text": ""}).status_code == 422
    assert t.client.post(
        "/v1/prd/classify-command", json={"text": "x" * 8001}
    ).status_code == 422
    assert called == []

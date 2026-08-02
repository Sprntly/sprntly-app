"""POST /v1/prd/clarify-task + app.prd_clarify — the clarify-first gate.

Issue d of the chat→PRD bug set: generation used to start straight from the
user's message and the author filled every gap with assumptions. The gate runs
on EVERY chat-PRD command (Apurva's directive — detailed-looking prompts
included) and either passes (sufficient) or returns targeted questions.

Contract under test:
  - verdict + questions pass through; blank prompts dropped, options capped at
    4, questions capped at 5
  - sufficient=true forced whenever the model returns no usable questions
    (an "insufficient but no questions" verdict must not strand the user)
  - fail-open: gateway error → sufficient, no questions (never blocks)
  - the route binds the acting company and folds attached docs into the input
  - validation: task too short → 422, no LLM call

LLM work is mocked at the gateway seam (app.prd_clarify.llm_call).
"""
from __future__ import annotations

from app import prd_clarify
from app.graph.gateway import LLMResult


def _llm_result(output) -> LLMResult:
    return LLMResult(
        output=output, model="m", prompt_version="prd-clarify-v1",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1, stop_reason="end_turn",
    )


def test_clarify_passes_through_questions(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_clarify, "llm_call", lambda **kw: _llm_result({
        "sufficient": False,
        "missing": ["Target users", "Success criteria"],
        "questions": [
            {"prompt": "Who are the target users?", "options": ["Admins", "End users"]},
            {"prompt": "How will you measure success?", "options": []},
        ],
    }))
    resp = t.client.post("/v1/prd/clarify-task", json={"task": "build a dashboard"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sufficient"] is False
    assert [q["prompt"] for q in body["questions"]] == [
        "Who are the target users?", "How will you measure success?",
    ]
    assert body["questions"][0]["options"] == ["Admins", "End users"]
    assert body["missing"] == ["Target users", "Success criteria"]


def test_clarify_sanitizes_and_caps_questions(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_clarify, "llm_call", lambda **kw: _llm_result({
        "sufficient": False,
        "questions": (
            [{"prompt": "   "}, {"prompt": "ok?", "options": ["a", "b", "c", "d", "e"]}]
            + [{"prompt": f"q{i}?"} for i in range(6)]
        ),
    }))
    body = t.client.post(
        "/v1/prd/clarify-task", json={"task": "build a dashboard"}
    ).json()
    assert len(body["questions"]) == 5  # capped, blank dropped
    assert body["questions"][0]["options"] == ["a", "b", "c", "d"]  # options capped


def test_clarify_forces_sufficient_when_no_usable_questions(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_clarify, "llm_call", lambda **kw: _llm_result({
        "sufficient": False, "questions": [{"prompt": "   "}],
    }))
    body = t.client.post(
        "/v1/prd/clarify-task", json={"task": "build a dashboard"}
    ).json()
    assert body["sufficient"] is True
    assert body["questions"] == []


def test_clarify_fails_open_on_gateway_error(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(prd_clarify, "llm_call", _boom)
    body = t.client.post(
        "/v1/prd/clarify-task", json={"task": "build a dashboard"}
    ).json()
    assert body == {"sufficient": True, "questions": [], "missing": []}


def test_clarify_binds_company_and_folds_docs(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"sufficient": True, "questions": []})

    monkeypatch.setattr(prd_clarify, "llm_call", _capture)
    resp = t.client.post("/v1/prd/clarify-task", json={
        "task": "FieldSync inspections",
        "source_docs": [{"name": "req.pdf", "content": "Doc-marker offline sync"}],
    })
    assert resp.status_code == 200
    assert seen["enterprise_id"] == t.company_id
    assert "FieldSync inspections" in seen["input"]
    assert "--- req.pdf ---" in seen["input"] and "Doc-marker" in seen["input"]


def test_clarify_validates_task(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    called = []
    monkeypatch.setattr(prd_clarify, "llm_call", lambda **kw: called.append(1))
    assert t.client.post("/v1/prd/clarify-task", json={"task": "ab"}).status_code == 422
    assert called == []


def test_clarify_passes_skip_default_and_drops_blank(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(prd_clarify, "llm_call", lambda **kw: _llm_result({
        "sufficient": False,
        "questions": [
            {"prompt": "Who are the target users?", "skip_default": " all end users "},
            {"prompt": "What is out of scope?", "skip_default": "   "},
        ],
    }))
    body = t.client.post(
        "/v1/prd/clarify-task", json={"task": "build a dashboard"}
    ).json()
    assert body["questions"][0]["skip_default"] == "all end users"
    assert body["questions"][1]["skip_default"] is None

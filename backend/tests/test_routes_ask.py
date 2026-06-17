"""Tests for app.routes.ask — POST /v1/ask + GET /v1/ask/{ask_id}.

The Ask flow is fire-and-forget (blur/remount-safe), mirroring PRD/evidence:
POST persists a `generating` row in `ask_jobs` and kicks `qa_agent.answer(...)`
in a background task, returning `{ask_id, status}`; the client polls
GET /v1/ask/{ask_id} for the answer. A cache hit is persisted onto an
immediately-`ready` job so the POST contract is uniform; the user-visible
result is identical to the old synchronous body.

The route sits behind `require_company` and the `dataset` slug must resolve to
the caller's company — otherwise an arbitrary client slug would seed a FOREIGN
company's corpus into the LLM answer.

Key behaviours covered:
- auth gate (no session → 401)
- foreign / unowned dataset → 404 (corpus-leak denial)
- POST returns an ask_id + persists a generating job; the worker fills the
  answer; GET walks generating → ready with the SAME citation-stripped shape
- cache hit → ready job carrying the cached payload, no LLM call
- a worker failure marks the job `error` and never crashes
- GET for a foreign / nonexistent ask_id → 404
"""
from __future__ import annotations

import json
import time

from app import db
from app.routes import ask as ask_route


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _poll_ask(client, ask_id, *, timeout=5.0):
    """Poll GET /v1/ask/{id} until the job leaves `generating` (the worker runs
    on the TestClient's event loop, so the answer lands within a tick or two)."""
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/ask/{ask_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "generating":
            return body
        time.sleep(0.02)
    return body


# ---- auth + tenant gate -----------------------------------------------------

def test_ask_without_session_returns_401(unauth_client, isolated_settings):
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    resp = unauth_client.post(
        "/v1/ask",
        json={"question": "What is the biggest churn driver?", "dataset": "acme"},
    )
    assert resp.status_code == 401


def test_ask_foreign_dataset_returns_404(tenant_client, isolated_settings):
    """Ask must reject a dataset slug that isn't the caller's — no corpus leak."""
    tenant_client.make(slug="company-a")
    _seed_corpus(isolated_settings["data_dir"], dataset="company-a")
    b = tenant_client.make(slug="company-b")
    resp = b.client.post(
        "/v1/ask",
        json={"question": "Tell me A's secrets?", "dataset": "company-a"},
    )
    assert resp.status_code == 404


def test_ask_unowned_dataset_returns_404(tenant_client, isolated_settings):
    """A slug that maps to no company at all is also 404 (fail closed)."""
    t = tenant_client.make(slug="acme")
    resp = t.client.post(
        "/v1/ask", json={"question": "What about nobody?", "dataset": "ghost"}
    )
    assert resp.status_code == 404


# ---- fire-and-forget contract (cache miss → worker) -------------------------

def test_ask_returns_ask_id_and_persists_generating_job(
    tenant_client, isolated_settings, fake_llm
):
    """POST returns {ask_id, status} and persists a job row for the caller."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "## Finding\n\nThe answer.",
        "key_points": ["k1"],
        "citations": [{"source": "a", "evidence": "x"}],
        "confidence": 0.9,
        "unanswered": "",
    }
    resp = t.client.post(
        "/v1/ask",
        json={"question": "What is the biggest churn driver?", "dataset": "acme"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["ask_id"], int)
    assert body["status"] in ("generating", "ready")
    # The job row exists and belongs to the caller's company.
    row = db.get_ask_job(body["ask_id"])
    assert row is not None
    assert row["company_id"] == t.company_id


def test_ask_worker_fills_answer_and_get_returns_old_shape(
    tenant_client, isolated_settings, fake_llm
):
    """The worker runs qa_agent.answer; GET walks generating → ready and returns
    the SAME citation-stripped shape the old synchronous POST returned."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "## Finding\n\nThe answer.",
        "key_points": ["k1"],
        "citations": [{"source": "a", "evidence": "x"}],
        "confidence": 0.9,
        "unanswered": "",
    }
    start = t.client.post(
        "/v1/ask",
        json={"question": "What is the biggest churn driver?", "dataset": "acme"},
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    assert body["answer"] == "## Finding\n\nThe answer."
    # Citations stripped on the way out (same as the old endpoint).
    assert body["citations"] == []
    assert body["key_points"] == ["k1"]
    assert body["confidence"] == 0.9


def test_ask_cache_miss_invokes_fake_llm_exactly_once(
    tenant_client, isolated_settings, fake_llm
):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }
    start = t.client.post(
        "/v1/ask", json={"question": "Some unique question?", "dataset": "acme"}
    ).json()
    _poll_ask(t.client, start["ask_id"])
    assert len(fake_llm["calls"]) == 1


def test_ask_short_question_is_rejected(tenant_client, isolated_settings):
    """Pydantic min_length=3 — anything shorter is a validation error."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    resp = t.client.post("/v1/ask", json={"question": "hi", "dataset": "acme"})
    assert resp.status_code == 422


# ---- cache hit path ---------------------------------------------------------

def test_ask_cache_hit_returns_ready_job_without_llm_call(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    monkeypatch.setattr(ask_route, "CACHE_HIT_DELAY_MIN_SECONDS", 0.0)
    monkeypatch.setattr(ask_route, "CACHE_HIT_DELAY_MAX_SECONDS", 0.0)

    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    question = "What are the biggest revenue drivers"
    cached_payload = {
        "answer": "**Cached answer**", "key_points": ["pre-warmed"],
        "citations": [], "confidence": 1.0, "unanswered": "",
    }
    cache_id = db.start_cached_ask(dataset="acme", question=question)
    db.complete_cached_ask(cache_id, json.dumps(cached_payload))

    fake_llm["calls"].clear()
    resp = t.client.post("/v1/ask", json={"question": question, "dataset": "acme"})
    assert resp.status_code == 200
    start = resp.json()
    # Cache hits resolve to a ready job immediately (no worker, no LLM).
    assert start["status"] == "ready"
    body = t.client.get(f"/v1/ask/{start['ask_id']}").json()
    assert body["status"] == "ready"
    assert body["answer"] == "**Cached answer**"
    assert body["key_points"] == ["pre-warmed"]
    assert fake_llm["calls"] == []


# ---- worker failure ---------------------------------------------------------

def test_ask_worker_failure_marks_error_and_does_not_crash(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A failure inside the answer pipeline marks the job `error` (best-effort)
    and the worker never propagates the exception."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    import app.qa_agent as qa_agent_mod

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("kaboom")

    monkeypatch.setattr(qa_agent_mod, "answer", _boom)

    start = t.client.post(
        "/v1/ask", json={"question": "A question that explodes?", "dataset": "acme"}
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "error"
    assert "kaboom" in (body["error"] or "")


# ---- GET status auth/ownership ----------------------------------------------

def test_get_ask_nonexistent_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/ask/999999")
    assert resp.status_code == 404


def test_get_ask_foreign_job_returns_404(tenant_client, isolated_settings, fake_llm):
    """A job belonging to another company is not readable (404, no disclosure)."""
    a = tenant_client.make(slug="company-a")
    _seed_corpus(isolated_settings["data_dir"], dataset="company-a")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }
    start = a.client.post(
        "/v1/ask", json={"question": "A's private question?", "dataset": "company-a"}
    ).json()
    b = tenant_client.make(slug="company-b")
    resp = b.client.get(f"/v1/ask/{start['ask_id']}")
    assert resp.status_code == 404

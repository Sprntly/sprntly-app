"""Tests for app.routes.ask — POST /v1/ask.

After the tenant-isolation fix the route sits behind `require_company` and the
`dataset` slug must resolve to the caller's company — otherwise an arbitrary
client slug would seed a FOREIGN company's corpus into the LLM answer.

Key behaviours covered:
- auth gate (no session → 401)
- foreign dataset → 404 (corpus-leak denial)
- happy path: cache miss → LLM called → answer returned + citations stripped
- cache-hit short-circuit returns the cached payload
"""
from __future__ import annotations

import json

from app import db
from app.routes import ask as ask_route


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


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


# ---- happy path (cache miss → LLM) ------------------------------------------

def test_ask_cache_miss_calls_llm_and_returns_answer(
    tenant_client, isolated_settings, fake_llm
):
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
    assert body["answer"] == "## Finding\n\nThe answer."
    assert body["citations"] == []


def test_ask_cache_miss_invokes_fake_llm_exactly_once(
    tenant_client, isolated_settings, fake_llm
):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }
    t.client.post(
        "/v1/ask", json={"question": "Some unique question?", "dataset": "acme"}
    )
    assert len(fake_llm["calls"]) == 1


def test_ask_short_question_is_rejected(tenant_client, isolated_settings):
    """Pydantic min_length=3 — anything shorter is a validation error."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    resp = t.client.post("/v1/ask", json={"question": "hi", "dataset": "acme"})
    assert resp.status_code == 422


# ---- cache hit path ---------------------------------------------------------

def test_ask_cache_hit_returns_without_llm_call(
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
    assert resp.json()["answer"] == "**Cached answer**"
    assert fake_llm["calls"] == []

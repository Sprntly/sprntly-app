"""Tests for app.routes.ask — POST /v1/ask.

Key behaviours covered:
- auth gate (no cookie → 401)
- happy path: cache miss → LLM called → answer returned + citations stripped
- cache-hit short-circuit returns the cached payload (with a synthetic delay
  we patch out to keep the test fast)
"""
from __future__ import annotations

import json

import pytest

from app import db
from app.routes import ask as ask_route


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    """Drop a one-file dataset under DATA_DIR so load_corpus() succeeds."""
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


# ---- auth gate --------------------------------------------------------------

def test_ask_without_session_returns_401(unauth_client, isolated_settings):
    _seed_corpus(isolated_settings["data_dir"])
    resp = unauth_client.post(
        "/v1/ask", json={"question": "What is the biggest churn driver?", "dataset": "asurion"}
    )
    assert resp.status_code == 401


# ---- happy path (cache miss → LLM) ------------------------------------------

def test_ask_cache_miss_calls_llm_and_returns_answer(
    app_client, isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "## Finding\n\nThe answer.",
        "key_points": ["k1"],
        "citations": [{"source": "a", "evidence": "x"}],
        "confidence": 0.9,
        "unanswered": "",
    }
    resp = app_client.post(
        "/v1/ask", json={"question": "What is the biggest churn driver?", "dataset": "asurion"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert body["answer"] == "## Finding\n\nThe answer."
    # Citations are always stripped before being returned to the UI.
    assert body["citations"] == []


def test_ask_cache_miss_invokes_fake_llm_exactly_once(
    app_client, isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "x",
        "key_points": [],
        "citations": [],
        "confidence": 0.5,
        "unanswered": "",
    }
    app_client.post("/v1/ask", json={"question": "Some unique question?", "dataset": "asurion"})
    assert len(fake_llm["calls"]) == 1


def test_ask_short_question_is_rejected(app_client, isolated_settings):
    """Pydantic min_length=3 — anything shorter is a validation error."""
    _seed_corpus(isolated_settings["data_dir"])
    resp = app_client.post("/v1/ask", json={"question": "hi", "dataset": "asurion"})
    assert resp.status_code == 422


# ---- cache hit path ---------------------------------------------------------

def test_ask_cache_hit_returns_without_llm_call(
    app_client, isolated_settings, fake_llm, monkeypatch
):
    """If a ready cached_asks row exists, the route returns it and never calls
    the LLM. We zero the synthetic delay so the test stays fast."""
    monkeypatch.setattr(ask_route, "CACHE_HIT_DELAY_MIN_SECONDS", 0.0)
    monkeypatch.setattr(ask_route, "CACHE_HIT_DELAY_MAX_SECONDS", 0.0)

    _seed_corpus(isolated_settings["data_dir"])
    question = "What are the biggest revenue drivers"
    cached_payload = {
        "answer": "**Cached answer**",
        "key_points": ["pre-warmed"],
        "citations": [],
        "confidence": 1.0,
        "unanswered": "",
    }
    cache_id = db.start_cached_ask(dataset="asurion", question=question)
    db.complete_cached_ask(cache_id, json.dumps(cached_payload))

    fake_llm["calls"].clear()
    resp = app_client.post("/v1/ask", json={"question": question, "dataset": "asurion"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "**Cached answer**"
    # Cache hit → LLM must not be called.
    assert fake_llm["calls"] == []

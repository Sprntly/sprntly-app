"""Cost/usage tracking for OpenAI embeddings (app.graph.embeddings).

Embeddings were the one LLM call site whose spend was untracked. These tests
pin: (1) the model is priced in MODEL_PRICING, (2) a successful embed captures
OpenAI's usage.prompt_tokens and writes a per-tenant, per-feature row to the
same audit spine (log_agent_decision) every Anthropic call uses, (3) telemetry
never breaks embedding, and (4) the no-key fallback records nothing.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.request

import pytest

import app.graph.embeddings as emb
from app.llm_telemetry import MODEL_PRICING, RunUsage


class _FakeResp:
    """Minimal context-manager stand-in for urlopen's return value."""

    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *exc):
        return False


def _openai_payload(n_vectors: int, prompt_tokens: int) -> dict:
    return {
        "data": [{"embedding": [0.1] * emb.EMBEDDING_DIM} for _ in range(n_vectors)],
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


@pytest.fixture
def _with_key(monkeypatch):
    monkeypatch.setattr(emb.settings, "openai_api_key", "sk-test", raising=False)


# ── pricing ──────────────────────────────────────────────────────────────────


def test_embedding_model_is_priced():
    assert emb.EMBEDDING_MODEL in MODEL_PRICING
    # input-only: 1M tokens of text-embedding-3-small ≈ $0.02.
    cost = RunUsage(input_tokens=1_000_000).est_cost_usd(emb.EMBEDDING_MODEL)
    assert cost == pytest.approx(0.02)
    # no output / no cache billing for embeddings
    p = MODEL_PRICING[emb.EMBEDDING_MODEL]
    assert p["output"] == 0.0 and p["cache_read"] == 0.0 and p["cache_write_1h"] == 0.0


# ── per-tenant + per-feature persistence ──────────────────────────────────────


def test_embed_logs_per_tenant_per_feature(monkeypatch, _with_key):
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(2, 1_000_000)),
    )

    out = emb.embed_texts(["a", "b"], enterprise_id="ent-123", purpose="kg_extract")

    assert len(out) == 2
    assert len(calls) == 1
    kw = calls[0]
    assert kw["enterprise_id"] == "ent-123"
    assert kw["agent"] == "embeddings"
    assert kw["decision_type"] == "embedding"
    assert kw["model"] == emb.EMBEDDING_MODEL
    assert kw["factors"]["purpose"] == "kg_extract"
    assert kw["factors"]["input_tokens"] == 1_000_000
    assert kw["factors"]["cost_usd"] == pytest.approx(0.02)


def test_embed_without_enterprise_id_skips_db_row(monkeypatch, _with_key):
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(1, 42)),
    )

    # No enterprise_id → still embeds, but writes no per-tenant row.
    out = emb.embed_texts(["a"])
    assert len(out) == 1
    assert calls == []


def test_embed_emits_grep_cost_line(monkeypatch, _with_key, caplog):
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision", lambda **kw: None
    )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(1, 1_000_000)),
    )
    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        emb.embed_texts(["a"], enterprise_id="ent-9", purpose="kg_retrieval")
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "embeddings.embed" in line
    assert "est_cost_usd=0.0200" in line
    assert "purpose=kg_retrieval" in line


# ── robustness ────────────────────────────────────────────────────────────────


def test_logging_failure_never_breaks_embedding(monkeypatch, _with_key):
    def _boom(**kw):
        raise RuntimeError("audit down")

    monkeypatch.setattr("app.graph.decision_log.log_agent_decision", _boom)
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(1, 5)),
    )
    # Embedding must still succeed despite the audit-write blowing up.
    out = emb.embed_texts(["a"], enterprise_id="ent-1", purpose="kg_extract")
    assert len(out) == 1 and len(out[0]) == emb.EMBEDDING_DIM


def test_missing_usage_object_is_tolerated(monkeypatch, _with_key):
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: calls.append(kw),
    )
    payload = {"data": [{"embedding": [0.0] * emb.EMBEDDING_DIM}]}  # no "usage"
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    out = emb.embed_texts(["a"], enterprise_id="ent-1", purpose="kg_extract")
    assert len(out) == 1
    assert calls[0]["factors"]["input_tokens"] == 0
    assert calls[0]["factors"]["cost_usd"] == 0.0


def test_no_api_key_records_nothing(monkeypatch):
    monkeypatch.setattr(emb.settings, "openai_api_key", "", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: calls.append(kw),
    )
    out = emb.embed_texts(["a"], enterprise_id="ent-1", purpose="kg_extract")
    assert out == [[0.0] * emb.EMBEDDING_DIM]  # zero-vector fallback
    assert calls == []  # no real call, no spend, no row

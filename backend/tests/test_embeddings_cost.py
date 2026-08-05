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


# ── input bounding ──────────────────────────────────────────────────────────


def _indexed_payload(n: int) -> dict:
    """A response whose i-th vector's first float is `i`, so positional order
    is verifiable rather than merely counted."""
    return {
        "data": [{"embedding": [float(i)] + [0.0] * (emb.EMBEDDING_DIM - 1)}
                  for i in range(n)],
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


def test_embed_texts_truncates_input_over_the_char_bound(monkeypatch, _with_key):
    calls: list = []

    def _fake(req, timeout=None):
        calls.append(req)
        return _FakeResp(_openai_payload(1, 1))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    huge = "x" * 30_000

    emb.embed_texts([huge])

    sent = json.loads(calls[0].data)["input"]
    assert len(sent) == 1
    assert len(sent[0]) == 24_000


# The real API's per-input token ceiling (8192 tokens), expressed in the
# module's own 4-chars/token approximation — used ONLY to make this fake
# stand-in behave like the real OpenAI endpoint (reject an oversized single
# input with HTTP 400), not to duplicate the module's own bound.
_REAL_API_CHAR_CEILING = 8192 * 4  # 32_768


def test_embed_texts_survives_a_composer_ceiling_question(monkeypatch, _with_key):
    calls: list = []

    def _fake(req, timeout=None):
        calls.append(req)
        payload = json.loads(req.data)
        if any(len(t) > _REAL_API_CHAR_CEILING for t in payload["input"]):
            raise urllib.error.HTTPError(
                emb._URL, 400, "Bad Request", {}, io.BytesIO(b""),
            )
        return _FakeResp(_openai_payload(len(payload["input"]), 1))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)

    out = emb.embed_texts(["q" * 100_000])

    assert len(out) == 1
    assert len(out[0]) == emb.EMBEDDING_DIM
    assert len(calls) == 1  # not retried on 400 — AC5


def test_embed_texts_leaves_input_at_the_exact_bound_untouched(
    monkeypatch, _with_key, caplog,
):
    calls: list = []

    def _fake(req, timeout=None):
        calls.append(req)
        return _FakeResp(_openai_payload(1, 1))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    exact = "y" * emb._MAX_EMBED_CHARS

    with caplog.at_level(logging.WARNING, logger="app.graph.embeddings"):
        emb.embed_texts([exact])

    sent = json.loads(calls[0].data)["input"]
    assert sent[0] == exact
    assert not any("truncated" in r.getMessage() for r in caplog.records)


def test_embed_texts_truncates_only_the_oversized_batch_member(monkeypatch, _with_key):
    calls: list = []

    def _fake(req, timeout=None):
        calls.append(req)
        return _FakeResp(_openai_payload(2, 1))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    huge = "a" * 30_000
    small = "b" * 100

    emb.embed_texts([huge, small])

    sent = json.loads(calls[0].data)["input"]
    assert len(sent[0]) == emb._MAX_EMBED_CHARS
    assert sent[1] == small  # byte-identical, untouched


def test_embed_texts_preserves_length_and_order_after_truncation(monkeypatch, _with_key):
    def _fake(req, timeout=None):
        n = len(json.loads(req.data)["input"])
        return _FakeResp(_indexed_payload(n))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    texts = ["c" * 30_000, "small-one", "d" * 25_000]

    out = emb.embed_texts(texts)

    assert len(out) == len(texts)
    for i, vec in enumerate(out):
        assert vec[0] == float(i)  # positional alignment preserved


def test_embed_texts_empty_list_makes_no_call_and_no_log(monkeypatch, _with_key, caplog):
    calls: list = []
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: calls.append(1),
    )

    with caplog.at_level(logging.WARNING):
        out = emb.embed_texts([])

    assert out == []
    assert calls == []
    assert caplog.records == []


def test_embed_texts_no_key_fallback_does_not_log_truncation(monkeypatch, caplog):
    monkeypatch.setattr(emb.settings, "openai_api_key", "", raising=False)

    with caplog.at_level(logging.WARNING, logger="app.graph.embeddings"):
        out = emb.embed_texts(["z" * 100_000])

    assert len(out) == 1
    assert len(out[0]) == emb.EMBEDDING_DIM
    assert not any("truncated" in r.getMessage() for r in caplog.records)


# ── error handling ───────────────────────────────────────────────────────────


def test_embed_texts_does_not_retry_http_400(monkeypatch, _with_key):
    calls: list = []

    def _fake(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(emb._URL, 400, "Bad Request", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)

    with pytest.raises(urllib.error.HTTPError):
        emb.embed_texts(["a"])

    assert len(calls) == 1


def test_embed_texts_logs_diagnosable_context_on_http_400(monkeypatch, _with_key, caplog):
    def _fake(req, timeout=None):
        raise urllib.error.HTTPError(emb._URL, 400, "Bad Request", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)

    with caplog.at_level(logging.WARNING, logger="app.graph.embeddings"):
        with pytest.raises(urllib.error.HTTPError):
            emb.embed_texts(["short", "a" * 50], enterprise_id="ent-42", purpose="kg_extract")

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "ent-42" in line
    assert "kg_extract" in line
    assert "max_element_chars=50" in line


# ── observability ────────────────────────────────────────────────────────────


def test_truncation_log_carries_identifiers_and_lengths_only(monkeypatch, _with_key, caplog):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(1, 1)),
    )
    huge = "e" * 30_000

    with caplog.at_level(logging.WARNING, logger="app.graph.embeddings"):
        emb.embed_texts([huge], enterprise_id="ent-7", purpose="kg_retrieval")

    records = [r for r in caplog.records if "truncated" in r.getMessage()]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "ent-7" in msg
    assert "kg_retrieval" in msg
    assert "index=0" in msg
    assert "original_chars=30000" in msg
    assert "truncated_to=24000" in msg


def test_truncation_log_never_contains_input_text(monkeypatch, _with_key, caplog):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(_openai_payload(1, 1)),
    )
    sentinel = "UNIQUE_SENTINEL_TOKEN_7f3a9c"
    huge = sentinel + ("f" * 30_000)

    with caplog.at_level(logging.WARNING, logger="app.graph.embeddings"):
        emb.embed_texts([huge])

    for r in caplog.records:
        assert sentinel not in r.getMessage()

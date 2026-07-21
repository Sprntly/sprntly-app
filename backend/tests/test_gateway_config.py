"""Tests for the LLM gateway (S2) + 4-layer config (S5)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------- llm retry layer ----------

def _msg(text="ok"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_creation_input_tokens=0,
                              cache_read_input_tokens=2),
        stop_reason="end_turn",
    )


def test_create_with_retries_retries_transient_then_succeeds(isolated_settings, monkeypatch):
    import anthropic
    from app import llm

    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise anthropic.APIConnectionError(request=None)
            return _msg()

    monkeypatch.setattr(llm, "_BACKOFF_BASE_S", 0.001)
    client = SimpleNamespace(messages=FakeMessages())
    out = llm._create_with_retries(client, model="m")
    assert calls["n"] == 3
    assert out.stop_reason == "end_turn"


def test_create_with_retries_gives_up_after_max(isolated_settings, monkeypatch):
    import anthropic
    from app import llm

    class FakeMessages:
        def create(self, **kw):
            raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(llm, "_BACKOFF_BASE_S", 0.001)
    with pytest.raises(anthropic.APIConnectionError):
        llm._create_with_retries(SimpleNamespace(messages=FakeMessages()), model="m")


def test_non_retryable_error_raises_immediately(isolated_settings, monkeypatch):
    from app import llm

    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kw):
            calls["n"] += 1
            raise ValueError("schema problem")  # not transient

    with pytest.raises(ValueError):
        llm._create_with_retries(SimpleNamespace(messages=FakeMessages()), model="m")
    assert calls["n"] == 1


# ---------- gateway ----------

def test_llm_call_returns_result_and_logs_telemetry(isolated_settings, monkeypatch):
    from app import llm
    from app.graph.gateway import llm_call

    monkeypatch.setattr(
        llm, "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg("hello"))),
    )
    r = llm_call(
        enterprise_id="ent-A", agent="synthesis", purpose="test",
        prompt_version="v1", system="sys", input="hi",
    )
    assert r.output == "hello"
    assert r.model == "claude-sonnet-4-6"
    assert r.input_tokens == 10 and r.output_tokens == 5
    assert r.cost_usd > 0
    assert r.latency_ms >= 0

    rows = isolated_settings["supabase"].table("agent_decision_log") \
        .select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    assert rows[0]["decision_type"] == "llm_call"
    assert rows[0]["factors"]["purpose"] == "test"
    assert rows[0]["prompt_version"] == "v1"
    # Cache accounting rides in factors — BOTH sides. cache_creation makes a
    # fleet racing one shared prefix visible (all cache_read=0 + creation>0).
    assert "cache_read_input_tokens" in rows[0]["factors"]
    assert "cache_creation_input_tokens" in rows[0]["factors"]


def test_llm_call_log_failure_does_not_break_call(isolated_settings, monkeypatch):
    from app import llm
    from app.graph import gateway

    monkeypatch.setattr(
        llm, "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg("ok"))),
    )
    with patch("app.graph.decision_log.log_agent_decision", side_effect=RuntimeError("db down")):
        r = gateway.llm_call(
            enterprise_id="ent-A", agent="synthesis", purpose="x",
            prompt_version="v1", system="s", input="u",
        )
    assert r.output == "ok"   # primary flow survived the audit failure


# ---------- 4-layer config ----------

def test_platform_defaults_resolve(isolated_settings):
    from app.graph.config_layers import resolve_config

    cfg = resolve_config()
    assert cfg["resolution"]["tau_high"] == 0.86
    assert cfg["staleness"]["windows_days"]["communication"] == 7
    assert cfg["staleness"]["windows_days"]["outcome_measured"] is None
    assert cfg["llm"]["embedding_model"] == "text-embedding-3-small"
    assert cfg["oncall"]["trigger"]["metric_zscore"] == 3.0


def test_enterprise_override_deep_merges(isolated_settings):
    from app.graph.config_layers import resolve_config

    isolated_settings["supabase"].table("enterprise_config").insert({
        "enterprise_id": "ent-A",
        "overrides": {"oncall": {"trigger": {"pct_drop": 0.10}}},
    }).execute()
    cfg = resolve_config(enterprise_id="ent-A")
    assert cfg["oncall"]["trigger"]["pct_drop"] == 0.10      # overridden
    assert cfg["oncall"]["trigger"]["metric_zscore"] == 3.0  # sibling preserved
    assert cfg["oncall"]["cooldown_hours"] == 24             # parent preserved


def test_no_override_row_falls_back_to_defaults(isolated_settings):
    from app.graph.config_layers import resolve_config

    cfg = resolve_config(enterprise_id="ent-without-row")
    assert cfg["feedback"]["ignored_after_days"] == 21


def test_config_get_dotted_path(isolated_settings):
    from app.graph.config_layers import config_get

    assert config_get("resolution.tau_low") == 0.72
    assert config_get("nope.nothing", default="d") == "d"


def test_goal_factor_config_keys_default(isolated_settings):
    from app.graph.config_layers import config_get

    assert config_get("scoring.goal_factor_enabled") is True
    assert config_get("scoring.goal_weight") == 1.0

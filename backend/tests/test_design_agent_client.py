"""Tests for app.design_agent.client.get_design_agent_client (AD16).

Covers the primary DESIGN_AGENT_ANTHROPIC_API_KEY read, the local-dev
fallback to ANTHROPIC_API_KEY (with a one-shot warning), the no-key
HTTPException(500), caching identity, and the key-value redaction
guarantee. No network is touched — `Anthropic(api_key=...)` only stores
the key; the HTTP connection pool is lazy.
"""
from __future__ import annotations

import importlib
import logging

import pytest
from anthropic import Anthropic
from fastapi import HTTPException

from app.design_agent import client as client_mod


@pytest.fixture(autouse=True)
def _reset_client_and_keys(monkeypatch):
    """Each test starts with a cleared client cache + warning state and both
    keys unset; tests opt into whichever key config they exercise.

    `client_mod.settings` is the same singleton as `app.config.settings`
    (imported via `from app.config import settings`), so patching attributes
    on it is what the factory reads at call time.
    """
    client_mod.reset_design_agent_client()
    monkeypatch.setattr(client_mod.settings, "design_agent_anthropic_api_key", "")
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "")
    yield
    client_mod.reset_design_agent_client()


def _warning_records(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "DESIGN_AGENT_ANTHROPIC_API_KEY" in r.message
    ]


# ---- creation ---------------------------------------------------------------

def test_returns_client_when_design_agent_key_set(monkeypatch, caplog):
    monkeypatch.setattr(
        client_mod.settings, "design_agent_anthropic_api_key", "sk-test"
    )
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        c = client_mod.get_design_agent_client()
    assert isinstance(c, Anthropic)
    # Primary-key path must NOT emit the fallback warning.
    assert _warning_records(caplog) == []


def test_caches_client_across_calls(monkeypatch):
    monkeypatch.setattr(
        client_mod.settings, "design_agent_anthropic_api_key", "sk-test"
    )
    a = client_mod.get_design_agent_client()
    b = client_mod.get_design_agent_client()
    assert a is b


def test_client_disables_sdk_own_retry_layer(monkeypatch):
    """The SDK's own opaque retry (max_retries=2 default, no callback hook)
    must be disabled — agent_loop's own retry loop (runner.py) is the single
    source of truth for retry-on-transient-failure, mirroring app.llm's
    identical _client_for_key precedent."""
    monkeypatch.setattr(
        client_mod.settings, "design_agent_anthropic_api_key", "sk-test"
    )
    c = client_mod.get_design_agent_client()
    assert c.max_retries == 0


# ---- fallback handling ------------------------------------------------------

def test_falls_back_to_anthropic_key_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(client_mod.settings, "design_agent_anthropic_api_key", "")
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "sk-fallback")
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        c = client_mod.get_design_agent_client()
    assert isinstance(c, Anthropic)
    warnings = _warning_records(caplog)
    assert len(warnings) == 1
    assert "DESIGN_AGENT_ANTHROPIC_API_KEY" in warnings[0].message


def test_fallback_warning_emitted_once_per_process(monkeypatch, caplog):
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "sk-fallback")
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        for _ in range(5):
            client_mod.get_design_agent_client()
    assert len(_warning_records(caplog)) == 1


# ---- error handling ---------------------------------------------------------

def test_raises_http_500_when_no_key_set():
    with pytest.raises(HTTPException) as excinfo:
        client_mod.get_design_agent_client()
    assert excinfo.value.status_code == 500
    detail = excinfo.value.detail
    assert "DESIGN_AGENT_ANTHROPIC_API_KEY" in detail
    assert "ANTHROPIC_API_KEY" in detail


def test_no_raise_at_import_time():
    """Reloading the module with both keys empty must not raise — config
    errors surface at first call (request time), never at import time."""
    importlib.reload(client_mod)
    # Re-clear state on the freshly reloaded module for subsequent tests.
    client_mod.reset_design_agent_client()


# ---- edge cases -------------------------------------------------------------

def test_whitespace_only_key_treated_as_unset(monkeypatch, caplog):
    monkeypatch.setattr(
        client_mod.settings, "design_agent_anthropic_api_key", "   "
    )
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "sk-fallback")
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        c = client_mod.get_design_agent_client()
    assert isinstance(c, Anthropic)
    assert len(_warning_records(caplog)) == 1


def test_reset_clears_cache_and_warning_state(monkeypatch, caplog):
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "sk-fallback")
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        client_mod.get_design_agent_client()
        assert len(_warning_records(caplog)) == 1
        # After reset, the warning state is cleared so the next fallback
        # emits the warning again.
        client_mod.reset_design_agent_client()
        client_mod.get_design_agent_client()
    assert len(_warning_records(caplog)) == 2


def test_warning_log_redacts_key_value(monkeypatch, caplog):
    monkeypatch.setattr(client_mod.settings, "anthropic_api_key", "sk-fallback")
    with caplog.at_level(logging.WARNING, logger="app.design_agent.client"):
        client_mod.get_design_agent_client()
    record = _warning_records(caplog)[0]
    # The warning names the env var only — never the key value.
    assert "sk-fallback" not in record.message

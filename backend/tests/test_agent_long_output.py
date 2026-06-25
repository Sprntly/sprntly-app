"""Regression tests for long-output LLM routing in the multi-agent doc pipeline.

The PRD "generate full system" sub-agents (technical design, risk analysis,
traceability matrix, QA test cases) produce large markdown docs. They were
calling the gateway on the DEFAULT 120s non-streamed path and tripping
`httpx.ReadTimeout` in production. The fix: `llm_call(long_output=True, ...)`
streams the response on the 600s long read timeout. These tests pin that the
gateway honours the flag AND that every large-doc agent sets it — so a future
edit dropping it fails CI instead of silently re-introducing the timeout.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest


# ── gateway routing: long_output → stream on the long read timeout ────────────


def test_llm_call_long_output_streams_on_long_timeout(monkeypatch):
    from app.graph import gateway
    from app.llm import LONG_REQUEST_TIMEOUT_S

    captured: dict = {}

    def fake_call_md(*, meta_out, stream, timeout, **kw):
        captured["stream"] = stream
        captured["timeout"] = timeout
        meta_out.update(
            model=kw.get("model", "m"), input_tokens=1, output_tokens=1,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
            stop_reason="end_turn",
        )
        return "doc"

    monkeypatch.setattr(gateway, "call_md", fake_call_md)

    # Default caller → non-streamed, default per-request timeout.
    gateway.llm_call(
        enterprise_id="e", agent="a", purpose="p", prompt_version="v1",
        system="s", input="i", log=False,
    )
    assert captured == {"stream": False, "timeout": None}

    # long_output=True → streamed on the long read timeout.
    gateway.llm_call(
        enterprise_id="e", agent="a", purpose="p", prompt_version="v1",
        system="s", input="i", long_output=True, log=False,
    )
    assert captured["stream"] is True
    assert captured["timeout"] == LONG_REQUEST_TIMEOUT_S


# ── per-agent guard: every large-doc agent sets long_output=True ──────────────

_AGENTS = [
    ("app.agents.technical_design", "generate_technical_design_sync"),
    ("app.agents.risk_analysis", "generate_risk_analysis_sync"),
    ("app.agents.traceability_matrix", "generate_traceability_matrix_sync"),
    ("app.agents.qa_test_cases", "generate_qa_test_cases_sync"),
]


def _call_with_filled_args(fn, prd):
    """Call a *_sync generator filling each param: enterprise_id/prd get real
    values, every other (markdown context) arg gets an empty string."""
    kwargs = {}
    for name, p in inspect.signature(fn).parameters.items():
        if name == "enterprise_id":
            kwargs[name] = "ent-1"
        elif name == "prd":
            kwargs[name] = prd
        elif p.default is not inspect.Parameter.empty:
            continue  # leave optional args at their default
        else:
            kwargs[name] = ""
    return fn(**kwargs)


@pytest.mark.parametrize("module_path,fn_name", _AGENTS)
def test_doc_agent_sets_long_output(monkeypatch, module_path, fn_name):
    import importlib

    mod = importlib.import_module(module_path)
    captured: dict = {}

    def fake_llm_call(**kw):
        captured.update(kw)
        return SimpleNamespace(
            output="# doc", model="m", prompt_version="v1",
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
            stop_reason="end_turn",
        )

    # Patch the name bound INSIDE the agent module (it does `from ... import llm_call`).
    monkeypatch.setattr(mod, "llm_call", fake_llm_call)

    _call_with_filled_args(getattr(mod, fn_name), {"id": 1, "title": "T"})

    assert captured.get("long_output") is True, (
        f"{module_path} must call llm_call(long_output=True) so its large doc "
        f"streams on the long read timeout"
    )

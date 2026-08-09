"""Unit tests for the shared LLM cost-summary primitive (P1-04).

`app.llm_telemetry` is pure-Python (stdlib logging + dataclasses) and
unit-testable in isolation, with no live backend. Tests follow the
existing `caplog.at_level(..., logger="app.<module>")` convention from
test_design_agent_client.py.
"""
from __future__ import annotations

import logging
import types

import pytest

from app.llm_telemetry import (
    MODEL_PRICING,
    RunUsage,
    UnknownModelError,
    log_llm_run,
)

TELEMETRY_LOGGER = "app.llm_telemetry"


def _record_message(caplog) -> str:
    records = [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(records) == 1, f"expected exactly one telemetry record, got {len(records)}"
    return records[0].getMessage()


# ─── Exports ──────────────────────────────────────────────────────────────


def test_module_exports_canonical_primitive():
    assert "claude-sonnet-4-6" in MODEL_PRICING
    assert "claude-opus-4-7" in MODEL_PRICING  # AD2 escape hatch
    assert "claude-haiku-4-5" in MODEL_PRICING  # ROUTER_MODEL
    assert issubclass(UnknownModelError, KeyError)


# ─── Haiku pricing (router spend) ───────────────────────────────────────────


def test_haiku_row_matches_published_rates():
    """Anthropic's pricing page (2026-08): $1/MTok in, $5/MTok out, $0.10/MTok
    cache hits. Cache WRITE is the 5-minute tier (1.25x base input) because
    `app/llm.py::_build_base_kwargs` only ever sends `{"type": "ephemeral"}`
    with no `ttl` — the 1h tier this dict's key name implies is never requested.
    See the naming-mismatch note above MODEL_PRICING."""
    p = MODEL_PRICING["claude-haiku-4-5"]
    assert p["input"] == pytest.approx(1.0 / 1_000_000)
    assert p["output"] == pytest.approx(5.0 / 1_000_000)
    assert p["cache_read"] == pytest.approx(0.1 / 1_000_000)
    assert p["cache_write_1h"] == pytest.approx(1.25 / 1_000_000)
    # 0.1x and 1.25x of base input — the published multipliers.
    assert p["cache_read"] == pytest.approx(p["input"] * 0.1)
    assert p["cache_write_1h"] == pytest.approx(p["input"] * 1.25)


def test_haiku_usage_costs_more_than_zero():
    """`RunUsage.est_cost_usd` prices a haiku run instead of raising."""
    usage = RunUsage(
        cache_creation_input_tokens=1_000,
        cache_read_input_tokens=10_000,
        input_tokens=2_000,
        output_tokens=500,
    )
    cost = usage.est_cost_usd("claude-haiku-4-5")
    expected = (
        1_000 * 1.25 / 1_000_000
        + 10_000 * 0.1 / 1_000_000
        + 2_000 * 1.0 / 1_000_000
        + 500 * 5.0 / 1_000_000
    )
    assert cost == pytest.approx(expected)
    assert cost > 0


def test_gateway_est_cost_prices_the_router_instead_of_failing_open():
    """The defect this row fixes. `gateway._est_cost` does
    `MODEL_PRICING.get(model)` and returns 0.0 on a miss — it fails OPEN, unlike
    `est_cost_usd` which raises UnknownModelError. With no haiku row, every
    `qa-router` entry in `agent_decision_log` recorded cost_usd=0.0, so router
    spend looked free rather than unmeasured."""
    from app.graph.gateway import _est_cost

    meta = {
        "model": "claude-haiku-4-5",
        "input_tokens": 2_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 10_000,
        "cache_creation_input_tokens": 1_000,
    }
    assert _est_cost(meta) > 0
    assert _est_cost(meta) == pytest.approx(
        RunUsage(
            cache_creation_input_tokens=1_000,
            cache_read_input_tokens=10_000,
            input_tokens=2_000,
            output_tokens=500,
        ).est_cost_usd("claude-haiku-4-5")
    )
    # An unpriced model still fails open in the gateway (unchanged behaviour).
    assert _est_cost({"model": "claude-not-a-model", "input_tokens": 10}) == 0.0


# ─── RunUsage.add ───────────────────────────────────────────────────────────


def test_run_usage_add_accumulates():
    usage = RunUsage()
    for i in range(3):
        usage.add(types.SimpleNamespace(
            cache_creation_input_tokens=10,
            cache_read_input_tokens=20,
            input_tokens=30,
            output_tokens=40,
        ))
    assert usage.cache_creation_input_tokens == 30
    assert usage.cache_read_input_tokens == 60
    assert usage.input_tokens == 90
    assert usage.output_tokens == 120


def test_run_usage_add_ignores_missing_attributes():
    usage = RunUsage()
    # An Anthropic usage object on a non-cached call omits the cache_* fields.
    usage.add(types.SimpleNamespace(input_tokens=100, output_tokens=50))
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


def test_run_usage_add_treats_none_as_zero():
    usage = RunUsage()
    usage.add(types.SimpleNamespace(
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        input_tokens=5,
        output_tokens=None,
    ))
    assert usage.cache_creation_input_tokens == 0
    assert usage.input_tokens == 5
    assert usage.output_tokens == 0


# ─── est_cost_usd ────────────────────────────────────────────────────────────


def test_run_usage_est_cost_sonnet_4_6():
    usage = RunUsage(
        cache_creation_input_tokens=1000,
        cache_read_input_tokens=2000,
        input_tokens=500,
        output_tokens=300,
    )
    # Hand-computed from MODEL_PRICING["claude-sonnet-4-6"]:
    # 1000*6e-6 + 2000*0.3e-6 + 500*3e-6 + 300*15e-6
    expected = 0.006 + 0.0006 + 0.0015 + 0.0045
    assert usage.est_cost_usd("claude-sonnet-4-6") == pytest.approx(expected)


def test_run_usage_est_cost_opus_4_7_is_larger():
    usage = RunUsage(
        cache_creation_input_tokens=1000,
        cache_read_input_tokens=2000,
        input_tokens=500,
        output_tokens=300,
    )
    sonnet = usage.est_cost_usd("claude-sonnet-4-6")
    opus = usage.est_cost_usd("claude-opus-4-7")
    # Same usage; Opus pricing is strictly higher — proves model lookup works.
    expected_opus = 0.01 + 0.001 + 0.0025 + 0.0075
    assert opus == pytest.approx(expected_opus)
    assert opus > sonnet


def test_run_usage_est_cost_unknown_model_fails_closed():
    usage = RunUsage(input_tokens=100)
    # claude-sonnet-4-7 does not exist (AD2); silent zero would mask spend.
    with pytest.raises(UnknownModelError):
        usage.est_cost_usd("claude-sonnet-4-7")


# ─── log_llm_run ─────────────────────────────────────────────────────────────


def test_log_llm_run_emits_canonical_shape(caplog):
    usage = RunUsage(cache_read_input_tokens=100, input_tokens=200, output_tokens=50)
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        log_llm_run(
            operation="design_agent.run.complete",
            identifier={"prototype_id": 42, "scenario": "A", "mode": "scaffold"},
            usage=usage,
            duration_ms=1234,
            status="complete",
            model="claude-sonnet-4-6",
            error_class=None,
            iters=3,
        )
    msg = _record_message(caplog)
    # All required tokens present, in canonical order.
    ordered = [
        "design_agent.run.complete",
        "mode=scaffold", "prototype_id=42", "scenario=A",  # identifier, alphabetical
        "iters=3",
        "cached_input_tokens=100",
        "input_tokens=200",
        "output_tokens=50",
        "duration_ms=1234",
        "est_cost_usd=",
        "status=complete",
        "error_class=",
    ]
    last = -1
    for token in ordered:
        idx = msg.find(token)
        assert idx != -1, f"missing {token!r} in {msg!r}"
        assert idx > last, f"{token!r} out of order in {msg!r}"
        last = idx


def test_log_llm_run_renders_identifier_alphabetically(caplog):
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        log_llm_run(
            operation="x.op",
            identifier={"prd_id": 1, "scenario": "A", "prototype_id": 42},
            usage=RunUsage(),
            duration_ms=1,
            status="complete",
            model="claude-sonnet-4-6",
        )
    msg = _record_message(caplog)
    assert "prd_id=1 prototype_id=42 scenario=A" in msg


def test_log_llm_run_passes_through_extra_kwargs(caplog):
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        log_llm_run(
            operation="x.op",
            identifier={"prototype_id": 1},
            usage=RunUsage(),
            duration_ms=1,
            status="complete",
            model="claude-sonnet-4-6",
            variant="v2",
            iters=3,
        )
    msg = _record_message(caplog)
    assert "iters=3" in msg          # dedicated slot, after identifiers
    assert "variant=v2" in msg       # generic extra, rendered after required fields
    # iters precedes the token fields; variant trails status/error_class.
    assert msg.index("iters=3") < msg.index("cached_input_tokens=")
    assert msg.index("variant=v2") > msg.index("status=complete")


def test_log_llm_run_emits_error_class_when_present(caplog):
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        log_llm_run(
            operation="x.op",
            identifier={"prototype_id": 1},
            usage=RunUsage(),
            duration_ms=1,
            status="error",
            model="claude-sonnet-4-6",
            error_class="RuntimeError",
        )
    msg = _record_message(caplog)
    assert "status=error" in msg
    assert "error_class=RuntimeError" in msg


def test_log_llm_run_raises_on_unknown_model(caplog):
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        with pytest.raises(UnknownModelError):
            log_llm_run(
                operation="x.op",
                identifier={"prototype_id": 1},
                usage=RunUsage(input_tokens=100),
                duration_ms=1,
                status="complete",
                model="claude-sonnet-4-7",  # fails closed
            )

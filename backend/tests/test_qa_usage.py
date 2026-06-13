"""Q&A usage aggregation."""
from __future__ import annotations

from app.qa_usage import aggregate_usage


def test_aggregate_sums_cost_and_tokens_with_breakdown():
    rows = [
        {"agent": "qa-router", "factors": {"cost_usd": 0.001, "input_tokens": 100, "output_tokens": 10}},
        {"agent": "qa", "factors": {"cost_usd": 0.02, "input_tokens": 2000, "output_tokens": 500}},
        {"agent": "qa", "factors": {"cost_usd": 0.03, "input_tokens": 1000, "output_tokens": 400}},
    ]
    agg = aggregate_usage(rows)
    assert agg["calls"] == 3
    assert agg["cost_usd"] == 0.051
    assert agg["input_tokens"] == 3100
    assert agg["output_tokens"] == 910
    assert agg["by_agent"]["qa"]["calls"] == 2
    assert agg["by_agent"]["qa-router"]["calls"] == 1


def test_aggregate_handles_missing_factors():
    agg = aggregate_usage([{"agent": "qa"}, {"agent": "qa", "factors": {}}])
    assert agg["calls"] == 2
    assert agg["cost_usd"] == 0.0
    assert agg["input_tokens"] == 0


def test_aggregate_empty():
    agg = aggregate_usage([])
    assert agg == {
        "calls": 0, "cost_usd": 0.0, "input_tokens": 0,
        "output_tokens": 0, "by_agent": {},
    }

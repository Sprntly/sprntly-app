"""Stage 2 — Temporal Dynamics tests."""

from __future__ import annotations

import pytest

from ds_agent.stages.temporal_dynamics import run_temporal_dynamics

from tests.synth_fixtures import temporal_dataset


def test_temporal_finds_stable_and_emerging_drivers() -> None:
    df = temporal_dataset(n=4000, seed=42)
    out = run_temporal_dynamics(df, goal_metric="retention_30d", time_buckets=4)

    assert out.stage == 2
    assert out.findings, "expected at least one temporal finding"

    patterns = {f.feature: f.metadata.get("pattern") for f in out.findings}
    # feature_A is planted as stable
    assert patterns.get("feature_A") in {"stable", "emerging"}, patterns
    # at least one emerging OR degrading finding overall
    has_dynamic = any(p in ("emerging", "degrading") for p in patterns.values())
    assert has_dynamic, f"expected emerging or degrading finding, got {patterns}"


def test_temporal_handles_no_time_column() -> None:
    import pandas as pd

    df = pd.DataFrame({"a": [1.0] * 200, "retention_30d": [0, 1] * 100})
    out = run_temporal_dynamics(df, goal_metric="retention_30d")
    assert out.metadata.get("skipped") is True
    assert out.metadata.get("reason") == "no_time_column"


def test_temporal_reports_concept_drift_metadata() -> None:
    df = temporal_dataset(n=4000, seed=42)
    out = run_temporal_dynamics(df, goal_metric="retention_30d", time_buckets=4)
    assert "concept_drift_detected" in out.metadata
    assert "concept_drift_jaccard" in out.metadata
    assert out.metadata["n_buckets"] == 4

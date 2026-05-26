"""Express tier — verify DATA_SUMMARY shape + JSON parsing + Claude mock."""

from __future__ import annotations

import json

import pandas as pd

from ds_agent.tiers.express import (
    EXPRESS_PROMPT_V1,
    build_data_summary,
    run_express,
)


def test_build_data_summary_captures_correlations() -> None:
    df = pd.DataFrame(
        {
            "x": range(100),
            "y": [i * 2 for i in range(100)],  # perfectly correlated with goal
            "noise": [0] * 100,
            "retention_30d": [1 if i > 50 else 0 for i in range(100)],
        }
    )
    summary = build_data_summary(df, goal_metric="retention_30d")
    assert summary.n_rows == 100
    assert summary.goal_metric == "retention_30d"
    assert "x" in summary.feature_corr_with_goal or "y" in summary.feature_corr_with_goal


def test_run_express_with_fake_client_parses_findings() -> None:
    captured = {}

    def fake_client(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps(
            {
                "findings": [
                    {
                        "feature": "posts_first_week",
                        "direction": "positive",
                        "confidence": "HIGH",
                        "importance": 0.85,
                        "rationale": "Strong positive correlation",
                    },
                    {
                        "feature": "mobile_only_user",
                        "direction": "negative",
                        "confidence": "MEDIUM",
                        "importance": 0.55,
                        "rationale": "Mobile-only users churn faster",
                    },
                ],
                "summary": "Posts and platform diversity drive retention.",
            }
        )

    df = pd.DataFrame(
        {
            "posts_first_week": list(range(200)),
            "mobile_only_user": [0, 1] * 100,
            "retention_30d": [1, 0] * 100,
        }
    )
    summary = build_data_summary(df, goal_metric="retention_30d")
    result = run_express(summary, client=fake_client)

    assert len(result.findings) == 2
    assert result.findings[0].feature == "posts_first_week"
    assert result.findings[0].direction == "positive"
    assert result.findings[0].confidence == "HIGH"
    assert result.cost_estimate_usd == 0.10
    assert result.prompt_version == "v1.0"

    # Verify the prompt was built from the DATA_SUMMARY
    assert "DATA_SUMMARY" in captured["prompt"]
    assert "retention_30d" in captured["prompt"]
    assert "n_rows" in captured["prompt"]


def test_express_handles_fenced_json_response() -> None:
    def fake_client(prompt: str) -> str:
        return "```json\n" + json.dumps({"findings": [], "summary": "ok"}) + "\n```"

    df = pd.DataFrame({"a": [1.0] * 100, "retention_30d": [0, 1] * 50})
    summary = build_data_summary(df, goal_metric="retention_30d")
    result = run_express(summary, client=fake_client)
    assert result.summary_text == "ok"


def test_express_resilient_to_malformed_json() -> None:
    def fake_client(prompt: str) -> str:
        return "not json"

    df = pd.DataFrame({"a": [1.0] * 100, "retention_30d": [0, 1] * 50})
    summary = build_data_summary(df, goal_metric="retention_30d")
    result = run_express(summary, client=fake_client)
    assert result.findings == []
    assert "not json" in result.summary_text or result.summary_text == "not json"


def test_express_prompt_template_has_required_slots() -> None:
    assert "{data_summary_json}" in EXPRESS_PROMPT_V1
    assert "DATA_SUMMARY" in EXPRESS_PROMPT_V1

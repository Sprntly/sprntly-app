"""End-to-end smoke test on the synthetic dataset.

We plant a handful of ground-truth effects in `synthetic.py`; the
pipeline (Stage 1 only for now, LLM skipped) should surface most of
them in its top findings.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ds_agent import pipeline, synthetic


def test_pipeline_recovers_strong_effects():
    """Stage 1 should find the two strong common effects.

    The rare-segment truths (invites_sent + comments_first_week, ~2% of users)
    aren't expected here — those are Stage 3 (Tail Analysis) territory.
    """
    df = synthetic.generate(n_users=4_000, seed=42)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "synthetic.csv"
        df.to_csv(path, index=False)

        result = pipeline.run(
            csv_path=str(path),
            goal_metric="retention_30d",
            synthesize=False,
            top_k=10,
        )

    behaviors_surfaced = {f["behavior"] for f in result["findings"]}
    must_have = {"posts_first_week", "mobile_only_user"}
    missing = must_have - behaviors_surfaced
    assert not missing, (
        f"Stage 1 missed required ground truths: {missing}. "
        f"Surfaced: {behaviors_surfaced}"
    )

    # Sanity: shape conforms to the spec
    assert result["summary"]["total_findings"] == len(result["findings"])
    for f in result["findings"]:
        assert f["confidence_score"] in {"HIGH", "MEDIUM", "LOW"}
        assert f["impact_type"] in {"positive", "negative"}
        assert isinstance(f["confidence_breakdown"], dict)


def test_directionality_of_strong_effects():
    """The signs should be right: posts is positive, mobile_only is negative."""
    df = synthetic.generate(n_users=4_000, seed=42)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "synthetic.csv"
        df.to_csv(path, index=False)

        result = pipeline.run(
            csv_path=str(path),
            goal_metric="retention_30d",
            synthesize=False,
            top_k=10,
        )

    by_behavior = {f["behavior"]: f for f in result["findings"]}
    assert by_behavior["posts_first_week"]["impact_type"] == "positive"
    assert by_behavior["mobile_only_user"]["impact_type"] == "negative"

"""Stage 4 — Causal Validation tests (PSM + DiD)."""

from __future__ import annotations

import pytest

from ds_agent.stages.causal_validation import (
    run_causal_validation,
    run_did,
    run_psm,
)

from tests.synth_fixtures import did_dataset


def test_did_recovers_ground_truth() -> None:
    df = did_dataset(n=4000, true_did=0.20, seed=42)
    # The dataset uses signup_date — we want a clean pre/post split, so
    # use the original `signup_date` since DiD inspects it as a datetime.
    # But we planted `is_post` via that date, so DiD over `signup_date`
    # should split the same way (median of date == midpoint).
    did = run_did(df, treatment_col="treatment", time_col="signup_date", goal_metric="goal")
    # within ±25% of true 0.20
    assert 0.15 <= did.estimate <= 0.25, f"DiD estimate off: {did.estimate}"
    assert did.p_value < 0.01
    assert did.n_obs == len(df)


def test_psm_recovers_treatment_effect() -> None:
    df = did_dataset(n=4000, true_did=0.20, seed=42)
    # Without time, PSM should still catch the treatment effect (≈ 0.10 main
    # + 0.10 average DiD over half post-period = ~0.20 ATT)
    psm = run_psm(df, treatment_col="treatment", goal_metric="goal")
    assert psm.n_treated_matched > 100
    # PSM should find a meaningful positive estimate (treatment + DiD interaction
    # average over the population). Tolerant bounds — PSM ignores time so the
    # exact ATT is ≈0.10 main effect + 0.10 expected DiD bump = ≈0.15–0.25.
    assert psm.estimate > 0.05, f"PSM estimate too small: {psm.estimate}"
    assert psm.p_value < 0.05


def test_triangulation_marks_high_confidence_when_both_agree() -> None:
    df = did_dataset(n=4000, true_did=0.20, seed=42)
    out = run_causal_validation(
        df, goal_metric="goal", candidates=["treatment"], time_col="signup_date"
    )
    assert out.findings, "expected one causal finding"
    f = out.findings[0]
    assert f.feature == "treatment"
    assert f.direction == "positive"
    assert f.confidence == "HIGH"
    assert f.metadata["triangulated"] is True


def test_psm_handles_no_covariates_gracefully() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {"treatment": [0, 1] * 100, "goal": [0.1, 0.5] * 100}
    )
    psm = run_psm(df, treatment_col="treatment", goal_metric="goal")
    # With no covariates the matcher should return a zero/empty result rather than crash.
    assert psm.n_treated_matched == 0
    assert psm.p_value == 1.0

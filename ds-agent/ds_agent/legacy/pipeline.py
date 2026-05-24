"""End-to-end pipeline orchestration.

Stage 1 (pattern_discovery) only for now. Stages 2–5 will plug in here
once they exist.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import confidence, ingest
from .output import assemble
from .stages import pattern_discovery


def _consolidate_findings(stage1: dict[str, Any]) -> list[dict[str, Any]]:
    """Group findings by behavior.

    The top-level finding for each behavior is the global SHAP result (if
    any). PCA and Stratified_SHAP attach to the same behavior as extra
    `supporting_analyses` and a `segment_variation` sub-field, instead of
    competing as separate top-K slots. A behavior that surfaces ONLY in
    stratified findings is preserved as a stratified-only finding.
    """
    by_behavior: dict[str, dict[str, Any]] = {}

    # Step 1: global SHAP findings become the canonical per-behavior entries.
    for f in stage1.get("shap", []):
        by_behavior[f["behavior"]] = {
            "behavior": f["behavior"],
            "scope": "global",
            "effect_size": f["effect_size"],
            "effect_size_std": f.get("effect_size_std"),
            "directionality": f["directionality"],
            "seed_agreement": f.get("seed_agreement", 1.0),
            "sample_size": f["sample_size"],
            "supporting_analyses": ["SHAP"],
            "segment_variation": [],
        }

    # Step 2: credit PCA to whichever behavior it loads most heavily on.
    for f in stage1.get("pca", []):
        top_behavior = f["behaviors"][0] if f.get("behaviors") else None
        if not top_behavior:
            continue
        entry = by_behavior.get(top_behavior)
        if entry:
            if "PCA" not in entry["supporting_analyses"]:
                entry["supporting_analyses"].append("PCA")
        else:
            by_behavior[top_behavior] = {
                "behavior": top_behavior,
                "scope": "pca_only",
                "factor": f["factor"],
                "factor_loadings": f["behaviors"],
                "effect_size": f["effect_size"],
                "directionality": f["directionality"],
                "sample_size": None,
                "seed_agreement": 1.0,
                "supporting_analyses": ["PCA"],
                "segment_variation": [],
            }

    # Step 3: stratified findings — attach as variation if a global entry
    # exists, otherwise promote to a stratified-only finding.
    stratified_by_behavior: dict[str, list[dict[str, Any]]] = {}
    for f in stage1.get("stratified", []):
        stratified_by_behavior.setdefault(f["behavior"], []).append(f)

    for behavior, strata in stratified_by_behavior.items():
        entry = by_behavior.get(behavior)
        if entry:
            if "Stratified_SHAP" not in entry["supporting_analyses"]:
                entry["supporting_analyses"].append("Stratified_SHAP")
            global_dir = entry["directionality"]
            global_effect = entry["effect_size"]
            for s in strata:
                disagrees = s["directionality"] != global_dir
                # Only surface a stratum-level callout if it adds information:
                # either the direction flips, or the local effect is meaningfully larger.
                if disagrees or s["effect_size"] >= 1.5 * global_effect:
                    entry["segment_variation"].append(
                        {
                            "stratum": s["stratum"],
                            "effect_size": s["effect_size"],
                            "directionality": s["directionality"],
                            "sample_size": s["sample_size"],
                            "differs_from_global": disagrees,
                        }
                    )
        else:
            # Pick the strongest stratum as the representative
            best = max(strata, key=lambda s: s["effect_size"])
            by_behavior[behavior] = {
                "behavior": behavior,
                "scope": "stratified_only",
                "stratum": best["stratum"],
                "effect_size": best["effect_size"],
                "directionality": best["directionality"],
                "sample_size": best["sample_size"],
                # Stratified runs once per stratum → no cross-seed evidence.
                "seed_agreement": 0.33,
                "supporting_analyses": ["Stratified_SHAP"],
                "segment_variation": [
                    {
                        "stratum": s["stratum"],
                        "effect_size": s["effect_size"],
                        "directionality": s["directionality"],
                        "sample_size": s["sample_size"],
                        "differs_from_global": False,
                    }
                    for s in strata
                ],
            }

    out = list(by_behavior.values())
    for f in out:
        f["num_methods_supporting"] = len(f["supporting_analyses"])
    return out


def run(
    csv_path: str,
    goal_metric: str,
    *,
    business_model: str = "saas",
    analytics_tool: str = "csv",
    synthesize: bool = True,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run the agent end-to-end and return the spec-shaped JSON dict."""
    meta = ingest.load(csv_path, goal_metric)
    is_binary = bool(meta.data_quality.get("goal_metric_is_binary"))

    stage1 = pattern_discovery.run(
        df=meta.df,
        numeric_features=meta.numeric_features,
        categorical_features=meta.categorical_features,
        goal_metric=meta.goal_metric,
        is_binary=is_binary,
    )

    findings = _consolidate_findings(stage1)

    # Score confidence, then keep only the top-K by impact
    for f in findings:
        f["confidence_score"] = confidence.score(f, meta.df)
    findings = confidence.rank_by_impact(findings)[:top_k]

    if synthesize:
        from .synthesis import Synthesizer  # lazy import: avoids needing the key for tests

        synth = Synthesizer()
        for f in findings:
            try:
                summary = synth.summarize(f, goal_metric=meta.goal_metric)
                f["narrative"] = summary["narrative"]
                f["recommended_action"] = summary["recommended_action"]
            except Exception as exc:  # noqa: BLE001 — never block the run on the LLM
                f["narrative"] = f"[LLM synthesis failed: {exc}]"
                f["recommended_action"] = ""

    run_id = "ds_agent_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    return assemble(
        run_id=run_id,
        analytics_tool=analytics_tool,
        goal_metric=meta.goal_metric,
        business_model=business_model,
        data_quality=meta.data_quality,
        findings=findings,
    )

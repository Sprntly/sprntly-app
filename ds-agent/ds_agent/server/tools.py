"""Tools the chat agent (Claude) can invoke against a loaded CSV.

Kept deliberately small for v1 — describe / set_goal / run_discovery /
focus_on_finding. Stages 2–5 of the spec will add more tools here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .. import confidence, ingest, pipeline as ds_pipeline
from ..stages import pattern_discovery


# ─────────────────────── tool definitions for Claude ───────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "describe_dataset",
        "description": (
            "Returns the column inventory, dtypes, completeness, and a small preview of the "
            "currently loaded dataset. Always call this first before running any analyses so "
            "you know what's available."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_goal_metric",
        "description": (
            "Set the column the agent should optimize for (e.g. retention_30d, revenue_30d, "
            "engagement_score). Required before running pattern discovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "description": "Name of the goal column. Must exist in the dataset.",
                }
            },
            "required": ["metric"],
        },
    },
    {
        "name": "run_pattern_discovery",
        "description": (
            "Run Stage 1 of the data-science pipeline (PCA + SHAP + Stratified) and return the "
            "ranked behavioral drivers. Expensive — only call once per goal-metric, then refer "
            "back to its output for follow-ups."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_k": {
                    "type": "integer",
                    "default": 8,
                    "description": "How many top findings to return (default 8).",
                }
            },
        },
    },
    {
        "name": "focus_on_finding",
        "description": (
            "Drill into a single behavior from the last pattern-discovery run — returns the "
            "full finding payload including per-stratum variation so you can answer "
            "follow-up questions like 'what about by region?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "behavior": {
                    "type": "string",
                    "description": "Name of the behavior column to focus on.",
                }
            },
            "required": ["behavior"],
        },
    },
]


# ─────────────────────── execution ───────────────────────


class _ToolError(RuntimeError):
    """Lightweight wrapper so the chat loop can serialize errors back to Claude."""


def execute(name: str, params: dict[str, Any], session: Any) -> dict[str, Any]:
    """Dispatch a tool call. `session` is a SessionState; we mutate it in place."""
    try:
        if name == "describe_dataset":
            return _describe_dataset(session)
        if name == "set_goal_metric":
            return _set_goal_metric(session, params)
        if name == "run_pattern_discovery":
            return _run_pattern_discovery(session, params)
        if name == "focus_on_finding":
            return _focus_on_finding(session, params)
        raise _ToolError(f"unknown_tool:{name}")
    except _ToolError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — never let a tool error crash the chat loop
        return {"error": f"{type(exc).__name__}: {exc}"}


def _require_csv(session: Any) -> Path:
    if not session.csv_path:
        raise _ToolError(
            "no_dataset_loaded — ask the user to upload a CSV or pick a sample dataset first."
        )
    return Path(session.csv_path)


def _describe_dataset(session: Any) -> dict[str, Any]:
    path = _require_csv(session)
    df = pd.read_csv(path)
    completeness = {col: round(1.0 - df[col].isna().mean(), 3) for col in df.columns}
    dtypes = {col: str(df[col].dtype) for col in df.columns}
    preview = df.head(5).to_dict(orient="records")
    return {
        "label": session.dataset_label or path.name,
        "row_count": int(len(df)),
        "columns": [
            {
                "name": col,
                "dtype": dtypes[col],
                "completeness": completeness[col],
                "sample_values": [
                    str(v) for v in df[col].dropna().unique()[:5]
                ],
            }
            for col in df.columns
        ],
        "preview_rows": preview,
        "current_goal_metric": session.goal_metric,
    }


def _set_goal_metric(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    metric = params.get("metric")
    if not metric:
        raise _ToolError("set_goal_metric:missing_metric")
    path = _require_csv(session)
    df = pd.read_csv(path, nrows=1)
    if metric not in df.columns:
        raise _ToolError(f"set_goal_metric:unknown_column '{metric}' (columns: {list(df.columns)})")
    session.goal_metric = metric
    return {"ok": True, "goal_metric": metric}


def _run_pattern_discovery(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _require_csv(session)
    if not session.goal_metric:
        raise _ToolError("run_pattern_discovery:goal_metric_not_set — call set_goal_metric first")
    top_k = int(params.get("top_k", 8))

    meta = ingest.load(str(path), session.goal_metric)
    is_binary = bool(meta.data_quality.get("goal_metric_is_binary"))
    stage1 = pattern_discovery.run(
        df=meta.df,
        numeric_features=meta.numeric_features,
        categorical_features=meta.categorical_features,
        goal_metric=meta.goal_metric,
        is_binary=is_binary,
    )
    findings = ds_pipeline._consolidate_findings(stage1)
    for f in findings:
        f["confidence_score"] = confidence.score(f, meta.df)
    findings = confidence.rank_by_impact(findings)[:top_k]

    # Cache full findings for focus_on_finding to drill into
    session.last_run = {
        "goal_metric": session.goal_metric,
        "data_quality": meta.data_quality,
        "findings": findings,
    }

    # Return a stripped-down version so we don't blow Claude's context budget
    summary = []
    for i, f in enumerate(findings):
        summary.append(
            {
                "rank": i + 1,
                "behavior": f.get("behavior"),
                "directionality": f.get("directionality"),
                "effect_size": round(f.get("effect_size", 0.0), 4),
                "confidence": f["confidence_score"]["label"],
                "supporting_analyses": f.get("supporting_analyses", []),
                "has_segment_variation": bool(f.get("segment_variation")),
            }
        )
    return {
        "goal_metric": session.goal_metric,
        "sample_size": meta.data_quality.get("sample_size"),
        "completeness": meta.data_quality.get("completeness"),
        "findings": summary,
    }


def _focus_on_finding(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    behavior = params.get("behavior")
    if not behavior:
        raise _ToolError("focus_on_finding:missing_behavior")
    if not session.last_run:
        raise _ToolError("focus_on_finding:no_prior_run — call run_pattern_discovery first")
    for f in session.last_run["findings"]:
        if f.get("behavior") == behavior:
            return {
                "behavior": f.get("behavior"),
                "directionality": f.get("directionality"),
                "effect_size": f.get("effect_size"),
                "effect_size_std": f.get("effect_size_std"),
                "confidence": f["confidence_score"]["label"],
                "confidence_breakdown": f["confidence_score"]["factors"],
                "supporting_analyses": f.get("supporting_analyses", []),
                "segment_variation": f.get("segment_variation", []),
                "sample_size": f.get("sample_size"),
            }
    available = [f.get("behavior") for f in session.last_run["findings"]]
    raise _ToolError(
        f"focus_on_finding:unknown_behavior '{behavior}' (available: {available})"
    )

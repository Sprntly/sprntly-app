"""Express tier — structured single Claude API call.

Spec §3.2 Express:
  - Build a DataSummary (means, completeness, top correlations with goal)
  - Single Claude call with the spec's structured-output prompt
  - Parse JSON response into ExpressResult
  - No local algorithms run
  - Cost: ~$0.10 / 10min

The API client is swappable via the ``client`` kwarg so tests can pass a
``FakeAnthropic`` without monkey-patching imports.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from ..types import DataSummary, Finding


EXPRESS_PROMPT_V1 = """You are Sprntly's data-science agent (Express tier).

Given a compact DATA_SUMMARY of a SaaS analytics table, produce up to 5
findings about behavioural drivers of the goal metric. Output STRICT JSON:

{
  "findings": [
    {
      "feature": "<column_or_segment>",
      "direction": "positive" | "negative",
      "confidence": "LOW" | "MEDIUM" | "HIGH",
      "importance": <float in 0..1>,
      "rationale": "<one sentence>"
    }
  ],
  "summary": "<2-sentence overview>"
}

DATA_SUMMARY:
{data_summary_json}
"""


@dataclass
class ExpressResult:
    findings: list[Finding] = field(default_factory=list)
    summary_text: str = ""
    elapsed_seconds: float = 0.0
    cost_estimate_usd: float = 0.10
    prompt_version: str = "v1.0"
    raw_response: str = ""


class AnthropicLike(Protocol):
    def messages(self, **kwargs: Any) -> Any: ...


def build_data_summary(
    user_table: pd.DataFrame, goal_metric: str, *, top_k_corr: int = 10
) -> DataSummary:
    n = len(user_table)
    n_feat = max(0, user_table.shape[1] - 1)
    completeness = float(1.0 - user_table.isna().mean().mean())

    numeric_features = [
        c
        for c in user_table.columns
        if c not in {goal_metric, "user_id"} and pd.api.types.is_numeric_dtype(user_table[c])
    ]
    means = {c: float(user_table[c].mean()) for c in numeric_features[:20]}

    corrs: dict[str, float] = {}
    if goal_metric in user_table.columns:
        y = user_table[goal_metric].astype(float).fillna(0.0)
        for c in numeric_features:
            x = user_table[c].astype(float).fillna(0.0)
            if x.std() == 0 or y.std() == 0:
                continue
            corrs[c] = float(np.corrcoef(x, y)[0, 1])
    # Keep top-K by absolute correlation
    top = sorted(corrs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_k_corr]
    corrs_top = {k: round(v, 3) for k, v in top}

    return DataSummary(
        n_rows=n,
        n_features=n_feat,
        goal_metric=goal_metric,
        completeness=round(completeness, 3),
        feature_means={k: round(v, 3) for k, v in means.items()},
        feature_corr_with_goal=corrs_top,
    )


def _default_client() -> Any:
    """Lazy import the real Anthropic SDK so tests don't need an API key."""
    import anthropic  # noqa: WPS433 — intentional lazy import

    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", "test"))


def _call_claude(client: Any, prompt: str) -> str:
    """Adapter — supports both the real SDK and a callable test fake."""
    if callable(client):
        return client(prompt)
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    # Real SDK response: msg.content is a list of TextBlocks
    parts = []
    for block in getattr(msg, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts) or str(msg)


def _parse_response(text: str) -> tuple[list[Finding], str]:
    """Parse JSON envelope; strip ```json fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop leading and trailing fence lines
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return [], cleaned[:500]

    findings: list[Finding] = []
    for item in payload.get("findings", []):
        try:
            findings.append(
                Finding(
                    feature=str(item["feature"]),
                    importance=float(item.get("importance", 0.0)),
                    direction=item.get("direction", "positive"),
                    confidence=item.get("confidence", "MEDIUM"),
                    metadata={"rationale": item.get("rationale", ""), "source": "express"},
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return findings, str(payload.get("summary", ""))


def run_express(
    data_summary: DataSummary,
    prompt_version: str = "v1.0",
    *,
    client: Any | None = None,
) -> ExpressResult:
    """Run the Express tier. ``client`` may be a callable(prompt)->str for tests."""
    started = time.perf_counter()
    prompt = EXPRESS_PROMPT_V1.replace(
        "{data_summary_json}",
        json.dumps(
            {
                "n_rows": data_summary.n_rows,
                "n_features": data_summary.n_features,
                "goal_metric": data_summary.goal_metric,
                "completeness": data_summary.completeness,
                "feature_means": data_summary.feature_means,
                "feature_corr_with_goal": data_summary.feature_corr_with_goal,
            },
            indent=2,
        ),
    )

    use_client = client if client is not None else _default_client()
    raw = _call_claude(use_client, prompt)
    findings, summary_text = _parse_response(raw)

    return ExpressResult(
        findings=findings,
        summary_text=summary_text,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.10,
        prompt_version=prompt_version,
        raw_response=raw[:5000],
    )

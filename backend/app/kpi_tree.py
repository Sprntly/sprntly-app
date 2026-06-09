"""KPI tree — the company's North Star + supporting metrics (config entity).

Schema follows KG_Engineering_Spec §3.1.1 and backs design-v4 page 05
(onboarding North Star picker) + page 09 (dashboard) + Synthesis scoring
(§4c strategic-alignment anchoring).

Each metric is now a `{metric, description}` pair: a short name plus a
free-text description that gives the goal-fit classifier richer context.
The earlier numeric fields (weight / current_value / target_value /
target_window_days) have been removed from the schema — the description
replaces them as the unit of strategic context.

Storage: `companies.kpi_tree jsonb` (column from the onboarding migration).
Reads are tolerant of LEGACY rows that still carry the old numeric fields:
the models ignore unknown keys and default `description` to "" — no data
migration is needed (backward-compatible jsonb read). Version increments on
every save.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.client import require_client

logger = logging.getLogger(__name__)


class NorthStar(BaseModel):
    # Ignore legacy numeric fields (current_value/target_value/…) on read.
    model_config = ConfigDict(extra="ignore")

    metric: str
    description: str = ""


class PrimaryMetric(BaseModel):
    # Ignore legacy fields (weight/current_value/target_value) on read.
    model_config = ConfigDict(extra="ignore")

    metric: str
    description: str = ""


class SecondarySignal(BaseModel):
    # Ignore legacy fields (current_value/direction) on read.
    model_config = ConfigDict(extra="ignore")

    metric: str
    description: str = ""
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"


class KpiTree(BaseModel):
    north_star: NorthStar
    primary_metrics: list[PrimaryMetric] = Field(default_factory=list, max_length=4)
    secondary_signals: list[SecondarySignal] = Field(default_factory=list, max_length=6)
    version: int = 1

    @field_validator("north_star", mode="before")
    @classmethod
    def _coerce_north_star(cls, v):
        """Tolerate legacy/hand-edited shapes stored in `companies.kpi_tree`.

        Older rows persisted the north star as a bare string (e.g. "Revenue")
        rather than a `{metric: ...}` object. Normalize on read: a non-empty
        string → `{"metric": <string>}`; None/empty/garbage → a safe default
        metric so the tree parses and goal-fit classification keeps working
        instead of raising a ValidationError. (No data migration — read-side
        normalization only.)
        """
        if isinstance(v, str):
            metric = v.strip()
            return {"metric": metric} if metric else {"metric": "North Star"}
        if v is None:
            return {"metric": "North Star"}
        if isinstance(v, NorthStar):
            # Already a valid model (e.g. constructed in-process) — pass through.
            return v
        if isinstance(v, dict):
            # An object missing/empty `metric` still needs a usable label.
            if not str(v.get("metric") or "").strip():
                return {**v, "metric": "North Star"}
            return v
        # Any other shape (number, list, …) → default rather than raise.
        return {"metric": "North Star"}

    def render_for_prompt(self) -> str:
        """Compact text block for agent prompts (Synthesis judge, DS).

        Each line is `<metric> — <description>` so the goal-fit classifier
        reads the PM's own words as the strategic-alignment context. No
        weights/targets are emitted (they are no longer part of the schema);
        metrics are treated equally, with the North Star as the primary anchor.
        """
        def line(metric: str, description: str) -> str:
            metric = (metric or "").strip()
            description = (description or "").strip()
            return f"{metric} — {description}" if description else metric

        ns = self.north_star
        lines = [f"North star: {line(ns.metric, ns.description)}"]
        for m in self.primary_metrics:
            lines.append(f"Primary: {line(m.metric, m.description)}")
        for s in self.secondary_signals:
            lines.append(f"Secondary: {line(s.metric, s.description)}")
        return "\n".join(lines)


def load_kpi_tree(enterprise_id: str) -> Optional[KpiTree]:
    """Read the company's KPI tree; None if unset/empty/invalid."""
    r = (
        require_client().table("companies")
        .select("kpi_tree")
        .eq("id", enterprise_id)
        .execute()
    )
    if not r.data:
        return None
    raw = r.data[0].get("kpi_tree") or {}
    if not raw or not raw.get("north_star"):
        return None
    try:
        return KpiTree.model_validate(raw)
    except Exception:  # noqa: BLE001 — tolerate legacy/hand-edited shapes
        logger.warning("invalid kpi_tree for %s; ignoring", enterprise_id, exc_info=True)
        return None


def save_kpi_tree(enterprise_id: str, tree: KpiTree) -> KpiTree:
    """Persist; bumps version past whatever is currently stored."""
    current = load_kpi_tree(enterprise_id)
    tree.version = (current.version + 1) if current else max(1, tree.version)
    (
        require_client().table("companies")
        .update({"kpi_tree": tree.model_dump()})
        .eq("id", enterprise_id)
        .execute()
    )
    return tree

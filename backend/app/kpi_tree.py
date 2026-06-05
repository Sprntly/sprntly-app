"""KPI tree — the company's North Star + supporting metrics (config entity).

Schema follows KG_Engineering_Spec §3.1.1 and backs design-v4 page 05
(onboarding North Star picker) + page 09 (dashboard) + Synthesis scoring
(§4c strategic-alignment anchoring).

Storage: `companies.kpi_tree jsonb` (column from the onboarding migration —
previously unwired). Version increments on every update; weights across
primary metrics must sum to ~1.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.client import require_client

logger = logging.getLogger(__name__)


class NorthStar(BaseModel):
    metric: str
    current_value: Optional[float] = None
    target_value: Optional[float] = None
    target_window_days: Optional[int] = Field(default=None, gt=0)


class PrimaryMetric(BaseModel):
    metric: str
    current_value: Optional[float] = None
    target_value: Optional[float] = None
    weight: float = Field(gt=0, le=1)


class SecondarySignal(BaseModel):
    metric: str
    current_value: Optional[float] = None
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"


class KpiTree(BaseModel):
    north_star: NorthStar
    primary_metrics: list[PrimaryMetric] = Field(default_factory=list, max_length=4)
    secondary_signals: list[SecondarySignal] = Field(default_factory=list, max_length=6)
    version: int = 1

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "KpiTree":
        if self.primary_metrics:
            total = sum(m.weight for m in self.primary_metrics)
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"primary_metrics weights must sum to 1.0 (got {total:.2f})"
                )
        return self

    def render_for_prompt(self) -> str:
        """Compact text block for agent prompts (Synthesis judge, DS)."""
        def n(v: float) -> str:
            return f"{v:g}"
        ns = self.north_star
        lines = [f"North star: {ns.metric}"
                 + (f" — current {n(ns.current_value)}" if ns.current_value is not None else "")
                 + (f", target {n(ns.target_value)}" if ns.target_value is not None else "")
                 + (f" within {ns.target_window_days}d" if ns.target_window_days else "")]
        for m in self.primary_metrics:
            lines.append(
                f"Primary (weight {m.weight:.0%}): {m.metric}"
                + (f" — current {n(m.current_value)}" if m.current_value is not None else "")
                + (f", target {n(m.target_value)}" if m.target_value is not None else "")
            )
        for s in self.secondary_signals:
            lines.append(f"Secondary: {s.metric} ({s.direction})")
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

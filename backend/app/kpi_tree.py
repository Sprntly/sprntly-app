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


class SelectedMetric(BaseModel):
    """One metric the PM picked on the onboarding metrics page."""

    model_config = ConfigDict(extra="ignore")

    metric: str
    description: str = ""


class MetricSelection(BaseModel):
    """The PM's metric picks; the backend infers the North Star from them.

    The onboarding metrics page no longer asks the PM to name a North Star.
    It shows a flat list of generated metrics, the PM picks a handful (the UI
    asks for 3–5), and we infer which of those is the North Star here on the
    server. At least one metric is required so a North Star can be chosen; the
    upper bound keeps the inferred tree within the KPI-tree schema (1 North
    Star + up to 4 primaries).
    """

    metrics: list[SelectedMetric] = Field(min_length=1, max_length=5)


# Priority tiers for North-Star inference, strongest first. A metric whose name
# matches an earlier tier outranks one matching a later tier; within a tier the
# PM's own ordering breaks ties. These keywords favour durable outcome metrics
# (retention/revenue) over activity/vanity counts — the usual North-Star advice.
_NORTH_STAR_KEYWORD_TIERS: tuple[tuple[str, ...], ...] = (
    ("retention", "retained", "nrr", "net revenue", "churn", "ltv", "lifetime"),
    ("revenue", "arr", "mrr", "bookings", "gmv", "transaction volume", "reconciled volume"),
    ("active", "engagement", "engaged", "dau", "wau", "mau", "weekly active", "daily active"),
    ("activation", "activated", "aha", "onboarded", "time-to-value", "conversion"),
)


def infer_north_star(metrics: list[SelectedMetric]) -> int:
    """Pick the index of the metric that best serves as the North Star.

    Scores each metric by the strongest keyword tier its name matches (tier 0 =
    best). The lowest-tier (strongest) match wins; ties fall back to the PM's
    own ordering, and a list with no keyword matches at all yields index 0 (the
    PM's first pick). Deterministic — no model call — so it is cheap and easy to
    test, and can be swapped for a classifier later behind the same signature.
    """
    best_index = 0
    best_tier = len(_NORTH_STAR_KEYWORD_TIERS)  # worse than any real tier
    for i, m in enumerate(metrics):
        name = (m.metric or "").lower()
        for tier, keywords in enumerate(_NORTH_STAR_KEYWORD_TIERS):
            if any(kw in name for kw in keywords):
                if tier < best_tier:
                    best_tier = tier
                    best_index = i
                break
    return best_index


def build_tree_from_selection(metrics: list[SelectedMetric]) -> KpiTree:
    """Build a KPI tree from the PM's picks, inferring the North Star server-side.

    The inferred North Star becomes `north_star`; the remaining picks (in their
    original order) become `primary_metrics` (capped at the schema's 4). We never
    emit `secondary_signals` from onboarding — that distinction is no longer part
    of the picker. Each metric's name is trimmed; blanks are dropped upstream by
    validation, but we defensively skip empties here too.
    """
    cleaned = [
        SelectedMetric(metric=m.metric.strip(), description=(m.description or "").strip())
        for m in metrics
        if m.metric and m.metric.strip()
    ]
    if not cleaned:
        # Should be unreachable (MetricSelection requires ≥1), but keep the tree
        # valid rather than raising if every name was whitespace.
        return KpiTree(north_star=NorthStar(metric="North Star"))

    ns_index = infer_north_star(cleaned)
    north_star = cleaned[ns_index]
    rest = [m for i, m in enumerate(cleaned) if i != ns_index]
    return KpiTree(
        north_star=NorthStar(metric=north_star.metric, description=north_star.description),
        primary_metrics=[
            PrimaryMetric(metric=m.metric, description=m.description) for m in rest[:4]
        ],
        secondary_signals=[],
    )


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

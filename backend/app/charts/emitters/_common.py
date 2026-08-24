"""Typed inputs and shared plumbing for the deterministic chart emitters.

The rule this package exists to enforce: **the agent picks *which* chart; the
pipeline *builds* the spec.** Funnels, Kaplan-Meier curves, interrupted time
series, difference-in-differences and BH-corrected segment cuts are statistics —
they do not get to be improvised into whatever JSON a model felt like emitting.
So each emitter takes real typed rows (the dataclasses below), not a `dict` blob,
and returns a validated `ChartSpec`.

Every emitter follows the same shape::

    emitter(rows, *, title, subtitle=None, caption=None,
            provenance=None, width=..., height=...) -> ChartSpec

and raises `ValueError` on empty input — an empty chart is a lie the report
should not print. Callers that may legitimately have nothing to show check first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.charts.spec import VEGA_LITE_SCHEMA_URL, ChartProvenance, ChartSpec
from app.charts.theme import ANNOTATION, CHART_COLORS

DEFAULT_WIDTH = 560
DEFAULT_HEIGHT = 300

PRIMARY = CHART_COLORS[0]
"""The single-series colour, named in the SPEC rather than left to the config.

The shared theme sets `range.category`, which Vega applies only when something is
*encoded* by colour. A single-series bar or line has no colour encoding, so
without this it would render in Vega-Lite's own default steel blue — on the
server AND in the browser, since both consume the same config. Naming the colour
in the spec fixes both renderers at once and keeps the config byte-identical to
the mirror Phase 2 ships.
"""

ANNOTATION_COLOR = ANNOTATION
"""Rules and labels that are chrome, not data. Same reasoning as `PRIMARY`."""


# ── typed rows ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimePoint:
    """One observation on a time axis. `series` splits multi-line charts."""

    t: str
    y: float
    series: str | None = None


@dataclass(frozen=True)
class CIPoint:
    """A time observation with a confidence interval: `lo <= y <= hi`."""

    t: str
    y: float
    lo: float
    hi: float
    series: str | None = None


@dataclass(frozen=True)
class Category:
    """One labelled magnitude — the bar-chart row."""

    label: str
    value: float


@dataclass(frozen=True)
class StackedCell:
    """One cell of a stacked bar: `label` is the column, `group` the stack band."""

    label: str
    group: str
    value: float


@dataclass(frozen=True)
class FunnelStage:
    """One funnel step. Order is the order you pass them in — never re-sorted."""

    label: str
    count: float


@dataclass(frozen=True)
class SurvivalPoint:
    """A Kaplan-Meier estimate at time `t`; `lo`/`hi` draw the CI band."""

    t: float
    survival: float
    lo: float | None = None
    hi: float | None = None
    group: str | None = None


@dataclass(frozen=True)
class ITSPoint:
    """An interrupted-time-series observation. `period` is "pre" or "post"."""

    t: str
    y: float
    period: str


@dataclass(frozen=True)
class DiDPoint:
    """A difference-in-differences observation for one `group` at time `t`."""

    t: str
    y: float
    group: str
    counterfactual: bool = False


@dataclass(frozen=True)
class ForestRow:
    """One segment's effect estimate with its interval, for a forest plot."""

    label: str
    estimate: float
    lo: float
    hi: float
    significant: bool = False


# ── plumbing ─────────────────────────────────────────────────────────────────

def require_rows(rows: Sequence[Any], emitter: str) -> None:
    if not rows:
        raise ValueError(f"{emitter}() requires at least one row; got none")


def base_spec(
    *,
    width: int,
    height: int,
    title: str | None,
    subtitle: str | None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA_URL,
        "width": width,
        "height": height,
    }
    if title:
        block: dict[str, Any] = {"text": title}
        if subtitle:
            block["subtitle"] = subtitle
        spec["title"] = block
    return spec


def ordered_sort(values: Sequence[str]) -> list[str]:
    """De-duplicated, input-order list — used as an explicit Vega-Lite `sort`.

    Vega-Lite sorts nominal domains alphabetically by default, which silently
    reorders funnel stages and pre/post periods into nonsense. Every categorical
    encoding an emitter produces pins its domain order explicitly.
    """
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def build(
    *,
    name: str,
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    title: str | None,
    subtitle: str | None,
    caption: str | None,
    provenance: ChartProvenance | None,
) -> ChartSpec:
    """Assemble + validate. `spec` carries no `data`; `ChartSpec` inlines `rows`.

    `ChartSpec.build` rather than `ChartSpec(...)` so a bug in an emitter surfaces
    as the `ChartSpecError` that says which rule it broke and where, instead of a
    pydantic `ValidationError` wrapping it.
    """
    prov = provenance.model_copy(deep=True) if provenance else ChartProvenance()
    if prov.generated_by is None:
        prov.generated_by = f"emitters.{name}"
    if prov.rows is None:
        prov.rows = len(rows)
    return ChartSpec.build(
        spec=spec,
        data=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=prov,
    )

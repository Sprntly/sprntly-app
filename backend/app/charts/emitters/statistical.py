"""Statistical emitters: Kaplan-Meier survival curves and segment forest plots."""
from __future__ import annotations

from typing import Any, Sequence

from app.charts.spec import ChartProvenance, ChartSpec
from app.charts.theme import NEGATIVE, NEUTRAL

from ._common import (
    ANNOTATION_COLOR,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    PRIMARY,
    ForestRow,
    SurvivalPoint,
    base_spec,
    build,
    ordered_sort,
    require_rows,
)


def survival_km(
    points: Sequence[SurvivalPoint],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    time_title: str = "Days",
    survival_title: str = "Survival",
    group_title: str = "Cohort",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """A Kaplan-Meier curve — stepped, because survival is a step function.

    `interpolate: "step-after"` is not cosmetic: a straight line between two
    estimates asserts that attrition happened smoothly between the observed
    event times, which is precisely what K-M does *not* claim. The CI band (drawn
    only when every point carries `lo`/`hi`) is likewise passed in, never fitted
    here.
    """
    require_rows(points, "survival_km")
    has_ci = all(p.lo is not None and p.hi is not None for p in points)
    rows: list[dict[str, Any]] = []
    for p in points:
        row: dict[str, Any] = {"t": p.t, "survival": p.survival}
        if has_ci:
            row["lo"] = p.lo
            row["hi"] = p.hi
        if p.group is not None:
            row["group"] = p.group
        rows.append(row)

    multi = any(p.group is not None for p in points)
    color: dict[str, Any] | None = None
    if multi:
        color = {
            "field": "group",
            "type": "nominal",
            "title": group_title,
            "sort": ordered_sort([p.group or "" for p in points]),
        }

    x_enc = {"field": "t", "type": "quantitative", "title": time_title}
    y_enc = {
        "field": "survival",
        "type": "quantitative",
        "title": survival_title,
        "scale": {"domain": [0, 1]},
        "axis": {"format": "%"},
    }

    layers: list[dict[str, Any]] = []
    if has_ci:
        band_enc: dict[str, Any] = {
            "x": x_enc,
            "y": {**y_enc, "field": "lo"},
            "y2": {"field": "hi"},
        }
        if color:
            band_enc["color"] = color
        band_mark: dict[str, Any] = {
            "type": "area",
            "opacity": 0.15,
            "interpolate": "step-after",
        }
        if not multi:
            band_mark["color"] = PRIMARY
        layers.append({"mark": band_mark, "encoding": band_enc})
    line_enc: dict[str, Any] = {"x": x_enc, "y": y_enc}
    if color:
        line_enc["color"] = color
    line_mark: dict[str, Any] = {
        "type": "line",
        "interpolate": "step-after",
        "strokeWidth": 2,
        "tooltip": True,
    }
    if not multi:
        line_mark["color"] = PRIMARY
    layers.append({"mark": line_mark, "encoding": line_enc})

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["layer"] = layers
    return build(
        name="survival_km",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def segment_forest(
    segments: Sequence[ForestRow],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    effect_title: str = "Effect",
    null_value: float = 0.0,
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int | None = None,
) -> ChartSpec:
    """A forest plot of per-segment effects with intervals and a null line.

    `ForestRow.significant` is the caller's verdict — after whatever multiplicity
    correction (BH-FDR in the DS engine) applies — and it only drives colour.
    Nothing is re-tested here, and nothing infers significance from whether the
    interval crosses `null_value`, because with a corrected procedure those two
    can legitimately disagree.

    The height defaults to a per-row allowance rather than a fixed box, so 3
    segments and 30 segments both stay readable.
    """
    require_rows(segments, "segment_forest")
    rows = [
        {
            "label": s.label,
            "estimate": s.estimate,
            "lo": s.lo,
            "hi": s.hi,
            "significant": "Significant" if s.significant else "Not significant",
        }
        for s in segments
    ]

    label_enc: dict[str, Any] = {
        "field": "label",
        "type": "nominal",
        "title": None,
        "sort": ordered_sort([s.label for s in segments]),
    }
    color_enc: dict[str, Any] = {
        "field": "significant",
        "type": "nominal",
        "title": None,
        "scale": {
            "domain": ["Significant", "Not significant"],
            "range": [NEGATIVE, NEUTRAL],
        },
    }
    resolved_height = height if height is not None else max(120, 26 * len(segments) + 40)

    spec = base_spec(
        width=width, height=resolved_height, title=title, subtitle=subtitle
    )
    spec["layer"] = [
        {
            "data": {"values": [{"null_value": null_value}]},
            "mark": {
                "type": "rule",
                "strokeDash": [4, 3],
                "strokeWidth": 1,
                "color": ANNOTATION_COLOR,
            },
            "encoding": {"x": {"field": "null_value", "type": "quantitative"}},
        },
        {
            "mark": {"type": "rule", "strokeWidth": 1.5},
            "encoding": {
                "y": label_enc,
                "x": {"field": "lo", "type": "quantitative", "title": effect_title},
                "x2": {"field": "hi"},
                "color": color_enc,
            },
        },
        {
            "mark": {"type": "point", "filled": True, "size": 70, "tooltip": True},
            "encoding": {
                "y": label_enc,
                "x": {"field": "estimate", "type": "quantitative", "title": effect_title},
                "color": color_enc,
            },
        },
    ]
    return build(
        name="segment_forest",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )

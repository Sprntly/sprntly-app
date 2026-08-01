"""Time-axis emitters: plain series, series with a CI band, ITS and DiD."""
from __future__ import annotations

from typing import Any, Literal, Sequence

from app.charts.spec import ChartProvenance, ChartSpec

from ._common import (
    ANNOTATION_COLOR,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    PRIMARY,
    CIPoint,
    DiDPoint,
    ITSPoint,
    TimePoint,
    base_spec,
    build,
    ordered_sort,
    require_rows,
)

XType = Literal["temporal", "ordinal"]


def _x(field: str, x_type: XType, title: str | None) -> dict[str, Any]:
    enc: dict[str, Any] = {"field": field, "type": x_type, "title": title}
    if x_type == "temporal":
        enc["axis"] = {"format": "%b %d", "labelAngle": 0}
    else:
        enc["axis"] = {"labelAngle": 0}
    return enc


def timeseries(
    points: Sequence[TimePoint],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    y_title: str = "Value",
    x_title: str = "Date",
    series_title: str = "Series",
    x_type: XType = "temporal",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """A line per `series` over time. One line when no point sets `series`."""
    require_rows(points, "timeseries")
    rows = [
        {"t": p.t, "y": p.y, **({"series": p.series} if p.series is not None else {})}
        for p in points
    ]
    multi = any(p.series is not None for p in points)

    encoding: dict[str, Any] = {
        "x": _x("t", x_type, x_title),
        "y": {"field": "y", "type": "quantitative", "title": y_title},
    }
    if multi:
        encoding["color"] = {
            "field": "series",
            "type": "nominal",
            "title": series_title,
            "sort": ordered_sort([p.series or "" for p in points]),
        }

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["mark"] = {"type": "line", "point": True, "tooltip": True}
    if not multi:
        # The point overlay does NOT inherit the line's colour — with
        # `point: true` Vega gives it the default mark colour, which is how a
        # single-series line ends up navy-dotted in the Sprntly blue. Colour both.
        spec["mark"]["color"] = PRIMARY
        spec["mark"]["point"] = {"filled": True, "color": PRIMARY}
    spec["encoding"] = encoding
    return build(
        name="timeseries",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def timeseries_with_ci(
    points: Sequence[CIPoint],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    y_title: str = "Value",
    x_title: str = "Date",
    series_title: str = "Series",
    x_type: XType = "temporal",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """A line with its confidence band — the band is an area layer under the line.

    The interval is *passed in*, never derived here: the emitter draws statistics,
    it does not compute them.
    """
    require_rows(points, "timeseries_with_ci")
    rows = [
        {
            "t": p.t,
            "y": p.y,
            "lo": p.lo,
            "hi": p.hi,
            **({"series": p.series} if p.series is not None else {}),
        }
        for p in points
    ]
    multi = any(p.series is not None for p in points)
    color: dict[str, Any] | None = None
    if multi:
        color = {
            "field": "series",
            "type": "nominal",
            "title": series_title,
            "sort": ordered_sort([p.series or "" for p in points]),
        }

    band_enc: dict[str, Any] = {
        "x": _x("t", x_type, x_title),
        "y": {"field": "lo", "type": "quantitative", "title": y_title},
        "y2": {"field": "hi"},
    }
    line_enc: dict[str, Any] = {
        "x": _x("t", x_type, x_title),
        "y": {"field": "y", "type": "quantitative", "title": y_title},
    }
    if color:
        band_enc["color"] = color
        line_enc["color"] = color

    band_mark: dict[str, Any] = {"type": "area", "opacity": 0.18}
    line_mark: dict[str, Any] = {"type": "line", "point": True, "tooltip": True}
    if not multi:
        band_mark["color"] = PRIMARY
        line_mark["color"] = PRIMARY
        line_mark["point"] = {"filled": True, "color": PRIMARY}  # see timeseries()

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["layer"] = [
        {"mark": band_mark, "encoding": band_enc},
        {"mark": line_mark, "encoding": line_enc},
    ]
    return build(
        name="timeseries_with_ci",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def _intervention_layer(
    intervention_at: str,
    x_type: XType,
    label: str | None,
) -> list[dict[str, Any]]:
    """A dashed rule (plus optional label) at the moment of the intervention.

    Carries its own inline single-row data so it never has to filter the main
    rows — the emitters are transform-free by construction, which is what keeps
    `spec.py`'s "no expression bindings" rule from ever being inconvenient.
    """
    rule_data = {"values": [{"at": intervention_at}]}
    x_enc = {"field": "at", "type": x_type}
    layers: list[dict[str, Any]] = [
        {
            "data": rule_data,
            "mark": {
                "type": "rule",
                "strokeDash": [4, 3],
                "strokeWidth": 1,
                "color": ANNOTATION_COLOR,
            },
            "encoding": {"x": x_enc},
        }
    ]
    if label:
        layers.append(
            {
                "data": rule_data,
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "top",
                    "dx": 4,
                    "dy": 2,
                    "text": label,
                    "color": ANNOTATION_COLOR,
                },
                "encoding": {"x": x_enc},
            }
        )
    return layers


def its(
    points: Sequence[ITSPoint],
    *,
    intervention_at: str,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    y_title: str = "Value",
    x_title: str = "Date",
    intervention_label: str | None = "Intervention",
    x_type: XType = "temporal",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """Interrupted time series: pre and post segments, split at the intervention.

    The two segments are separate lines because `period` colours them — a single
    line across the cut would visually assert continuity the analysis is
    explicitly testing for. Points are drawn so the reader sees the observations,
    not just the fit.
    """
    require_rows(points, "its")
    rows = [{"t": p.t, "y": p.y, "period": p.period} for p in points]
    period_sort = ordered_sort([p.period for p in points])

    shared: dict[str, Any] = {
        "x": _x("t", x_type, x_title),
        "y": {"field": "y", "type": "quantitative", "title": y_title},
        "color": {
            "field": "period",
            "type": "nominal",
            "title": "Period",
            "sort": period_sort,
        },
    }

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["layer"] = [
        {"mark": {"type": "line", "strokeWidth": 2}, "encoding": shared},
        {"mark": {"type": "point", "filled": True, "tooltip": True}, "encoding": shared},
        *_intervention_layer(intervention_at, x_type, intervention_label),
    ]
    return build(
        name="its",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def did(
    points: Sequence[DiDPoint],
    *,
    intervention_at: str,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    y_title: str = "Value",
    x_title: str = "Date",
    group_title: str = "Group",
    intervention_label: str | None = "Intervention",
    x_type: XType = "temporal",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """Difference-in-differences: treated vs control, with the counterfactual dashed.

    `DiDPoint.counterfactual=True` rows are emitted as a `kind` of
    `"counterfactual"` and dashed, so the estimated parallel trend is never
    mistaken for an observation. That distinction is the entire claim of a DiD.
    """
    require_rows(points, "did")
    rows = [
        {
            "t": p.t,
            "y": p.y,
            "group": p.group,
            "kind": "counterfactual" if p.counterfactual else "observed",
        }
        for p in points
    ]

    encoding: dict[str, Any] = {
        "x": _x("t", x_type, x_title),
        "y": {"field": "y", "type": "quantitative", "title": y_title},
        "color": {
            "field": "group",
            "type": "nominal",
            "title": group_title,
            "sort": ordered_sort([p.group for p in points]),
        },
        "strokeDash": {
            "field": "kind",
            "type": "nominal",
            "title": "Series",
            "sort": ordered_sort([row["kind"] for row in rows]),
        },
        "detail": {"field": "kind", "type": "nominal"},
    }

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["layer"] = [
        {"mark": {"type": "line", "strokeWidth": 2, "tooltip": True}, "encoding": encoding},
        *_intervention_layer(intervention_at, x_type, intervention_label),
    ]
    return build(
        name="did",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )

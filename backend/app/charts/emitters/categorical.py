"""Categorical emitters: bar, stacked bar, funnel, distribution."""
from __future__ import annotations

import math
from typing import Any, Literal, Sequence

from app.charts.spec import ChartProvenance, ChartSpec

from ._common import (
    ANNOTATION_COLOR,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    PRIMARY,
    Category,
    FunnelStage,
    StackedCell,
    base_spec,
    build,
    ordered_sort,
    require_rows,
)

Orientation = Literal["vertical", "horizontal"]


def bar(
    categories: Sequence[Category],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    value_title: str = "Value",
    label_title: str = "",
    orientation: Orientation = "vertical",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """One bar per category, in the order given.

    The domain order is pinned (`sort`) rather than left to Vega-Lite's
    alphabetical default — callers that want ranked bars sort their input, and
    callers that pass an inherently ordered set (stages, buckets, months) get
    that order preserved instead of scrambled.
    """
    require_rows(categories, "bar")
    rows = [{"label": c.label, "value": c.value} for c in categories]
    label_enc: dict[str, Any] = {
        "field": "label",
        "type": "nominal",
        "title": label_title or None,
        "sort": ordered_sort([c.label for c in categories]),
    }
    value_enc: dict[str, Any] = {
        "field": "value",
        "type": "quantitative",
        "title": value_title,
    }

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["mark"] = {"type": "bar", "tooltip": True, "color": PRIMARY}
    if orientation == "horizontal":
        spec["encoding"] = {"y": label_enc, "x": value_enc}
    else:
        spec["encoding"] = {"x": label_enc, "y": value_enc}
    return build(
        name="bar",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def stacked_bar(
    cells: Sequence[StackedCell],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    value_title: str = "Value",
    label_title: str = "",
    group_title: str = "Group",
    normalize: bool = False,
    orientation: Orientation = "vertical",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """Bars split into bands. `normalize=True` stacks to 100% instead of totals."""
    require_rows(cells, "stacked_bar")
    rows = [{"label": c.label, "group": c.group, "value": c.value} for c in cells]

    label_enc: dict[str, Any] = {
        "field": "label",
        "type": "nominal",
        "title": label_title or None,
        "sort": ordered_sort([c.label for c in cells]),
    }
    value_enc: dict[str, Any] = {
        "field": "value",
        "type": "quantitative",
        "title": ("Share" if normalize else value_title),
        "stack": ("normalize" if normalize else "zero"),
    }
    if normalize:
        value_enc["axis"] = {"format": "%"}

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["mark"] = {"type": "bar", "tooltip": True}
    encoding: dict[str, Any] = {
        "color": {
            "field": "group",
            "type": "nominal",
            "title": group_title,
            "sort": ordered_sort([c.group for c in cells]),
        }
    }
    if orientation == "horizontal":
        encoding["y"] = label_enc
        encoding["x"] = value_enc
    else:
        encoding["x"] = label_enc
        encoding["y"] = value_enc
    spec["encoding"] = encoding
    return build(
        name="stacked_bar",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def funnel(
    stages: Sequence[FunnelStage],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    count_title: str = "Users",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """A funnel as ordered horizontal bars, annotated with both conversion rates.

    Two rates are computed **in Python**, not in a Vega transform, and both ride
    along in the rows so the table fallback carries them too:

    * `pct_of_first` — share of the entry cohort still present at this stage;
    * `pct_of_prev` — the step conversion, which is where the drop actually is.

    A funnel that only shows `pct_of_first` hides which step is broken, which is
    the single question a funnel gets asked.
    """
    require_rows(stages, "funnel")
    first = stages[0].count
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for stage in stages:
        pct_first = (stage.count / first) if first else 0.0
        pct_prev = (stage.count / prev) if prev else 1.0
        rows.append(
            {
                "label": stage.label,
                "count": stage.count,
                "pct_of_first": round(pct_first, 4),
                "pct_of_prev": round(pct_prev, 4),
                "annotation": f"{_fmt_count(stage.count)}  ({pct_first * 100:.0f}%)",
            }
        )
        prev = stage.count

    label_enc: dict[str, Any] = {
        "field": "label",
        "type": "nominal",
        "title": None,
        "sort": ordered_sort([s.label for s in stages]),
    }
    count_enc: dict[str, Any] = {
        "field": "count",
        "type": "quantitative",
        "title": count_title,
    }

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["layer"] = [
        {
            "mark": {"type": "bar", "tooltip": True, "color": PRIMARY},
            "encoding": {"y": label_enc, "x": count_enc},
        },
        {
            "mark": {
                "type": "text",
                "align": "left",
                "baseline": "middle",
                "dx": 6,
                "color": ANNOTATION_COLOR,
            },
            "encoding": {
                "y": label_enc,
                "x": count_enc,
                "text": {"field": "annotation", "type": "nominal"},
            },
        },
    ]
    return build(
        name="funnel",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )


def _fmt_count(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def distribution(
    values: Sequence[float],
    *,
    bins: int = 20,
    title: str | None = None,
    subtitle: str | None = None,
    caption: str | None = None,
    value_title: str = "Value",
    count_title: str = "Count",
    provenance: ChartProvenance | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> ChartSpec:
    """A histogram whose bins are computed **here**, not by a Vega bin transform.

    Binning in Python keeps the emitter data-closed in the strongest sense: the
    rows that ship with the chart *are* the bars, so the table fallback shows the
    same numbers the picture does, and the spec has no transform at all. It also
    means the bin edges are reproducible and testable.

    Degenerate inputs are handled rather than exploded: a single distinct value
    produces one bin around it; non-finite values are dropped.
    """
    finite = [float(v) for v in values if math.isfinite(float(v))]
    require_rows(finite, "distribution")
    if bins < 1:
        raise ValueError("distribution() needs bins >= 1")

    lo, hi = min(finite), max(finite)
    if lo == hi:  # every value identical — one bin, centred, non-zero width
        half = abs(lo) * 0.005 or 0.5
        edges = [lo - half, lo + half]
    else:
        step = (hi - lo) / bins
        edges = [lo + step * i for i in range(bins)] + [hi]

    counts = [0] * (len(edges) - 1)
    for value in finite:
        idx = len(counts) - 1
        if value < hi:
            for i in range(len(counts)):
                if edges[i] <= value < edges[i + 1]:
                    idx = i
                    break
        counts[idx] += 1

    rows = [
        {
            "bin_start": round(edges[i], 6),
            "bin_end": round(edges[i + 1], 6),
            "count": counts[i],
        }
        for i in range(len(counts))
    ]

    spec = base_spec(width=width, height=height, title=title, subtitle=subtitle)
    spec["mark"] = {"type": "bar", "tooltip": True, "color": PRIMARY}
    spec["encoding"] = {
        "x": {"field": "bin_start", "type": "quantitative", "title": value_title},
        "x2": {"field": "bin_end"},
        "y": {"field": "count", "type": "quantitative", "title": count_title},
    }
    return build(
        name="distribution",
        spec=spec,
        rows=rows,
        title=title,
        subtitle=subtitle,
        caption=caption,
        provenance=provenance,
    )

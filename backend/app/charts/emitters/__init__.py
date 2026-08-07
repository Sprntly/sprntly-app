"""Deterministic chart emitters — one typed function per chart kind.

Import the emitter, not the JSON::

    from app.charts import emitters
    chart = emitters.funnel(
        [emitters.FunnelStage("Signed up", 1200), emitters.FunnelStage("Activated", 430)],
        title="Activation funnel",
    )

Every function returns a validated `ChartSpec`; every input is a typed row
(`emitters._common`), never a dict blob. See `_common` for why.
"""
from ._common import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    Category,
    CIPoint,
    DiDPoint,
    ForestRow,
    FunnelStage,
    ITSPoint,
    StackedCell,
    SurvivalPoint,
    TimePoint,
)
from .categorical import bar, distribution, funnel, stacked_bar
from .statistical import segment_forest, survival_km
from .timeseries import did, its, timeseries, timeseries_with_ci

__all__ = [
    # typed rows
    "Category",
    "CIPoint",
    "DiDPoint",
    "ForestRow",
    "FunnelStage",
    "ITSPoint",
    "StackedCell",
    "SurvivalPoint",
    "TimePoint",
    "DEFAULT_WIDTH",
    "DEFAULT_HEIGHT",
    # emitters
    "bar",
    "did",
    "distribution",
    "funnel",
    "its",
    "segment_forest",
    "stacked_bar",
    "survival_km",
    "timeseries",
    "timeseries_with_ci",
]

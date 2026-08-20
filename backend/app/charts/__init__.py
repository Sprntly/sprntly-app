"""Sprntly charts — one spec contract, two renderers.

`ChartSpec` (Vega-Lite v6.4 + its own rows + provenance) is the single chart
contract. `render.py` turns it into static SVG/PNG for the HTML surfaces that
render in a no-scripts iframe; Phase 2's `VegaChart.tsx` will hand the identical
spec to `vega-embed` for the interactive React surfaces.

Nothing in the app calls this package yet — Phase 0 is the foundation only.
"""
from app.charts.spec import (
    ALTAIR_SCHEMA_VERSION,
    VEGA_LITE_SCHEMA_URL,
    VL_VERSION,
    ChartProvenance,
    ChartSpec,
    ChartSpecError,
    validate_vega_lite_spec,
)

__all__ = [
    "ALTAIR_SCHEMA_VERSION",
    "ChartProvenance",
    "ChartSpec",
    "ChartSpecError",
    "VEGA_LITE_SCHEMA_URL",
    "VL_VERSION",
    "validate_vega_lite_spec",
]

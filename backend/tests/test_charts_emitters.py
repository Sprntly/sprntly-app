"""The ten deterministic emitters: golden files, plus the maths they encode.

## The golden-file decision (read before adding one)

**Two tiers, and NO byte-exact SVG.**

*Tier 1 — the spec, byte-exact.* `fixtures/charts/<name>.spec.json` is the
emitted Vega-Lite document. It is pure Python dict-building with no renderer in
the loop, so it is identical on every machine, and it is the thing the contract
is actually about: which mark, which encodings, which sort order, which numbers.
A diff here is a real change to what we draw.

*Tier 2 — the render, structural.* `fixtures/charts/<name>.svg.json` records a
*signature* of the rendered SVG: element counts by tag, the set of colours, and
the text content. Not the bytes.

Byte-exact SVG goldens were considered and rejected. `vl_convert` bakes measured
text geometry into path coordinates, so the bytes depend on which fonts the host
can resolve — a golden generated on a developer Mac would fail on the Linux CI
runner for reasons that have nothing to do with the code. The signature was
chosen because it is *demonstrably* font-independent: rendering each of these
charts under `Liberation Sans`, `DejaVu Sans`, a Geist-first stack and a
deliberately unresolvable family produces four different byte streams and the
same signature every time. It still catches everything a chart golden is for —
a mark that changed type, a series that vanished, a palette that shifted, a label
that stopped being drawn — while ignoring sub-pixel geometry that no reader can
see.

The goldens are generated on macOS/arm64. That is safe *because* of the tier
split — tier 1 has no renderer in it, and tier 2 is the property the font probe
above shows to be host-independent — but "host-independent" had to be earned
once. The first version of these fixtures WAS machine-specific: five of them
were PDT artifacts, because Vega-Lite's default temporal scale is the host's
local time, so `timeseries` drew 25 text elements here and 26 under `TZ=UTC`.
Measured: unpinned, Tokyo loses `Jan 01` and Los Angeles loses `Jan 15`; pinned
to `scale: {"type": "utc"}`, all three are identical. `_x()` in
`emitters/timeseries.py` carries the pin, `test_every_temporal_encoding_pins_utc`
guards it on every emitter, and `test_the_same_chart_draws_the_same_dates_in_
every_timezone` re-runs the render under three timezones to guard the property
rather than only the spec.

That is the lesson worth keeping: **if CI disagrees with a golden, read the diff
— do not regenerate.** Regenerating on the CI host would have made this green
and left the real bug live, because the divergence that matters is not
Mac-vs-Linux, it is the server SVG against the same spec drawn in a reader's
browser.

Regenerate deliberately, never reflexively::

    cd backend && python -c "import sys; sys.path.insert(0, '.'); \
        from tests.test_charts_emitters import regenerate_goldens; regenerate_goldens()"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.charts import emitters as E
from app.charts.render import render_svg
from app.charts.spec import VEGA_LITE_SCHEMA_URL, ChartProvenance, ChartSpec

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "charts"


# ── the fixture charts (one per emitter) ─────────────────────────────────────

def _charts() -> dict[str, ChartSpec]:
    """Deliberately small, deliberately fixed. Every value here is a golden input."""
    return {
        "timeseries": E.timeseries(
            [
                E.TimePoint("2026-01-01", 120.0),
                E.TimePoint("2026-01-08", 138.0),
                E.TimePoint("2026-01-15", 131.0),
            ],
            title="Weekly active teams",
            y_title="Teams",
        ),
        "timeseries_multi": E.timeseries(
            [
                E.TimePoint("2026-01-01", 120.0, "Enterprise"),
                E.TimePoint("2026-01-08", 138.0, "Enterprise"),
                E.TimePoint("2026-01-01", 64.0, "Self-serve"),
                E.TimePoint("2026-01-08", 71.0, "Self-serve"),
            ],
            title="Weekly active teams by plan",
            y_title="Teams",
        ),
        "timeseries_with_ci": E.timeseries_with_ci(
            [
                E.CIPoint("2026-01-01", 0.42, 0.38, 0.46),
                E.CIPoint("2026-01-08", 0.47, 0.42, 0.52),
            ],
            title="Activation rate",
            y_title="Rate",
        ),
        "bar": E.bar(
            [
                E.Category("Search", 42.0),
                E.Category("Filters", 31.0),
                E.Category("Export", 12.0),
            ],
            title="Mentions by theme",
            value_title="Mentions",
        ),
        "bar_horizontal": E.bar(
            [E.Category("Search", 42.0), E.Category("Filters", 31.0)],
            title="Mentions by theme",
            orientation="horizontal",
        ),
        "stacked_bar": E.stacked_bar(
            [
                E.StackedCell("Jan", "New", 30.0),
                E.StackedCell("Jan", "Returning", 70.0),
                E.StackedCell("Feb", "New", 45.0),
                E.StackedCell("Feb", "Returning", 65.0),
            ],
            title="Sessions by cohort",
        ),
        "stacked_bar_normalized": E.stacked_bar(
            [
                E.StackedCell("Jan", "New", 30.0),
                E.StackedCell("Jan", "Returning", 70.0),
            ],
            title="Session mix",
            normalize=True,
        ),
        "funnel": E.funnel(
            [
                E.FunnelStage("Signed up", 1200),
                E.FunnelStage("Created a company", 780),
                E.FunnelStage("Generated a PRD", 305),
            ],
            title="Onboarding funnel",
        ),
        "survival_km": E.survival_km(
            [
                E.SurvivalPoint(0, 1.0, 1.0, 1.0),
                E.SurvivalPoint(7, 0.82, 0.76, 0.88),
                E.SurvivalPoint(14, 0.71, 0.63, 0.79),
            ],
            title="Retention",
        ),
        "its": E.its(
            [
                E.ITSPoint("2026-01-01", 100.0, "pre"),
                E.ITSPoint("2026-01-08", 104.0, "pre"),
                E.ITSPoint("2026-01-22", 131.0, "post"),
                E.ITSPoint("2026-01-29", 136.0, "post"),
            ],
            intervention_at="2026-01-15",
            title="Weekly signups around the change",
        ),
        "did": E.did(
            [
                E.DiDPoint("2026-01-01", 100.0, "Treated"),
                E.DiDPoint("2026-02-01", 130.0, "Treated"),
                E.DiDPoint("2026-01-01", 95.0, "Control"),
                E.DiDPoint("2026-02-01", 101.0, "Control"),
                E.DiDPoint("2026-02-01", 106.0, "Treated", True),
            ],
            intervention_at="2026-01-15",
            title="Treated vs control",
        ),
        "segment_forest": E.segment_forest(
            [
                E.ForestRow("Enterprise", 0.18, 0.09, 0.27, True),
                E.ForestRow("Mid-market", 0.04, -0.03, 0.11),
                E.ForestRow("Self-serve", -0.12, -0.21, -0.03, True),
            ],
            title="Effect by segment",
        ),
        "distribution": E.distribution(
            [1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 9.0],
            bins=4,
            title="Time to first PRD",
        ),
    }


# ── the two signatures ───────────────────────────────────────────────────────

def spec_golden(chart: ChartSpec) -> str:
    return json.dumps(chart.to_payload(), indent=2, sort_keys=True) + "\n"


def svg_signature(svg: str) -> dict:
    """Host-independent structure of a rendered SVG. See the module docstring."""
    root = ET.fromstring(svg)
    tags: dict[str, int] = {}
    colors: set[str] = set()
    texts: list[str] = []
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        tags[tag] = tags.get(tag, 0) + 1
        for attribute in ("fill", "stroke"):
            value = element.get(attribute)
            if value and value != "none":
                colors.add(value.lower())
        if tag == "text" and element.text:
            texts.append(element.text)
    return {"tags": tags, "colors": sorted(colors), "texts": sorted(texts)}


def svg_golden(chart: ChartSpec) -> str:
    svg = render_svg(chart)
    assert svg.startswith("<svg"), "emitter fell back to a table while generating a golden"
    return json.dumps(svg_signature(svg), indent=2, sort_keys=True) + "\n"


def regenerate_goldens() -> None:  # pragma: no cover - operator entry point
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, chart in _charts().items():
        (GOLDEN_DIR / f"{name}.spec.json").write_text(spec_golden(chart), encoding="utf-8")
        (GOLDEN_DIR / f"{name}.svg.json").write_text(svg_golden(chart), encoding="utf-8")
    print(f"regenerated {len(_charts()) * 2} goldens in {GOLDEN_DIR}")


CHART_NAMES = sorted(_charts())


@pytest.mark.parametrize("name", CHART_NAMES)
def test_spec_golden(name):
    """Tier 1: the emitted Vega-Lite document, byte-exact."""
    path = GOLDEN_DIR / f"{name}.spec.json"
    assert path.exists(), f"missing golden {path} — see this module's docstring"
    assert spec_golden(_charts()[name]) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CHART_NAMES)
def test_svg_signature_golden(name):
    """Tier 2: the rendered structure, host-independent."""
    path = GOLDEN_DIR / f"{name}.svg.json"
    assert path.exists(), f"missing golden {path} — see this module's docstring"
    assert svg_golden(_charts()[name]) == path.read_text(encoding="utf-8")


def test_every_emitter_has_a_golden():
    """A new emitter without a fixture would otherwise be silently untested."""
    exported = {
        name
        for name in E.__all__
        if callable(getattr(E, name)) and not name[0].isupper()
    }
    covered = {
        # fixture name -> emitter name (the variants share an emitter)
        "timeseries_multi": "timeseries",
        "bar_horizontal": "bar",
        "stacked_bar_normalized": "stacked_bar",
    }
    exercised = {covered.get(name, name) for name in CHART_NAMES}
    assert exported <= exercised, f"emitters with no golden: {sorted(exported - exercised)}"


# ── the contract every emitter shares ────────────────────────────────────────

@pytest.mark.parametrize("name", CHART_NAMES)
def test_every_chart_is_data_closed_and_carries_its_rows(name):
    chart = _charts()[name]
    assert chart.data, "a chart must ship the rows it was built from"
    assert chart.spec["$schema"] == VEGA_LITE_SCHEMA_URL
    assert "url" not in json.dumps(chart.spec)
    assert chart.provenance is not None
    assert chart.provenance.rows == len(chart.data)
    assert chart.provenance.generated_by.startswith("emitters.")


@pytest.mark.parametrize("name", CHART_NAMES)
def test_every_chart_renders(name):
    assert render_svg(_charts()[name]).startswith("<svg")


def _temporal_encodings(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("type") == "temporal":
            out.append(node)
        for value in node.values():
            _temporal_encodings(value, out)
    elif isinstance(node, list):
        for item in node:
            _temporal_encodings(item, out)
    return out


@pytest.mark.parametrize("name", CHART_NAMES)
def test_every_temporal_encoding_pins_utc(name):
    """Vega-Lite's default temporal scale is the HOST's local time.

    That default is a cross-renderer bug, not a preference: the same stored spec
    renders `Jan 15` in the server SVG on a UTC box and drops it for a reader in
    Los Angeles when the browser draws it. One chart, two pictures, no error on
    either side. It also made five of these goldens PDT artifacts.
    """
    for encoding in _temporal_encodings(_charts()[name].spec):
        assert encoding.get("scale", {}).get("type") == "utc", (
            f"temporal encoding on {encoding.get('field')!r} has no UTC scale"
        )


def _texts_under_tz(tz: str, chart_name: str) -> list[str]:
    """Render in a subprocess with `TZ` set, and report the drawn text."""
    script = (
        "import json,sys;"
        "sys.path.insert(0, '.');"
        "from tests.test_charts_emitters import _charts, svg_signature;"
        "from app.charts.render import render_svg;"
        f"print(json.dumps(svg_signature(render_svg(_charts()[{chart_name!r}]))['texts']))"
    )
    env = {**os.environ, "TZ": tz}
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("name", ["timeseries", "its"])
def test_the_same_chart_draws_the_same_dates_in_every_timezone(name):
    """The regression itself, not just the spec property that prevents it.

    Before the UTC pin these two gained and lost axis labels depending on the
    host: `timeseries` drew 25 text elements in PDT and 26 under `TZ=UTC`.
    """
    assert _texts_under_tz("UTC", name) == _texts_under_tz("Asia/Tokyo", name)
    assert _texts_under_tz("UTC", name) == _texts_under_tz("America/Los_Angeles", name)


VEGA_DEFAULT_BLUE = "#4c78a8"


@pytest.mark.parametrize("name", CHART_NAMES)
def test_no_chart_falls_back_to_vega_default_colours(name):
    """The shared theme only supplies `range.category`, which Vega applies to
    *encoded* colour. A single-series chart has none, so it would draw in
    Vega-Lite's own steel blue unless the emitter names the colour in the spec —
    and it would do so in the browser too, since both renderers read that config.
    Every emitter therefore names its colours; this is the guard.
    """
    colors = svg_signature(render_svg(_charts()[name]))["colors"]
    assert VEGA_DEFAULT_BLUE not in colors


EMPTY_CALLS = {
    "timeseries": lambda: E.timeseries([]),
    "timeseries_with_ci": lambda: E.timeseries_with_ci([]),
    "bar": lambda: E.bar([]),
    "stacked_bar": lambda: E.stacked_bar([]),
    "funnel": lambda: E.funnel([]),
    "survival_km": lambda: E.survival_km([]),
    "its": lambda: E.its([], intervention_at="2026-01-15"),
    "did": lambda: E.did([], intervention_at="2026-01-15"),
    "segment_forest": lambda: E.segment_forest([]),
    "distribution": lambda: E.distribution([]),
}


@pytest.mark.parametrize("name", sorted(EMPTY_CALLS))
def test_emitters_refuse_empty_input(name):
    """An empty chart is a lie the report should not print — the caller checks first."""
    with pytest.raises(ValueError, match="at least one row"):
        EMPTY_CALLS[name]()


def test_caller_provenance_survives_and_is_topped_up():
    chart = E.bar(
        [E.Category("a", 1)],
        title="t",
        provenance=ChartProvenance(source="product analytics", evidence_ids=["ev-1"]),
    )
    assert chart.provenance.source == "product analytics"
    assert chart.provenance.evidence_ids == ["ev-1"]
    assert chart.provenance.generated_by == "emitters.bar"
    assert chart.provenance.rows == 1


# ── the statistics the emitters are responsible for ──────────────────────────

def test_funnel_computes_both_conversion_rates():
    chart = E.funnel(
        [
            E.FunnelStage("Signed up", 1000),
            E.FunnelStage("Activated", 500),
            E.FunnelStage("Retained", 100),
        ],
        title="f",
    )
    assert [row["pct_of_first"] for row in chart.data] == [1.0, 0.5, 0.1]
    # The step conversion is the one that says WHERE the drop is: 50% then 20%.
    assert [row["pct_of_prev"] for row in chart.data] == [1.0, 0.5, 0.2]


def test_funnel_survives_an_empty_entry_cohort_without_dividing_by_zero():
    chart = E.funnel([E.FunnelStage("Signed up", 0), E.FunnelStage("Activated", 0)], title="f")
    assert [row["pct_of_first"] for row in chart.data] == [0.0, 0.0]


def test_funnel_keeps_stage_order_even_when_counts_rise():
    """Stages are a sequence, not a ranking — Vega must not re-sort them."""
    chart = E.funnel(
        [E.FunnelStage("Zebra", 10), E.FunnelStage("Apple", 90)], title="f"
    )
    sort = chart.spec["layer"][0]["encoding"]["y"]["sort"]
    assert sort == ["Zebra", "Apple"]


def test_distribution_bins_are_computed_in_python_not_by_a_transform():
    chart = E.distribution([0.0, 1.0, 2.0, 3.0], bins=2, title="d")
    assert "transform" not in json.dumps(chart.spec)
    assert [row["count"] for row in chart.data] == [2, 2]
    assert chart.data[0]["bin_start"] == 0.0
    assert chart.data[-1]["bin_end"] == 3.0


def test_distribution_puts_the_maximum_in_the_last_bin():
    chart = E.distribution([1.0, 2.0, 3.0], bins=3, title="d")
    assert sum(row["count"] for row in chart.data) == 3
    assert chart.data[-1]["count"] == 1


def test_distribution_handles_a_single_repeated_value():
    chart = E.distribution([5.0, 5.0, 5.0], bins=10, title="d")
    assert len(chart.data) == 1
    assert chart.data[0]["count"] == 3
    assert chart.data[0]["bin_start"] < 5.0 < chart.data[0]["bin_end"]


def test_distribution_drops_non_finite_values():
    chart = E.distribution([1.0, float("nan"), 2.0, float("inf")], bins=2, title="d")
    assert sum(row["count"] for row in chart.data) == 2


def test_distribution_rejects_a_nonsense_bin_count():
    with pytest.raises(ValueError, match="bins >= 1"):
        E.distribution([1.0, 2.0], bins=0)


def test_survival_curve_is_stepped_not_interpolated():
    """A straight line between event times would claim smooth attrition."""
    chart = E.survival_km([E.SurvivalPoint(0, 1.0), E.SurvivalPoint(7, 0.8)], title="s")
    line = chart.spec["layer"][-1]["mark"]
    assert line["interpolate"] == "step-after"


def test_survival_ci_band_only_appears_when_every_point_has_one():
    with_ci = E.survival_km(
        [E.SurvivalPoint(0, 1.0, 0.9, 1.0), E.SurvivalPoint(7, 0.8, 0.7, 0.9)], title="s"
    )
    partial = E.survival_km(
        [E.SurvivalPoint(0, 1.0, 0.9, 1.0), E.SurvivalPoint(7, 0.8)], title="s"
    )
    assert len(with_ci.spec["layer"]) == 2
    assert len(partial.spec["layer"]) == 1
    assert "lo" not in partial.data[0]


def test_segment_forest_does_not_infer_significance_from_the_interval():
    """With a corrected procedure, "crosses zero" and "not significant" can differ."""
    chart = E.segment_forest(
        [E.ForestRow("crosses zero but flagged", 0.01, -0.2, 0.3, significant=True)],
        title="f",
    )
    assert chart.data[0]["significant"] == "Significant"


def test_segment_forest_height_grows_with_the_number_of_rows():
    small = E.segment_forest([E.ForestRow("a", 0.1, 0.0, 0.2)], title="f")
    large = E.segment_forest(
        [E.ForestRow(f"s{i}", 0.1, 0.0, 0.2) for i in range(20)], title="f"
    )
    assert large.spec["height"] > small.spec["height"]


def test_did_marks_the_counterfactual_as_such():
    chart = E.did(
        [
            E.DiDPoint("2026-01-01", 10.0, "Treated"),
            E.DiDPoint("2026-02-01", 12.0, "Treated", counterfactual=True),
        ],
        intervention_at="2026-01-15",
        title="d",
    )
    kinds = [row["kind"] for row in chart.data]
    assert kinds == ["observed", "counterfactual"]
    assert chart.spec["layer"][0]["encoding"]["strokeDash"]["field"] == "kind"


def test_its_draws_the_intervention_rule_from_its_own_inline_data():
    chart = E.its(
        [E.ITSPoint("2026-01-01", 1.0, "pre"), E.ITSPoint("2026-02-01", 2.0, "post")],
        intervention_at="2026-01-15",
        title="i",
    )
    rule = chart.spec["layer"][2]
    assert rule["mark"]["type"] == "rule"
    assert rule["data"]["values"] == [{"at": "2026-01-15"}]
    # The main rows stay clean: the annotation is not smuggled into the data set.
    assert all("at" not in row for row in chart.data)


def test_normalized_stack_uses_a_percentage_axis():
    chart = E.stacked_bar(
        [E.StackedCell("Jan", "New", 1.0), E.StackedCell("Jan", "Old", 3.0)],
        title="s",
        normalize=True,
    )
    assert chart.spec["encoding"]["y"]["stack"] == "normalize"
    assert chart.spec["encoding"]["y"]["axis"]["format"] == "%"


def test_multi_series_timeseries_pins_the_legend_order():
    chart = E.timeseries(
        [E.TimePoint("2026-01-01", 1.0, "Zebra"), E.TimePoint("2026-01-02", 2.0, "Apple")],
        title="t",
    )
    assert chart.spec["encoding"]["color"]["sort"] == ["Zebra", "Apple"]


def test_single_series_timeseries_has_no_legend():
    chart = E.timeseries([E.TimePoint("2026-01-01", 1.0)], title="t")
    assert "color" not in chart.spec["encoding"]
    assert "series" not in chart.data[0]

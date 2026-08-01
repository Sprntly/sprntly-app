"""Server-side rendering: the happy path, and every way it is allowed to fail.

The contract under test is "a chart never takes down a report". So most of this
file is failure injection — renderer raises, renderer hangs, renderer returns
junk, input too big, output too big — each asserting the same two things: the
caller gets usable markup back, and the failure is *counted*.

PNG is asserted on magic bytes and dimensions only. Comparing PNG pixels across
platforms is a rasteriser bug report waiting to happen; the useful question is
"did we get a real PNG at the size we asked for".
"""
from __future__ import annotations

import struct
import time

import pytest
import vl_convert

from app.charts import emitters as E
from app.charts.render import (
    DEFAULT_PNG_SCALE,
    MAX_PNG_BYTES,
    MAX_ROWS,
    MAX_SVG_BYTES,
    render_all_svg,
    render_png,
    render_svg,
    render_table_html,
)
from app.charts.spec import VL_VERSION, ChartSpec, ChartSpecError


@pytest.fixture
def chart() -> ChartSpec:
    return E.bar(
        [E.Category("alpha", 3), E.Category("beta", 5)],
        title="Bar",
        caption="A caption",
    )


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR is always the first chunk: 8-byte signature, 4-byte length, 4-byte type.
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# ── happy paths ──────────────────────────────────────────────────────────────

def test_render_svg_returns_svg_and_counts_it(chart):
    stats: dict[str, int] = {}
    svg = render_svg(chart, stats=stats)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert stats == {"charts_rendered": 1}


def test_render_svg_draws_real_text_not_a_raster(chart):
    """The point of SVG over PNG: the words are selectable, searchable text."""
    svg = render_svg(chart)
    assert "<text" in svg
    assert "alpha" in svg and "Bar" in svg


def test_render_svg_applies_the_theme(chart):
    """The bars come out in the Sprntly palette, not Vega's default steel blue."""
    svg = render_svg(chart).lower()
    assert "#5b7fff" in svg
    assert "#4c78a8" not in svg  # vega-lite's own default category colour


def test_dark_mode_changes_the_background(chart):
    assert render_svg(chart, mode="light") != render_svg(chart, mode="dark")


def test_render_png_is_a_real_png_at_the_requested_scale(chart):
    stats: dict[str, int] = {}
    at_1x = render_png(chart, scale=1.0, stats=stats)
    at_2x = render_png(chart, scale=DEFAULT_PNG_SCALE, stats=stats)
    assert at_1x and at_1x.startswith(b"\x89PNG\r\n\x1a\n")
    assert at_2x and at_2x.startswith(b"\x89PNG\r\n\x1a\n")
    w1, h1 = _png_size(at_1x)
    w2, h2 = _png_size(at_2x)
    assert (w2, h2) == (w1 * 2, h1 * 2)
    assert stats == {"charts_rendered": 2}


def test_render_all_svg_renders_a_batch_into_one_stats_dict(chart):
    stats: dict[str, int] = {}
    out = render_all_svg([chart, chart, chart], stats=stats)
    assert len(out) == 3
    assert stats["charts_rendered"] == 3


# ── the renderer is called with both locks on ────────────────────────────────

def test_renderer_is_pinned_and_forbidden_from_fetching(chart, monkeypatch):
    captured: dict = {}

    def fake(payload, **kwargs):
        captured.update(kwargs)
        return "<svg></svg>"

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", fake)
    render_svg(chart)
    assert captured["vl_version"] == VL_VERSION  # never inferred from $schema
    assert captured["allowed_base_urls"] == []   # second lock, independent of spec.py
    assert captured["config"]["range"]["category"][0] == "#5B7FFF"


def test_a_url_that_somehow_reached_the_renderer_still_cannot_fetch(chart):
    """Belt and braces: bypass validation, confirm vl_convert refuses anyway."""
    chart.spec["data"] = {"url": "https://example.com/rows.json"}
    stats: dict[str, int] = {}
    out = render_svg(chart, stats=stats)
    assert out.startswith("<figure")
    assert stats == {"charts_dropped": 1}


# ── failure injection ────────────────────────────────────────────────────────

def test_renderer_exception_degrades_to_the_table(chart, monkeypatch):
    def boom(payload, **kwargs):
        raise RuntimeError("vl-convert exploded")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", boom)
    stats: dict[str, int] = {}
    out = render_svg(chart, stats=stats)
    assert out.startswith('<figure class="sprntly-chart-fallback">')
    assert "alpha" in out and "<table>" in out
    assert stats == {"charts_dropped": 1}


def test_renderer_junk_output_degrades_to_the_table(chart, monkeypatch):
    monkeypatch.setattr(vl_convert, "vegalite_to_svg", lambda p, **k: "not markup")
    stats: dict[str, int] = {}
    assert render_svg(chart, stats=stats).startswith("<figure")
    assert stats == {"charts_dropped": 1}


def test_timeout_bounds_the_wait_and_degrades(chart, monkeypatch):
    def slow(payload, **kwargs):
        time.sleep(2.0)
        return "<svg></svg>"

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", slow)
    stats: dict[str, int] = {}
    started = time.monotonic()
    out = render_svg(chart, timeout_s=0.05, stats=stats)
    elapsed = time.monotonic() - started
    assert out.startswith("<figure")
    assert stats == {"charts_dropped": 1}
    # The wait is bounded. The WORK is not — the daemon thread runs to completion;
    # see the module docstring in render.py. This asserts the caller's contract.
    assert elapsed < 1.0


def test_oversized_input_is_refused_before_the_renderer_sees_it(monkeypatch):
    big = E.timeseries(
        [E.TimePoint(f"2026-01-{(i % 28) + 1:02d}", float(i)) for i in range(MAX_ROWS + 1)],
        title="Too much",
    )

    def must_not_be_called(payload, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError("renderer was called despite the row cap")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", must_not_be_called)
    stats: dict[str, int] = {}
    assert render_svg(big, stats=stats).startswith("<figure")
    assert stats == {"charts_dropped": 1}


def _spec_carried(n_rows: int, title: str = "Spec-carried") -> ChartSpec:
    """The shape the contract blesses and Phase 1's altair sandbox always emits:
    rows inlined in the spec, envelope `data` empty."""
    return ChartSpec(
        spec={
            "data": {"values": [{"a": f"r{i}", "b": i} for i in range(n_rows)]},
            "mark": "bar",
            "encoding": {
                "x": {"field": "a", "type": "nominal"},
                "y": {"field": "b", "type": "quantitative"},
            },
        },
        title=title,
    )


def test_the_row_cap_measures_rows_wherever_they_live(monkeypatch):
    """`len(chart.data)` reads 0 on a spec-carried chart, so the cap that is the
    real defence (the timeout is not — it bounds the wait, not the work) never
    fired on the one shape Phase 1 always produces."""
    big = _spec_carried(MAX_ROWS + 1, title="Too much")
    assert not big.data  # the envelope is empty: this is the point

    def must_not_be_called(payload, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError("renderer was called despite the row cap")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", must_not_be_called)
    stats: dict[str, int] = {}
    assert render_svg(big, stats=stats).startswith("<figure")
    assert stats == {"charts_dropped": 1}


def test_the_fallback_tabulates_spec_carried_rows_rather_than_claiming_no_data(
    monkeypatch,
):
    """Printing "No data." over a chart that has rows is worse than looking
    broken — it is a false claim about the analysis."""
    chart = _spec_carried(3)
    monkeypatch.setattr(
        vl_convert, "vegalite_to_svg", lambda p, **k: (_ for _ in ()).throw(RuntimeError())
    )
    out = render_svg(chart)
    assert "No data." not in out
    assert "<table>" in out
    assert "r0" in out and "r2" in out


# ── the shapes Phase 1 actually produces ─────────────────────────────────────
#
# These go through `altair` for real rather than hand-writing what we think it
# emits. The premise "altair.to_dict() inlines data.values" was asserted in code
# comments on both PRs and is false — it emits a `datasets` map and a `name`
# reference — and no hand-written fixture would have caught that, because a
# hand-written fixture encodes the same wrong belief.

def _altair_spec(chart_obj) -> dict:
    """altair's own output, minus the top-level `config` it always adds.

    Popping `config` is the documented Phase 1 constraint: `spec.py` rejects a
    spec-level config (it REPLACES the theme rather than merging), and altair
    attaches one to every chart, so the Phase 1 adapter must strip it.
    """
    spec = chart_obj.to_dict()
    spec.pop("config", None)
    return spec


def test_an_altair_authored_chart_renders_rather_than_saying_no_data():
    """altair emits `data: {name}` + `datasets`, NOT inline values."""
    alt = pytest.importorskip("altair")
    pd = pytest.importorskip("pandas")

    frame = pd.DataFrame({"a": ["A", "B", "C"], "b": [3, 5, 2]})
    spec = _altair_spec(alt.Chart(frame).mark_bar().encode(x="a:N", y="b:Q"))
    assert "datasets" in spec and "values" not in (spec.get("data") or {})

    chart = ChartSpec.build(spec=spec, data=[])
    assert chart.row_count() == 3
    stats: dict[str, int] = {}
    out = render_svg(chart, stats=stats)
    assert out.startswith("<svg"), "an altair chart degraded to a table"
    assert stats == {"charts_rendered": 1}


def test_a_layered_altair_chart_renders_too():
    """`alt.layer(...)` carries rows per layer — a root-only reader sees none."""
    alt = pytest.importorskip("altair")
    pd = pytest.importorskip("pandas")

    frame = pd.DataFrame({"a": ["A", "B"], "b": [3, 5]})
    spec = _altair_spec(
        alt.layer(
            alt.Chart(frame).mark_bar().encode(x="a:N", y="b:Q"),
            alt.Chart(frame).mark_point().encode(x="a:N", y="b:Q"),
        )
    )
    chart = ChartSpec.build(spec=spec, data=[])
    assert chart.row_count() > 0
    assert render_svg(chart).startswith("<svg")


def test_altair_always_emits_a_config_which_phase_1_must_strip():
    """Documents the constraint rather than leaving it to be discovered as a
    100%-failure-rate mystery: every altair chart carries a top-level `config`,
    and a spec-level config is refused because it replaces the Sprntly theme."""
    alt = pytest.importorskip("altair")
    pd = pytest.importorskip("pandas")

    frame = pd.DataFrame({"a": ["A"], "b": [1]})
    raw = alt.Chart(frame).mark_bar().encode(x="a:N", y="b:Q").to_dict()
    assert "config" in raw
    with pytest.raises(ChartSpecError, match="config"):
        ChartSpec.build(spec=raw, data=[])
    # ...and it is fine the moment the adapter pops it.
    assert ChartSpec.build(spec=_altair_spec(
        alt.Chart(frame).mark_bar().encode(x="a:N", y="b:Q")
    ), data=[]).row_count() == 1


def test_the_row_cap_sees_rows_carried_by_a_named_dataset(monkeypatch):
    """The cap calls row_count(), so the datasets shape must reach it too."""
    spec = {
        "data": {"name": "d"},
        "datasets": {"d": [{"a": f"r{i}", "b": i} for i in range(MAX_ROWS + 1)]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "a", "type": "nominal"},
            "y": {"field": "b", "type": "quantitative"},
        },
    }
    chart = ChartSpec.build(spec=spec, title="Too much, by reference")

    def must_not_be_called(payload, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError("renderer was called despite the row cap")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", must_not_be_called)
    stats: dict[str, int] = {}
    assert render_svg(chart, stats=stats).startswith("<figure")
    assert stats == {"charts_dropped": 1}


def test_the_fallback_tabulates_rows_held_in_a_named_dataset(monkeypatch):
    chart = ChartSpec.build(
        spec={
            "data": {"name": "d"},
            "datasets": {"d": [{"a": "keep-me", "b": 1}]},
            "mark": "bar",
            "encoding": {"x": {"field": "a", "type": "nominal"}},
        },
        title="By reference",
    )
    monkeypatch.setattr(
        vl_convert, "vegalite_to_svg", lambda p, **k: (_ for _ in ()).throw(RuntimeError())
    )
    out = render_svg(chart)
    assert "No data." not in out
    assert "keep-me" in out


def test_oversized_output_is_refused(chart, monkeypatch):
    monkeypatch.setattr(
        vl_convert, "vegalite_to_svg", lambda p, **k: "<svg>" + "x" * MAX_SVG_BYTES
    )
    stats: dict[str, int] = {}
    assert render_svg(chart, stats=stats).startswith("<figure")
    assert stats == {"charts_dropped": 1}


def test_png_failure_returns_none_not_an_exception(chart, monkeypatch):
    monkeypatch.setattr(
        vl_convert, "vegalite_to_png", lambda p, **k: (_ for _ in ()).throw(RuntimeError())
    )
    stats: dict[str, int] = {}
    assert render_png(chart, stats=stats) is None
    assert stats == {"charts_dropped": 1}


def test_oversized_png_is_refused(chart, monkeypatch):
    """The SVG cap does not cover the raster path — these bytes land in docx/email."""
    monkeypatch.setattr(
        vl_convert,
        "vegalite_to_png",
        lambda p, **k: b"\x89PNG\r\n\x1a\n" + b"x" * MAX_PNG_BYTES,
    )
    stats: dict[str, int] = {}
    assert render_png(chart, stats=stats) is None
    assert stats == {"charts_dropped": 1}


def test_png_non_png_output_returns_none(chart, monkeypatch):
    monkeypatch.setattr(vl_convert, "vegalite_to_png", lambda p, **k: b"GIF89a")
    assert render_png(chart) is None


def test_stats_is_optional(chart, monkeypatch):
    monkeypatch.setattr(vl_convert, "vegalite_to_svg", lambda p, **k: "<svg/>")
    render_svg(chart)  # must not raise on stats=None


# ── the fallback table itself ────────────────────────────────────────────────

def test_table_fallback_escapes_untrusted_row_values():
    """Rows can come from the model's sandbox; the table is not a markup channel."""
    chart = E.bar([E.Category("<script>alert(1)</script>", 1)], title="<b>t</b>")
    out = render_table_html(chart)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;b&gt;t&lt;/b&gt;" in out


def test_table_fallback_handles_an_empty_row_set():
    """An empty chart is constructible only with an explicit empty data block —
    `data=[]` alone leaves the spec with no data at all, which Vega-Lite rejects
    (asserted next). Either way the fallback says so rather than rendering a
    headerless table."""
    chart = ChartSpec(
        spec={
            "data": {"values": []},
            "mark": "bar",
            "encoding": {"x": {"field": "a", "type": "nominal"}},
        },
        data=[],
        title="Empty",
    )
    out = render_table_html(chart)
    assert "No data." in out
    assert "<table>" not in out


def test_a_chart_with_no_rows_degrades_to_the_table_rather_than_an_empty_frame(
    monkeypatch,
):
    """An empty plot frame — axes, a title, nothing in it — reads as a broken
    chart, and is indistinguishable from a render that lost its rows."""
    chart = ChartSpec(
        spec={
            "data": {"values": []},
            "mark": "bar",
            "encoding": {"x": {"field": "a", "type": "nominal"}},
        },
        data=[],
        title="Nothing to draw",
    )

    def must_not_be_called(payload, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError("renderer was called for a chart with no rows")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", must_not_be_called)
    stats: dict[str, int] = {}
    out = render_svg(chart, stats=stats)
    assert out.startswith("<figure")
    assert "No data." in out
    assert stats == {"charts_dropped": 1}


def test_a_chart_with_no_data_at_all_is_rejected_at_construction():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChartSpec(spec={"mark": "bar", "encoding": {}}, data=[])


def test_table_fallback_caps_rows_and_says_how_many_it_hid():
    chart = E.bar([E.Category(f"c{i}", i) for i in range(120)], title="Long")
    out = render_table_html(chart)
    assert out.count("<tr>") == 51  # 1 header + 50 capped rows
    assert "+70 more row(s)." in out


def test_table_fallback_carries_title_subtitle_and_caption():
    chart = E.bar(
        [E.Category("a", 1)], title="Title", subtitle="Sub", caption="Cap"
    )
    out = render_table_html(chart)
    assert "Title" in out and "Sub" in out and "Cap" in out

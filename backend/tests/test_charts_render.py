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
    MAX_ROWS,
    MAX_SVG_BYTES,
    render_all_svg,
    render_png,
    render_svg,
    render_table_html,
)
from app.charts.spec import VL_VERSION, ChartSpec


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

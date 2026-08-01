"""Server-side chart rendering: `ChartSpec` -> static SVG / PNG, never an exception.

Vega-Lite needs JavaScript, and every model-generated HTML surface in Sprntly
renders in an iframe **without** `allow-scripts` (`EvidenceHtmlBrief`,
`HtmlReportView`, `PrdHtmlView`, `StreamingHtmlPreview`). That sandbox is the
containment boundary for model-authored HTML and is not up for negotiation — so
anything destined for those surfaces is rendered here, ahead of time, to static
SVG. `vl_convert` does it in-process (Rust + an embedded V8), with no browser and
no Node.

## The fallback contract — what a caller actually gets on failure

`render_svg` **never raises and never returns nothing.** It returns a string of
HTML-embeddable markup:

* success -> an `<svg>…</svg>` element, ~2-4 KB, real selectable text;
* failure -> `render_table_html(chart)`: a `<figure class="sprntly-chart-fallback">`
  containing the chart's title/subtitle, a `<table>` of `ChartSpec.data`, and the
  caption. Every value HTML-escaped.

That is the whole point. A chart is a *presentation* of rows the report already
has; if the presentation fails, the rows are still true, so the report degrades
to a table instead of 500-ing. A malformed spec must never be able to take down a
VoC report or a DS answer.

`render_png` cannot fall back to markup — a caller wants bytes — so it returns
`None` on failure and the caller (docx/pdf/email) substitutes `render_table_html`.

## Telemetry

Both renderers accept an optional `stats` dict and increment `charts_rendered` /
`charts_dropped` in it, mirroring the counters `app.ds.claude_analysis._log_run`
already writes into its decision-log `factors`. Same names, same meaning — a
non-zero `charts_dropped` is the signal that charts are failing, and it stays
comparable across the PNG era and the SVG one. The caller owns the dict, so the
counters are per-run rather than process-global (several reports render
concurrently in one worker).

## Timeout — and why the input caps matter more

`vl_convert` is a blocking call into Rust; it cannot be cancelled. The timeout
runs it on a daemon thread and stops *waiting* after `timeout_s`, returning the
fallback. **It bounds the caller's wait, not the work.** A wedged conversion
keeps burning a thread — and because the call holds the GIL for its duration, a
pathological spec degrades the whole worker, not just its own request. Nobody
should read `timeout_s` as a hard kill; there is no such thing here.

So the real defence is refusing the input: `MAX_ROWS` caps what is handed to the
renderer, and `MAX_SVG_BYTES` / `MAX_PNG_BYTES` cap what comes back. All three are
module constants with no env var by design — Phase 0 adds no configuration surface, and a knob
here would be a knob on a security boundary.
"""
from __future__ import annotations

import html
import json
import logging
import queue
import threading
from typing import Any, Callable, Sequence, TypeVar

from app.charts.spec import VL_VERSION, ChartSpec
from app.charts.theme import Mode, theme_config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 20.0
"""Generous: a first render pays V8 startup (~1-2 s). Steady state is ~50-150 ms."""

DEFAULT_PNG_SCALE = 2.0
"""Retina-ish raster for docx/pdf/email, where SVG is not an option."""

MAX_ROWS = 5_000
"""Rows we will hand `vl_convert`. The real timeout — see the module docstring.

Well above any legitimate report chart (a dense daily series over two years is
~730) and far below what makes a render pathological. Over the cap, the chart
degrades to its table rather than gambling a worker thread on it.
"""

MAX_SVG_BYTES = 2_000_000
"""Ceiling on returned markup — a runaway guard, deliberately NOT a page budget.

Measured on the emitters in this PR, a rendered chart is 9-17 KB of SVG (the
*spec* is the 2-4 KB object; the SVG is the drawn geometry). So the cap sits
~100x above anything legitimate and only catches the case where a spec renders
"successfully" into something that would bloat a stored report — a scatter of
5,000 points, say. Tightening it toward the observed range would start dropping
real charts into their tables, which is the failure mode with the worse product
consequence: a reader who sees a table thinks the analysis is thin.

The *page-weight* budget is a different question and is not settled here. Phase 1
owns it, because it owns the constants that exist today
(`_MAX_TOTAL_CHART_B64_BYTES = 100_000` and friends in `app/ds/claude_analysis`),
and it is the phase that can measure the swap: ~15 KB of inline SVG against ~48 KB
of base64 PNG for the same chart. Deciding a total-per-report SVG budget before
that measurement exists would be guessing.
"""

MAX_PNG_BYTES = 8_000_000
"""The same guard for the raster path, which the SVG cap does not cover.

PNG is where a runaway is *cheapest to produce and most expensive to store*: the
same spec at `scale=2` rasterises four times the pixels, and these bytes go into
docx/pdf/email payloads rather than into markup. The ratio to a normal chart is
looser than the SVG one because raster size tracks canvas area rather than data
volume — a legitimate wide chart at 2x is already a few hundred KB.
"""

T = TypeVar("T")


def _bump(stats: dict[str, int] | None, key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def _call_with_timeout(fn: Callable[[], T], timeout_s: float) -> T:
    """Run `fn` on a daemon thread; raise `TimeoutError` if it outruns the budget."""
    box: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            box.put((True, fn()))
        except BaseException as exc:  # noqa: BLE001 - relayed to the caller below
            box.put((False, exc))

    thread = threading.Thread(target=runner, name="vl-convert", daemon=True)
    thread.start()
    try:
        ok, payload = box.get(timeout=timeout_s)
    except queue.Empty:
        raise TimeoutError(f"chart render exceeded {timeout_s}s") from None
    if not ok:
        raise payload  # type: ignore[misc]
    return payload  # type: ignore[return-value]


# ── the fallback ─────────────────────────────────────────────────────────────

_MAX_FALLBACK_ROWS = 50
_MAX_FALLBACK_COLS = 12


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:g}"
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value)
    return html.escape(text, quote=False)


def _fallback_rows(chart: ChartSpec) -> list[dict[str, Any]]:
    """The rows to tabulate, from wherever they actually live.

    The envelope's `data` when it has any, else the spec's own inline `values` —
    the same places `ChartSpec.row_count()` looks. Reading only `chart.data`
    made the fallback print "No data." over a chart that had plenty, which is
    worse than looking broken: it is a false claim about the analysis, and it
    breaks this module's promise that if the presentation fails the rows are
    still true.
    """
    if chart.data:
        return chart.data
    values = (chart.spec.get("data") or {}).get("values")
    return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []


def render_table_html(chart: ChartSpec) -> str:
    """The chart's rows as an HTML table — the fallback, and a public helper.

    Also the building block for Phase 2's expand-to-table disclosure, and what a
    PNG caller substitutes when `render_png` returns `None`.

    Everything is escaped: these rows can originate in the model's code-execution
    sandbox, so they are untrusted text as far as this function is concerned.
    """
    title = html.escape(chart.title or "Chart", quote=False)
    parts = ['<figure class="sprntly-chart-fallback">']
    parts.append(f"<figcaption><strong>{title}</strong>")
    if chart.subtitle:
        parts.append(f"<br><span>{html.escape(chart.subtitle, quote=False)}</span>")
    parts.append("</figcaption>")

    all_rows = _fallback_rows(chart)
    rows = all_rows[:_MAX_FALLBACK_ROWS]
    if not rows:
        parts.append("<p><em>No data.</em></p>")
    else:
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        columns = columns[:_MAX_FALLBACK_COLS]
        parts.append("<table><thead><tr>")
        parts.extend(f"<th>{_cell(col)}</th>" for col in columns)
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append("<tr>")
            parts.extend(f"<td>{_cell(row.get(col))}</td>" for col in columns)
            parts.append("</tr>")
        parts.append("</tbody></table>")
        if len(all_rows) > _MAX_FALLBACK_ROWS:
            hidden = len(all_rows) - _MAX_FALLBACK_ROWS
            parts.append(f"<p><em>+{hidden} more row(s).</em></p>")

    if chart.caption:
        parts.append(f"<p>{html.escape(chart.caption, quote=False)}</p>")
    parts.append("</figure>")
    return "".join(parts)


# ── the renderers ────────────────────────────────────────────────────────────

def _render(
    chart: ChartSpec,
    *,
    fmt: str,
    mode: Mode,
    timeout_s: float,
    scale: float,
) -> Any:
    import vl_convert as vlc  # local import: 33 MB of Rust, off the startup path

    if chart.row_count() == 0:
        # A chart with no rows renders as an empty plot frame: axes, a title, and
        # nothing in it. That reads as a broken chart rather than as "no data",
        # and it is indistinguishable from a render that silently lost its rows.
        # The table says the true thing in one line.
        raise ValueError("chart carries no rows")
    rows = chart.row_count()
    if rows > MAX_ROWS:
        # `row_count()`, not `len(chart.data)`: an envelope with empty `data` and
        # rows inlined in `spec["data"]["values"]` is a shape the contract
        # explicitly blesses — and the one Phase 1's altair sandbox always
        # produces. Measuring the envelope there measures 0, and the cap that is
        # supposed to be the real defence (the timeout is not, see above) never
        # fires: 200k rows went through at 7.4 s and 48.9 MB of SVG, dropped by
        # MAX_SVG_BYTES only after all the work was done, with the GIL held.
        raise ValueError(f"chart carries {rows} rows, over the {MAX_ROWS} cap")

    payload = json.dumps(chart.spec)
    config = theme_config(mode)
    kwargs: dict[str, Any] = {
        "vl_version": VL_VERSION,  # pinned — never infer from `$schema`
        "config": config,
        "show_warnings": False,
        # Second lock, independent of spec.py's validation: even a spec that
        # somehow carried a URL past validation cannot fetch anything.
        "allowed_base_urls": [],
    }
    if fmt == "svg":
        return _call_with_timeout(
            lambda: vlc.vegalite_to_svg(payload, **kwargs), timeout_s
        )
    return _call_with_timeout(
        lambda: vlc.vegalite_to_png(payload, scale=scale, **kwargs), timeout_s
    )


def render_svg(
    chart: ChartSpec,
    *,
    mode: Mode = "light",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    stats: dict[str, int] | None = None,
) -> str:
    """Render to static SVG markup. Never raises — see the module docstring.

    Returns `<svg>…</svg>` on success, `render_table_html(chart)` on any failure
    (bad spec, timeout, missing `vl_convert`, renderer bug). `stats` — if given —
    gets `charts_rendered` or `charts_dropped` bumped.
    """
    try:
        svg = _render(chart, fmt="svg", mode=mode, timeout_s=timeout_s, scale=1.0)
    except Exception:  # noqa: BLE001 - a report must never die on a chart
        logger.exception("chart SVG render failed; falling back to table")
        _bump(stats, "charts_dropped")
        return render_table_html(chart)
    if not isinstance(svg, str) or "<svg" not in svg:
        logger.error("chart SVG render returned no SVG; falling back to table")
        _bump(stats, "charts_dropped")
        return render_table_html(chart)
    if len(svg) > MAX_SVG_BYTES:
        logger.error(
            "chart SVG is %d bytes, over the %d cap; falling back to table",
            len(svg),
            MAX_SVG_BYTES,
        )
        _bump(stats, "charts_dropped")
        return render_table_html(chart)
    _bump(stats, "charts_rendered")
    return svg


def render_png(
    chart: ChartSpec,
    *,
    mode: Mode = "light",
    scale: float = DEFAULT_PNG_SCALE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    stats: dict[str, int] | None = None,
) -> bytes | None:
    """Render to PNG bytes for docx/pdf/email. Never raises.

    Returns `None` on failure — bytes have no table fallback, so the caller
    substitutes `render_table_html(chart)` itself. `stats` is bumped as in
    `render_svg`.
    """
    try:
        png = _render(chart, fmt="png", mode=mode, timeout_s=timeout_s, scale=scale)
    except Exception:  # noqa: BLE001
        logger.exception("chart PNG render failed")
        _bump(stats, "charts_dropped")
        return None
    if not isinstance(png, (bytes, bytearray)) or not bytes(png).startswith(b"\x89PNG"):
        logger.error("chart PNG render returned no PNG")
        _bump(stats, "charts_dropped")
        return None
    if len(png) > MAX_PNG_BYTES:
        logger.error(
            "chart PNG is %d bytes, over the %d cap; dropping",
            len(png),
            MAX_PNG_BYTES,
        )
        _bump(stats, "charts_dropped")
        return None
    _bump(stats, "charts_rendered")
    return bytes(png)


def render_all_svg(
    charts: Sequence[ChartSpec],
    *,
    mode: Mode = "light",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    stats: dict[str, int] | None = None,
) -> list[str]:
    """Convenience for report pipelines: render a batch, same never-raise contract."""
    return [
        render_svg(chart, mode=mode, timeout_s=timeout_s, stats=stats) for chart in charts
    ]

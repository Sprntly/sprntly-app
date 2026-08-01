"""The single Sprntly chart theme — source of truth for both renderers.

One theme, two consumers:

* **Server** — `render.py` hands `theme_config(mode)` to `vl_convert` as the
  Vega-Lite `config` block, so every SSR chart in a report looks the same.
* **Browser** — `web/app/lib/chart-theme.json` is a *generated mirror* of
  `theme_json()`; Phase 2's `VegaChart.tsx` reads it through
  `web/app/lib/chart-theme.ts` and passes `vega.light` straight to `vega-embed`.
  The mirror is written by `backend/scripts/export_chart_theme.py`, never
  hand-edited, and `tests/test_charts_theme.py` fails the build if the committed
  file and this module drift apart.

`vega.light` / `vega.dark` are **literal Vega config objects**, not Sprntly
design tokens — the exact dict each renderer passes to its renderer. If either
side translated tokens into a config, the two would drift; consuming the
identical object makes drift impossible by construction. For the same reason
there is not a single `var(--…)` in the output: `vl_convert` renders headless
with no DOM, where `var(--surface)` resolves to nothing. Every token is resolved
to a literal here, per mode.

The theme lives in `config`, not in the specs. That keeps a stored `ChartSpec`
portable — restyling every chart Sprntly has ever rendered is a change to this
file, not a migration over stored artifacts. (`spec.py` rejects a top-level
`config` for the same reason: a spec that carried its own would replace this one
rather than merge with it.)

## Why the generated file lives under `web/`

It is the file the client actually imports, and a `web/` module cannot import
from `backend/` without either `experimental.externalDir` in `next.config.ts` or
a web build that breaks until this PR lands. Generating into `web/` costs this
backend PR one generated file in someone else's directory; the alternative costs
the frontend a build dependency on merge order. The path and the exact byte
layout are the ones Phase 2 (PR #985) already ships — `theme_json_bytes()`
reproduces that file exactly, which is also what keeps the two PRs from
conflicting over it.

## Three things are pinned on purpose

**`CHART_COLORS` is lifted verbatim from `web/app/components/shared/InlineChart.tsx`**
(the `CHART_COLORS` export, same eight hexes, same order). Nothing may change
visually by accident when a surface moves from `InlineChart` to Vega — and a
re-ordering silently recolours every existing chart, because Vega assigns colours
by domain index. `tests/test_charts_theme.py` re-reads the TSX and compares.

**There are two font stacks, and which one you want depends on who is drawing.**
`FONT_SSR` is Liberation-first: `vl_convert` measures text server-side and bakes
the resulting geometry into the SVG, so the font it MEASURES with must be the
font that ends up DRAWN. Liberation Sans is metric-compatible with
Arial/Helvetica and is present on the Linux runners. `FONT_CLIENT` is the app's
Geist stack, for the browser, where the engine that measures is the engine that
draws. Every font-bearing key in a config block holds the *same* string, so the
client swaps one for the other with a single deep replace (`clientConfig()` on
the TS side); `tests/test_charts_theme.py` asserts that invariant.

**Sizes are small on purpose.** These charts are read inline in a report at
roughly 420 px wide, not projected. 10 px axis labels and a 13 px title are what
survive that; bigger type crowds out the data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

Mode = Literal["light", "dark"]

THEME_VERSION = 1
"""Bump when the mirror's SHAPE changes; the frontend reads it to fail loudly."""

# The *major* schema URL, which is what the client's `vega-lite` package
# satisfies. Emitted specs stamp altair's exact build (`spec.VEGA_LITE_SCHEMA_URL`,
# `.../v6.4.1.json`) — both are true; this one is the compatibility statement.
VEGA_LITE_SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v6.json"

# ── palette ──────────────────────────────────────────────────────────────────

CHART_COLORS: list[str] = [
    "#5B7FFF",
    "#6FCF97",
    "#F2994A",
    "#BB6BD9",
    "#56CCF2",
    "#EB5757",
    "#F2C94C",
    "#27AE60",
]
"""Verbatim from `InlineChart.tsx` — see the module docstring before touching."""

# Semantic accents for emitters that encode meaning rather than category
# (`segment_forest` colours by significance). Server-side only: they are not part
# of the mirrored config, because the client renders the same spec, which already
# names these colours explicitly.
ACCENT = "#179463"          # --accent (brand green)
POSITIVE = "#27AE60"
NEGATIVE = "#C13838"        # --danger
NEUTRAL = "#828D87"         # --muted
ANNOTATION = "#4A554F"      # --ink-2, for rules/labels that are not data

# ── typography ───────────────────────────────────────────────────────────────

FONT_SSR = "Liberation Sans, Arial, Helvetica, sans-serif"
"""Server-side (vl_convert): measured is drawn. See the module docstring."""

FONT_CLIENT = "Geist, -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"
"""Browser-side (vega-embed). The app's own font."""

FONT_SIZE_TITLE = 13
FONT_SIZE_AXIS_TITLE = 11
FONT_SIZE_LABEL = 10

# ── per-mode surface tokens (resolved from web/app/globals.css) ──────────────
#
# Literal values only — `vl_convert` has no stylesheet, so a `var(--…)` here
# would render as nothing rather than as the token.
_TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "text": "#15201B",   # --ink
        "muted": "#828D87",  # --muted
        "line": "#EEF0EE",   # --surface-3, the quietest visible rule
    },
    # The app ships no dark mode yet (no `prefers-color-scheme`, no `[data-theme]`
    # in globals.css) and Phase 2 consumes `light` only. `dark` is emitted anyway
    # so that adding a theme toggle later is a theme change, not a contract change.
    "dark": {
        "text": "#F2F4F3",
        "muted": "#9AA5A0",
        "line": "#2C3733",
    },
}


def _config_for(mode: Mode, font: str) -> dict[str, Any]:
    """The literal Vega config for one mode. Key order is part of the contract."""
    t = _TOKENS[mode]
    return {
        # Transparent, not white: these charts sit on report surfaces whose
        # background is set by the page, and a baked-in white block is visible
        # the moment the page is not white.
        "background": "transparent",
        "font": font,
        "padding": 4,
        "autosize": {"type": "fit", "contains": "padding"},
        "range": {"category": list(CHART_COLORS)},
        "view": {"stroke": None, "continuousWidth": 420, "continuousHeight": 200},
        "title": {
            "fontSize": FONT_SIZE_TITLE,
            "fontWeight": 600,
            "color": t["text"],
            "anchor": "start",
            "offset": 8,
        },
        "axis": {
            "labelFontSize": FONT_SIZE_LABEL,
            "labelColor": t["muted"],
            "titleFontSize": FONT_SIZE_AXIS_TITLE,
            "titleColor": t["muted"],
            "titleFontWeight": 500,
            "domainColor": t["line"],
            "tickColor": t["line"],
            "gridColor": t["line"],
            "gridWidth": 1,
            # Long category labels get ellipsised rather than pushing the plot
            # area down to nothing.
            "labelLimit": 140,
        },
        "legend": {
            "labelFontSize": FONT_SIZE_LABEL,
            "labelColor": t["text"],
            "titleFontSize": FONT_SIZE_LABEL,
            "titleColor": t["muted"],
            "symbolType": "circle",
            "symbolSize": 60,
        },
        "line": {"strokeWidth": 2.5},
        "bar": {"cornerRadiusEnd": 3},
        "arc": {"stroke": None},
        "point": {"size": 40, "filled": True},
    }


def theme_config(mode: Mode = "light", *, font: str = FONT_SSR) -> dict[str, Any]:
    """The Vega-Lite `config` block for `mode`. A fresh dict on every call.

    Defaults to the SSR font because the server is this function's only caller;
    the browser gets its config through the mirror and swaps in `FONT_CLIENT`.
    """
    if mode not in _TOKENS:
        raise ValueError(f"unknown chart theme mode: {mode!r}")
    return json.loads(json.dumps(_config_for(mode, font)))  # deep copy, JSON-safe


# ── the generated mirror ─────────────────────────────────────────────────────

# These are `$comment` keys in the emitted JSON: the mirror is a generated file
# that a frontend engineer will open, and JSON cannot carry a code comment.
_COMMENT = (
    "Sprntly chart theme — the WEB-SIDE MIRROR of backend/app/charts/theme.py. "
    "The backend module is the source of truth for the values; this file is the copy "
    "the client renderer (vega-embed) reads, so the server SVG renderer (vl-convert) "
    "and the client cannot drift. It lives under web/ ON PURPOSE: Phase 0 and Phase 2 "
    "are separate PRs, a backend-owned file imported from web/ would make the web build "
    "depend on Phase 0 landing first, and a client `import` of a path above the project "
    "root needs experimental.externalDir in next.config.ts (which also carries the Sentry "
    "wrapper and the static-export switch, and is not worth touching for this). Phase 0 "
    "adds a test that regenerates and diffs this file against theme.py. `categorical` is "
    "lifted VERBATIM from web/app/components/shared/InlineChart.tsx CHART_COLORS, in "
    "order, so nothing changes colour by accident; web/app/lib/__tests__/chart-theme.test.ts "
    "fails on drift between the two."
)

_COMMENT_FONTS = (
    "TWO font stacks on purpose. `font` is the SSR stack: vl-convert measures text "
    "server-side and emits real <text> elements, so the font it MEASURES with has to be "
    "the font that ends up DRAWN — Liberation Sans is metric-compatible with "
    "Arial/Helvetica and is present in the render container. `fontClient` is the browser "
    "stack, where the browser both measures and draws, so it can use the app's real UI "
    "font. The client renderer swaps fontClient in over font; see chart-theme.ts "
    "clientConfig()."
)

_COMMENT_VEGA = (
    "Full Vega-Lite `config` objects, passed straight through to the renderer. NO CSS "
    "custom properties anywhere — vl-convert has no DOM and cannot resolve "
    "var(--…), so a theme value that works on the client and blanks on the server is "
    "exactly the drift this file exists to prevent. `dark` is emitted even though the app "
    "ships no dark mode yet, so the contract is fixed before a theme toggle lands rather "
    "than after."
)


def theme_json() -> dict[str, Any]:
    """The whole theme as a plain serialisable dict — the frontend mirror payload.

    Shape (stable; `version` bumps if it changes)::

        {
          "$comment": …, "version": 1,
          "vegaLiteSchema": "https://vega.github.io/schema/vega-lite/v6.json",
          "$commentFonts": …,
          "font": "<SSR stack>", "fontClient": "<Geist stack>",
          "categorical": [8 hex strings, in order],
          "$commentVega": …,
          "vega": {"light": {<vega config>}, "dark": {<vega config>}}
        }

    Key order is deliberate and is preserved on write — this is a file humans
    read. `vega.*` carries the SSR font; the client swaps in `fontClient`.
    """
    return {
        "$comment": _COMMENT,
        "version": THEME_VERSION,
        "vegaLiteSchema": VEGA_LITE_SCHEMA_URL,
        "$commentFonts": _COMMENT_FONTS,
        "font": FONT_SSR,
        "fontClient": FONT_CLIENT,
        "categorical": list(CHART_COLORS),
        "$commentVega": _COMMENT_VEGA,
        "vega": {"light": theme_config("light"), "dark": theme_config("dark")},
    }


# repo root = backend/app/charts/theme.py -> charts -> app -> backend -> repo
_REPO_ROOT = Path(__file__).resolve().parents[3]
THEME_MIRROR_PATH = _REPO_ROOT / "web" / "app" / "lib" / "chart-theme.json"
"""Generated file, owned by this module. Phase 2 imports it; nobody edits it."""


def theme_json_bytes() -> str:
    """The mirror's exact bytes: 2-space indent, insertion order, trailing newline.

    `sort_keys=False` and `ensure_ascii=True` are both load-bearing — they are
    what make this reproduce the committed file byte for byte.
    """
    return json.dumps(theme_json(), indent=2) + "\n"

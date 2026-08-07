"""The chart theme: verbatim palette, one pinned font, and no silent mirror drift.

Three separate drift risks, three separate tests:

1. `CHART_COLORS` vs `InlineChart.tsx` — the palette is *lifted* from the
   frontend so that moving a surface onto Vega changes nothing visually. Re-read
   the TSX and compare, or the promise decays the first time someone reorders one
   of the two lists.
2. `web/app/lib/chart-theme.json` vs `theme.py` — the generated mirror Phase 2
   consumes. A theme edit without a regenerated mirror means two themes.
3. That same file is what Phase 2 (PR #985) imports, and #985 carries its own
   copy on its branch. `theme_json_bytes()` reproduces those bytes exactly, so
   the two PRs add an identical blob and merge without a conflict — the drift
   test is what keeps it that way after either side edits the theme.

The mirror lives under `web/` rather than `backend/` because a `web/` module
cannot import from above the project root without `experimental.externalDir`,
and a backend-owned path would make the web build depend on this PR landing
first. It is generated, never hand-edited.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.charts.theme import (
    CHART_COLORS,
    FONT_CLIENT,
    FONT_SSR,
    THEME_MIRROR_PATH,
    THEME_VERSION,
    VEGA_LITE_SCHEMA_URL,
    theme_config,
    theme_json,
    theme_json_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INLINE_CHART_TSX = REPO_ROOT / "web" / "app" / "components" / "shared" / "InlineChart.tsx"


def _colors_from_tsx(source: str) -> list[str]:
    block = re.search(r"export const CHART_COLORS\s*=\s*\[(.*?)\]", source, re.S)
    assert block, "CHART_COLORS array not found in InlineChart.tsx"
    return re.findall(r"#[0-9A-Fa-f]{3,8}", block.group(1))


# ── 1. the palette is verbatim ───────────────────────────────────────────────

def test_chart_colors_match_inline_chart_tsx_exactly():
    assert INLINE_CHART_TSX.exists(), f"missing {INLINE_CHART_TSX}"
    from_tsx = _colors_from_tsx(INLINE_CHART_TSX.read_text(encoding="utf-8"))
    # Order matters as much as membership: Vega assigns colours by domain index,
    # so a reordering recolours every existing chart.
    assert from_tsx == CHART_COLORS


def test_palette_is_wired_into_the_categorical_range():
    assert theme_config("light")["range"]["category"] == CHART_COLORS


# ── 2. one font, pinned, everywhere ──────────────────────────────────────────

def _font_values(node, out=None):
    """Every value under a font-bearing key, at any depth."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and ("font" in key.lower() and "size" not in key.lower() and "weight" not in key.lower()):
                out.append(value)
            else:
                _font_values(value, out)
    elif isinstance(node, list):
        for item in node:
            _font_values(item, out)
    return out


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_font_key_holds_the_same_pinned_stack(mode):
    values = _font_values(theme_config(mode))
    assert values, "no font keys found in the config — the theme lost its typography"
    assert set(values) == {FONT_SSR}


def test_client_font_swap_is_a_single_string_replace():
    """Phase 2's contract: replace every value equal to `font` with `fontClient`."""
    config = theme_config("light", font=FONT_CLIENT)
    assert set(_font_values(config)) == {FONT_CLIENT}


def test_ssr_font_is_not_the_web_font():
    """They are deliberately different — see theme.py on measured-vs-drawn metrics."""
    assert FONT_SSR != FONT_CLIENT
    assert "Liberation Sans" in FONT_SSR
    assert "Geist" in FONT_CLIENT


# ── theme_config hygiene ─────────────────────────────────────────────────────

def test_theme_config_returns_a_fresh_dict_each_call():
    first = theme_config("light")
    first["range"]["category"].append("#000000")
    assert theme_config("light")["range"]["category"] == CHART_COLORS


def test_light_and_dark_differ_where_it_matters():
    """Ink and rules flip; the palette and the transparent surface do not."""
    light, dark = theme_config("light"), theme_config("dark")
    assert light["axis"]["labelColor"] != dark["axis"]["labelColor"]
    assert light["title"]["color"] != dark["title"]["color"]
    assert light["axis"]["gridColor"] != dark["axis"]["gridColor"]
    assert light["range"]["category"] == dark["range"]["category"]
    # Both are transparent on purpose: the page owns the surface, and a baked-in
    # background is visible the moment the page is not that colour.
    assert light["background"] == dark["background"] == "transparent"


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown chart theme mode"):
        theme_config("sepia")  # type: ignore[arg-type]


def test_no_css_variables_survive_into_the_config():
    """`vl_convert` has no DOM: a `var(--…)` resolves to nothing server-side.

    Scoped to the config blocks — the `$commentVega` prose says the word.
    """
    assert "var(--" not in json.dumps(theme_json()["vega"])


# ── 3. the generated mirror ──────────────────────────────────────────────────

def test_theme_mirror_is_committed_and_current():
    assert THEME_MIRROR_PATH.exists(), (
        f"{THEME_MIRROR_PATH} is missing — run: python scripts/export_chart_theme.py"
    )
    assert THEME_MIRROR_PATH.read_text(encoding="utf-8") == theme_json_bytes(), (
        "the committed chart-theme mirror has drifted from app/charts/theme.py; "
        "run: python scripts/export_chart_theme.py"
    )


def test_the_mirror_is_the_file_the_frontend_imports():
    """Guards the path itself: Phase 2 imports `web/app/lib/chart-theme.json`."""
    assert THEME_MIRROR_PATH == REPO_ROOT / "web" / "app" / "lib" / "chart-theme.json"


def test_mirror_shape_is_what_phase_2_was_promised():
    payload = theme_json()
    assert list(payload) == [
        "$comment",
        "version",
        "vegaLiteSchema",
        "$commentFonts",
        "font",
        "fontClient",
        "categorical",
        "$commentVega",
        "vega",
    ]
    assert payload["version"] == THEME_VERSION
    assert payload["vegaLiteSchema"] == VEGA_LITE_SCHEMA_URL
    assert payload["categorical"] == CHART_COLORS
    assert payload["font"] == FONT_SSR
    assert payload["fontClient"] == FONT_CLIENT
    assert set(payload["vega"]) == {"light", "dark"}
    for mode in ("light", "dark"):
        assert payload["vega"][mode]["range"]["category"] == CHART_COLORS


def test_mirror_bytes_are_stable_and_human_readable():
    """It is a generated file people open: 2-space indent, insertion order, \n."""
    text = theme_json_bytes()
    assert text.endswith("}\n")
    assert '\n  "version": 1,' in text
    # Insertion order, not alphabetical — `$comment` leads, `vega` trails.
    assert text.index('"$comment"') < text.index('"version"') < text.index('"vega"')

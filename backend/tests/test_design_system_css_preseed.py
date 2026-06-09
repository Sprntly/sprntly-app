"""Unit tests for the _render_palette_css CSS pre-seed output.

Verifies that the rendered index.css satisfies the scaffold contract:
  - The three @tailwind directives are present and precede :root.
  - If a Google font is set, @import precedes @tailwind base.
  - All token values in :root are HSL channel triplets (no # or hsl() wrapper).
  - Helper round-trips are correct for black, white, and mid-tone colours.
"""
from __future__ import annotations

import re

import pytest

from app.design_agent.runner import _hex_to_hsl_channels, _render_palette_css


# ─── Helpers ─────────────────────────────────────────────────────────────────

_HSL_CHANNELS = re.compile(
    r"^\d+(\.\d+)?\s+\d+(\.\d+)?%\s+\d+(\.\d+)?%$"
)


def _root_block(css: str) -> str:
    """Extract the text of the :root { ... } block from the rendered CSS."""
    start = css.index(":root {")
    end = css.index("}", start)
    return css[start : end + 1]


def _css_vars(css: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*([^;]+);", css))


# ─── _hex_to_hsl_channels sanity checks ──────────────────────────────────────


def test_hex_to_hsl_white():
    assert _hex_to_hsl_channels("#ffffff") == "0 0% 100%"


def test_hex_to_hsl_black():
    assert _hex_to_hsl_channels("#000000") == "0 0% 0%"


def test_hex_to_hsl_pure_red():
    # #ff0000 is full red, 0° hue, 100% saturation, 50% lightness
    assert _hex_to_hsl_channels("#ff0000") == "0 100% 50%"


def test_hex_to_hsl_pure_blue():
    # #0000ff is full blue, 240° hue, 100% saturation, 50% lightness
    assert _hex_to_hsl_channels("#0000ff") == "240 100% 50%"


def test_hex_to_hsl_mid_grey():
    # #808080 — neutral grey, no saturation
    result = _hex_to_hsl_channels("#808080")
    h, rest = result.split(" ", 1)
    assert rest == "0% 50%"
    assert h == "0"


# ─── @tailwind directives ────────────────────────────────────────────────────

_BASE_PALETTE = {
    "background": "#1e1e2e",
    "accent": "#cba6f7",
    "is_dark": True,
    "swatches": ["#1e1e2e", "#313244", "#6c7086"],
    "font_family": None,
    "font_weights": [400, 700],
}


def test_tailwind_base_directive_present():
    css = _render_palette_css(_BASE_PALETTE)
    assert "@tailwind base;" in css


def test_tailwind_components_directive_present():
    css = _render_palette_css(_BASE_PALETTE)
    assert "@tailwind components;" in css


def test_tailwind_utilities_directive_present():
    css = _render_palette_css(_BASE_PALETTE)
    assert "@tailwind utilities;" in css


def test_tailwind_directives_precede_root():
    css = _render_palette_css(_BASE_PALETTE)
    root_pos = css.index(":root")
    assert css.index("@tailwind base;") < root_pos
    assert css.index("@tailwind components;") < root_pos
    assert css.index("@tailwind utilities;") < root_pos


def test_all_three_tailwind_directives_in_correct_order():
    css = _render_palette_css(_BASE_PALETTE)
    base_pos = css.index("@tailwind base;")
    comp_pos = css.index("@tailwind components;")
    util_pos = css.index("@tailwind utilities;")
    assert base_pos < comp_pos < util_pos


# ─── @import placement ───────────────────────────────────────────────────────


def test_google_font_import_precedes_tailwind_base():
    palette = {**_BASE_PALETTE, "font_family": "Inter", "font_weights": [400, 700]}
    css = _render_palette_css(palette)
    assert "@import url(" in css
    assert css.index("@import url(") < css.index("@tailwind base;")


def test_non_google_font_has_no_import():
    palette = {**_BASE_PALETTE, "font_family": "Helvetica Neue"}
    css = _render_palette_css(palette)
    assert "@import url(" not in css
    # But the font should still appear in --font-sans
    assert "Helvetica Neue" in css


def test_no_font_has_no_import():
    palette = {**_BASE_PALETTE, "font_family": None}
    css = _render_palette_css(palette)
    assert "@import url(" not in css


# ─── HSL token values — no hex, no hsl() wrapper ─────────────────────────────


def test_root_tokens_are_hsl_channel_triplets_not_hex():
    css = _render_palette_css(_BASE_PALETTE)
    root = _root_block(css)
    # No bare #rrggbb in :root
    assert not re.search(r"#[0-9a-fA-F]{6}", root), (
        "Found a hex colour inside :root — tokens must be HSL channel triplets"
    )


def test_root_tokens_have_no_hsl_wrapper():
    css = _render_palette_css(_BASE_PALETTE)
    root = _root_block(css)
    assert "hsl(" not in root, "Token values must not be wrapped in hsl() — Tailwind adds the wrapper"


def test_background_token_matches_hsl_pattern():
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert _HSL_CHANNELS.match(vars_["background"]), (
        f"--background value {vars_['background']!r} is not an HSL channel triplet"
    )


def test_foreground_token_matches_hsl_pattern():
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert _HSL_CHANNELS.match(vars_["foreground"])


def test_primary_and_accent_match_hsl_pattern():
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert _HSL_CHANNELS.match(vars_["primary"])
    assert _HSL_CHANNELS.match(vars_["accent"])


def test_border_and_input_match_hsl_pattern():
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert _HSL_CHANNELS.match(vars_["border"])
    assert _HSL_CHANNELS.match(vars_["input"])


def test_muted_foreground_matches_hsl_pattern():
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert _HSL_CHANNELS.match(vars_["muted-foreground"])


# ─── Full token set ───────────────────────────────────────────────────────────

_REQUIRED_TOKENS = [
    "background", "foreground",
    "card", "card-foreground",
    "popover", "popover-foreground",
    "primary", "primary-foreground",
    "secondary", "secondary-foreground",
    "muted", "muted-foreground",
    "accent", "accent-foreground",
    "destructive", "destructive-foreground",
    "border", "input", "ring",
    "radius",
]


@pytest.mark.parametrize("token", _REQUIRED_TOKENS)
def test_required_token_is_emitted(token: str):
    css = _render_palette_css(_BASE_PALETTE)
    vars_ = _css_vars(css)
    assert token in vars_, f"--{token} missing from rendered CSS"


# ─── Light palette variant ───────────────────────────────────────────────────


def test_light_palette_foreground_is_dark():
    palette = {
        "background": "#ffffff",
        "accent": "#3b82f6",
        "is_dark": False,
        "swatches": ["#ffffff", "#f3f4f6", "#9ca3af"],
        "font_family": None,
        "font_weights": [400],
    }
    css = _render_palette_css(palette)
    vars_ = _css_vars(css)
    # Background must be white
    assert vars_["background"] == "0 0% 100%"
    # Foreground must resolve to the dark default (#1a1a1a → low-lightness HSL)
    fg_l = int(vars_["foreground"].split()[-1].rstrip("%"))
    assert fg_l < 20, "Light palette foreground should be dark (low lightness)"


def test_dark_palette_foreground_is_light():
    palette = {
        "background": "#0f172a",
        "accent": "#f59e0b",
        "is_dark": True,
        "swatches": ["#0f172a", "#1e293b", "#94a3b8"],
        "font_family": None,
        "font_weights": [400],
    }
    css = _render_palette_css(palette)
    vars_ = _css_vars(css)
    # Foreground must resolve to the light default (#f4f1ea → high-lightness HSL)
    fg_l = int(vars_["foreground"].split()[-1].rstrip("%"))
    assert fg_l > 80, "Dark palette foreground should be light (high lightness)"


# ─── @layer base apply rules ─────────────────────────────────────────────────


def test_apply_border_border_rule_present():
    css = _render_palette_css(_BASE_PALETTE)
    assert "@apply border-border;" in css


def test_apply_bg_background_text_foreground_present():
    css = _render_palette_css(_BASE_PALETTE)
    assert "@apply bg-background text-foreground;" in css

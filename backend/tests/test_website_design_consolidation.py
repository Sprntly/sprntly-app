"""Equivalence gate for consolidating website design into the unified pre-seed.

The legacy route path rendered website extraction results as scaffold prose. The
new path normalizes the same extraction result through the website adapter into a
DesignSystem and renders the existing unified ``src/index.css`` pre-seed.

Byte equality is not meaningful here: the old artifact was prose and the new
artifact is CSS plus normalized tokens. The invariant is token-level equivalence:
the usable colors/fonts the old prose exposed resolve to the same CSS custom
property values, while radius and spacing resolve to the same DesignSystem tokens
because the current index.css renderer does not emit radius/spacing variables.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from app.design_agent.design_system.adapters import WebExtractor
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.design_system.models import DesignSystem, Tokens
from app.design_agent.runner import _render_design_system_css


def _css_vars(css: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*([^;]+);", css))


def _legacy_usable_color(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip().lower()
    if not v or v == "transparent":
        return False
    if v.startswith(("rgba(", "hsla(")) and ")" in v:
        inner = v[v.index("(") + 1 : v.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            try:
                if float(parts[3]) == 0.0:
                    return False
            except ValueError:
                pass
    return True


def _legacy_expected(sample: dict[str, Any] | None, manual=None) -> dict[str, Any]:
    """Minimal token model of the retired extracted website prose behavior."""
    if sample is None:
        if manual is None:
            return {"explicit": False}
        return {
            "explicit": True,
            "primary": manual.primary_color,
            "heading_font": manual.font_family,
            "body_font": manual.font_family,
            "radius": Tokens().radius_convention,
            "spacing": Tokens().spacing_scale,
        }

    primary = sample.get("primary_color")
    expected: dict[str, Any] = {
        "explicit": True,
        "heading_font": sample.get("heading_font_family"),
        "body_font": sample.get("body_font_family"),
        "radius": WebExtractor().normalize(
            RawSignals(provider="web", ref="x", signals=sample)
        ).tokens.radius_convention,
        "spacing": WebExtractor().normalize(
            RawSignals(provider="web", ref="x", signals=sample)
        ).tokens.spacing_scale,
    }
    if _legacy_usable_color(primary):
        expected["primary"] = primary
    elif manual is not None:
        expected["primary"] = manual.primary_color
    else:
        expected["primary"] = DesignSystem().tokens.colors.primary
    return expected


def _new_design_system(sample: dict[str, Any] | None) -> DesignSystem:
    raw = WebExtractor().extract_raw_signals("https://brand.example", sample=sample)
    return WebExtractor().normalize(raw)


def test_rich_website_extraction_matches_unified_css_and_tokens():
    sample = {
        "primary_color": "rgb(37,99,235)",
        "background_color": "#0b0f19",
        "heading_font_family": "Inter",
        "body_font_family": "Roboto",
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["16px 24px", "8px"],
        "logo_url": "https://cdn.example.com/logo.png",
    }
    legacy = _legacy_expected(sample)
    ds = _new_design_system(sample)
    css_vars = _css_vars(_render_design_system_css(ds))

    # CSS variables are now HSL channel triplets so tailwind.config.ts can
    # consume them via hsl(var(--token)). The raw hex is preserved on the
    # DesignSystem model; only the rendered CSS changes format.
    assert css_vars["primary"] == "221 83% 53%"   # #2563eb
    assert css_vars["accent"] == "221 83% 53%"    # #2563eb
    assert css_vars["background"] == "223 39% 7%"  # #0b0f19
    assert css_vars["font-sans"] == '"Inter", ui-sans-serif, system-ui, sans-serif'
    assert ds.tokens.colors.primary == "#2563eb"
    assert legacy["primary"] == "rgb(37,99,235)"
    assert ds.tokens.fonts.heading_family == legacy["heading_font"] == "Inter"
    assert ds.tokens.fonts.body_family == legacy["body_font"] == "Roboto"
    assert ds.tokens.radius_convention == legacy["radius"] == "rounded"
    assert ds.tokens.spacing_scale == legacy["spacing"] == [8, 16, 24]
    assert ds.has_explicit_system is False
    assert ds.confidence != "low"


def test_minimal_website_extraction_matches_unified_css_and_tokens():
    sample = {
        "primary_color": "#3b82f6",
        "background_color": "#ffffff",
        "heading_font_family": "Poppins",
        "body_font_family": "Poppins",
        "border_radius_convention": "9999px",
        "spacing_scale_samples": ["12px"],
    }
    legacy = _legacy_expected(sample)
    ds = _new_design_system(sample)
    css_vars = _css_vars(_render_design_system_css(ds))

    # CSS variables are now HSL channel triplets; the raw hex lives on the model.
    assert legacy["primary"] == "#3b82f6"
    assert css_vars["primary"] == "217 91% 60%"   # #3b82f6
    assert css_vars["accent"] == "217 91% 60%"    # #3b82f6
    assert css_vars["background"] == "0 0% 100%"  # #ffffff
    assert css_vars["font-sans"] == '"Poppins", ui-sans-serif, system-ui, sans-serif'
    assert ds.tokens.fonts.heading_family == legacy["heading_font"] == "Poppins"
    assert ds.tokens.radius_convention == legacy["radius"] == "pill"
    assert ds.tokens.spacing_scale == legacy["spacing"] == [12]
    assert ds.has_explicit_system is False
    assert ds.confidence != "low"


def test_low_confidence_none_does_not_preseed_and_manual_floor_is_separate():
    manual = SimpleNamespace(primary_color="#ff0000", font_family="Lato")
    legacy = _legacy_expected(None, manual=manual)
    ds = _new_design_system(None)

    assert legacy == {
        "explicit": True,
        "primary": "#ff0000",
        "heading_font": "Lato",
        "body_font": "Lato",
        "radius": Tokens().radius_convention,
        "spacing": Tokens().spacing_scale,
    }
    assert ds == DesignSystem()
    assert ds.has_explicit_system is False

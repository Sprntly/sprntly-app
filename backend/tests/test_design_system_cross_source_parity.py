"""Cross-source parity test: all three adapters route through the shared kernel.

Drives a synthetic gather bag through each of WebExtractor.normalize,
FigmaExtractor.normalize, and GithubExtractor.normalize, then asserts the
shared completeness invariants uniformly across all three resulting
DesignSystem objects. No DB, no network, no model calls.
"""
from __future__ import annotations

import pytest

from app.design_agent.design_system.adapters import (
    FigmaExtractor,
    GithubExtractor,
    WebExtractor,
)
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.design_system.models import Colors, DesignSystem, Fonts


# ── Baseline leak-sentinels (the Pydantic model defaults) ────────────────────
# Any asserted field that equals one of these values when a real candidate was
# supplied indicates a silent baseline leak.

_ACCENT_BASELINE = Colors().accent          # "#2563eb"
_PRIMARY_BASELINE = Colors().primary        # "#2563eb"
_BORDER_BASELINE = Colors().border          # "#e5e7eb"
_MUTED_BASELINE = Colors().muted            # "#6b7280"
_BACKGROUND_BASELINE = Colors().background  # "#ffffff"
_FOREGROUND_BASELINE = Colors().foreground  # "#111111"
_HEADING_BASELINE = Fonts().heading_family  # "system-ui, sans-serif"
_BODY_BASELINE = Fonts().body_family        # "system-ui, sans-serif"


# ── Per-source fixture values ────────────────────────────────────────────────
# Each source gets a distinct chromatic accent so the three fixtures are
# independently identifiable.  All values are deliberately != their baselines.

# Web fixture values
_WEB_ACCENT = "#e63946"       # vivid red — high saturation, clearly chromatic
_WEB_SURFACE = "#f5f0eb"
_WEB_BORDER = "#d4cfc8"
_WEB_MUTED = "#7a746e"
_WEB_BG = "#faf9f7"
_WEB_FG = "#1a1a1a"
_WEB_HEADING = "Poppins"
_WEB_BODY = "Poppins"

# Figma fixture values
_FIGMA_ACCENT = "#0d9488"     # teal — high saturation, clearly chromatic
_FIGMA_SURFACE = "#1e293b"
_FIGMA_BORDER = "#334155"
_FIGMA_MUTED = "#94a3b8"
_FIGMA_BG = "#0f172a"
_FIGMA_FG = "#f8fafc"
_FIGMA_HEADING = "Inter"
_FIGMA_BODY = "Inter"

# GitHub fixture values
_GITHUB_ACCENT = "#7c3aed"    # violet — explicit config hit, weight 2.0
_GITHUB_SURFACE = "#1f2937"
_GITHUB_BORDER = "#374151"
_GITHUB_MUTED = "#9ca3af"
_GITHUB_BG = "#111827"
_GITHUB_FG = "#f9fafb"
_GITHUB_HEADING = "Plus Jakarta Sans"
_GITHUB_BODY = "Plus Jakarta Sans"


# ── Fixture builders ─────────────────────────────────────────────────────────


def _web_fixture() -> RawSignals:
    """Synthetic gather bag for WebExtractor.normalize.

    Key names match exactly what WebExtractor.normalize reads from raw.signals:
    background_color, color_candidates [{color, area, saturation}],
    neutral_candidates [{role, color, area}], container_observations,
    observed_component_types, heading_font_family, body_font_family,
    border_radius_convention, spacing_scale_samples.
    """
    signals = {
        "background_color": _WEB_BG,
        "color_candidates": [
            {"color": _WEB_ACCENT, "area": 9000, "saturation": 0.80},
            {"color": "#2a2a2a", "area": 15000, "saturation": 0.0},
        ],
        "neutral_candidates": [
            {"role": "surface", "color": _WEB_SURFACE, "area": 5000},
            {"role": "border", "color": _WEB_BORDER, "area": 200},
            {"role": "muted", "color": _WEB_MUTED, "area": 80},
        ],
        "container_observations": [
            {"has_border": True, "has_shadow": False},
            {"has_border": True, "has_shadow": False},
            {"has_border": False, "has_shadow": True},
        ],
        "observed_component_types": ["button", "card", "input"],
        "heading_font_family": _WEB_HEADING,
        "body_font_family": _WEB_BODY,
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["8px 16px", "24px"],
    }
    return RawSignals(provider="web", ref="https://example.com", signals=signals)


def _figma_fixture() -> RawSignals:
    """Synthetic gather bag for FigmaExtractor.normalize.

    Key names match exactly what FigmaExtractor.normalize reads from raw.signals
    (the gather_figma_signals output shape):
    theme_background, theme_is_dark, foreground, color_candidates [{hex, weight}],
    neutral_candidates [{role, hex, weight}], container_observations,
    observed_component_types, heading_font_family, body_font_family,
    font_weights_observed, radius_convention, spacing_px,
    explicit_color_styles, explicit_text_styles.

    Both explicit_color_styles and explicit_text_styles are set truthy so
    score_confidence can reach "high".
    """
    signals = {
        "theme_background": _FIGMA_BG,
        "theme_is_dark": True,
        "foreground": _FIGMA_FG,
        "color_candidates": [
            {"hex": _FIGMA_ACCENT, "weight": 6000.0},
            {"hex": "#1a1a1a", "weight": 60000.0},  # achromatic — does not compete for accent
        ],
        "neutral_candidates": [
            {"role": "surface", "hex": _FIGMA_SURFACE, "weight": 20000.0},
            {"role": "border", "hex": _FIGMA_BORDER, "weight": 5000.0},
            {"role": "muted", "hex": _FIGMA_MUTED, "weight": 3000.0},
        ],
        "container_observations": [
            {"has_border": False, "has_shadow": True},
            {"has_border": False, "has_shadow": True},
        ],
        "observed_component_types": ["button", "card", "badge"],
        "heading_font_family": _FIGMA_HEADING,
        "body_font_family": _FIGMA_BODY,
        "font_weights_observed": [400, 600, 700],
        "radius_convention": "rounded",
        "spacing_px": [8, 16, 24],
        "explicit_color_styles": True,
        "explicit_text_styles": True,
    }
    return RawSignals(provider="figma", ref="figma-file-key", signals=signals)


def _github_fixture() -> RawSignals:
    """Synthetic gather bag for GithubExtractor.normalize.

    Key names match exactly what GithubExtractor.normalize reads from raw.signals
    (the gather_github_signals / extract_raw_signals output shape):
    files_present, colors (role-keyed: primary/background/foreground/surface/
    muted/border), fonts, spacing, radius, shadows, components,
    inferred_components.

    Using explicit config keys (colors dict) so explicit.accent/neutrals are True
    and confidence can reach "high" or "medium".
    """
    signals = {
        "files_present": ["tailwind.config.ts", "tokens.json"],
        "colors": {
            "primary": _GITHUB_ACCENT,
            "background": _GITHUB_BG,
            "foreground": _GITHUB_FG,
            "surface": _GITHUB_SURFACE,
            "muted": _GITHUB_MUTED,
            "border": _GITHUB_BORDER,
        },
        "fonts": [_GITHUB_HEADING],
        "spacing": [8, 16, 24, 32],
        "radius": "0.375rem",
        "shadows": ["0 1px 3px rgba(0,0,0,0.12)"],
        "components": ["button", "card"],
        "inferred_components": ["input", "badge"],
    }
    return RawSignals(provider="github", ref="org/repo", signals=signals)


# ── Normalize helpers ────────────────────────────────────────────────────────


def _normalize_web() -> DesignSystem:
    return WebExtractor().normalize(_web_fixture())


def _normalize_figma() -> DesignSystem:
    return FigmaExtractor().normalize(_figma_fixture())


def _normalize_github() -> DesignSystem:
    return GithubExtractor().normalize(_github_fixture())


def _all_results() -> list[tuple[str, DesignSystem]]:
    return [
        ("web", _normalize_web()),
        ("figma", _normalize_figma()),
        ("github", _normalize_github()),
    ]


# ── Completeness helper ──────────────────────────────────────────────────────

_ASSERTED_FIELDS_AND_BASELINES = [
    # (accessor_fn, baseline_value, description)
    (lambda ds: ds.tokens.colors.accent,          _ACCENT_BASELINE,     "accent"),
    (lambda ds: ds.tokens.colors.primary,         _PRIMARY_BASELINE,    "primary"),
    (lambda ds: ds.tokens.colors.border,          _BORDER_BASELINE,     "border"),
    (lambda ds: ds.tokens.colors.muted,           _MUTED_BASELINE,      "muted"),
    (lambda ds: ds.tokens.colors.background,      _BACKGROUND_BASELINE, "background"),
    (lambda ds: ds.tokens.colors.foreground,      _FOREGROUND_BASELINE, "foreground"),
    (lambda ds: ds.tokens.fonts.heading_family,   _HEADING_BASELINE,    "heading_family"),
    (lambda ds: ds.tokens.fonts.body_family,      _BODY_BASELINE,       "body_family"),
]


def _assert_no_baseline_leaks(source: str, ds: DesignSystem) -> None:
    """Walk every load-bearing asserted field and assert value != baseline."""
    for accessor, baseline, field_name in _ASSERTED_FIELDS_AND_BASELINES:
        value = accessor(ds)
        assert value != baseline, (
            f"[{source}] field '{field_name}' is still at baseline {baseline!r} "
            f"— silent baseline leak when a real signal was supplied"
        )


# ── Test 1: Accent resolves to the gathered chromatic candidate ──────────────


def test_all_sources_resolve_chromatic_accent():
    """Each source maps its chromatic candidate onto accent; never the baseline blue."""
    expected = {
        "web": _WEB_ACCENT,
        "figma": _FIGMA_ACCENT,
        "github": _GITHUB_ACCENT,
    }
    for source, ds in _all_results():
        exp = expected[source]
        assert ds.tokens.colors.accent == exp, (
            f"[{source}] accent expected {exp!r}, got {ds.tokens.colors.accent!r}"
        )
        assert ds.tokens.colors.accent != _ACCENT_BASELINE, (
            f"[{source}] accent must not equal the baseline {_ACCENT_BASELINE!r}"
        )
        assert ds.tokens.colors.primary == ds.tokens.colors.accent, (
            f"[{source}] primary must equal accent; "
            f"got primary={ds.tokens.colors.primary!r}, accent={ds.tokens.colors.accent!r}"
        )


# ── Test 2: Neutrals resolve to fixture values ───────────────────────────────


def test_all_sources_resolve_neutrals():
    """surface/muted/border each equal the fixture's gathered value for every source."""
    expected_surface = {"web": _WEB_SURFACE, "figma": _FIGMA_SURFACE, "github": _GITHUB_SURFACE}
    expected_border = {"web": _WEB_BORDER, "figma": _FIGMA_BORDER, "github": _GITHUB_BORDER}
    expected_muted = {"web": _WEB_MUTED, "figma": _FIGMA_MUTED, "github": _GITHUB_MUTED}

    for source, ds in _all_results():
        assert ds.tokens.colors.surface == expected_surface[source], (
            f"[{source}] surface expected {expected_surface[source]!r}, "
            f"got {ds.tokens.colors.surface!r}"
        )
        assert ds.tokens.colors.border == expected_border[source], (
            f"[{source}] border expected {expected_border[source]!r}, "
            f"got {ds.tokens.colors.border!r}"
        )
        assert ds.tokens.colors.muted == expected_muted[source], (
            f"[{source}] muted expected {expected_muted[source]!r}, "
            f"got {ds.tokens.colors.muted!r}"
        )


# ── Test 3: No baseline leak when candidates were present ────────────────────


def test_no_baseline_leak_when_candidates_present():
    """accent/primary != #2563eb; border != #e5e7eb; muted != #6b7280 for all sources."""
    for source, ds in _all_results():
        assert ds.tokens.colors.accent != _ACCENT_BASELINE, (
            f"[{source}] accent leak: still at {_ACCENT_BASELINE!r}"
        )
        assert ds.tokens.colors.primary != _PRIMARY_BASELINE, (
            f"[{source}] primary leak: still at {_PRIMARY_BASELINE!r}"
        )
        assert ds.tokens.colors.border != _BORDER_BASELINE, (
            f"[{source}] border leak: still at {_BORDER_BASELINE!r}"
        )
        assert ds.tokens.colors.muted != _MUTED_BASELINE, (
            f"[{source}] muted leak: still at {_MUTED_BASELINE!r}"
        )


# ── Test 4: Pass-through fields are populated ────────────────────────────────


def test_passthrough_fields_populated():
    """background/foreground/heading_family/body_family are fixture values, not baselines."""
    expected_bg = {"web": _WEB_BG, "figma": _FIGMA_BG, "github": _GITHUB_BG}
    expected_fg = {"web": _WEB_FG, "figma": _FIGMA_FG, "github": _GITHUB_FG}
    expected_heading = {"web": _WEB_HEADING, "figma": _FIGMA_HEADING, "github": _GITHUB_HEADING}
    expected_body = {"web": _WEB_BODY, "figma": _FIGMA_BODY, "github": _GITHUB_BODY}

    for source, ds in _all_results():
        assert ds.tokens.colors.background == expected_bg[source], (
            f"[{source}] background expected {expected_bg[source]!r}, "
            f"got {ds.tokens.colors.background!r}"
        )
        assert ds.tokens.colors.background != _BACKGROUND_BASELINE, (
            f"[{source}] background leak: still at {_BACKGROUND_BASELINE!r}"
        )
        assert ds.tokens.colors.foreground == expected_fg[source], (
            f"[{source}] foreground expected {expected_fg[source]!r}, "
            f"got {ds.tokens.colors.foreground!r}"
        )
        assert ds.tokens.colors.foreground != _FOREGROUND_BASELINE, (
            f"[{source}] foreground leak: still at {_FOREGROUND_BASELINE!r}"
        )
        assert ds.tokens.fonts.heading_family == expected_heading[source], (
            f"[{source}] heading_family expected {expected_heading[source]!r}, "
            f"got {ds.tokens.fonts.heading_family!r}"
        )
        assert ds.tokens.fonts.heading_family != _HEADING_BASELINE, (
            f"[{source}] heading_family leak: still at {_HEADING_BASELINE!r}"
        )
        assert ds.tokens.fonts.body_family == expected_body[source], (
            f"[{source}] body_family expected {expected_body[source]!r}, "
            f"got {ds.tokens.fonts.body_family!r}"
        )
        assert ds.tokens.fonts.body_family != _BODY_BASELINE, (
            f"[{source}] body_family leak: still at {_BODY_BASELINE!r}"
        )


# ── Test 5: Inventory non-empty and contains known primitives ────────────────


def test_inventory_non_empty_per_source():
    """component_inventory is non-empty and contains the fixture's known primitives."""
    # button and card appear in all three fixtures
    required = {"button", "card"}
    for source, ds in _all_results():
        assert ds.component_inventory, (
            f"[{source}] component_inventory must not be empty"
        )
        inventory_set = set(ds.component_inventory)
        for primitive in required:
            assert primitive in inventory_set, (
                f"[{source}] component_inventory missing expected primitive {primitive!r}; "
                f"got {ds.component_inventory!r}"
            )


# ── Test 6: Confidence is honest per source ───────────────────────────────────


def test_confidence_honest_per_source():
    """Per-source confidence tiers are exact: web in {medium,low}; figma == high;
    github in {high,medium}."""
    web_ds = _normalize_web()
    figma_ds = _normalize_figma()
    github_ds = _normalize_github()

    # Web: no explicit flags → bounded to medium or low, never high
    assert web_ds.confidence in {"medium", "low"}, (
        f"web confidence must be 'medium' or 'low'; got {web_ds.confidence!r}"
    )
    assert web_ds.confidence != "high", (
        "web confidence must never reach 'high' (no explicit.* flags set)"
    )

    # Figma: explicit_color_styles + explicit_text_styles + gathered neutrals → high
    assert figma_ds.confidence == "high", (
        f"figma confidence with explicit styles + gathered neutrals must be 'high'; "
        f"got {figma_ds.confidence!r}"
    )

    # GitHub: explicit config map → at least medium; explicit font declared → high
    assert github_ds.confidence in {"high", "medium"}, (
        f"github confidence with explicit config must be 'high' or 'medium'; "
        f"got {github_ds.confidence!r}"
    )


# ── Test 7: Completeness — every signalled field non-default ─────────────────


def test_completeness_every_signalled_field_non_default():
    """The field-walk helper finds no load-bearing field left at its baseline
    for any source that supplied a real signal."""
    for source, ds in _all_results():
        _assert_no_baseline_leaks(source, ds)


# ── Test 8: Empty gather returns baseline per source ─────────────────────────


def test_empty_gather_returns_baseline_per_source():
    """normalize(RawSignals(signals={})) returns DesignSystem() for all three sources.

    This negative control proves the no-leak assertions above are non-vacuous:
    if an empty bag resolved to a non-baseline output, the no-leak checks above
    would pass trivially regardless of whether signals are being consumed.
    """
    neutral = DesignSystem()

    web_empty = WebExtractor().normalize(
        RawSignals(provider="web", ref="https://example.com", signals={})
    )
    assert web_empty == neutral, (
        f"web empty bag must equal DesignSystem(); got {web_empty!r}"
    )

    figma_empty = FigmaExtractor().normalize(
        RawSignals(provider="figma", ref="file-key", signals={})
    )
    assert figma_empty == neutral, (
        f"figma empty bag must equal DesignSystem(); got {figma_empty!r}"
    )

    github_empty = GithubExtractor().normalize(
        RawSignals(provider="github", ref="org/repo", signals={})
    )
    assert github_empty == neutral, (
        f"github empty bag must equal DesignSystem(); got {github_empty!r}"
    )

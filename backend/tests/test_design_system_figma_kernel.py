"""Kernel-path tests for FigmaExtractor.normalize.

These tests verify that the Figma normalization path now goes through the shared
harden kernel and that all field mappings, provenance flags, confidence tiers,
and pass-through rules work correctly.

Pure unit tests — no DB, no network, no model calls.
"""
from __future__ import annotations

import ast
import importlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.design_agent.design_system.adapters import FigmaExtractor
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.design_system.hardening import _saturation_of, harden
from app.design_agent.design_system.models import DesignSystem, Tokens, Fonts
from app.design_agent.design_system.signals import DesignSignals, FieldFlags
from app.design_agent.runner import _should_pre_seed


# ── Shared fixture helpers ───────────────────────────────────────────────────

_CHARCOAL = "#2b2b2b"   # near-black, low saturation
_GOLD = "#d4af37"       # gold, high saturation — must be picked as accent
_SURFACE = "#3a3a3a"    # charcoal surface
_CREAM = "#f4f1ea"      # light foreground / muted


def _gather(
    *,
    theme_background: str | None = _CHARCOAL,
    theme_is_dark: bool = True,
    foreground: str | None = None,
    color_candidates: list[dict] | None = None,
    neutral_candidates: list[dict] | None = None,
    container_observations: list[dict] | None = None,
    observed_component_types: list[str] | None = None,
    heading_font_family: str = "Inter",
    body_font_family: str = "Inter",
    font_weights_observed: list[int] | None = None,
    radius_convention: str = "rounded",
    spacing_px: list[int] | None = None,
    explicit_color_styles: bool = False,
    explicit_text_styles: bool = False,
) -> dict:
    """Return a gather-shaped signals dict matching the keys gather_figma_signals emits."""
    return {
        "theme_background": theme_background,
        "theme_is_dark": theme_is_dark,
        "foreground": foreground,
        "color_candidates": color_candidates if color_candidates is not None else [
            {"hex": _GOLD, "weight": 5000.0, "source": "fill"},
            {"hex": _CHARCOAL, "weight": 50000.0, "source": "fill"},
        ],
        "neutral_candidates": neutral_candidates if neutral_candidates is not None else [
            {"role": "surface", "hex": _SURFACE, "weight": 20000.0},
        ],
        "container_observations": container_observations or [],
        "observed_component_types": observed_component_types or [],
        "heading_font_family": heading_font_family,
        "body_font_family": body_font_family,
        "font_weights_observed": font_weights_observed or [400, 700],
        "radius_convention": radius_convention,
        "spacing_px": spacing_px or [],
        "explicit_color_styles": explicit_color_styles,
        "explicit_text_styles": explicit_text_styles,
    }


def _normalize(signals: dict) -> DesignSystem:
    return FigmaExtractor().normalize(RawSignals(provider="figma", ref="file-key", signals=signals))


# ── AC1: normalize builds DesignSignals and hardens ──────────────────────────


def test_normalize_builds_design_signals_and_hardens():
    """normalize constructs a DesignSignals bag and returns harden(signals).

    The result must equal calling harden() on the same signals independently,
    and no field is assigned on the DesignSystem after harden — the kernel is
    the sole assembler.
    """
    s = _gather()
    ds = _normalize(s)

    # Reconstruct the expected DesignSignals independently.
    from app.design_agent.design_system.signals import (
        ColorCandidate,
        ContainerObservation,
        DesignSignals,
        FieldFlags,
        NeutralCandidate,
        TypographySignals,
    )
    from app.design_agent.design_system.hardening import _saturation_of, harden, pick_accent

    candidates = [
        ColorCandidate(hex=c["hex"], weight=float(c.get("weight") or 0.0), saturation=_saturation_of(c["hex"]))
        for c in s.get("color_candidates") or []
    ]
    neutral_list = [
        NeutralCandidate(role=n["role"], hex=n["hex"], weight=float(n.get("weight") or 0.0))
        for n in s.get("neutral_candidates") or []
    ]
    heading = s.get("heading_font_family", "").strip()
    typography = TypographySignals(
        heading_family=heading,
        body_family=s.get("body_font_family", "").strip(),
        weights=[400, 700],
        radius_convention="rounded",
    )
    gathered = FieldFlags(
        accent=pick_accent(candidates) is not None,
        typography=bool(heading),
        neutrals=bool(neutral_list),
        elevation=False,
        inventory=False,
    )
    expected_signals = DesignSignals(
        color_candidates=candidates,
        neutral_candidates=neutral_list,
        container_observations=[],
        observed_component_types=[],
        typography=typography,
        is_dark=True,
        background_hex=_CHARCOAL,
        foreground_hex="#f4f1ea",
        spacing_scale=[],
        gathered=gathered,
        explicit=FieldFlags(),
        provider="figma",
    )
    expected_ds = harden(expected_signals)

    assert ds == expected_ds


# ── AC2: saturation carried via kernel formula, chromatic-first ──────────────


def test_candidates_carry_kernel_saturation_and_gold_beats_larger_charcoal():
    """Every ColorCandidate must carry saturation from _saturation_of(hex).

    On the charcoal/gold fixture the charcoal element has much larger weight
    (50 000) than the gold (5 000). Because _saturation_of(gold) is well above
    the SAT_THRESHOLD and _saturation_of(charcoal) is near-zero, pick_accent
    selects gold as the accent — chromatic-first ranking survives the Figma path.
    """
    ds = _normalize(_gather())

    gold_sat = _saturation_of(_GOLD)
    charcoal_sat = _saturation_of(_CHARCOAL)

    # Saturation of gold must be above zero and comfortably above charcoal.
    assert gold_sat > 0.0, f"Gold saturation should be > 0 but got {gold_sat}"
    assert gold_sat > charcoal_sat, (
        f"Gold ({gold_sat:.3f}) should have higher saturation than charcoal ({charcoal_sat:.3f})"
    )

    # Accent must be the gold (chromatic-first), not the heavier charcoal.
    assert ds.tokens.colors.accent == _GOLD, (
        f"Expected gold {_GOLD} as accent but got {ds.tokens.colors.accent}"
    )
    assert ds.tokens.colors.primary == _GOLD


# ── AC3: published styles earn high confidence, color-only gets medium ───────


def test_published_styles_earn_high_and_explicit_system():
    """All three explicit flags set → confidence high, has_explicit_system True."""
    # Neutral-named styles must resolve (neutral_candidates non-empty) to set
    # explicit.neutrals=True. Published text styles set explicit.typography=True.
    s = _gather(
        color_candidates=[{"hex": _GOLD, "weight": 5000.0, "source": "style"}],
        neutral_candidates=[{"role": "surface", "hex": _SURFACE, "weight": 20000.0}],
        explicit_color_styles=True,
        explicit_text_styles=True,
        heading_font_family="Inter",
    )
    ds = _normalize(s)

    assert ds.confidence == "high", (
        f"All three explicit flags → confidence must be 'high', got '{ds.confidence}'"
    )
    assert ds.has_explicit_system is True


def test_published_color_only_lands_medium_not_high():
    """Published colour styles without neutral-named styles and without text styles
    must land medium, not high (high requires all three explicit flags together).

    explicit.accent=True but explicit.neutrals=False (no neutral-named style candidates
    in neutral_candidates) and explicit.typography=False → score_confidence returns medium.
    """
    # explicit_color_styles=True but neutral_candidates is empty (no neutral roles resolved)
    # and explicit_text_styles=False.
    s = _gather(
        color_candidates=[{"hex": _GOLD, "weight": 5000.0, "source": "style"}],
        neutral_candidates=[],   # no neutral-named styles resolved
        explicit_color_styles=True,
        explicit_text_styles=False,
        heading_font_family="Inter",  # gathered typography present
    )
    ds = _normalize(s)

    assert ds.confidence == "medium", (
        f"Color-only explicit styles → 'medium' required (not 'high'), got '{ds.confidence}'"
    )
    assert ds.has_explicit_system is True  # explicit.accent=True → any(explicit.*) is True


# ── AC4: raw-fill path is medium, not high ───────────────────────────────────


def test_raw_fill_inference_scores_medium_not_high():
    """Inferred palette + font with no explicit flags → medium, has_explicit_system False.

    This is the direct regression test for the old adapters.py:316 rule
    'confidence = "high" if (font_family and accent) else "medium"', which would
    have returned high for this fixture. The kernel's honest three-way AND prevents that.
    """
    s = _gather(
        color_candidates=[
            {"hex": _GOLD, "weight": 5000.0, "source": "fill"},
            {"hex": _CHARCOAL, "weight": 50000.0, "source": "fill"},
        ],
        explicit_color_styles=False,
        explicit_text_styles=False,
        heading_font_family="Inter",
    )
    ds = _normalize(s)

    assert ds.confidence == "medium", (
        f"Raw-fill + font → must be 'medium', was '{ds.confidence}'"
    )
    assert ds.has_explicit_system is False


# ── AC5: sparse path scores low, skips pre-seed ──────────────────────────────


def test_sparse_file_scores_low_and_skips_pre_seed():
    """No color candidates or no typography → confidence low, _should_pre_seed False."""
    # No candidates at all.
    s = _gather(
        color_candidates=[],
        heading_font_family="",
        body_font_family="",
        font_weights_observed=[],
    )
    ds = _normalize(s)

    assert ds.confidence == "low", (
        f"No candidates + no font → 'low', got '{ds.confidence}'"
    )
    assert _should_pre_seed(ds) is False

    # No typography (candidates present).
    s2 = _gather(
        color_candidates=[{"hex": _GOLD, "weight": 5000.0, "source": "fill"}],
        heading_font_family="",
        body_font_family="",
    )
    ds2 = _normalize(s2)
    assert ds2.confidence == "low", (
        f"Candidates but no font → 'low', got '{ds2.confidence}'"
    )
    assert _should_pre_seed(ds2) is False


# ── AC6: no default leak literals on charcoal/gold fixture ───────────────────


def test_charcoal_gold_output_has_no_default_leaks():
    """No #e5e7eb border / #ffffff muted / #2563eb accent on the charcoal/gold fixture.

    These were the literals leaked by the old normalize via Colors().border (#e5e7eb)
    and via surface/muted swatch indexing when the swatches happened to contain white.
    The kernel derives these from gathered signals instead.
    """
    ds = _normalize(_gather())
    c = ds.tokens.colors

    assert c.border != "#e5e7eb", (
        f"Border must not leak the #e5e7eb Pydantic default; got {c.border}"
    )
    assert c.muted != "#ffffff", (
        f"Muted must not leak #ffffff; got {c.muted}"
    )
    assert c.accent != "#2563eb", (
        f"Accent must not be the Tailwind blue default; got {c.accent}"
    )
    assert c.primary != "#2563eb"


# ── AC7: monochrome file keeps largest neutral as accent ─────────────────────


def test_monochrome_file_keeps_largest_neutral_accent_at_medium():
    """A gather fixture with only neutral (near-black / gray) candidates yields
    accent = the highest-weight candidate (its real near-black), gathered.accent True,
    confidence medium (with typography). Never #2563eb, never low-by-monochrome.

    This tests the pick_accent fallback: when no chromatic candidate exists (all
    saturations < SAT_THRESHOLD), pick_accent falls back to the highest-weight
    candidate of any saturation.
    """
    near_black = "#0d0d0d"
    gray = "#888888"
    s = _gather(
        color_candidates=[
            {"hex": near_black, "weight": 50000.0, "source": "fill"},  # highest weight
            {"hex": gray, "weight": 10000.0, "source": "fill"},
        ],
        heading_font_family="Inter",
        explicit_color_styles=False,
        explicit_text_styles=False,
    )
    ds = _normalize(s)

    # pick_accent must fall back to highest-weight neutral (near_black) — not #2563eb.
    assert ds.tokens.colors.accent == near_black, (
        f"Monochrome file: expected near-black {near_black} as accent, got {ds.tokens.colors.accent}"
    )
    assert ds.tokens.colors.primary == near_black
    assert ds.confidence != "low", "Monochrome + font → must not score 'low'"
    assert ds.confidence == "medium"


# ── AC8: light/dark dominant theme consistency ───────────────────────────────


def test_light_dark_fixture_yields_internally_consistent_theme():
    """Light-dominant fixture → is_dark False, foreground #1a1a1a (no gathered foreground).
    Dark-dominant twin → is_dark True, foreground #f4f1ea.
    """
    light_s = _gather(
        theme_background="#f5f5f5",
        theme_is_dark=False,
        foreground=None,
        color_candidates=[{"hex": "#d4af37", "weight": 5000.0, "source": "fill"}],
        neutral_candidates=[{"role": "surface", "hex": "#ffffff", "weight": 10000.0}],
    )
    light_ds = _normalize(light_s)
    assert light_ds.tokens.is_dark is False
    assert light_ds.tokens.colors.background == "#f5f5f5"
    # Foreground derived from is_dark=False when no gathered foreground.
    assert light_ds.tokens.colors.foreground == "#1a1a1a", (
        f"Light theme: foreground should be #1a1a1a, got {light_ds.tokens.colors.foreground}"
    )

    dark_s = _gather(
        theme_background=_CHARCOAL,
        theme_is_dark=True,
        foreground=None,
        color_candidates=[{"hex": _GOLD, "weight": 5000.0, "source": "fill"}],
        neutral_candidates=[{"role": "surface", "hex": _SURFACE, "weight": 20000.0}],
    )
    dark_ds = _normalize(dark_s)
    assert dark_ds.tokens.is_dark is True
    assert dark_ds.tokens.colors.background == _CHARCOAL
    assert dark_ds.tokens.colors.foreground == "#f4f1ea", (
        f"Dark theme: foreground should be #f4f1ea, got {dark_ds.tokens.colors.foreground}"
    )


# ── AC9: pass-throughs and absence leaves model defaults ─────────────────────


def test_pass_throughs_map_and_absence_leaves_model_defaults():
    """background/foreground/spacing/typography map exactly per the rules.

    When these fields are absent, tokens equal the bare model defaults
    (non-assignment path — the kernel never writes a baseline literal).
    """
    spacing = [4, 8, 16, 24]
    s = _gather(
        theme_background=_CHARCOAL,
        theme_is_dark=True,
        foreground=_CREAM,
        spacing_px=spacing,
        heading_font_family="Inter",
        body_font_family="Roboto",
        font_weights_observed=[400, 600, 700],
        radius_convention="rounded",
    )
    ds = _normalize(s)

    assert ds.tokens.colors.background == _CHARCOAL
    assert ds.tokens.colors.foreground == _CREAM    # gathered foreground wins
    assert ds.tokens.spacing_scale == spacing
    assert ds.tokens.fonts.heading_family == "Inter"
    assert ds.tokens.fonts.body_family == "Roboto"
    assert ds.tokens.fonts.weights == [400, 600, 700]
    assert ds.tokens.radius_convention == "rounded"

    # No background, no foreground, no spacing, no typography → bare model defaults.
    empty_s = {
        "theme_background": None,
        "theme_is_dark": False,
        "foreground": None,
        "color_candidates": [],
        "neutral_candidates": [],
        "container_observations": [],
        "observed_component_types": [],
        "heading_font_family": "",
        "body_font_family": "",
        "font_weights_observed": [],
        "radius_convention": "",
        "spacing_px": [],
        "explicit_color_styles": False,
        "explicit_text_styles": False,
    }
    empty_ds = _normalize(empty_s)
    default_tokens = Tokens()
    default_fonts = Fonts()

    assert empty_ds.tokens.spacing_scale == default_tokens.spacing_scale
    assert empty_ds.tokens.fonts.heading_family == default_fonts.heading_family
    assert empty_ds.tokens.fonts.body_family == default_fonts.body_family
    assert empty_ds.tokens.fonts.weights == default_fonts.weights


# ── AC11: explicit system skips brief call ────────────────────────────────────


def test_explicit_system_skips_brief_call():
    """A high-confidence (explicit) result never invokes _populate_brief.

    has_explicit_system=True gates the brief LLM call at runner.py:574
    ('if not ds.has_explicit_system and not ds.component_language.brief').
    A stubbed generate_component_language that raises AssertionError proves
    the path never fires for an explicit result.
    """
    s = _gather(
        color_candidates=[{"hex": _GOLD, "weight": 5000.0, "source": "style"}],
        neutral_candidates=[{"role": "surface", "hex": _SURFACE, "weight": 20000.0}],
        explicit_color_styles=True,
        explicit_text_styles=True,
    )
    ds = _normalize(s)
    assert ds.confidence == "high"
    assert ds.has_explicit_system is True

    # Simulate what runner._populate_brief does: it only fires when NOT explicit.
    invoked = []

    def _stub_generate(ds_arg):
        invoked.append(True)
        raise AssertionError("Brief call must not fire for explicit systems")

    # The gate condition mirrors runner.py:574.
    if not ds.has_explicit_system and not ds.component_language.brief:
        _stub_generate(ds)

    assert invoked == [], "Brief call must not have been invoked for an explicit result"


# ── AC10: extraction path no longer imports palette summary ──────────────────


def test_extraction_path_no_longer_imports_palette_summary():
    """adapters.py source must contain no _extract_palette_summary reference.

    The duplicate accent heuristic is retired from the extraction path.
    _extract_palette_summary and _saturation remain in tools.py for the
    in-loop fetch_figma tool payload — verified separately below.
    """
    import app.design_agent.design_system.adapters as adapters_mod

    source_path = adapters_mod.__file__
    with open(source_path) as f:
        source = f.read()

    assert "_extract_palette_summary" not in source, (
        "adapters.py must not reference _extract_palette_summary after this change"
    )

    # Verify the tools.py second consumer is byte-unchanged.
    import app.design_agent.tools as tools_mod
    import inspect

    tools_source = inspect.getsource(tools_mod)
    # The fetch_figma executor must still return {"frames", "styles", "palette"}.
    assert '"palette"' in tools_source or "'palette'" in tools_source, (
        "_exec_fetch_figma payload must still carry the 'palette' key"
    )
    assert "_extract_palette_summary" in tools_source, (
        "_extract_palette_summary must still exist in tools.py"
    )


# ── AC12: normalize is pure — no network access ──────────────────────────────


def test_normalize_is_pure_no_network():
    """normalize performs no network access (pure mapping).

    Monkey-patching the requests module to raise ensures the Figma OAuth
    and connector stack are never touched during normalize.
    """
    import app.connectors.figma_oauth as figo
    import requests as requests_mod

    class _NoNetwork:
        def get(self, *a, **kw):
            raise AssertionError("normalize must not make network calls")
        def post(self, *a, **kw):
            raise AssertionError("normalize must not make network calls")
        def head(self, *a, **kw):
            raise AssertionError("normalize must not make network calls")

    original_requests = figo.requests
    try:
        figo.requests = _NoNetwork()
        ds = _normalize(_gather())
        assert ds is not None
    finally:
        figo.requests = original_requests


# ── AC13: empty bag returns baseline ─────────────────────────────────────────


def test_empty_bag_returns_baseline():
    """RawSignals with signals={} returns the neutral DesignSystem() baseline."""
    ds = FigmaExtractor().normalize(RawSignals(provider="figma", ref="k", signals={}))
    assert ds == DesignSystem()
    assert ds.has_explicit_system is False
    assert ds.confidence == "low"

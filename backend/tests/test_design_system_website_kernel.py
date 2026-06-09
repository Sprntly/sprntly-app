"""Regression gate for the website-source kernel refactor.

The web source's extraction DECISIONS moved out of the browser and into the
shared Python kernel: the sampler is now a dumb emitter of candidate lists, and
``WebExtractor.normalize`` builds a ``DesignSignals`` bag and returns
``harden(signals)``. This is a PARITY-PRESERVING refactor — the live extraction
of a real brand site must produce the EXACT same final tokens as before.

These tests pin the byte-identical sprntly.ai output as literals, and assert the
data path routes through the kernel (no inline decisions, no anthropic import).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.design_agent.scenarios.website import (
    _below_confidence,
    _map_sample,
)
from app.design_agent.design_system.adapters import WebExtractor
from app.design_agent.design_system.extractors import RawSignals


_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _sprntly_containers() -> list[dict]:
    """15 containers, consistently constructed: 12 has_border / 10 has_shadow /
    7 both. (7 both) + (5 border-only) + (3 shadow-only) = 15."""
    out: list[dict] = []
    for _ in range(7):
        out.append({"has_border": True, "has_shadow": True})
    for _ in range(5):
        out.append({"has_border": True, "has_shadow": False})
    for _ in range(3):
        out.append({"has_border": False, "has_shadow": True})
    return out


def _sprntly_raw() -> dict:
    """A sprntly-shaped RAW page.evaluate() return: a green brand CTA AND a
    larger monochrome button, real surface/border/muted neutrals, a
    border-dominant container scan, a 6-type inventory, and the kept scalars."""
    return {
        "color_candidates": [
            {"color": "rgb(14,107,79)", "area": 8000, "saturation": 0.77},
            {"color": "rgb(10,10,10)", "area": 12000, "saturation": 0.0},
        ],
        "neutral_candidates": [
            {"role": "surface", "color": "rgb(250,250,247)", "area": 5000},
            {"role": "border", "color": "rgb(232,229,221)", "area": 100},
            {"role": "muted", "color": "rgb(90,87,81)", "area": 50},
        ],
        "container_observations": _sprntly_containers(),
        "observed_component_types": [
            "avatar", "badge", "button", "card", "input", "tabs",
        ],
        "background_color": "rgb(250,250,247)",
        "heading_font_family": '"Inter", sans-serif',
        "body_font_family": "Inter",
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["12px 24px"],
    }


def _normalize(raw: dict):
    """Map a raw sample and normalize it through the web adapter."""
    mapped = _map_sample(raw)
    return WebExtractor().normalize(
        RawSignals(provider="web", ref="https://sprntly.ai", signals=mapped)
    )


def test_sprntly_fixture_reproduces_w_series_output():
    """The sprntly sample reproduces the EXACT W-series final tokens — the
    byte-identical parity gate for this refactor."""
    ds = _normalize(_sprntly_raw())

    assert ds.tokens.colors.accent == "#0e6b4f"
    assert ds.tokens.colors.primary == "#0e6b4f"
    assert ds.tokens.colors.surface == "#fafaf7"
    assert ds.tokens.colors.border == "#e8e5dd"
    assert ds.tokens.colors.muted == "#5a5751"
    assert ds.tokens.colors.foreground == "#1a1a1a"
    assert ds.tokens.elevation_style == "borders"
    assert ds.component_inventory == [
        "avatar", "badge", "button", "card", "input", "tabs",
    ]
    assert ds.confidence == "medium"
    assert ds.has_explicit_system is False


def test_accent_is_cta_green_not_blue_default():
    """The chromatic CTA green wins the accent slot — never the baseline blue,
    and never the larger monochrome button."""
    ds = _normalize(_sprntly_raw())
    assert ds.tokens.colors.accent == "#0e6b4f"
    assert ds.tokens.colors.accent != "#2563eb"


def test_map_sample_emits_candidate_lists():
    """A raw dict through _map_sample carries the candidate lists and has NONE of
    the old decided-field keys."""
    mapped = _map_sample(_sprntly_raw())

    assert isinstance(mapped["color_candidates"], list)
    assert isinstance(mapped["neutral_candidates"], list)
    assert isinstance(mapped["container_observations"], list)
    assert isinstance(mapped["observed_component_types"], list)
    assert mapped["color_candidates"]
    assert mapped["neutral_candidates"]
    assert mapped["container_observations"]
    assert mapped["observed_component_types"]

    for dead_key in ("primary_color", "surface_color", "elevation_hint", "component_counts"):
        assert dead_key not in mapped


def test_normalize_builds_design_signals_and_hardens():
    """normalize routes through the kernel: constructing the equivalent
    DesignSignals by hand and calling harden gives the same accent."""
    from app.design_agent.design_system.hardening import harden
    from app.design_agent.design_system.signals import (
        ColorCandidate,
        ContainerObservation,
        DesignSignals,
        FieldFlags,
        NeutralCandidate,
        TypographySignals,
    )

    by_hand = DesignSignals(
        color_candidates=[
            ColorCandidate(hex="#0e6b4f", weight=8000.0, saturation=0.77),
            ColorCandidate(hex="#0a0a0a", weight=12000.0, saturation=0.0),
        ],
        neutral_candidates=[
            NeutralCandidate(role="surface", hex="#fafaf7", weight=5000.0),
            NeutralCandidate(role="border", hex="#e8e5dd", weight=100.0),
            NeutralCandidate(role="muted", hex="#5a5751", weight=50.0),
        ],
        container_observations=[
            ContainerObservation(
                has_border=bool(o["has_border"]), has_shadow=bool(o["has_shadow"])
            )
            for o in _sprntly_containers()
        ],
        observed_component_types=["avatar", "badge", "button", "card", "input", "tabs"],
        typography=TypographySignals(
            heading_family="Inter", body_family="Inter", radius_convention="rounded"
        ),
        is_dark=False,
        background_hex="#fafaf7",
        foreground_hex="#1a1a1a",
        spacing_scale=[12, 24],
        gathered=FieldFlags(
            accent=True, typography=True, neutrals=True, elevation=True, inventory=True
        ),
        explicit=FieldFlags(),
        provider="web",
    )
    expected = harden(by_hand)
    got = _normalize(_sprntly_raw())

    assert got.tokens.colors.accent == expected.tokens.colors.accent
    assert got.tokens.colors.surface == expected.tokens.colors.surface
    assert got.tokens.colors.border == expected.tokens.colors.border
    assert got.tokens.colors.muted == expected.tokens.colors.muted
    assert got.tokens.elevation_style == expected.tokens.elevation_style
    assert got.confidence == expected.confidence
    assert got.component_inventory == expected.component_inventory


def test_empty_bag_returns_baseline():
    """An empty signal bag (the sampler's failure/low-confidence sentinel)
    short-circuits to the neutral baseline DesignSystem."""
    ds = WebExtractor().normalize(RawSignals(provider="web", ref="x", signals={}))
    assert ds.confidence == "low"
    assert ds.tokens.colors.accent == "#2563eb"


def test_below_confidence_on_no_usable_chromatic():
    """A transparent-only chromatic candidate with a heading is below-confidence;
    the sprntly sample is not."""
    transparent = _map_sample(
        {
            "color_candidates": [{"color": "rgba(0,0,0,0)", "area": 100, "saturation": 0.0}],
            "heading_font_family": "Inter",
        }
    )
    assert _below_confidence(transparent) is True
    assert _below_confidence(_map_sample(_sprntly_raw())) is False


def test_neutral_only_keeps_neutral_accent_medium():
    """A monochrome-branded sample whose only CTA candidate is a NEUTRAL keeps
    that real near-black accent (the largest-neutral fallback), NOT the baseline
    blue. With a heading present and a non-None accent, gathered.accent is True
    and confidence is medium."""
    raw = {
        "color_candidates": [{"color": "rgb(10,10,10)", "area": 9000, "saturation": 0.0}],
        "neutral_candidates": [{"role": "surface", "color": "rgb(250,250,247)", "area": 5000}],
        "heading_font_family": "Inter",
        "body_font_family": "Inter",
        "background_color": "rgb(255,255,255)",
    }
    ds = _normalize(raw)
    assert ds.tokens.colors.accent == "#0a0a0a"
    assert ds.tokens.colors.accent != "#2563eb"
    assert ds.confidence == "medium"


def test_no_candidates_baseline_low():
    """A sample with NO CTA candidates at all leaves the baseline accent and
    downgrades to low (gathered.accent is False)."""
    raw = {
        "color_candidates": [],
        "neutral_candidates": [{"role": "surface", "color": "rgb(250,250,247)", "area": 5000}],
        "heading_font_family": "Inter",
        "body_font_family": "Inter",
        "background_color": "rgb(255,255,255)",
    }
    ds = _normalize(raw)
    assert ds.tokens.colors.accent == "#2563eb"
    assert ds.confidence == "low"


def test_no_anthropic_import_added():
    """Importing the website scenario + the adapters module in a FRESH subprocess
    must NOT pull `anthropic` into that process's sys.modules. (Asserted in a
    subprocess so the result is order-independent, unlike this process's already
    populated sys.modules.)"""
    probe = (
        "import sys\n"
        "import app.design_agent.scenarios.website\n"
        "import app.design_agent.design_system.adapters\n"
        "print('ANTHROPIC' if 'anthropic' in sys.modules else 'CLEAN')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_BACKEND_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "CLEAN" in result.stdout
    assert "ANTHROPIC" not in result.stdout

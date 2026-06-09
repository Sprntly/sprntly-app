"""Tests for the pure-Python HARDEN kernel.

Each test maps to an acceptance criterion: the five extraction heuristics ported
out of the website sampler must behave identically without a browser, and the
assembler must obey no-silent-default (absent fields stay at the model baseline
by non-assignment, never written with a baked literal).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from app.design_agent.design_system.hardening import (
    SAT_THRESHOLD,
    _saturation_of,
    assemble_inventory,
    derive_elevation,
    harden,
    pick_accent,
    pick_neutrals,
    score_confidence,
)
from app.design_agent.design_system.signals import (
    ColorCandidate,
    ContainerObservation,
    DesignSignals,
    FieldFlags,
    NeutralCandidate,
    TypographySignals,
)


def test_pick_accent_chromatic_beats_monochrome_and_zero_area():
    candidates = [
        ColorCandidate(hex="#0a0a0a", weight=5, saturation=0.0),
        ColorCandidate(hex="#0e6b4f", weight=2, saturation=0.77),
        ColorCandidate(hex="#d97706", weight=0.0, saturation=0.7),
    ]
    assert pick_accent(candidates) == "#0e6b4f"


def test_pick_accent_all_neutral_returns_largest_neutral():
    candidates = [
        ColorCandidate(hex="#0a0a0a", weight=12.0, saturation=0.0),
        ColorCandidate(hex="#cccccc", weight=3.0, saturation=0.0),
    ]
    # No chromatic candidate, but candidates exist → highest-weight neutral wins
    # (a monochrome-branded site keeps its real near-black accent, not the baseline).
    assert pick_accent(candidates) == "#0a0a0a"


def test_pick_accent_empty_returns_none():
    assert pick_accent([]) is None


def test_derive_elevation_border_ahead_yields_borders():
    # 15 observations: 12 has_border, 10 has_shadow, 7 with both.
    # 7 both, 5 border-only (=12 border), 3 shadow-only (=10 shadow), 0 neither.
    obs: list[ContainerObservation] = []
    obs += [ContainerObservation(has_border=True, has_shadow=True) for _ in range(7)]
    obs += [ContainerObservation(has_border=True, has_shadow=False) for _ in range(5)]
    obs += [ContainerObservation(has_border=False, has_shadow=True) for _ in range(3)]
    assert sum(1 for o in obs if o.has_border) == 12
    assert sum(1 for o in obs if o.has_shadow) == 10
    assert derive_elevation(obs) == "borders"


def test_derive_elevation_shadow_majority_yields_shadows():
    obs = [
        ContainerObservation(has_shadow=True),
        ContainerObservation(has_shadow=True),
        ContainerObservation(has_shadow=True),
        ContainerObservation(has_border=True),
    ]
    assert derive_elevation(obs) == "shadows"


def test_derive_elevation_empty_returns_empty_string():
    assert derive_elevation([]) == ""


def test_pick_neutrals_resolves_warm_tones():
    cands = [
        NeutralCandidate(role="surface", hex="#fafaf7"),
        NeutralCandidate(role="border", hex="#e8e5dd"),
        NeutralCandidate(role="muted", hex="#5a5751"),
    ]
    assert pick_neutrals(cands, foreground=None) == {
        "surface": "#fafaf7",
        "border": "#e8e5dd",
        "muted": "#5a5751",
    }


def test_pick_neutrals_border_derived_from_foreground_when_absent():
    derived = pick_neutrals([], foreground="#0a0a0a")
    assert derived["border"] is not None
    assert len(derived["border"]) == 7 and derived["border"].startswith("#")
    assert derived["border"] != "#e5e7eb"

    none_case = pick_neutrals([], foreground=None)
    assert none_case["border"] is None


def test_assemble_inventory_known_only_sorted_deduped():
    observed = ["button", "card", "Button", "unknownthing", "badge"]
    assert assemble_inventory(observed) == ["badge", "button", "card"]


def test_score_confidence_high_medium_low():
    high = DesignSignals(
        explicit=FieldFlags(accent=True, neutrals=True, typography=True)
    )
    assert score_confidence(high) == "high"

    medium = DesignSignals(gathered=FieldFlags(accent=True, typography=True))
    assert score_confidence(medium) == "medium"

    assert score_confidence(DesignSignals()) == "low"


def test_score_confidence_medium_without_neutrals():
    sig = DesignSignals(
        gathered=FieldFlags(accent=True, typography=True, neutrals=False)
    )
    assert score_confidence(sig) == "medium"


def test_score_confidence_low_when_heading_absent():
    sig = DesignSignals(gathered=FieldFlags(accent=True, typography=False))
    assert score_confidence(sig) == "low"


def test_saturation_of_matches_hsl():
    assert _saturation_of("#0e6b4f") > SAT_THRESHOLD  # chromatic green
    assert _saturation_of("#808080") == 0.0  # gray
    assert _saturation_of("rgb(14,107,79)") == pytest.approx(_saturation_of("#0e6b4f"))


def test_harden_sprntly_web_fixture_matches_w_series():
    obs: list[ContainerObservation] = []
    obs += [ContainerObservation(has_border=True, has_shadow=True) for _ in range(7)]
    obs += [ContainerObservation(has_border=True, has_shadow=False) for _ in range(5)]
    obs += [ContainerObservation(has_border=False, has_shadow=True) for _ in range(3)]

    signals = DesignSignals(
        color_candidates=[
            ColorCandidate(hex="#0e6b4f", weight=1000.0, saturation=0.77),
            ColorCandidate(hex="#0a0a0a", weight=5000.0, saturation=0.0),
        ],
        neutral_candidates=[
            NeutralCandidate(role="surface", hex="#fafaf7"),
            NeutralCandidate(role="border", hex="#e8e5dd"),
            NeutralCandidate(role="muted", hex="#5a5751"),
        ],
        container_observations=obs,
        observed_component_types=["avatar", "badge", "button", "card", "input", "tabs"],
        typography=TypographySignals(heading_family="Inter", radius_convention="rounded"),
        background_hex="#fafaf7",
        foreground_hex="#1a1a1a",
        is_dark=False,
        spacing_scale=[4, 8, 16],
        gathered=FieldFlags(accent=True, neutrals=True, typography=True),
        explicit=FieldFlags(),
    )

    ds = harden(signals)
    assert ds.tokens.colors.accent == "#0e6b4f"
    assert ds.tokens.colors.primary == "#0e6b4f"
    assert ds.tokens.colors.surface == "#fafaf7"
    assert ds.tokens.colors.border == "#e8e5dd"
    assert ds.tokens.colors.muted == "#5a5751"
    assert ds.tokens.colors.foreground == "#1a1a1a"
    assert ds.tokens.elevation_style == "borders"
    assert ds.component_inventory == ["avatar", "badge", "button", "card", "input", "tabs"]
    assert ds.confidence == "medium"
    assert ds.has_explicit_system is False


def test_harden_empty_signals_low_confidence_baseline_visible():
    ds = harden(DesignSignals())
    assert ds.confidence == "low"
    # The model default, reached by non-assignment — not written by the kernel.
    assert ds.tokens.colors.accent == "#2563eb"


def test_harden_maps_passthrough_fields():
    signals = DesignSignals(
        background_hex="#fafaf7",
        foreground_hex="#1a1a1a",
        is_dark=False,
        typography=TypographySignals(heading_family="Inter", radius_convention="rounded"),
        spacing_scale=[4, 8, 16],
    )
    ds = harden(signals)
    assert ds.tokens.colors.background == "#fafaf7"
    assert ds.tokens.colors.foreground == "#1a1a1a"
    assert ds.tokens.is_dark is False
    assert ds.tokens.fonts.heading_family == "Inter"
    assert ds.tokens.radius_convention == "rounded"
    assert ds.tokens.spacing_scale == [4, 8, 16]

    # Empty pass-throughs leave the baseline.
    baseline = harden(DesignSignals())
    assert baseline.tokens.colors.background == "#ffffff"
    assert baseline.tokens.radius_convention == "rounded"
    assert baseline.tokens.spacing_scale == [4, 8, 12, 16, 24, 32, 48]


def test_harden_foreground_maps_and_derives_border():
    signals = DesignSignals(foreground_hex="#1a1a1a")
    ds = harden(signals)
    assert ds.tokens.colors.foreground == "#1a1a1a"
    assert len(ds.tokens.colors.border) == 7 and ds.tokens.colors.border.startswith("#")
    assert ds.tokens.colors.border != "#e5e7eb"

    empty = harden(DesignSignals(foreground_hex=""))
    assert empty.tokens.colors.foreground == "#111111"


def test_harden_does_not_call_reconcile():
    obs = [
        ContainerObservation(has_border=True, has_shadow=True),
        ContainerObservation(has_border=True, has_shadow=False),
        ContainerObservation(has_shadow=True),
    ]
    signals = DesignSignals(container_observations=obs)
    ds = harden(signals)
    assert ds.tokens.elevation_style == derive_elevation(obs)


def test_no_silent_default_when_candidate_present():
    signals = DesignSignals(
        color_candidates=[
            ColorCandidate(hex="#0e6b4f", weight=10.0, saturation=0.77),
        ]
    )
    ds = harden(signals)
    assert ds.tokens.colors.accent == "#0e6b4f"
    assert ds.tokens.colors.accent != "#2563eb"


def test_kernel_imports_no_anthropic():
    import app.design_agent.design_system.hardening as kernel

    # Importing the kernel must not transitively pull in anthropic. Checked in a
    # FRESH interpreter — asserting against this process's sys.modules would be
    # order-dependent (a sibling test that imports the SDK pollutes the global
    # table), so spawn a clean process that imports only the kernel and reports.
    backend_dir = pathlib.Path(__file__).resolve().parents[1]
    probe = (
        "import sys, importlib;"
        "importlib.import_module('app.design_agent.design_system.hardening');"
        "print(int(any(m == 'anthropic' or m.startswith('anthropic.')"
        " for m in sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0", "importing the kernel pulled in anthropic"
    assert not any(
        "anthropic" in str(getattr(kernel, name, "")).lower()
        for name in vars(kernel)
        if not name.startswith("__")
    )
    # No kernel function references a brief generator / model call.
    for fn_name in (
        "harden",
        "pick_accent",
        "pick_neutrals",
        "derive_elevation",
        "assemble_inventory",
        "score_confidence",
        "_saturation_of",
    ):
        fn = getattr(kernel, fn_name)
        code_names = fn.__code__.co_names
        assert not any("brief" in n.lower() for n in code_names)
        assert not any("anthropic" in n.lower() for n in code_names)

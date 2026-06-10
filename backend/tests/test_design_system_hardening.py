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
    CHROMA_THRESHOLD,
    SAT_THRESHOLD,
    _chroma_of,
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


# ---------------------------------------------------------------------------
# Absolute-chroma chromatic gate — luminance-stability fix
# ---------------------------------------------------------------------------

def test_chroma_of_stable_at_luminance_extremes():
    """Tinted near-black and near-white score low absolute chroma but high HSL
    saturation, proving the two formulas diverge at luminance extremes.

    The HSL formula divides by a near-zero denominator at extreme luminance, so a
    tinted near-black like #0e1116 gets an inflated "saturation" that falsely passes
    the old SAT_THRESHOLD=0.15 gate. Absolute chroma (max-min)/255 is immune because
    the raw channel spread is tiny regardless of luminance. This test documents that
    divergence explicitly so a reader can see why the gate was changed.
    """
    # Tinted near-black: absolute chroma is low (tiny channel spread); HSL sat is
    # inflated because denominator (1-|2L-1|) approaches zero near black.
    assert _chroma_of("#0e1116") == pytest.approx(0.031, abs=1e-3)
    assert _saturation_of("#0e1116") == pytest.approx(0.222, abs=1e-3)
    # Absolute chroma correctly classifies it as neutral; HSL sat falsely does not.
    assert _chroma_of("#0e1116") < CHROMA_THRESHOLD
    assert _saturation_of("#0e1116") > SAT_THRESHOLD  # the old bug

    # Tinted near-white: same story.
    assert _chroma_of("#f4f1ea") == pytest.approx(0.039, abs=1e-3)
    assert _saturation_of("#f4f1ea") == pytest.approx(0.313, abs=1e-3)
    assert _chroma_of("#f4f1ea") < CHROMA_THRESHOLD
    assert _saturation_of("#f4f1ea") > SAT_THRESHOLD  # the old bug

    # Near-pure black (max==min) — both formulas agree on zero.
    assert _chroma_of("#0a0a0a") == pytest.approx(0.0, abs=1e-6)
    assert _saturation_of("#0a0a0a") == pytest.approx(0.0, abs=1e-6)


def test_chroma_classifies_real_plotline_candidates():
    """Pin absolute-chroma classifications for every color in the measured real-file
    set (Plotline Figma file + sprntly.ai web extraction).

    Chromatic colours score >= CHROMA_THRESHOLD; near-neutrals score below it.
    Values are measured precisely — if a future change moves any of these, the fix
    broke something.
    """
    # Chromatic — must be >= CHROMA_THRESHOLD
    assert _chroma_of("#e8a33d") == pytest.approx(0.671, abs=1e-3)  # Plotline gold
    assert _chroma_of("#0e6b4f") == pytest.approx(0.365, abs=1e-3)  # sprntly green
    assert _chroma_of("#d97706") == pytest.approx(0.827, abs=1e-3)  # orange
    for h in ("#e8a33d", "#0e6b4f", "#d97706"):
        assert _chroma_of(h) >= CHROMA_THRESHOLD, f"{h} should be chromatic"

    # Near-neutral — must be < CHROMA_THRESHOLD
    neutrals = {
        "#0e1116": 0.031,   # Plotline near-black canvas (was falsely chromatic under HSL gate)
        "#161b22": 0.047,   # Plotline near-black surface (same problem)
        "#f4f1ea": 0.039,   # Plotline warm white (same problem)
        "#fafaf7": 0.012,   # sprntly surface
        "#e8e5dd": 0.043,   # sprntly border
        "#5a5751": 0.035,   # sprntly muted
        "#0a0a0a": 0.000,   # pure near-black
    }
    for h, expected in neutrals.items():
        assert _chroma_of(h) == pytest.approx(expected, abs=1e-3), f"{h} chroma mismatch"
        assert _chroma_of(h) < CHROMA_THRESHOLD, f"{h} should be neutral"

    # Unparseable input returns 0.0 without raising.
    assert _chroma_of("") == 0.0
    assert _chroma_of("notacolor") == 0.0


def test_pick_accent_plotline_returns_gold_over_large_near_black():
    """Gold accent wins despite a far-larger area near-black canvas.

    This reproduces the exact failure from the live Plotline Figma gate: gold
    #e8a33d (area ~33k) was losing to near-black canvas #0e1116 (area ~3.13M)
    because the HSL gate falsely classified #0e1116 as chromatic (sat 0.222 >
    0.15). With absolute chroma, #0e1116 is correctly neutral and the gold wins.
    """
    candidates = [
        # Near-black canvas: large area but near-neutral — must NOT win accent
        ColorCandidate(hex="#0e1116", weight=3_130_000.0, saturation=0.222),
        # Another near-black surface — also near-neutral
        ColorCandidate(hex="#161b22", weight=800_000.0, saturation=0.214),
        # Warm white near-neutral
        ColorCandidate(hex="#f4f1ea", weight=200_000.0, saturation=0.313),
        # Gold brand accent — lower area but the only genuinely chromatic colour
        ColorCandidate(hex="#e8a33d", weight=33_000.0, saturation=0.788),
    ]
    assert pick_accent(candidates) == "#e8a33d"


def test_pick_accent_sprntly_unchanged_returns_green():
    """The sprntly.ai web extraction result is byte-identical after the gate change.

    The sprntly near-black (#0a0a0a) has zero chroma (pure gray, max==min), so it
    was always neutral under both gates. Green #0e6b4f remains the chromatic winner.
    This test asserts that the fix did not disturb the web extraction path.
    """
    candidates = [
        ColorCandidate(hex="#0e6b4f", weight=1000.0, saturation=0.77),
        ColorCandidate(hex="#0a0a0a", weight=5000.0, saturation=0.0),
        # Zero-area orange — present in the real web extraction but weight=0.
        ColorCandidate(hex="#d97706", weight=0.0, saturation=0.7),
    ]
    assert pick_accent(candidates) == "#0e6b4f"


def test_pick_accent_all_neutral_fallback_preserved():
    """When every candidate is below the chroma threshold, the highest-weight
    neutral is returned — never None, never the baseline.

    This is the monochrome-brand fallback: a site using only near-grays still gets
    its real dominant color as the accent rather than falling back to the hardcoded
    default. The empty-list case still returns None (the only valid None path).
    """
    all_neutral = [
        ColorCandidate(hex="#0a0a0a", weight=5000.0, saturation=0.0),
        ColorCandidate(hex="#cccccc", weight=1000.0, saturation=0.0),
        # Tinted near-black — below chroma threshold despite inflated HSL sat
        ColorCandidate(hex="#0e1116", weight=3000.0, saturation=0.222),
    ]
    # Highest weight among all candidates (since none is chromatic) is #0a0a0a
    result = pick_accent(all_neutral)
    assert result == "#0a0a0a"
    assert result is not None

    # Empty set — the only case that returns None.
    assert pick_accent([]) is None


def test_saturation_of_unchanged():
    """_saturation_of output is numerically unchanged after the refactor to use
    _rgb_channels internally — the HSL formula was not altered, only the channel
    parsing was factored out into a shared helper.

    These values were the ground truth before the refactor; any change here means
    the refactor accidentally mutated the HSL helper.
    """
    # Pure chromatic green — well-known saturation
    assert _saturation_of("#0e6b4f") == pytest.approx(0.769, abs=1e-3)
    # Pure gray — always zero
    assert _saturation_of("#808080") == 0.0
    # rgb() form must produce the same result as hex
    assert _saturation_of("rgb(14,107,79)") == pytest.approx(_saturation_of("#0e6b4f"))
    # rgba() form — alpha channel ignored
    assert _saturation_of("rgba(14,107,79,1)") == pytest.approx(_saturation_of("#0e6b4f"))
    # Tinted near-black — inflated HSL sat (documents the known extreme-luminance issue)
    assert _saturation_of("#0e1116") == pytest.approx(0.222, abs=1e-3)
    # Near-pure black — zero (max==min guard)
    assert _saturation_of("#0a0a0a") == pytest.approx(0.0, abs=1e-6)
    # Unparseable — zero without raising
    assert _saturation_of("") == 0.0
    assert _saturation_of("notacolor") == 0.0

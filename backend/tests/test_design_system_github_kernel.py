"""Kernel-path tests for GithubExtractor.normalize.

These tests verify that the GitHub normalization path now goes through the shared
harden kernel and that all field mappings, provenance flags, confidence tiers, and
pass-through rules work correctly.

Pure unit tests — no DB, no network, no model calls.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.design_agent.design_system.adapters import GithubExtractor
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.design_system.models import DesignSystem, Tokens

_BACKEND_DIR = Path(__file__).resolve().parents[1]


# ── Shared helpers ────────────────────────────────────────────────────────────


def _raw(signals: dict) -> RawSignals:
    """Wrap a signals dict in a RawSignals with provider='github'."""
    return RawSignals(provider="github", ref="org/repo", signals=signals)


def _normalize(signals: dict) -> DesignSystem:
    return GithubExtractor().normalize(_raw(signals))


def _with_files(**extra) -> dict:
    """Return a minimal signals dict that passes the empty-bag sentinel.

    files_present is required to get past the guard; callers extend with
    any additional keys they want to test.
    """
    base = {"files_present": ["tailwind.config.ts"], "colors": {}, "fonts": []}
    base.update(extra)
    return base


# ── Creation tests ─────────────────────────────────────────────────────────


def test_explicit_colors_map_to_tokens():
    """Explicit config colors route to the correct token fields.

    A bag with colors primary/border/background should produce matching
    tokens.colors.accent, tokens.colors.primary, tokens.colors.border,
    and tokens.colors.background. The kernel maps primary to both accent
    and primary slots.
    """
    ds = _normalize(_with_files(
        colors={
            "primary": "#0e6b4f",
            "border": "#dddddd",
            "background": "#ffffff",
        },
    ))
    assert ds.tokens.colors.accent == "#0e6b4f", (
        f"Expected accent #0e6b4f, got {ds.tokens.colors.accent}"
    )
    assert ds.tokens.colors.primary == "#0e6b4f", (
        f"Expected primary #0e6b4f, got {ds.tokens.colors.primary}"
    )
    assert ds.tokens.colors.border == "#dddddd", (
        f"Expected border #dddddd, got {ds.tokens.colors.border}"
    )
    assert ds.tokens.colors.background == "#ffffff", (
        f"Expected background #ffffff, got {ds.tokens.colors.background}"
    )


def test_explicit_config_and_font_scores_high():
    """Explicit config colors + explicit font declaration → confidence high, has_explicit_system True.

    This is the 'all three explicit flags' path: explicit.accent, explicit.neutrals
    (via a border/surface), and explicit.typography (via a recognized font) each set True.
    score_confidence returns 'high' only when all three are set.
    """
    ds = _normalize(_with_files(
        colors={"primary": "#10b981", "border": "#cccccc"},
        fonts=["Inter"],
    ))
    assert ds.confidence == "high", (
        f"Explicit config + font → confidence must be 'high', got '{ds.confidence}'"
    )
    assert ds.has_explicit_system is True


def test_background_derives_foreground_dark():
    """Dark background derives a light foreground when no explicit foreground is provided.

    The derivation rule: luminance(background) < 128 → foreground = '#f4f1ea'.
    This preserves the rule from the previous implementation.
    """
    ds = _normalize(_with_files(colors={"background": "#000000"}))
    assert ds.tokens.colors.foreground == "#f4f1ea", (
        f"Dark background → foreground must be #f4f1ea, got {ds.tokens.colors.foreground}"
    )


def test_background_derives_foreground_light():
    """Light background derives a dark foreground when no explicit foreground is provided.

    The derivation rule: luminance(background) >= 128 → foreground = '#1a1a1a'.
    """
    ds = _normalize(_with_files(colors={"background": "#ffffff"}))
    assert ds.tokens.colors.foreground == "#1a1a1a", (
        f"Light background → foreground must be #1a1a1a, got {ds.tokens.colors.foreground}"
    )


# ── Routing / weighting tests ──────────────────────────────────────────────


def test_no_chromatic_prefilter_neutral_accent_survives():
    """A near-neutral lone candidate still becomes the accent token.

    normalize performs no chromatic pre-filter; every resolved color reaches the
    kernel. When no chromatic candidate exists, pick_accent falls back to the
    highest-weight candidate regardless of chroma. The accent must be the actual
    resolved color, not the default baseline.
    """
    ds = _normalize(_with_files(colors={"primary": "#0a0a0a"}))
    assert ds.tokens.colors.accent == "#0a0a0a", (
        f"Near-neutral lone candidate must become accent; got {ds.tokens.colors.accent}"
    )


def test_explicit_primary_outranks_inferred_primary():
    """Explicit config primary (weight 2.0) out-ranks inferred className primary (weight 1.0).

    When both explicit and inferred maps have the same 'primary' role key, the
    explicit one must win because it enters the seam with a higher weight.
    """
    ds = _normalize(_with_files(
        colors={"primary": "#0e6b4f"},
        inferred_colors={"primary": "#3b82f6"},
    ))
    assert ds.tokens.colors.accent == "#0e6b4f", (
        f"Explicit primary must out-rank inferred; expected #0e6b4f, got {ds.tokens.colors.accent}"
    )


def test_inventory_routed_through_assemble_inventory():
    """Inventory is routed raw to the kernel; assemble_inventory deduplicates and filters.

    The union of components and inferred_components is passed as observed_component_types.
    assemble_inventory (in the kernel) normalises case, drops non-primitive names, and
    sorts the result. The normalize method must not pre-sort or pre-filter.
    """
    ds = _normalize(_with_files(
        components=["button", "Button", "dashboard"],
        inferred_components=["card"],
    ))
    # 'button' and 'Button' deduplicate to 'button'; 'dashboard' is not a primitive.
    assert ds.component_inventory == ["button", "card"], (
        f"Expected ['button', 'card'], got {ds.component_inventory}"
    )


# ── Confidence / provenance tests ──────────────────────────────────────────


def test_inferred_only_never_scores_high():
    """Inferred className signals without an explicit config file never earn 'high' confidence.

    explicit.* flags are all False when only inferred_colors and inferred_fonts
    are present. score_confidence can return at most 'medium' (requires gathered.accent
    and gathered.typography) or 'low'.
    """
    ds = _normalize(_with_files(
        inferred_colors={"primary": "#10b981"},
        inferred_fonts=["font-weight"],
    ))
    assert ds.confidence in {"medium", "low"}, (
        f"Inferred-only signals must not reach 'high'; got '{ds.confidence}'"
    )
    assert ds.confidence != "high"


def test_has_explicit_system_false_for_inferred_only():
    """has_explicit_system is False when only inferred signals are present.

    has_explicit_system reflects any(explicit.*); all flags are False when the
    source has no real config/CSS-var/token file colors or font declarations.
    """
    ds = _normalize(_with_files(
        inferred_colors={"primary": "#10b981"},
        inferred_fonts=["font-weight"],
    ))
    assert ds.has_explicit_system is False


# ── No-silent-default tests ────────────────────────────────────────────────


def test_no_baseline_literals_written_in_normalize():
    """normalize's method body contains no default-baseline hex literals.

    The literals '#2563eb', '#e5e7eb', '#6b7280' are the Pydantic model defaults.
    Writing them inline would re-introduce the shared-default-leak the kernel
    path was built to remove. This test does a source scan of the method body.

    Additionally confirms the method ends with 'return harden(signals)' — no
    field is assigned on the DesignSystem after harden (harden is the sole assembler).
    """
    import app.design_agent.design_system.adapters as adapters_mod

    source_path = adapters_mod.__file__
    with open(source_path) as f:
        source = f.read()

    # Parse out just the GithubExtractor.normalize body for a targeted scan.
    tree = ast.parse(source)
    method_source = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "GithubExtractor":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "normalize":
                    method_source = ast.get_source_segment(source, item)
                    break

    assert method_source is not None, "Could not locate GithubExtractor.normalize in source"

    for literal in ("#2563eb", "#e5e7eb", "#6b7280"):
        assert literal not in method_source, (
            f"Baseline literal {literal!r} must not appear in normalize's body"
        )

    # The final non-blank, non-comment line must be 'return harden(signals)'.
    lines = [
        line.strip()
        for line in method_source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines[-1] == "return harden(signals)  # sole assembler — no field assigned on the result after this" or lines[-1].startswith("return harden("), (
        f"Last substantive line must be 'return harden(...)'; got: {lines[-1]!r}"
    )


def test_radius_absent_leaves_model_default():
    """No radius/inferred_radius signal → tokens.radius_convention equals the model default.

    The previous implementation computed _radius_convention("") which floors to
    "rounded" even with no evidence. The new path leaves radius_convention at the
    model default by non-assignment when no real signal is present.
    """
    ds = _normalize(_with_files())
    default_radius = Tokens().radius_convention
    assert ds.tokens.radius_convention == default_radius, (
        f"Absent radius → model default {default_radius!r}; got {ds.tokens.radius_convention!r}"
    )


def test_radius_present_from_signal():
    """A real radius signal is converted to the correct convention.

    '0.5rem' is a standard rounded border-radius value; _radius_convention maps
    it to 'rounded'.
    """
    ds = _normalize(_with_files(radius="0.5rem"))
    assert ds.tokens.radius_convention == "rounded", (
        f"radius='0.5rem' → convention 'rounded'; got {ds.tokens.radius_convention!r}"
    )


def test_spacing_absent_leaves_model_default():
    """No spacing/inferred_spacing signal → tokens.spacing_scale equals the model default.

    An empty list signals absence to the kernel; the kernel then leaves
    Tokens.spacing_scale at the model default by non-assignment.
    """
    ds = _normalize(_with_files())
    default_spacing = Tokens().spacing_scale
    assert ds.tokens.spacing_scale == default_spacing, (
        f"Absent spacing → model default; got {ds.tokens.spacing_scale!r}"
    )


def test_spacing_present_from_signal():
    """A real spacing signal is passed through to tokens.spacing_scale exactly.

    The kernel assigns the list as-is when it is non-empty.
    """
    ds = _normalize(_with_files(spacing=[4, 8, 16]))
    assert ds.tokens.spacing_scale == [4, 8, 16], (
        f"spacing=[4,8,16] → tokens.spacing_scale must be [4,8,16]; got {ds.tokens.spacing_scale!r}"
    )


def test_elevation_not_forced_by_loose_shadow_signal():
    """Shadows in the gather bag do not force elevation_style.

    The previous implementation set elevation_style='shadows' whenever any shadow
    token was present. GitHub gather has no per-container observations (no real
    evidence of how elevation is applied). Passing container_observations=[] means
    the kernel's derive_elevation returns "" and leaves Tokens.elevation_style at
    the model default — the coarse any-shadow heuristic is not carried forward.
    """
    ds = _normalize(_with_files(shadows=["0 1px 2px #000"]))
    default_elevation = Tokens().elevation_style
    assert ds.tokens.elevation_style == default_elevation, (
        f"Shadow tokens alone must not force elevation; expected model default "
        f"'{default_elevation}', got '{ds.tokens.elevation_style}'"
    )


# ── Edge / error tests ─────────────────────────────────────────────────────


def test_empty_bag_returns_baseline():
    """RawSignals with signals={} returns the neutral DesignSystem() baseline.

    The empty-bag sentinel fires before any signal processing.
    """
    ds = GithubExtractor().normalize(RawSignals(provider="github", signals={}))
    assert ds == DesignSystem()
    assert ds.has_explicit_system is False
    assert ds.confidence == "low"
    assert ds.component_inventory == []


def test_bag_without_files_present_returns_baseline():
    """A bag with neither files_present nor inference_files returns the baseline.

    Both keys must be absent (or empty) to trigger the sentinel.
    """
    ds = _normalize({"colors": {"primary": "#0e6b4f"}})
    assert ds == DesignSystem(), (
        "No files_present/inference_files → sentinel must return baseline DesignSystem()"
    )


# ── Existing-file integrity ────────────────────────────────────────────────


def test_registry_github_unchanged():
    """registry.get('github') still returns a GithubExtractor with the expected attributes.

    The rewrite touches only the normalize method body; the class identity,
    category, and provider must be unchanged.
    """
    from app.design_agent.design_system.extractors import registry

    adapter = registry.get("github")
    assert isinstance(adapter, GithubExtractor), (
        f"registry.get('github') must return a GithubExtractor; got {type(adapter)}"
    )
    assert adapter.category == "codebase", (
        f"GithubExtractor.category must be 'codebase'; got {adapter.category!r}"
    )
    assert adapter.provider == "github", (
        f"GithubExtractor.provider must be 'github'; got {adapter.provider!r}"
    )


def test_adapters_module_imports_without_anthropic():
    """Importing adapters.py in a fresh subprocess must not pull in the anthropic package.

    normalize is a pure mapping function — no model calls. The subprocess probe
    is used so the result is independent of the order tests run; the test process
    itself may have anthropic already loaded by the time this test executes.
    """
    probe = (
        "import sys\n"
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
    assert "CLEAN" in result.stdout, (
        "Importing adapters.py must not pull in the 'anthropic' package; "
        f"got stdout={result.stdout!r}"
    )
    assert "ANTHROPIC" not in result.stdout

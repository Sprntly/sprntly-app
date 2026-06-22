"""Tests for semantic (status) colour capture in the design-system pipeline.

The website sampler scrapes a rich palette but the kernel previously discarded
every status colour: `SemanticColors` existed in the schema yet `harden()` never
populated `colors.semantic`, and `_render_palette_css` hardcoded `--destructive`
and emitted no `--warning`/`--success`. This suite proves the end-to-end fidelity
fix:

  * the sampler-mapping layer threads `semantic_candidates` through normalize,
  * the kernel's `pick_semantics` hue-buckets candidates into warning/error/success,
  * the renderer emits `--warning`/`--error`/`--success` from the captured values,
  * accent / primary selection is untouched by the new capture,
  * the GitHub adapter reaches `colors.semantic` from its `destructive` role,
  * a thin signal leaves `colors.semantic` at defaults and the pre-existing CSS
    vars byte-identical, and
  * hue bucketing maps known reds/ambers/greens correctly and rejects grays.

Pure unit tests — no DB, no browser, no model, no real build.
"""
from __future__ import annotations

from app.design_agent.design_system.adapters import GithubExtractor, WebExtractor
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.design_system.hardening import harden, pick_accent, pick_semantics
from app.design_agent.design_system.models import SemanticColors
from app.design_agent.design_system.signals import (
    ColorCandidate,
    DesignSignals,
    SemanticCandidate,
)
from app.design_agent.runner import _render_palette_css


_DEFAULTS = SemanticColors()


def _css_vars(css: str) -> dict[str, str]:
    import re

    return dict(re.findall(r"--([a-z-]+):\s*([^;]+);", css))


# ─── Sampler → adapter threading ──────────────────────────────────────────────


def test_semantic_candidates_emitted():
    """The sampler JS can't be unit-run headless cheaply, so we assert at the
    adapter level that raw semantic_candidates thread through WebExtractor.normalize
    into the resolved design system's colors.semantic (rgb() inputs included to
    cover the sampler's raw-colour shape)."""
    raw = RawSignals(
        provider="web",
        ref="https://example.com",
        signals={
            "background_color": "#ffffff",
            "color_candidates": [{"color": "#2563eb", "area": 5000.0, "saturation": 0.6}],
            "heading_font_family": "Inter",
            "semantic_candidates": [
                {"role": "error", "color": "rgb(220, 38, 38)", "kind": "fill"},
                {"role": "warning", "color": "#d97706", "kind": "fill"},
            ],
        },
    )
    ds = WebExtractor().normalize(raw)
    assert ds.tokens.colors.semantic.error == "#dc2626"
    assert ds.tokens.colors.semantic.warning == "#d97706"
    # Unmatched bucket keeps the default.
    assert ds.tokens.colors.semantic.success == _DEFAULTS.success


# ─── Kernel: pick_semantics + harden ──────────────────────────────────────────


def test_harden_populates_semantic():
    """Captured warning + error candidates with DISTINCT-from-default hexes move
    colors.semantic.warning/error OFF their model defaults."""
    signals = DesignSignals(
        semantic_candidates=[
            SemanticCandidate(role="warning", hex="#f59e0b", kind="fill", weight=1.0),  # amber, ≠ default
            SemanticCandidate(role="error", hex="#b91c1c", kind="fill", weight=1.0),    # red, ≠ default
        ]
    )
    ds = harden(signals)
    assert ds.tokens.colors.semantic.warning == "#f59e0b"
    assert ds.tokens.colors.semantic.error == "#b91c1c"
    assert ds.tokens.colors.semantic.warning != _DEFAULTS.warning
    assert ds.tokens.colors.semantic.error != _DEFAULTS.error
    # Unmatched success bucket keeps the default.
    assert ds.tokens.colors.semantic.success == _DEFAULTS.success


def test_hue_bucketing():
    """Known yellow / red / green hexes bucket to warning / error / success; an
    ambiguous gray buckets to nothing."""
    cands = [
        SemanticCandidate(role="x", hex="#d97706", kind="fill"),  # amber → warning
        SemanticCandidate(role="x", hex="#dc2626", kind="fill"),  # red → error
        SemanticCandidate(role="x", hex="#16a34a", kind="fill"),  # green → success
        SemanticCandidate(role="x", hex="#888888", kind="fill"),  # gray → none
    ]
    out = pick_semantics(cands)
    assert out["warning"] == "#d97706"
    assert out["error"] == "#dc2626"
    assert out["success"] == "#16a34a"

    gray_only = pick_semantics([SemanticCandidate(role="x", hex="#888888", kind="fill")])
    assert gray_only == {"success": None, "error": None, "warning": None}


def test_pick_semantics_weight_tiebreak():
    """Within a bucket, the highest-weight candidate wins."""
    out = pick_semantics(
        [
            SemanticCandidate(role="error", hex="#ef4444", kind="fill", weight=1.0),
            SemanticCandidate(role="error", hex="#b91c1c", kind="fill", weight=5.0),
        ]
    )
    assert out["error"] == "#b91c1c"


# ─── Renderer ────────────────────────────────────────────────────────────────


def test_renderer_emits_semantic_vars():
    palette = {
        "background": "#ffffff",
        "accent": "#2563eb",
        "is_dark": False,
        "swatches": ["#ffffff", "#f3f4f6", "#6b7280"],
        "font_family": None,
        "font_weights": [400, 700],
        "semantic": {"success": "#16a34a", "error": "#dc2626", "warning": "#d97706"},
    }
    css = _render_palette_css(palette)
    vars_ = _css_vars(css)
    assert vars_["warning"] == "32 95% 44%"   # #d97706
    assert vars_["error"] == "0 72% 51%"      # #dc2626
    assert vars_["success"] == "142 76% 36%"  # #16a34a
    # --destructive is now token-driven from the same error value.
    assert vars_["destructive"] == "0 72% 51%"


# ─── Accent invariance ───────────────────────────────────────────────────────


def test_accent_unchanged_with_semantics():
    """Adding semantic candidates must not change accent / primary selection."""
    base = DesignSignals(
        color_candidates=[
            ColorCandidate(hex="#2563eb", weight=5000.0, saturation=0.6),
            ColorCandidate(hex="#111111", weight=9000.0, saturation=0.0),
        ]
    )
    with_sem = DesignSignals(
        color_candidates=[
            ColorCandidate(hex="#2563eb", weight=5000.0, saturation=0.6),
            ColorCandidate(hex="#111111", weight=9000.0, saturation=0.0),
        ],
        semantic_candidates=[
            SemanticCandidate(role="error", hex="#dc2626", kind="fill", weight=1.0),
            SemanticCandidate(role="success", hex="#16a34a", kind="fill", weight=1.0),
        ],
    )
    ds_base = harden(base)
    ds_sem = harden(with_sem)
    assert ds_base.tokens.colors.accent == ds_sem.tokens.colors.accent
    assert ds_base.tokens.colors.primary == ds_sem.tokens.colors.primary
    # pick_accent itself is identical regardless of semantics.
    assert pick_accent(base.color_candidates) == pick_accent(with_sem.color_candidates)


# ─── GitHub parity ───────────────────────────────────────────────────────────


def test_github_semantic_parity():
    """GithubExtractor.normalize over signals["colors"] with a destructive role
    populates colors.semantic.error (destructive's red hue buckets to error)."""
    raw = RawSignals(
        provider="github",
        ref="org/repo",
        signals={
            "files_present": ["tailwind.config.ts"],
            "colors": {
                "primary": "#2563eb",
                "destructive": "#dc2626",
                "secondary": "#64748b",  # NOT a status colour — must be ignored
            },
        },
    )
    ds = GithubExtractor(installation_id=1).normalize(raw)
    assert ds.tokens.colors.semantic.error == "#dc2626"
    # secondary is not fed as a semantic candidate — warning/success stay default.
    assert ds.tokens.colors.semantic.warning == _DEFAULTS.warning
    assert ds.tokens.colors.semantic.success == _DEFAULTS.success


# ─── Thin-signal defaults + byte-identity of pre-existing vars ────────────────


def test_no_semantic_candidates_keeps_defaults():
    """A thin signal leaves colors.semantic at defaults, AND the pre-existing CSS
    vars (--destructive value, --secondary, neutrals, --card, foreground, accent/
    primary) stay byte-identical to a baseline render without the semantic key.
    The newly-added additive vars (--warning/--success/--error) are expected and
    are NOT part of the byte-identity claim (per the AC interpretation)."""
    # harden with no semantic candidates → defaults.
    ds = harden(DesignSignals())
    assert ds.tokens.colors.semantic == _DEFAULTS

    # Renderer: a baseline palette WITHOUT a semantic key vs WITH explicit defaults
    # must agree on every pre-existing var.
    base_palette = {
        "background": "#1e1e2e",
        "accent": "#cba6f7",
        "is_dark": True,
        "swatches": ["#1e1e2e", "#313244", "#6c7086"],
        "font_family": None,
        "font_weights": [400, 700],
    }
    css_no_key = _render_palette_css(base_palette)
    vars_no_key = _css_vars(css_no_key)

    # Pre-existing vars present and stable; --destructive defaults to the error
    # default's HSL (#dc2626 → "0 72% 51%"), unchanged from the prior hardcode.
    assert vars_no_key["destructive"] == "0 72% 51%"
    for token in (
        "background", "foreground", "card", "secondary", "secondary-foreground",
        "muted", "border", "input", "primary", "accent",
    ):
        assert token in vars_no_key, f"--{token} missing"

    # A palette that passes the explicit SemanticColors defaults yields the SAME
    # pre-existing vars (additive vars differ only by being present in both).
    with_defaults = dict(base_palette)
    with_defaults["semantic"] = {
        "success": _DEFAULTS.success,
        "error": _DEFAULTS.error,
        "warning": _DEFAULTS.warning,
    }
    vars_defaults = _css_vars(_render_palette_css(with_defaults))
    for token in (
        "background", "foreground", "card", "popover", "primary", "primary-foreground",
        "secondary", "secondary-foreground", "muted", "muted-foreground", "accent",
        "accent-foreground", "destructive", "destructive-foreground", "border",
        "input", "ring", "radius",
    ):
        assert vars_no_key.get(token) == vars_defaults.get(token), (
            f"--{token} drifted between no-semantic and default-semantic render"
        )
    # The new additive vars appear with the default values.
    assert vars_defaults["warning"] == "32 95% 44%"
    assert vars_defaults["success"] == "142 76% 36%"
    assert vars_defaults["error"] == "0 72% 51%"

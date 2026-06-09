"""The shared HARDEN kernel: pure-Python resolution of design signals.

This module is the sole consumer of `DesignSignals`. It folds a normalized,
source-agnostic bag of candidates plus provenance into a finished
`DesignSystem`. Every heuristic here is a faithful port of the rules that
previously lived welded inside the website sampler's JS-over-DOM string, lifted
out so they are testable without a browser.

The kernel is 100% deterministic: no I/O, no model calls, no anthropic import.
A bare `harden(DesignSignals())` returns the neutral `DesignSystem()` baseline
because every absent field is left at the model default by NON-assignment — the
kernel never writes a baked baseline literal of its own.
"""
from __future__ import annotations

from typing import Literal

from .models import Colors, DesignSystem, Fonts, Tokens
from .signals import (
    ColorCandidate,
    ContainerObservation,
    DesignSignals,
    NeutralCandidate,
)

# Import (do NOT re-implement) the canonical color helpers + component vocabulary.
# Importing `adapters` does not pull in anthropic/playwright/requests (verified),
# so the kernel keeps its no-anthropic-import property.
from .adapters import _COMPONENT_HINTS, _is_hex, _normalize_hex

# Chromatic-ness floor. A candidate at or above this saturation is treated as a
# real brand color; below it is a neutral that must never win the accent slot.
# Ported from website.py:108.
SAT_THRESHOLD = 0.15


def _saturation_of(color: str) -> float:
    """Faithful Python port of the JS `saturationOf` HSL formula (website.py:89-107).

    Accepts ``#rrggbb``, ``rgb(r,g,b)``, or ``rgba(r,g,b,a)``. Parses r,g,b; if
    max == min returns 0.0; else L = (max+min)/2 on 0..1 and returns
    ``(max-min)/(1-abs(2L-1))``. Returns 0.0 on anything unparseable.

    This is the canonical Python saturation for sources that gather a hex without
    a precomputed saturation (future Figma/GitHub gather layers reuse it). It is
    deliberately NOT tools.py:_saturation, which uses a different (max-min)/max
    formula and must not be used for accent selection.
    """
    if not color:
        return 0.0
    c = color.strip().lower()
    r = g = b = None
    if c.startswith(("rgb(", "rgba(")) and "(" in c and ")" in c:
        inner = c[c.index("(") + 1 : c.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 3:
            try:
                r, g, b = (float(parts[i]) for i in range(3))
            except ValueError:
                return 0.0
    elif c.startswith("#") and len(c) >= 7:
        try:
            r = int(c[1:3], 16)
            g = int(c[3:5], 16)
            b = int(c[5:7], 16)
        except ValueError:
            return 0.0
    else:
        return 0.0
    if r is None or g is None or b is None:
        return 0.0
    r /= 255.0
    g /= 255.0
    b /= 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx == mn:
        return 0.0
    lum = (mx + mn) / 2.0
    return (mx - mn) / (1 - abs(2 * lum - 1))


def pick_accent(candidates: list[ColorCandidate]) -> str | None:
    """Accent selection — chromatic-first, else largest neutral (website.py port).

    Keep candidates whose carried saturation >= SAT_THRESHOLD (chromatic). If any
    chromatic candidate exists, rank those by weight desc and return the top hex.
    If NONE is chromatic but candidates exist, fall back to the highest-weight
    candidate regardless of saturation (a monochrome-branded site's real near-
    black/near-white accent — today's behaviour). Return None ONLY when there are
    no candidates at all; the caller then leaves the baseline + downgrades.

    Note: the JS tie-break by top-of-page position is not reproducible here since
    ColorCandidate carries no `top` field — an accepted edge. Python's max returns
    the first max on ties, which is the stable, deterministic choice.
    """
    if not candidates:
        return None
    chromatic = [c for c in candidates if c.saturation >= SAT_THRESHOLD]
    pool = chromatic or candidates
    return max(pool, key=lambda c: c.weight).hex


def _blend_over_white(hex_color: str, alpha: float) -> str:
    """Blend a #rrggbb over white at `alpha`, returning #rrggbb.

    Per channel: round(alpha*channel + (1-alpha)*255). Used to derive a border
    tone from the foreground when no border candidate was gathered.
    """
    norm = _normalize_hex(hex_color)
    r = int(norm[1:3], 16)
    g = int(norm[3:5], 16)
    b = int(norm[5:7], 16)
    out = [
        round(alpha * ch + (1 - alpha) * 255)
        for ch in (r, g, b)
    ]
    return "#" + "".join(f"{max(0, min(255, ch)):02x}" for ch in out)


def pick_neutrals(
    candidates: list[NeutralCandidate], *, foreground: str | None
) -> dict[str, str | None]:
    """Resolve surface/border/muted from neutral candidates (website.py:171-197).

    For each role, return the highest-weight (stable) candidate's hex, else None.

    Central leak fix: when `border` has no candidate but `foreground` is a usable
    hex, derive border from the foreground at low alpha (~0.13) blended over white
    — a gathered relative — instead of leaving the #e5e7eb baseline. pick_neutrals
    only receives foreground (not background); blend-over-white is the documented
    light-mode simplification (the web byte-identical gate site has a real border
    candidate, so this path is not exercised there). Surface/muted absent -> None
    (caller leaves baseline + downgrades).
    """
    result: dict[str, str | None] = {"surface": None, "border": None, "muted": None}
    for role in ("surface", "border", "muted"):
        role_cands = [c for c in candidates if c.role == role]
        if role_cands:
            role_cands.sort(key=lambda c: c.weight, reverse=True)
            result[role] = role_cands[0].hex
    if result["border"] is None and foreground and _is_hex(_normalize_hex(foreground)):
        result["border"] = _blend_over_white(foreground, 0.13)
    return result


def derive_elevation(observations: list[ContainerObservation]) -> str:
    """Prevalence-count elevation derivation (website.py:206-226).

    shadow_count = #obs with has_shadow; border_count = #obs with has_border.
    If neither is observed at all, return "". Else: strictly more shadows ->
    "shadows"; otherwise (tie or borders ahead) -> "borders".
    """
    shadow_count = sum(1 for o in observations if o.has_shadow)
    border_count = sum(1 for o in observations if o.has_border)
    if shadow_count == 0 and border_count == 0:
        return ""
    return "shadows" if shadow_count > border_count else "borders"


def assemble_inventory(observed_types: list[str]) -> list[str]:
    """Keep known primitive types only, deduped + sorted (website.py:231-260).

    Compares case-insensitively against `_COMPONENT_HINTS`.
    """
    known = {h.lower() for h in _COMPONENT_HINTS}
    out = {t.lower() for t in observed_types if t.lower() in known}
    return sorted(out)


def score_confidence(signals: DesignSignals) -> Literal["high", "medium", "low"]:
    """Tiered confidence.

    high   -> explicit.accent AND explicit.neutrals AND explicit.typography
    medium -> NOT high, AND gathered.accent AND gathered.typography
    low    -> otherwise.

    The medium predicate is `gathered.accent AND gathered.typography` (heading) —
    NOT gathered.neutrals — reproducing today's web rule
    `medium if (primary and heading) else low`. `high` is a net-new tier only
    reachable by explicit sources; web never sets explicit.*, so web stays bounded
    to medium/low exactly as before.
    """
    if signals.explicit.accent and signals.explicit.neutrals and signals.explicit.typography:
        return "high"
    if signals.gathered.accent and signals.gathered.typography:
        return "medium"
    return "low"


def harden(signals: DesignSignals) -> DesignSystem:
    """Compose a finished `DesignSystem` from normalized signals.

    harden is the SOLE assembler (D1): pass-throughs map straight with no
    decisions; the heuristics live in the helper functions above. Every absent
    field is left at the model default by NON-assignment — never written with a
    baked baseline literal.
    """
    colors = Colors()

    # Accent: chromatic-first. None -> leave primary/accent at default.
    accent = pick_accent(signals.color_candidates)
    if accent is not None:
        colors.primary = accent
        colors.accent = accent

    # Neutrals: highest-weight per role, with foreground-derived border fallback.
    neutrals = pick_neutrals(
        signals.neutral_candidates,
        foreground=signals.foreground_hex or None,
    )
    if neutrals["surface"] is not None:
        colors.surface = neutrals["surface"]
    if neutrals["border"] is not None:
        colors.border = neutrals["border"]
    if neutrals["muted"] is not None:
        colors.muted = neutrals["muted"]

    # Non-heuristic pass-throughs (no-silent-default: empty/""/[] -> leave default).
    if signals.background_hex:
        colors.background = signals.background_hex
    if signals.foreground_hex:
        colors.foreground = signals.foreground_hex

    fonts = Fonts()
    if signals.typography.heading_family:
        fonts.heading_family = signals.typography.heading_family
    if signals.typography.body_family:
        fonts.body_family = signals.typography.body_family
    if signals.typography.weights:
        fonts.weights = signals.typography.weights

    tokens = Tokens(colors=colors, fonts=fonts, is_dark=signals.is_dark)
    if signals.typography.radius_convention:
        tokens.radius_convention = signals.typography.radius_convention
    if signals.spacing_scale:
        tokens.spacing_scale = signals.spacing_scale

    # Elevation: raw prevalence answer. Empty "" -> leave Tokens default.
    # harden does NOT reconcile (that runs later in the runner, post-brief).
    elevation = derive_elevation(signals.container_observations)
    if elevation:
        tokens.elevation_style = elevation

    inventory = assemble_inventory(signals.observed_component_types)
    confidence = score_confidence(signals)
    has_explicit_system = any(
        (
            signals.explicit.accent,
            signals.explicit.neutrals,
            signals.explicit.elevation,
            signals.explicit.inventory,
            signals.explicit.typography,
        )
    )

    return DesignSystem(
        tokens=tokens,
        component_inventory=inventory,
        has_explicit_system=has_explicit_system,
        confidence=confidence,
    )

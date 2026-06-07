"""Concrete design-source adapters: Figma and live website.

Each adapter folds one source's raw, provider-specific signals into the shared
`DesignSystem` shape. The two adapters here wrap extraction logic that already
exists elsewhere in the codebase — they do NOT reimplement the Figma document
walk or the headless-browser sampler. They only:

  1. Capture that source's signals into a `RawSignals` bag, and
  2. Map those signals onto the common `DesignSystem` tokens.

Mapping is deterministic. No model is consulted here: `component_language.brief`
stays its default empty string. A model-written brief is layered in later; until
then every field is filled by a fixed rule from the signals at hand.

Importing this module registers both adapters in the shared `registry`, so any
caller that imports the `design_system` package can resolve an adapter by
provider name. Resolution failures (a low-confidence website, an unreadable
Figma file) fall back to the neutral baseline `DesignSystem` rather than raising.
"""
from __future__ import annotations

import time

from app.design_agent.design_system.extractors import RawSignals, registry
from app.design_agent.design_system.models import (
    Colors,
    DesignSystem,
    Fonts,
    Tokens,
)

# Fonts we are willing to name in the type stack. Mirrors the runner's
# pre-seed allow-list so a font that survives extraction also survives rendering.
_KNOWN_WEB_FONTS = {
    "Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins",
    "Source Sans Pro", "Nunito", "Raleway", "Playfair Display",
    "Merriweather", "PT Sans", "Ubuntu", "DM Sans", "Plus Jakarta Sans",
}


def _luminance(hex_color: str) -> float:
    """Perceptual luminance of a #rrggbb string (same weights the Figma walk uses)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _is_hex(value: str | None) -> bool:
    """True for a literal #rrggbb string — the only color form we map into tokens."""
    return bool(value) and isinstance(value, str) and value.startswith("#") and len(value) == 7


# ─── Figma ────────────────────────────────────────────────────────────────


class FigmaExtractor:
    """Adapter for a connected Figma file.

    `extract_raw_signals` wraps the existing `_extract_palette_summary` walk over
    a fetched Figma document; `normalize` folds its background / accent / swatches
    / typography into `DesignSystem` tokens. The source reference is the Figma
    file key.
    """

    category = "design_tool"
    provider = "figma"

    def current_version(self, ref: str) -> str | None:
        """Return a cheap staleness marker for a Figma file without fetching nodes."""
        file_key = (ref or "").strip()
        access_token = (
            getattr(self, "figma_access_token", None)
            or getattr(self, "access_token", None)
        )
        if not file_key or not access_token:
            return None

        try:
            from app.connectors import figma_oauth

            resp = figma_oauth.requests.get(
                f"{figma_oauth.FIGMA_API_BASE}/files/{file_key}/meta",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if not resp.ok:
                return None
            payload = resp.json() or {}
        except Exception:
            return None

        # The /meta endpoint nests its fields under a top-level "file" object and
        # names the timestamp "last_touched_at"; the full-file endpoint instead
        # exposes "lastModified" at the top level. The version id also changes on
        # every edit, so any of these works as a staleness marker — return the
        # first present, checking both the top level and the nested "file" object.
        sources = [payload]
        file_meta = payload.get("file")
        if isinstance(file_meta, dict):
            sources.append(file_meta)
        for source in sources:
            for key in ("last_touched_at", "lastModified", "last_modified", "version"):
                marker = source.get(key)
                if isinstance(marker, str) and marker:
                    return marker
        return None

    def extract_raw_signals(self, ref: str, file_doc: dict | None = None) -> RawSignals:
        """Capture the dominant palette + typography from an already-fetched
        Figma document into a `RawSignals` bag.

        The document is fetched by the caller (it owns the access token and the
        page-depth budget) and passed in as `file_doc`. We reuse the existing
        `_extract_palette_summary` rather than re-walking the tree.
        """
        from app.design_agent.tools import _extract_palette_summary

        summary = _extract_palette_summary(file_doc or {}) or {}
        return RawSignals(provider=self.provider, ref=ref, signals=summary)

    def normalize(self, raw: RawSignals) -> DesignSystem:
        """Fold a Figma palette summary into the common `DesignSystem` shape."""
        s = raw.signals or {}
        background = s.get("background")
        accent = s.get("accent")
        is_dark = bool(s.get("is_dark"))

        if not _is_hex(background):
            # No usable palette — neutral baseline, low confidence.
            return DesignSystem()

        foreground = "#f4f1ea" if is_dark else "#1a1a1a"
        primary = accent if _is_hex(accent) else background
        # Surface / muted mirror the runner's swatch heuristic so the rendered
        # CSS stays identical to the long-standing Figma pre-seed. Note we use the
        # ORIGINAL (un-filtered) swatch ordering for surface/muted indexing so the
        # second/third swatch lands exactly where the legacy renderer put it.
        raw_swatches = s.get("swatches") or []
        surface = raw_swatches[1] if len(raw_swatches) > 1 else background
        muted = raw_swatches[2] if len(raw_swatches) > 2 else surface

        font_family = s.get("font_family")
        weights = [int(w) for w in (s.get("font_weights") or []) if isinstance(w, (int, float))]
        fonts = Fonts()
        if font_family:
            fonts = Fonts(
                heading_family=font_family,
                body_family=font_family,
                weights=weights or Fonts().weights,
            )

        colors = Colors(
            background=background,
            foreground=foreground,
            surface=surface if _is_hex(surface) else background,
            primary=primary,
            accent=primary,
            muted=muted if _is_hex(muted) else (surface if _is_hex(surface) else background),
            border=Colors().border,
        )
        # Figma signals here are inferred from fills and typography, not from a
        # documented design system. A real palette plus typography is a richer
        # signal than a palette alone.
        confidence = "high" if (font_family and accent) else "medium"
        return DesignSystem(
            tokens=Tokens(colors=colors, is_dark=is_dark, fonts=fonts),
            has_explicit_system=False,
            confidence=confidence,
        )


# ─── Website ──────────────────────────────────────────────────────────────


def _css_color_to_hex(value: str | None) -> str | None:
    """Best-effort conversion of a sampled CSS color to #rrggbb.

    Accepts an existing hex string, or an opaque ``rgb()`` / ``rgba(..., 1)``.
    Returns None for transparent, zero-alpha, or unparseable values so the caller
    falls back to a token default rather than emitting a broken color.
    """
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("#") and len(v) == 7:
        return v
    if v.startswith(("rgb(", "rgba(")) and ")" in v:
        inner = v[v.index("(") + 1 : v.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 3:
            try:
                if len(parts) == 4 and float(parts[3]) == 0.0:
                    return None  # transparent
                r, g, b = (int(round(float(parts[i]))) for i in range(3))
            except ValueError:
                return None
            return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
    return None


def _radius_convention(border_radius: str | None) -> str:
    """Map a sampled button border-radius to the token radius convention."""
    if not border_radius:
        return "rounded"
    v = border_radius.strip().lower()
    if v in ("0", "0px", "0%"):
        return "sharp"
    if v.endswith("%") or v in ("9999px",):
        return "pill"
    try:
        px = float(v.replace("px", ""))
    except ValueError:
        return "rounded"
    if px <= 0:
        return "sharp"
    if px >= 999:
        return "pill"
    return "rounded"


def _spacing_samples_to_scale(samples: list[str] | None) -> list[int]:
    """Pull integer pixel values out of sampled padding strings, sorted + deduped.

    Falls back to the default spacing scale when nothing parseable is sampled.
    """
    out: set[int] = set()
    for sample in samples or []:
        for token in str(sample).replace("px", " ").split():
            try:
                px = int(round(float(token)))
            except ValueError:
                continue
            if px > 0:
                out.add(px)
    return sorted(out) if out else list(Tokens().spacing_scale)


class WebExtractor:
    """Adapter for a live brand website.

    `extract_raw_signals` wraps the existing headless-browser `WebsiteDesignSystem`
    sampler; `normalize` folds its primary / background colors, heading / body
    fonts, radius, and spacing into `DesignSystem` tokens. The source reference is
    the normalized website URL. A low-confidence sample (the sampler's `None`
    sentinel) normalizes to the neutral baseline `DesignSystem`.
    """

    category = "website"
    provider = "web"

    def current_version(self, ref: str) -> str | None:
        """Return a cheap staleness marker for a website without rendering it."""
        url = (ref or "").strip()
        if not url:
            return None

        try:
            from app.connectors import figma_oauth

            resp = figma_oauth.requests.head(
                url,
                timeout=10,
                allow_redirects=True,
            )
            if not resp.ok:
                return None
            headers = getattr(resp, "headers", {}) or {}

            for header_name in ("ETag", "Last-Modified"):
                marker = headers.get(header_name)
                if isinstance(marker, str) and marker:
                    return marker
                if hasattr(headers, "items"):
                    for key, value in headers.items():
                        if (
                            str(key).lower() == header_name.lower()
                            and isinstance(value, str)
                            and value
                        ):
                            return value
        except Exception:
            return None

        return f"ttl-{int(time.time() // (30 * 86400))}"

    def extract_raw_signals(self, ref: str, sample: dict | None = None) -> RawSignals:
        """Capture a website's sampled design system into a `RawSignals` bag.

        The sample is produced by the caller (it owns the headless-browser run,
        which is async and best-effort). A `None` sample — the sampler's
        low-confidence / failure sentinel — is preserved as an empty bag so
        `normalize` returns the neutral baseline.
        """
        return RawSignals(provider=self.provider, ref=ref, signals=dict(sample or {}))

    def normalize(self, raw: RawSignals) -> DesignSystem:
        """Fold a website sample into the common `DesignSystem` shape.

        An empty bag (the low-confidence / failure case) yields the neutral
        baseline so callers always get a complete object.
        """
        s = raw.signals or {}
        if not s:
            return DesignSystem()

        primary = _css_color_to_hex(s.get("primary_color"))
        background = _css_color_to_hex(s.get("background_color"))
        is_dark = bool(background and _luminance(background) < 128)

        colors = Colors()
        if background:
            colors.background = background
            colors.foreground = "#f4f1ea" if is_dark else "#1a1a1a"
        if primary:
            colors.primary = primary
            colors.accent = primary

        heading = (s.get("heading_font_family") or "").strip()
        body = (s.get("body_font_family") or "").strip()
        fonts = Fonts()
        if heading:
            fonts.heading_family = heading
        if body:
            fonts.body_family = body

        tokens = Tokens(
            colors=colors,
            is_dark=is_dark,
            fonts=fonts,
            radius_convention=_radius_convention(s.get("border_radius_convention")),
            spacing_scale=_spacing_samples_to_scale(s.get("spacing_scale_samples")),
        )
        # Website signals are inferred from sampled computed styles, not from a
        # documented design system. A usable brand color plus a heading font is
        # the sampler's own confidence floor; meeting it here too keeps the
        # signal honest.
        has_system = bool(primary and heading)
        return DesignSystem(
            tokens=tokens,
            has_explicit_system=False,
            confidence="medium" if has_system else "low",
        )


# Register both adapters on import so the package's import side-effect populates
# the shared registry (mirrors the contract documented in extractors.py).
_FIGMA = FigmaExtractor()
_WEB = WebExtractor()
registry.register(_FIGMA)
registry.register(_WEB)

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

import base64
import json
import re
import time
from urllib.parse import quote

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

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6})\b")
_JS_HEX_PAIR_RE = re.compile(
    r"['\"]?([A-Za-z][A-Za-z0-9_-]*)['\"]?\s*:\s*['\"](#[0-9a-fA-F]{6})['\"]"
)
_CSS_VAR_RE = re.compile(r"--([A-Za-z0-9_-]+)\s*:\s*([^;{}]+);")
_FONT_DECL_RE = re.compile(r"font-family\s*:\s*([^;{}]+);", re.IGNORECASE)
_FONT_TOKEN_RE = re.compile(
    r"['\"]?([A-Za-z][A-Za-z0-9_-]*)['\"]?\s*:\s*(?:\[)?['\"]([^'\"\]]+)['\"]"
)
_SIZE_PAIR_RE = re.compile(
    r"['\"]?([A-Za-z0-9][A-Za-z0-9_-]*)['\"]?\s*:\s*['\"]([0-9.]+(?:px|rem))['\"]"
)
_SHADOW_PAIR_RE = re.compile(
    r"['\"]?([A-Za-z][A-Za-z0-9_-]*)['\"]?\s*:\s*['\"]([^'\"]*(?:rgba?\(|#[0-9a-fA-F]{3,6})[^'\"]*)['\"]"
)

_GITHUB_DESIGN_FILES = (
    "tailwind.config.ts",
    "tailwind.config.js",
    "tailwind.config.mjs",
    "tailwind.config.cjs",
    "components.json",
    "tokens.json",
    "style-dictionary.json",
    "app/globals.css",
    "src/index.css",
    "src/globals.css",
    "styles/globals.css",
    "package.json",
)

_COMPONENT_HINTS = (
    "accordion", "alert", "avatar", "badge", "button", "card", "checkbox",
    "dialog", "drawer", "dropdown", "form", "input", "menu", "modal",
    "popover", "select", "sheet", "table", "tabs", "textarea", "toast",
    "tooltip",
)


def _luminance(hex_color: str) -> float:
    """Perceptual luminance of a #rrggbb string (same weights the Figma walk uses)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _is_hex(value: str | None) -> bool:
    """True for a literal #rrggbb string — the only color form we map into tokens."""
    return bool(value) and isinstance(value, str) and value.startswith("#") and len(value) == 7


def _normalize_hex(value: str | None) -> str | None:
    """Return a lower-case #rrggbb token, or None for unsupported color forms."""
    if not value:
        return None
    v = value.strip()
    if _HEX_RE.fullmatch(v):
        return v.lower()
    return None


def _repo_ref_parts(ref: str) -> tuple[str, str | None]:
    """Split ``owner/repo`` or ``owner/repo@branch`` into API repo + branch."""
    cleaned = (ref or "").strip()
    if "@" not in cleaned:
        return cleaned, None
    repo, branch = cleaned.rsplit("@", 1)
    return repo.strip(), branch.strip() or None


def _parse_px_or_rem(value: str | None) -> int | None:
    if not value:
        return None
    v = value.strip().lower()
    try:
        if v.endswith("px"):
            return int(round(float(v[:-2])))
        if v.endswith("rem"):
            return int(round(float(v[:-3]) * 16))
    except ValueError:
        return None
    return None


def _first_known_font(values: list[str]) -> str | None:
    for raw in values:
        for part in str(raw).split(","):
            font = part.strip().strip("'\"")
            if font in _KNOWN_WEB_FONTS:
                return font
    for raw in values:
        font = str(raw).split(",", 1)[0].strip().strip("'\"")
        if font:
            return font
    return None


def _walk_json(value):
    """Yield all nested dict/list/scalar values from a decoded JSON object."""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


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


# ─── GitHub/codebase ────────────────────────────────────────────────────────


class GithubExtractor:
    """Adapter for explicit design-system files in a GitHub repository.

    B1 is deterministic-only: read a fixed, bounded list of likely token/config
    files through the GitHub App installation token and parse documented tokens.
    Missing files, unreadable files, and API failures return an empty signal bag
    so generation degrades to the neutral baseline.
    """

    category = "codebase"
    provider = "github"

    def __init__(self, installation_id: int | None = None) -> None:
        self.installation_id = installation_id

    def current_version(self, ref: str) -> str | None:
        if not self.installation_id:
            return None
        repo_full_name, branch = _repo_ref_parts(ref)
        if not repo_full_name or "/" not in repo_full_name:
            return None
        try:
            from app.connectors import github_app

            repo_resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{repo_full_name}",
                headers=github_app.headers_for_installation(self.installation_id),
                timeout=15,
            )
            if not repo_resp.ok:
                return None
            repo_payload = repo_resp.json() or {}
            branch_name = branch or repo_payload.get("default_branch")
            if not isinstance(branch_name, str) or not branch_name:
                pushed = repo_payload.get("pushed_at")
                return pushed if isinstance(pushed, str) and pushed else None
            quoted_branch = quote(branch_name, safe="")
            commit_resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{repo_full_name}/commits/{quoted_branch}",
                headers=github_app.headers_for_installation(self.installation_id),
                timeout=15,
            )
            if commit_resp.ok:
                sha = (commit_resp.json() or {}).get("sha")
                if isinstance(sha, str) and sha:
                    return sha
            pushed = repo_payload.get("pushed_at")
            return pushed if isinstance(pushed, str) and pushed else None
        except Exception:
            return None

    def _fetch_text_file(self, repo_full_name: str, path: str, branch: str | None) -> str | None:
        if not self.installation_id:
            return None
        try:
            from app.connectors import github_app

            params = {"ref": branch} if branch else None
            quoted_path = quote(path, safe="/")
            resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{repo_full_name}/contents/{quoted_path}",
                headers=github_app.headers_for_installation(self.installation_id),
                params=params,
                timeout=15,
            )
            if resp.status_code == 404 or not resp.ok:
                return None
            payload = resp.json() or {}
            if isinstance(payload, list):
                return None
            if int(payload.get("size") or 0) > 128_000:
                return None
            content = payload.get("content")
            if payload.get("encoding") != "base64" or not isinstance(content, str):
                return None
            return base64.b64decode(content).decode("utf-8", errors="ignore")
        except Exception:
            return None

    def extract_raw_signals(self, ref: str) -> RawSignals:
        repo_full_name, branch = _repo_ref_parts(ref)
        if not repo_full_name or "/" not in repo_full_name:
            return RawSignals(provider=self.provider, ref=ref, signals={})

        signals: dict = {
            "files_present": [],
            "colors": {},
            "fonts": [],
            "spacing": [],
            "radius": None,
            "shadows": [],
            "components": [],
        }
        components: set[str] = set()
        spacing: set[int] = set()
        shadows: list[str] = []

        for path in _GITHUB_DESIGN_FILES:
            text = self._fetch_text_file(repo_full_name, path, branch)
            if not text:
                continue
            signals["files_present"].append(path)
            lowered_path = path.lower()
            if lowered_path.endswith(".json"):
                self._collect_json_signals(text, signals, components, spacing, shadows)
            if lowered_path.endswith((".css", ".js", ".ts", ".mjs", ".cjs")):
                self._collect_text_signals(text, signals, components, spacing, shadows)

        signals["spacing"] = sorted(spacing)
        signals["shadows"] = shadows[:8]
        signals["components"] = sorted(components)
        return RawSignals(provider=self.provider, ref=ref, signals=signals)

    def _collect_json_signals(
        self,
        text: str,
        signals: dict,
        components: set[str],
        spacing: set[int],
        shadows: list[str],
    ) -> None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return

        for node in _walk_json(data):
            if isinstance(node, dict):
                for key, value in node.items():
                    if (
                        isinstance(value, dict)
                        and isinstance(value.get("value"), str)
                    ):
                        self._collect_named_value(
                            str(key), value["value"], signals, spacing, shadows
                        )
                    self._collect_named_value(str(key), value, signals, spacing, shadows)
            elif isinstance(node, str):
                self._collect_component_hints(node, components)
        if isinstance(data, dict):
            for key in ("components", "aliases"):
                section = data.get(key)
                if isinstance(section, dict):
                    for name in section:
                        self._collect_component_hints(str(name), components)

    def _collect_text_signals(
        self,
        text: str,
        signals: dict,
        components: set[str],
        spacing: set[int],
        shadows: list[str],
    ) -> None:
        for name, value in _JS_HEX_PAIR_RE.findall(text):
            signals["colors"].setdefault(name.lower(), value.lower())

        for var_name, raw_value in _CSS_VAR_RE.findall(text):
            key = var_name.lower()
            value = raw_value.strip()
            color = _normalize_hex(value)
            if color:
                signals["colors"].setdefault(key, color)
                continue
            size = _parse_px_or_rem(value)
            if size is not None:
                if "radius" in key:
                    signals["radius"] = value
                elif any(k in key for k in ("space", "spacing", "gap")):
                    spacing.add(size)

        for value in _FONT_DECL_RE.findall(text):
            if value:
                signals["fonts"].append(value.strip())

        for name, value in _FONT_TOKEN_RE.findall(text):
            if "font" in name.lower() or name.lower() in {"sans", "heading", "body"}:
                signals["fonts"].append(value.strip())

        for name, value in _SIZE_PAIR_RE.findall(text):
            lower = name.lower()
            px = _parse_px_or_rem(value)
            if px is None:
                continue
            if "radius" in lower or lower in {"sm", "md", "lg", "xl", "full"}:
                signals["radius"] = value
            if "space" in lower or "spacing" in lower or lower.isdigit():
                spacing.add(px)

        for name, value in _SHADOW_PAIR_RE.findall(text):
            if "shadow" in name.lower() and value not in shadows:
                shadows.append(value)

        self._collect_component_hints(text, components)

    def _collect_named_value(
        self,
        key: str,
        value,
        signals: dict,
        spacing: set[int],
        shadows: list[str],
    ) -> None:
        lower = key.lower()
        if isinstance(value, str):
            color = _normalize_hex(value)
            if color:
                signals["colors"].setdefault(lower, color)
                return
            px = _parse_px_or_rem(value)
            if px is not None:
                if "radius" in lower:
                    signals["radius"] = value
                elif "space" in lower or "spacing" in lower or lower.isdigit():
                    spacing.add(px)
            if "font" in lower:
                signals["fonts"].append(value)
            if "shadow" in lower and value not in shadows:
                shadows.append(value)
        elif isinstance(value, list) and (
            "font" in lower or lower in {"sans", "heading", "body"}
        ):
            for item in value:
                if isinstance(item, str):
                    signals["fonts"].append(item)

    def _collect_component_hints(self, text: str, components: set[str]) -> None:
        haystack = text.lower()
        for name in _COMPONENT_HINTS:
            if re.search(rf"\b{name}\b", haystack):
                components.add(name)

    def normalize(self, raw: RawSignals) -> DesignSystem:
        s = raw.signals or {}
        if not s or not s.get("files_present"):
            return DesignSystem()

        color_map = {
            str(k).lower(): v
            for k, v in (s.get("colors") or {}).items()
            if _normalize_hex(str(v))
        }

        def color(*names: str) -> str | None:
            for name in names:
                if name in color_map:
                    return color_map[name]
            for key, value in color_map.items():
                if any(name in key for name in names):
                    return value
            return None

        background = color("background", "bg")
        foreground = color("foreground", "text", "content")
        primary = color("primary", "brand", "accent")
        surface = color("surface", "card", "popover", "secondary")
        muted = color("muted", "neutral", "gray", "slate")
        border = color("border", "ring", "stroke")

        colors = Colors()
        if background:
            colors.background = background
        if foreground:
            colors.foreground = foreground
        elif background:
            colors.foreground = "#f4f1ea" if _luminance(background) < 128 else "#1a1a1a"
        if primary:
            colors.primary = primary
            colors.accent = primary
        if surface:
            colors.surface = surface
        if muted:
            colors.muted = muted
        if border:
            colors.border = border

        font = _first_known_font(s.get("fonts") or [])
        fonts = Fonts()
        if font:
            fonts.heading_family = font
            fonts.body_family = font

        spacing_scale = s.get("spacing") or list(Tokens().spacing_scale)
        radius = _radius_convention(str(s.get("radius") or ""))
        is_dark = bool(background and _luminance(background) < 128)
        has_tokens = bool(color_map or font or s.get("spacing") or s.get("radius") or s.get("shadows"))

        tokens = Tokens(
            colors=colors,
            is_dark=is_dark,
            fonts=fonts,
            spacing_scale=spacing_scale,
            radius_convention=radius,
            elevation_style="shadows" if s.get("shadows") else Tokens().elevation_style,
        )
        return DesignSystem(
            tokens=tokens,
            component_inventory=list(s.get("components") or []),
            has_explicit_system=has_tokens,
            confidence="high" if color_map and font else ("medium" if has_tokens else "low"),
        )


# Register both adapters on import so the package's import side-effect populates
# the shared registry (mirrors the contract documented in extractors.py).
_FIGMA = FigmaExtractor()
_WEB = WebExtractor()
_GITHUB = GithubExtractor()
registry.register(_FIGMA)
registry.register(_WEB)
registry.register(_GITHUB)

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

_GITHUB_UI_DIRS = (
    "components/ui",
    "src/components/ui",
    "app/components/ui",
    "components",
    "src/components",
    "app/components",
)
_GITHUB_MAX_DIRS = 6
_GITHUB_MAX_UI_FILES = 12
_GITHUB_MAX_UI_FILE_BYTES = 96_000
_GITHUB_EXPLICIT_FILE_BYTES = 128_000

# Monorepo frontend-subdir probe order. The gather only knows how to parse
# root-relative paths (e.g. "app/globals.css"), but monorepos keep the frontend
# under a subdir. We detect the winning subdir by probing "<prefix>package.json"
# in this order and pick the FIRST that exists. "" (repo root) stays first so a
# plain single-package repo behaves exactly as before. The detected prefix is
# prepended to every fetch path, then STRIPPED back off when keying the fetched
# dict / detection-path list so github_gather.py keeps seeing root-relative keys
# and needs no change.
_GITHUB_FRONTEND_PREFIXES = ("", "web/", "apps/web/", "frontend/", "client/")



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
    repo, branch = cleaned.split("@", 1)
    return repo.strip(), branch.strip() or None


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

    `extract_raw_signals` captures the rich gather signals from an
    already-fetched Figma document into a `RawSignals` bag. `normalize` folds
    those gather keys into a `DesignSignals` object and returns `harden(signals)`
    — no inline accent / neutral / confidence decision remains here. The source
    reference is the Figma file key.
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
        """Capture rich gather signals from an already-fetched Figma document.

        The document is fetched by the caller (it owns the access token and the
        page-depth budget) and passed in as ``file_doc``.

        The returned ``signals`` dict carries the rich gather keys from
        ``gather_figma_signals``:
            ``theme_background, theme_is_dark, foreground, color_candidates,
            neutral_candidates, container_observations, observed_component_types,
            heading_font_family, body_font_family, font_weights_observed,
            radius_convention, spacing_px, explicit_color_styles,
            explicit_text_styles``

        The legacy palette-summary keys (``background``, ``accent``, ``is_dark``,
        ``swatches``, ``font_family``, ``font_weights``) were removed here because
        ``normalize`` now reads the gather keys through the shared kernel. The
        duplicate accent/palette heuristic still lives in ``tools.py`` for the
        in-loop Figma fetch-tool payload — it is not deleted, just no longer
        consumed by design-system extraction.
        """
        from app.design_agent.design_system.figma_gather import gather_figma_signals

        doc = file_doc or {}

        # Obtain the optional Variables document only when we have an access token.
        variables_doc: dict | None = None
        access_token = (
            getattr(self, "figma_access_token", None)
            or getattr(self, "access_token", None)
        )
        if access_token and ref:
            try:
                from app.connectors.figma_oauth import fetch_file_variables
                variables_doc = fetch_file_variables(access_token, ref)
            except Exception:
                variables_doc = None

        signals = gather_figma_signals(doc, variables_doc=variables_doc)
        return RawSignals(provider=self.provider, ref=ref, signals=signals)

    def normalize(self, raw: RawSignals) -> DesignSystem:
        """Fold Figma gather signals into the common DesignSystem shape via the shared kernel.

        Constructs a DesignSignals object from the gather keys in raw.signals and
        returns harden(signals) directly. No inline accent / neutral / elevation /
        inventory / confidence decision is made here; all heuristics live in the
        kernel (hardening.py). Nothing is assigned on the returned DesignSystem
        after harden — harden is the sole assembler.

        An empty gather bag (the unusable-doc sentinel) returns the neutral
        baseline DesignSystem so callers always receive a complete object.
        """
        from app.design_agent.design_system.hardening import harden, pick_accent, _saturation_of
        from app.design_agent.design_system.signals import (
            ColorCandidate,
            ContainerObservation,
            DesignSignals,
            FieldFlags,
            NeutralCandidate,
            TypographySignals,
        )

        s = raw.signals or {}
        if not s:
            return DesignSystem()

        # Color candidates: saturation computed via the kernel's HSL formula
        # (_saturation_of in hardening.py). Never use tools.py:_saturation — that
        # function uses a different (max-min)/max formula and must not be used for
        # accent selection. Forgetting saturation leaves it at the 0.0 default and
        # causes pick_accent to degrade to weight-only, ignoring chromatic-ness.
        candidates: list[ColorCandidate] = [
            ColorCandidate(
                hex=c["hex"],
                weight=float(c.get("weight") or 0.0),
                saturation=_saturation_of(c["hex"]),
            )
            for c in (s.get("color_candidates") or [])
            if c.get("hex")
        ]

        neutral_list: list[NeutralCandidate] = [
            NeutralCandidate(
                role=n["role"],
                hex=n["hex"],
                weight=float(n.get("weight") or 0.0),
            )
            for n in (s.get("neutral_candidates") or [])
            if n.get("role") in ("surface", "border", "muted") and n.get("hex")
        ]

        container_list: list[ContainerObservation] = [
            ContainerObservation(
                has_border=bool(o.get("has_border")),
                has_shadow=bool(o.get("has_shadow")),
            )
            for o in (s.get("container_observations") or [])
        ]

        observed_types = [str(t) for t in (s.get("observed_component_types") or [])]

        heading = (s.get("heading_font_family") or "").strip()
        body = (s.get("body_font_family") or "").strip()
        weights = [
            int(w) for w in (s.get("font_weights_observed") or [])
            if isinstance(w, (int, float))
        ]
        radius_conv = (s.get("radius_convention") or "").strip()
        typography = TypographySignals(
            heading_family=heading,
            body_family=body,
            weights=weights,
            radius_convention=radius_conv,
        )

        # Non-heuristic pass-throughs. Background and foreground map straight;
        # the kernel's harden() handles absent values via NON-assignment.
        background_hex = s.get("theme_background") or ""
        is_dark = bool(s.get("theme_is_dark"))

        # Foreground rule: use the gathered dominant text-node fill when present;
        # else derive from the background theme when present; else absent ("").
        raw_foreground = s.get("foreground")
        if raw_foreground:
            foreground_hex = raw_foreground
        elif background_hex:
            foreground_hex = "#f4f1ea" if is_dark else "#1a1a1a"
        else:
            foreground_hex = ""

        spacing_scale = [
            int(p) for p in (s.get("spacing_px") or [])
            if isinstance(p, (int, float)) and int(p) > 0
        ]

        # Honest provenance flags drive score_confidence.
        # explicit.accent and explicit.neutrals both require explicit_color_styles
        # AND the corresponding gathered list to be non-empty — if published colour
        # styles resolve but none route to neutral roles, explicit.neutrals is False.
        # explicit.typography requires published text styles (explicit_text_styles).
        # gathered.* reflect what the kernel can actually pick (non-empty lists).
        # Every explicit.X=True also implies gathered.X=True.
        has_explicit_colors = bool(s.get("explicit_color_styles"))
        has_explicit_text = bool(s.get("explicit_text_styles"))

        explicit = FieldFlags(
            accent=has_explicit_colors and bool(candidates),
            neutrals=has_explicit_colors and bool(neutral_list),
            typography=has_explicit_text,
            elevation=False,   # no explicit elevation source in the gather layer
            inventory=False,   # component inventory is always inferred, not explicit
        )
        gathered = FieldFlags(
            accent=pick_accent(candidates) is not None,
            typography=bool(heading),
            neutrals=bool(neutral_list),
            elevation=bool(container_list),
            inventory=bool(observed_types),
        )
        # If a flag is explicit it must also be gathered.
        if explicit.accent:
            gathered.accent = True
        if explicit.neutrals:
            gathered.neutrals = True
        if explicit.typography:
            gathered.typography = True

        signals = DesignSignals(
            color_candidates=candidates,
            neutral_candidates=neutral_list,
            container_observations=container_list,
            observed_component_types=observed_types,
            typography=typography,
            is_dark=is_dark,
            background_hex=background_hex,
            foreground_hex=foreground_hex,
            spacing_scale=spacing_scale,
            gathered=gathered,
            explicit=explicit,
            provider="figma",
        )
        return harden(signals)  # sole assembler — no field assigned on the result after this


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
            from app.net_guard import UnsafeURLError, assert_public_url

            # SSRF guard: validate the user-supplied website URL before the
            # HEAD leaves the process, and re-validate every redirect hop
            # by hand (auto-redirect disabled) so a redirect to an internal
            # host is refused. An unsafe URL floors to None like any failure.
            try:
                current = url
                resp = None
                for _ in range(6):  # initial request + up to 5 redirects
                    assert_public_url(current)
                    resp = figma_oauth.requests.head(
                        current,
                        timeout=10,
                        allow_redirects=False,
                    )
                    location = getattr(resp, "headers", {}).get("Location") or \
                        getattr(resp, "headers", {}).get("location")
                    if getattr(resp, "is_redirect", False) and location:
                        from urllib.parse import urljoin

                        current = urljoin(current, location)
                        continue
                    break
            except UnsafeURLError:
                return None
            if resp is None or not resp.ok:
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

        The sampler is a dumb emitter of candidate lists; every decision lives in
        the shared kernel. This method GATHERS the raw candidates into a
        `DesignSignals` bag and returns `harden(signals)` — no inline accent /
        neutral / elevation / inventory / confidence decision is made here, and
        nothing is post-decorated onto the DesignSystem after harden.

        An empty bag (the low-confidence / failure case) yields the neutral
        baseline so callers always get a complete object.
        """
        from app.design_agent.design_system.hardening import harden, pick_accent
        from app.design_agent.design_system.signals import (
            ColorCandidate,
            ContainerObservation,
            DesignSignals,
            FieldFlags,
            NeutralCandidate,
            TypographySignals,
        )

        s = raw.signals or {}
        if not s:
            return DesignSystem()

        background = _css_color_to_hex(s.get("background_color"))
        is_dark = bool(background and _luminance(background) < 128)

        # Chromatic candidates: convert each raw colour; drop non-convertible.
        chromatic_list: list[ColorCandidate] = []
        for c in s.get("color_candidates") or []:
            hx = _css_color_to_hex(c.get("color"))
            if hx:
                chromatic_list.append(
                    ColorCandidate(
                        hex=hx,
                        weight=float(c.get("area") or 0.0),
                        saturation=float(c.get("saturation") or 0.0),
                    )
                )

        # Neutral candidates: convert each raw colour; keep only valid roles.
        neutral_list: list[NeutralCandidate] = []
        for n in s.get("neutral_candidates") or []:
            hx = _css_color_to_hex(n.get("color"))
            if hx and n.get("role") in ("surface", "border", "muted"):
                neutral_list.append(
                    NeutralCandidate(
                        role=n["role"],
                        hex=hx,
                        weight=float(n.get("area") or 0.0),
                    )
                )

        # Container observations for elevation derivation.
        container_list = [
            ContainerObservation(
                has_border=bool(o.get("has_border")),
                has_shadow=bool(o.get("has_shadow")),
            )
            for o in (s.get("container_observations") or [])
        ]

        heading = (s.get("heading_font_family") or "").strip()
        typography = TypographySignals(
            heading_family=heading,
            body_family=(s.get("body_font_family") or "").strip(),
            radius_convention=_radius_convention(s.get("border_radius_convention")),
        )

        observed_types = [str(t) for t in (s.get("observed_component_types") or [])]

        gathered = FieldFlags(
            accent=pick_accent(chromatic_list) is not None,
            typography=bool(heading),
            neutrals=bool(neutral_list),
            elevation=bool(container_list),
            inventory=bool(observed_types),
        )

        signals = DesignSignals(
            color_candidates=chromatic_list,
            neutral_candidates=neutral_list,
            container_observations=container_list,
            observed_component_types=observed_types,
            typography=typography,
            is_dark=is_dark,
            background_hex=background or "",
            # Non-heuristic foreground pass-through (today's exact rule).
            foreground_hex=("#f4f1ea" if is_dark else "#1a1a1a") if background else "",
            spacing_scale=_spacing_samples_to_scale(s.get("spacing_scale_samples")),
            gathered=gathered,
            explicit=FieldFlags(),
            provider="web",
        )
        return harden(signals)


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

            quoted_repo = quote(repo_full_name, safe="/")
            repo_resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{quoted_repo}",
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
                f"{github_app.GITHUB_API_BASE}/repos/{quoted_repo}/commits/{quoted_branch}",
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

    def _github_get_contents(self, repo_full_name: str, path: str, branch: str | None):
        if not self.installation_id:
            return None
        try:
            from app.connectors import github_app

            params = {"ref": branch} if branch else None
            quoted_path = quote(path, safe="/")
            quoted_repo = quote(repo_full_name, safe="/")
            resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{quoted_repo}/contents/{quoted_path}",
                headers=github_app.headers_for_installation(self.installation_id),
                params=params,
                timeout=15,
            )
            if resp.status_code == 404 or not resp.ok:
                return None
            return resp.json()
        except Exception:
            return None

    def _fetch_text_file(
        self,
        repo_full_name: str,
        path: str,
        branch: str | None,
        *,
        max_bytes: int = _GITHUB_EXPLICIT_FILE_BYTES,
        truncate: bool = False,
    ) -> str | None:
        payload = self._github_get_contents(repo_full_name, path, branch) or {}
        if isinstance(payload, list):
            return None
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            return None
        content = payload.get("content")
        if payload.get("encoding") != "base64" or not isinstance(content, str):
            return None
        # Oversize handling: UI component files (truncate=False) still DROP — a
        # half-read .tsx is useless. Design/CSS files (truncate=True) TRUNCATE to
        # max_bytes instead: a large globals.css (e.g. ~300KB) carries its `:root`
        # design tokens at the very top, so the first max_bytes is enough for the
        # token gather, whereas dropping it silently lost the brand tokens for any
        # real-world stylesheet bigger than the cap.
        if size > max_bytes and not truncate:
            return None
        try:
            raw = base64.b64decode(content)
            if truncate and len(raw) > max_bytes:
                raw = raw[:max_bytes]
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def _detect_frontend_prefix(self, repo_full_name: str, branch: str | None) -> str:
        """Return the winning frontend-subdir prefix for this repo.

        Probes ``<prefix>package.json`` across ``_GITHUB_FRONTEND_PREFIXES`` (root
        first) and returns the FIRST prefix whose ``package.json`` exists. Falls
        back to ``""`` (repo root) when none is found, so non-monorepo repos behave
        exactly as before. Uses the lightweight contents-metadata call rather than a
        body fetch so prefix detection costs at most one HEAD-equivalent per prefix.
        """
        for prefix in _GITHUB_FRONTEND_PREFIXES:
            if not prefix:
                continue  # root is the implicit fallback; only confirm subdirs
            payload = self._github_get_contents(
                repo_full_name, f"{prefix}package.json", branch
            )
            if isinstance(payload, dict) and payload.get("type") == "file":
                return prefix
        return ""

    def _list_ui_files(
        self, repo_full_name: str, branch: str | None, prefix: str = ""
    ) -> list[tuple[str, str]]:
        """List candidate UI component files under the (prefixed) UI dirs.

        Probes ``<prefix><dir>`` for each ``_GITHUB_UI_DIRS`` entry but returns
        each file keyed by its ROOT-RELATIVE path (prefix stripped) so downstream
        gather strategies — which match on root-relative paths — keep working
        unchanged in a monorepo.
        """
        out: list[tuple[str, str]] = []
        for directory in _GITHUB_UI_DIRS:
            payload = self._github_get_contents(
                repo_full_name, f"{prefix}{directory}", branch
            )
            if not isinstance(payload, list):
                continue
            for item in payload:
                if len(out) >= _GITHUB_MAX_UI_FILES:
                    return out
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                path = str(item.get("path") or "")
                name = str(item.get("name") or path.rsplit("/", 1)[-1])
                if not path.endswith((".tsx", ".ts", ".jsx", ".js")):
                    continue
                if not self._is_likely_component_file(name):
                    continue
                # Strip the detected prefix so the returned (path, name) is
                # root-relative; the adapter fetches at <prefix><path> below.
                rel_path = path[len(prefix):] if prefix and path.startswith(prefix) else path
                out.append((rel_path, name))
        return out

    def _is_likely_component_file(self, name: str) -> bool:
        stem = name.rsplit(".", 1)[0].lower()
        return stem in _COMPONENT_HINTS or stem in {
            "index", "button", "card", "input", "label", "badge",
        }

    def extract_ui_primitives(self, ref: str) -> dict[str, str]:
        """Return existing component files from the repo's strict UI primitive directory.

        Looks only in `components/ui`, `src/components/ui`, and `app/components/ui`
        (the canonical shadcn/ui locations) — not broader component trees. Filters
        to files whose stem is a known component hint, caps at _GITHUB_MAX_UI_FILES,
        and skips files that exceed _GITHUB_MAX_UI_FILE_BYTES or fail to fetch.
        Returns an empty dict when installation_id is None.
        """
        if not self.installation_id:
            return {}
        repo_full_name, branch = _repo_ref_parts(ref)
        if not repo_full_name or "/" not in repo_full_name:
            return {}

        _STRICT_UI_DIRS = ("components/ui", "src/components/ui", "app/components/ui")
        out: dict[str, str] = {}

        for directory in _STRICT_UI_DIRS:
            payload = self._github_get_contents(repo_full_name, directory, branch)
            if not isinstance(payload, list):
                continue
            for item in payload:
                if len(out) >= _GITHUB_MAX_UI_FILES:
                    return out
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                path = str(item.get("path") or "")
                name = str(item.get("name") or path.rsplit("/", 1)[-1])
                if not path.endswith((".tsx", ".ts", ".jsx", ".js")):
                    continue
                stem = name.rsplit(".", 1)[0].lower()
                if stem not in _COMPONENT_HINTS:
                    continue
                text = self._fetch_text_file(
                    repo_full_name, path, branch, max_bytes=_GITHUB_MAX_UI_FILE_BYTES
                )
                if text is None:
                    continue
                out[f"src/components/ui/{name}"] = text
            if out:
                return out

        return out

    def extract_raw_signals(self, ref: str) -> RawSignals:
        """Gather design tokens from a GitHub repository via the styling-system sub-registry.

        Fetch strategy:
          1. Fetch ``package.json`` + the bounded design-file list to detect the styling system.
          2. Detect the stack via the sub-registry (deps + file paths only — no bodies read
             for strategies that don't match).
          3. Fetch ONLY the winning strategy's file bodies.
          4. Call the strategy's ``gather`` function and return the gather dict.

        The ``_collect_*`` parsing helpers live in ``github_gather.py``; this method owns
        all network I/O and passes already-fetched content into the pure gather module.
        """
        from app.design_agent.design_system.github_gather import (
            gather_github_signals,
            styling_registry,
            degrade_strategy,
        )

        repo_full_name, branch = _repo_ref_parts(ref)
        if not repo_full_name or "/" not in repo_full_name:
            return RawSignals(provider=self.provider, ref=ref, signals={})

        # ── Step 0: Detect the frontend subdir (monorepo-aware) ──
        # Monorepos keep the frontend (and its globals.css / tailwind config /
        # package.json) under a subdir like "web/". Detect it once; everything
        # below probes "<prefix><root-relative-path>" but keys the fetched dict
        # and the detection-path list by the ROOT-RELATIVE path so the gather
        # strategies (which match exact root-relative paths) need no change.
        prefix = self._detect_frontend_prefix(repo_full_name, branch)

        # ── Step 1: Fetch package.json and the bounded design-file listing ──
        # Read package.json first so we can extract deps for detection.
        # Then fetch each design-file path within the explicit-file byte cap.
        # We record which paths are present (regardless of whether their body
        # fits the cap) so the strategy detector can do path-glob checks.

        fetched_design: dict[str, str] = {}  # root-rel path -> text (already-fetched bodies)
        all_design_paths: list[str] = []     # root-rel paths present in the repo (for detection)
        deps: set[str] = set()

        for path in _GITHUB_DESIGN_FILES:
            text = self._fetch_text_file(repo_full_name, f"{prefix}{path}", branch,
                                         max_bytes=_GITHUB_EXPLICIT_FILE_BYTES,
                                         truncate=True)
            if text is None:
                continue
            all_design_paths.append(path)
            fetched_design[path] = text
            # Extract dependency names from package.json for stack detection.
            if path == "package.json":
                try:
                    pkg = json.loads(text)
                    for section in ("dependencies", "devDependencies", "peerDependencies"):
                        deps.update((pkg.get(section) or {}).keys())
                except (TypeError, ValueError):
                    pass

        # ── Step 2: Detect the styling system ──
        # Detection reads only deps + the list of present paths — no extra fetches.
        strategy = styling_registry.detect(deps, all_design_paths)
        if strategy is None:
            strategy = degrade_strategy

        # ── Step 3: Fetch the winning strategy's UI-file bodies ──
        # UI files (component source files) provide inferred signals regardless of
        # which strategy won.  The file listing is already bounded to _GITHUB_MAX_UI_FILES
        # inside _list_ui_files; we enforce the cap here as well so callers that
        # substitute a test double cannot accidentally exceed it.
        # _list_ui_files returns ROOT-RELATIVE paths (prefix already stripped); we
        # fetch each body at "<prefix><path>" but key it back by the root-relative
        # path so the gather strategies see unchanged keys.
        # Pass the prefix only when non-empty so the common (root) path keeps the
        # historical two-argument call shape that existing callers / test doubles expect.
        if prefix:
            ui_listing = self._list_ui_files(repo_full_name, branch, prefix)
        else:
            ui_listing = self._list_ui_files(repo_full_name, branch)
        ui_fetched = 0
        for path, _name in ui_listing:
            if ui_fetched >= _GITHUB_MAX_UI_FILES:
                break
            if path in fetched_design:
                continue  # already fetched above (counts against the cap only once)
            text = self._fetch_text_file(
                repo_full_name, f"{prefix}{path}", branch, max_bytes=_GITHUB_MAX_UI_FILE_BYTES
            )
            if text is not None:
                fetched_design[path] = text
                ui_fetched += 1

        # ── Step 4: Gather via the winning strategy ──
        signals = gather_github_signals(fetched_design, deps, all_design_paths, _COMPONENT_HINTS)
        return RawSignals(provider=self.provider, ref=ref, signals=signals)

    def normalize(self, raw: RawSignals) -> DesignSystem:
        """Fold GitHub gather signals into the common DesignSystem shape via the shared kernel.

        Constructs a DesignSignals object from the gather keys in raw.signals and
        returns harden(signals) directly. No inline accent / neutral / elevation /
        inventory / confidence decision is made here; all heuristics live in the
        kernel (hardening.py). Nothing is assigned on the returned DesignSystem
        after harden — harden is the sole assembler.

        An empty gather bag (no recognized design files) returns the neutral
        baseline DesignSystem so callers always receive a complete object.
        """
        from app.design_agent.design_system.hardening import harden, pick_accent
        from app.design_agent.design_system.signals import (
            ColorCandidate,
            DesignSignals,
            FieldFlags,
            NeutralCandidate,
            TypographySignals,
        )

        # Preserve the exact empty-bag predicate from the previous implementation.
        s = raw.signals or {}
        if not s or not (s.get("files_present") or s.get("inference_files")):
            return DesignSystem()

        # Build separate explicit and inferred color maps (lowercased keys, validated hex).
        # Explicit: tokens sourced from a real config file (tailwind.config, tokens.json, CSS vars).
        # Inferred: colours observed in className frequencies across UI source files.
        color_map = {
            str(k).lower(): _normalize_hex(str(v))
            for k, v in (s.get("colors") or {}).items()
            if _normalize_hex(str(v))
        }
        inferred_color_map = {
            str(k).lower(): _normalize_hex(str(v))
            for k, v in (s.get("inferred_colors") or {}).items()
            if _normalize_hex(str(v))
        }

        def _resolve(*names: str) -> tuple[str | None, bool]:
            """Return (hex, from_explicit) for the first matching name across both maps.

            Resolution order mirrors today's color() helper:
            exact explicit -> substring explicit -> exact inferred -> substring inferred.
            The from_explicit flag drives weight and provenance flags downstream.
            """
            for name in names:
                if name in color_map:
                    return color_map[name], True
            for key, value in color_map.items():
                if any(name in key for name in names):
                    return value, True
            for name in names:
                if name in inferred_color_map:
                    return inferred_color_map[name], False
            for key, value in inferred_color_map.items():
                if any(name in key for name in names):
                    return value, False
            return None, False

        # Route each semantic role to its seam slot.
        # Accent/primary: placed in color_candidates; the kernel's pick_accent decides.
        # Neutral roles: placed in neutral_candidates; pick_neutrals decides.
        # Background/foreground: non-heuristic pass-throughs.
        #
        # Weight: explicit config hit gets 2.0, inferred className hit gets 1.0.
        # This makes a real config theme colour out-rank a className-frequency colour
        # when both resolve to the same role — no other preference is expressed here.
        #
        # Chromatic-ness is NOT evaluated in normalize. Every resolved colour enters
        # the seam at its role regardless of chroma; the kernel's pick_accent applies
        # the chromatic gate (_chroma_of) at ranking time. The saturation field on
        # ColorCandidate is informational metadata only and is left at 0.0 here because
        # GitHub gather has no per-candidate area or saturation measurement.
        color_candidates: list[ColorCandidate] = []
        neutral_candidates: list[NeutralCandidate] = []

        primary_hex, primary_explicit = _resolve("primary", "brand", "accent")
        if primary_hex:
            color_candidates.append(
                ColorCandidate(
                    hex=primary_hex,
                    weight=2.0 if primary_explicit else 1.0,
                    saturation=0.0,
                )
            )

        surface_hex, surface_explicit = _resolve("surface", "card", "popover", "secondary")
        if surface_hex:
            neutral_candidates.append(
                NeutralCandidate(
                    role="surface",
                    hex=surface_hex,
                    weight=2.0 if surface_explicit else 1.0,
                )
            )

        border_hex, border_explicit = _resolve("border", "ring", "stroke")
        if border_hex:
            neutral_candidates.append(
                NeutralCandidate(
                    role="border",
                    hex=border_hex,
                    weight=2.0 if border_explicit else 1.0,
                )
            )

        muted_hex, muted_explicit = _resolve("muted", "neutral", "gray", "slate")
        if muted_hex:
            neutral_candidates.append(
                NeutralCandidate(
                    role="muted",
                    hex=muted_hex,
                    weight=2.0 if muted_explicit else 1.0,
                )
            )

        background_hex_raw, _bg_explicit = _resolve("background", "bg")
        background_hex = background_hex_raw or ""

        foreground_hex_raw, _fg_explicit = _resolve("foreground", "text", "content")
        if foreground_hex_raw:
            foreground_hex = foreground_hex_raw
        elif background_hex:
            # Derive from background luminance when no foreground was gathered.
            # This preserves the rule that was inline in the previous implementation.
            foreground_hex = "#f4f1ea" if _luminance(background_hex) < 128 else "#1a1a1a"
        else:
            foreground_hex = ""

        # is_dark: only meaningful when a real background was resolved.
        is_dark = bool(background_hex and _luminance(background_hex) < 128)

        # Spacing: no-silent-default. Pass only real gathered values; an empty list
        # tells the kernel to leave Tokens.spacing_scale at the model default.
        # The previous implementation would pass Tokens().spacing_scale when nothing
        # was gathered — that silently baked in the default scale regardless of source.
        raw_spacing = s.get("spacing") or s.get("inferred_spacing") or []
        spacing_scale = [int(x) for x in raw_spacing if int(x) > 0]

        # Radius: no-silent-default. Only set a convention when a real signal exists.
        # _radius_convention("") floors to "rounded" even with no evidence; to avoid
        # silently injecting a rounded default when the repo has no radius signal,
        # we only call _radius_convention when a real value was gathered.
        raw_radius = s.get("radius") or s.get("inferred_radius")
        radius_conv = _radius_convention(str(raw_radius)) if raw_radius else ""

        # Font: widened lookup to cover className-inferred font signals.
        # The previous implementation read only s.get("fonts"); extending to fall back
        # on s.get("inferred_fonts") ensures className-inferred font names (e.g.
        # a font-family declaration extracted from a UI file) are not silently dropped.
        font = _first_known_font(s.get("fonts") or s.get("inferred_fonts") or [])

        typography = TypographySignals(
            heading_family=font or "",
            body_family=font or "",
            weights=[],   # GitHub gather does not collect numeric font weights
            radius_convention=radius_conv,
        )

        # Container observations for elevation derivation.
        # GitHub gather never produces per-container border/shadow pairs. The previous
        # implementation set elevation_style="shadows" whenever any shadow token was
        # present — a coarse any-shadow heuristic. Passing container_observations=[]
        # tells the kernel there is no real elevation evidence; it will leave
        # Tokens.elevation_style at the model default rather than forcing "shadows"
        # based on a loose signal.
        container_observations: list = []

        # Inventory: pass the raw union to the kernel; assemble_inventory (called
        # inside harden) handles case-insensitive filtering against _COMPONENT_HINTS,
        # dedup, and sort. Do not pre-sort or pre-filter here.
        observed_types = (
            list(s.get("components") or [])
            + list(s.get("inferred_components") or [])
        )

        # Provenance flags — drive score_confidence in the kernel.
        # explicit.accent / explicit.neutrals: require the resolution to have come from
        # the explicit config map (from_explicit=True), not the inferred className map.
        # explicit.typography: requires an explicit font declaration (not inferred).
        # explicit.elevation and explicit.inventory are always False for GitHub gather —
        # elevation comes only from container observations (none here) and component
        # inventory is always inferred from filenames.
        has_explicit_colors = bool(color_map)
        has_explicit_font = bool(_first_known_font(s.get("fonts") or []))

        explicit = FieldFlags(
            accent=has_explicit_colors and primary_explicit,
            neutrals=has_explicit_colors and (
                surface_explicit or border_explicit or muted_explicit
            ),
            typography=has_explicit_font,
            elevation=False,
            inventory=False,
        )
        gathered = FieldFlags(
            accent=pick_accent(color_candidates) is not None,
            neutrals=bool(neutral_candidates),
            typography=bool(font),
            elevation=bool(container_observations),
            inventory=bool(observed_types),
        )
        # Every explicit.X True also implies gathered.X True.
        if explicit.accent:
            gathered.accent = True
        if explicit.neutrals:
            gathered.neutrals = True
        if explicit.typography:
            gathered.typography = True

        signals = DesignSignals(
            color_candidates=color_candidates,
            neutral_candidates=neutral_candidates,
            container_observations=container_observations,
            observed_component_types=observed_types,
            typography=typography,
            is_dark=is_dark,
            background_hex=background_hex,
            foreground_hex=foreground_hex,
            spacing_scale=spacing_scale,
            gathered=gathered,
            explicit=explicit,
            provider="github",
        )
        return harden(signals)  # sole assembler — no field assigned on the result after this


# Register both adapters on import so the package's import side-effect populates
# the shared registry (mirrors the contract documented in extractors.py).
_FIGMA = FigmaExtractor()
_WEB = WebExtractor()
_GITHUB = GithubExtractor()
registry.register(_FIGMA)
registry.register(_WEB)
registry.register(_GITHUB)

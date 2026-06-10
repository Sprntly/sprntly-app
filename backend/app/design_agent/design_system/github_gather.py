"""Pure GitHub repository gather: detect the styling system from package.json deps
and file globs, route to the matching strategy, and produce the gather dict that
``GithubExtractor.normalize`` reads.

This module is pure — no network calls, no ``requests``, no ``github_app``,
no ``figma_oauth``, no ``anthropic`` import.  The adapter (``extract_raw_signals``)
does all fetching and passes already-fetched ``{path: text}`` content + a file-path
listing in here.  This keeps every strategy deterministic and offline-testable with
synthetic fixtures, exactly like ``figma_gather.py``.

Gather output contract
----------------------
Returns a plain ``dict`` with exactly these keys (all always present):

    colors          – dict[str, str]   explicit tokens from a real config/theme/CSS-vars
    inferred_colors – dict[str, str]   className-frequency inferred colors
    fonts           – list[str]        explicit font declarations
    inferred_fonts  – list[str]        className-inferred font signals
    spacing         – list[int]        explicit spacing tokens (px)
    inferred_spacing – list[int]       inferred Tailwind spacing
    radius          – str | None       explicit radius token value
    inferred_radius – str | None       inferred radius from className frequency
    shadows         – list[str]        explicit shadow tokens
    inferred_shadows – list[str]       inferred shadow tokens
    components      – list[str]        explicit component type names
    inferred_components – list[str]    filename / className inferred component names
    files_present   – list[str]        paths whose bodies were actually read
    inference_files – list[str]        UI-file paths used for inferred signals
    inference_stats – dict[str, int]   bookkeeping counters from the inferred pass

The ``files_present`` key is what ``GithubExtractor.normalize`` checks to decide
whether the bag is non-empty (the empty-bag predicate is ``not files_present and
not inference_files``).  A strategy that finds tokens must populate at least one of
those two lists.

Strategy selection
------------------
Each ``StylingStrategy`` declares a ``detect`` method that reads only
``package.json`` dependency names and file-path listings — no file bodies.  The
``StylingRegistry`` runs strategies in registration order; the FIRST one whose
``detect`` returns True wins.  Only THAT strategy's file bodies are fetched and
passed to ``gather``.  Strategies added later slot in as ``register(...)`` calls;
the kernel, ``DesignSignals``, and the other strategies are never touched.

Planned extension points (register when ready, in any order):
  styled-components / emotion (parse theme object),
  MUI / Chakra / Ant (createTheme config),
  CSS/SCSS modules (.module.css + SCSS $vars),
  Vue SFC token gather,
  vanilla HTML/CSS (:root or class-frequency rank).
"""
from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

# ── Shared regexes (reused from adapters; kept here so strategies are self-contained)

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
_TAILWIND_COLOR_CLASS_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|to)-([a-z]+)(?:-\d{2,3})?\b"
)
_TAILWIND_RADIUS_RE = re.compile(r"\brounded(?:-(none|sm|md|lg|xl|2xl|3xl|full))?\b")
_TAILWIND_SPACING_RE = re.compile(
    r"\b(?:p|px|py|pt|pr|pb|pl|gap|space-x|space-y|m|mx|my)-(\d+)\b"
)
_TAILWIND_SHADOW_RE = re.compile(r"\bshadow(?:-(sm|md|lg|xl|2xl|none))?\b")
_TAILWIND_WEIGHT_RE = re.compile(r"\bfont-(medium|semibold|bold)\b")
_TAILWIND_TEXT_SIZE_RE = re.compile(r"\btext-(xs|sm|base|lg|xl|2xl|3xl)\b")
_EXPORT_COMPONENT_RE = re.compile(
    r"\b(?:function|const)\s+([A-Z][A-Za-z0-9]*)|\bexport\s+\{\s*([A-Z][A-Za-z0-9]*)"
)

# Tailwind's built-in color palette — used for className-frequency inference.
_TAILWIND_COLORS: dict[str, str] = {
    "slate": "#64748b", "gray": "#6b7280", "zinc": "#71717a", "neutral": "#737373",
    "stone": "#78716c", "red": "#ef4444", "orange": "#f97316", "amber": "#f59e0b",
    "yellow": "#eab308", "lime": "#84cc16", "green": "#22c55e", "emerald": "#10b981",
    "teal": "#14b8a6", "cyan": "#06b6d4", "sky": "#0ea5e9", "blue": "#3b82f6",
    "indigo": "#6366f1", "violet": "#8b5cf6", "purple": "#a855f7", "fuchsia": "#d946ef",
    "pink": "#ec4899", "rose": "#f43f5e", "white": "#ffffff", "black": "#000000",
}

# CSS variable names that map to semantic role keys.
_CSS_VAR_ROLE_MAP: dict[str, str] = {
    "primary": "primary",
    "background": "background",
    "foreground": "foreground",
    "border": "border",
    "muted": "muted",
    "accent": "primary",       # shadcn uses --accent for the accent color
    "ring": "ring",
    "card": "card",
    "destructive": "destructive",
    "secondary": "secondary",
    "popover": "popover",
    "input": "input",
}


def _normalize_hex(value: str | None) -> str | None:
    """Return a lower-case #rrggbb token, or None for unsupported color forms."""
    if not value:
        return None
    v = value.strip()
    if _HEX_RE.fullmatch(v):
        return v.lower()
    return None


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


def _walk_json(value):
    """Yield all nested dict/list/scalar values from a decoded JSON object."""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _empty_gather_dict() -> dict:
    """Return a fully-populated but empty gather dict — the base all strategies start from."""
    return {
        "colors": {},
        "inferred_colors": {},
        "fonts": [],
        "inferred_fonts": [],
        "spacing": [],
        "inferred_spacing": [],
        "radius": None,
        "inferred_radius": None,
        "shadows": [],
        "inferred_shadows": [],
        "components": [],
        "inferred_components": [],
        "files_present": [],
        "inference_files": [],
        "inference_stats": {},
    }


# ── Parsing helpers (moved from adapters._collect_*; one home, no second copy) ────


def collect_named_value(
    key: str,
    value,
    signals: dict,
    spacing: set[int],
    shadows: list[str],
) -> None:
    """Store a named design token in the signals dict.

    Dispatches by value type: hex color → ``signals["colors"]``, px/rem size →
    spacing or radius, font name → ``signals["fonts"]``, shadow value → shadows list.
    Modifies ``signals``, ``spacing``, and ``shadows`` in-place.
    """
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


def collect_component_hints(text: str, components: set[str], hints: tuple[str, ...]) -> None:
    """Scan ``text`` for component-name tokens and add matches to ``components``."""
    haystack = text.lower()
    for name in hints:
        if re.search(rf"\b{name}\b", haystack):
            components.add(name)


def collect_json_signals(
    text: str,
    signals: dict,
    components: set[str],
    spacing: set[int],
    shadows: list[str],
    hints: tuple[str, ...],
) -> None:
    """Parse a JSON file body for design token key–value pairs and component names.

    Walks every nested node looking for ``{key: {value: <token>}}`` shapes (the
    Style Dictionary pattern) and flat ``key: value`` pairs.  Extracts colors,
    spacing, radius, fonts, shadows, and component name hints.
    """
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
                    collect_named_value(str(key), value["value"], signals, spacing, shadows)
                collect_named_value(str(key), value, signals, spacing, shadows)
        elif isinstance(node, str):
            collect_component_hints(node, components, hints)
    if isinstance(data, dict):
        for key in ("components", "aliases"):
            section = data.get(key)
            if isinstance(section, dict):
                for name in section:
                    collect_component_hints(str(name), components, hints)


def collect_text_signals(
    text: str,
    signals: dict,
    components: set[str],
    spacing: set[int],
    shadows: list[str],
    hints: tuple[str, ...],
) -> None:
    """Parse a CSS/JS/TS file body for design token declarations and component names.

    Handles: ``key: '#hex'`` JS pair syntax, ``--css-var: value;`` declarations,
    ``font-family: ...;`` rules, font token pairs, px/rem size pairs, shadow token
    pairs, and component name hints.  Does NOT handle the entire CSS-vars strategy
    (that is ``collect_css_var_tokens`` below) — this is for the tailwind-config /
    tokens.json JS/TS text path.
    """
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

    collect_component_hints(text, components, hints)


def collect_inferred_signals(
    text: str,
    file_name: str,
    colors: dict[str, str],
    spacing: set[int],
    shadows: list[str],
    fonts: list[str],
    components: set[str],
    stats: dict[str, int],
    hints: tuple[str, ...],
) -> None:
    """Infer design tokens from Tailwind className frequencies in a UI source file.

    Scans for color-class patterns (``bg-blue-500``, ``text-red-400``, etc.),
    radius classes (``rounded-lg``), spacing multipliers (``p-4``, ``gap-8``),
    shadow classes (``shadow-md``), and font-weight/text-size classes.  All
    results go into the *inferred* accumulators — never the explicit ones.

    Also extracts component names from the file stem and exported symbol names.
    """
    lower_file = file_name.rsplit(".", 1)[0].lower()
    if lower_file in hints:
        components.add(lower_file)
    collect_component_hints(text, components, hints)
    for match in _EXPORT_COMPONENT_RE.findall(text):
        exported = (match[0] or match[1] or "").strip()
        if exported:
            collect_component_hints(exported, components, hints)

    color_counts: dict[str, int] = {}
    for color_name in _TAILWIND_COLOR_CLASS_RE.findall(text):
        if color_name in _TAILWIND_COLORS:
            color_counts[color_name] = color_counts.get(color_name, 0) + 1
    for color_name, count in sorted(color_counts.items(), key=lambda item: item[1], reverse=True):
        if count >= 2 and "primary" not in colors:
            colors["primary"] = _TAILWIND_COLORS[color_name]
            stats["color_classes"] = stats.get("color_classes", 0) + count
            break
    if "bg-white" in text and "background" not in colors:
        colors["background"] = "#ffffff"
    if "bg-black" in text and "background" not in colors:
        colors["background"] = "#000000"
    if "text-white" in text and "foreground" not in colors:
        colors["foreground"] = "#ffffff"
    if "border-" in text and "border" not in colors:
        from app.design_agent.design_system.models import Colors
        colors["border"] = Colors().border

    radius_hits = _TAILWIND_RADIUS_RE.findall(text)
    if radius_hits:
        stats["radius_classes"] = stats.get("radius_classes", 0) + len(radius_hits)
        order = {"full": 4, "3xl": 3, "2xl": 3, "xl": 2, "lg": 2, "md": 1, "sm": 1, "": 1}
        current = stats.get("_radius_rank", 0)
        for value in radius_hits:
            rank = order.get(value or "", 1)
            if rank >= current:
                stats["_radius_rank"] = rank
                if value == "full":
                    stats["_radius"] = "9999px"
                elif value in {"2xl", "3xl"}:
                    stats["_radius"] = "24px"
                elif value in {"lg", "xl"}:
                    stats["_radius"] = "12px"
                elif value == "sm":
                    stats["_radius"] = "4px"
                else:
                    stats["_radius"] = "8px"

    for raw in _TAILWIND_SPACING_RE.findall(text):
        try:
            step = int(raw)
        except ValueError:
            continue
        if step > 0:
            spacing.add(step * 4)
            stats["spacing_classes"] = stats.get("spacing_classes", 0) + 1

    shadow_hits = _TAILWIND_SHADOW_RE.findall(text)
    if shadow_hits:
        stats["shadow_classes"] = stats.get("shadow_classes", 0) + len(shadow_hits)
        for value in shadow_hits:
            label = f"shadow-{value}" if value else "shadow"
            if label != "shadow-none" and label not in shadows:
                shadows.append(label)

    if _TAILWIND_WEIGHT_RE.search(text):
        fonts.append("font-weight")
        stats["font_classes"] = stats.get("font_classes", 0) + 1
    if _TAILWIND_TEXT_SIZE_RE.search(text):
        fonts.append("type-scale")
        stats["text_size_classes"] = stats.get("text_size_classes", 0) + 1


def collect_css_var_tokens(
    text: str,
    signals: dict,
    spacing: set[int],
) -> None:
    """Extract CSS custom-property declarations from a ``:root { ... }`` block.

    For each ``--token: value;`` declaration, role-keys the token into
    ``signals["colors"]`` (using ``_CSS_VAR_ROLE_MAP``) or into
    ``signals["radius"]`` / ``spacing`` for size tokens.  Only hex values map
    to colors; oklch / hsl / rgb tokens that cannot be parsed to #rrggbb are
    skipped rather than forwarded as opaque strings.
    """
    for var_name, raw_value in _CSS_VAR_RE.findall(text):
        key = var_name.lower()
        value = raw_value.strip()
        color = _normalize_hex(value)
        if color:
            # Map the CSS variable name to its semantic role key.
            role_key = _CSS_VAR_ROLE_MAP.get(key, key)
            signals["colors"].setdefault(role_key, color)
            continue
        size = _parse_px_or_rem(value)
        if size is not None:
            if "radius" in key:
                signals["radius"] = value
            elif any(k in key for k in ("space", "spacing", "gap")):
                spacing.add(size)


# ── Component-location helper (framework-orthogonal, shared by all token strategies) ──


def resolve_component_location(file_paths: list[str]) -> str:
    """Detect the frontend framework from file listings and return the component-location rule.

    Framework detection is ORTHOGONAL to the styling-system strategy: a Tailwind-React
    repo and a CSS-vars-React repo use the same PascalCase ``components/`` rule.

    Rules:
      React / Next.js (any ``.tsx`` or ``.jsx`` path detected, OR ``react`` in deps):
        PascalCase files in ``components/``, ``components/ui/``.
      Vue (``.vue`` files detected): ``.vue`` SFC names in ``components/``.
      Unknown: default to the React/Next rule (most common case).

    Returns a plain-English string naming the location convention.  The strategies
    pass this through as documentation; the adapter uses it to drive ``_list_ui_files``.
    """
    has_tsx_jsx = any(p.endswith((".tsx", ".jsx")) for p in file_paths)
    has_vue = any(p.endswith(".vue") for p in file_paths)
    if has_vue:
        return "vue-sfc"
    if has_tsx_jsx:
        return "react-next-pascal"
    # Default: assume React/Next since it's the vast majority of repos.
    return "react-next-pascal"


# ── StylingStrategy Protocol ───────────────────────────────────────────────────


@runtime_checkable
class StylingStrategy(Protocol):
    """Interface every styling strategy must satisfy.

    ``detect`` reads ONLY dependency names and file-path listings — no file bodies —
    so detection is cheap and does not inflate I/O for strategies that don't match.
    ``gather`` receives already-fetched ``{path: text}`` content and returns the
    gather dict.  It must NEVER make network calls.
    """

    name: str
    explicit: bool  # True when the strategy parses a real config/theme (earns "high")

    def detect(self, deps: set[str], file_paths: list[str]) -> bool:
        """Return True when this strategy matches the repo's styling system."""
        ...

    def gather(self, fetched: dict[str, str], hints: tuple[str, ...]) -> dict:
        """Parse already-fetched file content and return the gather dict."""
        ...


# ── StylingRegistry ─────────────────────────────────────────────────────────


class StylingRegistry:
    """Ordered registry of styling strategies.

    Strategies are evaluated in registration order; the first match wins.
    Adding a new strategy = one ``register(...)`` call; no other code changes.
    """

    def __init__(self) -> None:
        self._strategies: list[StylingStrategy] = []

    def register(self, strategy: StylingStrategy) -> None:
        """Append ``strategy`` to the end of the evaluation order."""
        self._strategies.append(strategy)

    def detect(self, deps: set[str], file_paths: list[str]) -> StylingStrategy | None:
        """Return the first strategy whose ``detect`` returns True, or None on no match."""
        for s in self._strategies:
            if s.detect(deps, file_paths):
                return s
        return None


# ── Tailwind / shadcn strategy ────────────────────────────────────────────────


class TailwindStrategy:
    """Tailwind CSS / shadcn-ui styling strategy.

    Detects from ``package.json`` deps or the presence of a ``tailwind.config.*``
    or ``components.json``.  Gathers real theme tokens (colors, fonts, radius,
    shadows) from the config file when present; falls back to className-frequency
    inference when the config carries no custom theme.

    A real config theme produces ``colors`` (explicit bucket) at ``explicit=True``,
    which earns ``score_confidence → "high"`` when fonts and neutrals are also
    resolved.  A Tailwind repo with no custom theme produces ``inferred_colors`` at
    ``explicit=False``, flooring confidence to ``"medium"`` or ``"low"``.
    """

    name = "tailwind"
    explicit = True  # may be overridden to False at gather time when no theme is found

    _config_paths = frozenset({
        "tailwind.config.ts",
        "tailwind.config.js",
        "tailwind.config.mjs",
        "tailwind.config.cjs",
    })

    def detect(self, deps: set[str], file_paths: list[str]) -> bool:
        """Return True when ``tailwindcss`` is a dep, or a tailwind config / components.json is present."""
        if "tailwindcss" in deps:
            return True
        if any(p in file_paths for p in self._config_paths):
            return True
        if "components.json" in file_paths:
            return True
        return False

    def gather(self, fetched: dict[str, str], hints: tuple[str, ...]) -> dict:
        """Parse Tailwind config and UI files; return the gather dict.

        1. Parse ``tailwind.config.*`` for ``theme.colors`` / ``theme.extend.colors``,
           ``fontFamily``, ``borderRadius``, ``boxShadow``.
        2. If a real theme was found, populate ``signals["colors"]`` (explicit bucket).
        3. If no custom theme was found, fall back to className-frequency inference
           across all fetched file bodies (inferred bucket).
        """
        signals = _empty_gather_dict()
        components: set[str] = set()
        spacing: set[int] = set()
        shadows: list[str] = []
        inferred_components: set[str] = set()
        inferred_spacing: set[int] = set()
        inferred_shadows: list[str] = []
        inferred_fonts: list[str] = []
        inferred_colors: dict[str, str] = {}
        inference_stats: dict[str, int] = {}

        found_explicit_theme = False

        # Parse design config files (tailwind.config.*, tokens.json, etc.)
        for path, text in fetched.items():
            lower_path = path.lower()
            if any(lower_path.endswith(config_name) for config_name in self._config_paths) or \
               lower_path.endswith("components.json") or \
               lower_path.endswith("tokens.json") or \
               lower_path.endswith("style-dictionary.json"):
                signals["files_present"].append(path)
                if lower_path.endswith(".json"):
                    before = dict(signals["colors"])
                    collect_json_signals(text, signals, components, spacing, shadows, hints)
                    if signals["colors"] != before:
                        found_explicit_theme = True
                else:
                    # JS/TS/MJS/CJS config file
                    before = dict(signals["colors"])
                    collect_text_signals(text, signals, components, spacing, shadows, hints)
                    if signals["colors"] != before:
                        found_explicit_theme = True

            elif lower_path in ("app/globals.css", "src/index.css", "src/globals.css", "styles/globals.css"):
                # CSS globals may co-exist with Tailwind (CSS variables for shadcn-ui).
                signals["files_present"].append(path)
                before = dict(signals["colors"])
                collect_css_var_tokens(text, signals, spacing)
                if signals["colors"] != before:
                    found_explicit_theme = True
                collect_text_signals(text, signals, components, spacing, shadows, hints)

        # UI files: always go through inferred path for className signals.
        # Even for Tailwind repos with a real theme, UI files contribute inferred
        # component names and supplement with className-frequency colors.
        for path, text in fetched.items():
            lower_path = path.lower()
            # Skip design-config files already processed above.
            if any(lower_path.endswith(config_name) for config_name in self._config_paths):
                continue
            if lower_path.endswith("components.json") or \
               lower_path.endswith("tokens.json") or \
               lower_path.endswith("style-dictionary.json"):
                continue
            if lower_path in ("app/globals.css", "src/index.css", "src/globals.css", "styles/globals.css"):
                continue
            # This is a UI source file.
            file_name = path.rsplit("/", 1)[-1]
            signals["inference_files"].append(path)
            collect_inferred_signals(
                text, file_name,
                inferred_colors, inferred_spacing, inferred_shadows,
                inferred_fonts, inferred_components, inference_stats, hints,
            )

        # Decide which color bucket to use:
        # - Real theme found → explicit colors bucket (stays as populated by collect_*).
        # - No real theme → move inferred colors to inferred bucket; no explicit colors.
        if not found_explicit_theme:
            # No custom theme: fall through to inferred-only path.
            signals["colors"] = {}
            signals["inferred_colors"] = inferred_colors
        else:
            # Real theme: keep explicit colors; still add inferred for supplement.
            signals["inferred_colors"] = inferred_colors

        signals["spacing"] = sorted(spacing)
        signals["shadows"] = shadows[:8]
        signals["components"] = sorted(components)
        signals["inferred_spacing"] = sorted(inferred_spacing)
        signals["inferred_radius"] = inference_stats.get("_radius")
        signals["inferred_shadows"] = inferred_shadows[:8]
        signals["inferred_fonts"] = inferred_fonts[:8]
        signals["inferred_components"] = sorted(inferred_components)
        signals["inference_stats"] = inference_stats
        return signals


# ── CSS custom-properties strategy ────────────────────────────────────────────


class CssVarsStrategy:
    """CSS custom-properties styling strategy.

    Detects from the presence of one of the canonical globals CSS paths
    (``app/globals.css``, ``src/index.css``, ``src/globals.css``,
    ``styles/globals.css``) when Tailwind is NOT detected first.

    Gathers ``--token: value`` declarations from the ``:root`` block, role-keying
    by variable name (``--primary`` → ``colors["primary"]``, ``--border`` →
    ``colors["border"]``, etc.).  Real CSS-var declarations produce ``colors``
    (explicit bucket) at ``explicit=True``, which earns ``score_confidence →
    "high"`` when fonts and neutrals also resolve.
    """

    name = "css-vars"
    explicit = True

    _globals_paths = frozenset({
        "app/globals.css",
        "src/index.css",
        "src/globals.css",
        "styles/globals.css",
    })

    def detect(self, deps: set[str], file_paths: list[str]) -> bool:
        """Return True when NOT Tailwind and a globals CSS path is present."""
        # The Tailwind strategy is registered first, so if we reach this detect()
        # it means Tailwind was NOT selected.  We still guard explicitly.
        if "tailwindcss" in deps:
            return False
        return any(p in file_paths for p in self._globals_paths)

    def gather(self, fetched: dict[str, str], hints: tuple[str, ...]) -> dict:
        """Parse CSS variables from the globals file; return the gather dict.

        Extracts ``--token: #hex`` entries from the ``:root`` block and
        role-keys them into the explicit ``colors`` bucket.  Font declarations
        and component name hints are also extracted.  Falls through to
        className-frequency inference for UI source files.
        """
        signals = _empty_gather_dict()
        components: set[str] = set()
        spacing: set[int] = set()
        shadows: list[str] = []
        inferred_components: set[str] = set()
        inferred_spacing: set[int] = set()
        inferred_shadows: list[str] = []
        inferred_fonts: list[str] = []
        inferred_colors: dict[str, str] = {}
        inference_stats: dict[str, int] = {}

        for path, text in fetched.items():
            lower_path = path.lower()
            if lower_path in self._globals_paths:
                signals["files_present"].append(path)
                collect_css_var_tokens(text, signals, spacing)
                # Also parse font-family declarations from the globals file.
                for value in _FONT_DECL_RE.findall(text):
                    if value:
                        signals["fonts"].append(value.strip())
                collect_component_hints(text, components, hints)
            elif lower_path.endswith(".json"):
                signals["files_present"].append(path)
                collect_json_signals(text, signals, components, spacing, shadows, hints)
            elif lower_path.endswith((".js", ".ts", ".mjs", ".cjs")):
                # Non-globals text files (tokens.js, etc.)
                signals["files_present"].append(path)
                collect_text_signals(text, signals, components, spacing, shadows, hints)
            else:
                # UI source files → inferred signals.
                file_name = path.rsplit("/", 1)[-1]
                signals["inference_files"].append(path)
                collect_inferred_signals(
                    text, file_name,
                    inferred_colors, inferred_spacing, inferred_shadows,
                    inferred_fonts, inferred_components, inference_stats, hints,
                )

        signals["spacing"] = sorted(spacing)
        signals["shadows"] = shadows[:8]
        signals["components"] = sorted(components)
        signals["inferred_colors"] = inferred_colors
        signals["inferred_spacing"] = sorted(inferred_spacing)
        signals["inferred_radius"] = inference_stats.get("_radius")
        signals["inferred_shadows"] = inferred_shadows[:8]
        signals["inferred_fonts"] = inferred_fonts[:8]
        signals["inferred_components"] = sorted(inferred_components)
        signals["inference_stats"] = inference_stats
        return signals


# ── Graceful-degrade fallback ──────────────────────────────────────────────────


class DegradeStrategy:
    """Best-effort gather when no recognized styling system is detected.

    Runs className-frequency color inference and filename component hints
    at ``explicit=False`` (so ``score_confidence`` floors to ``"medium"`` or
    ``"low"``).  Never raises; returns a non-empty gather dict when any
    inferred signal is found (so ``normalize`` produces a valid ``DesignSystem``
    rather than the bare baseline).
    """

    name = "degrade"
    explicit = False

    def detect(self, deps: set[str], file_paths: list[str]) -> bool:
        """Always returns False — this strategy is used only as the detect-miss arm."""
        return False

    def gather(self, fetched: dict[str, str], hints: tuple[str, ...]) -> dict:
        """Run best-effort gather over all fetched file bodies.

        Design-config files (JSON, JS/TS/CSS config files) are parsed for explicit
        tokens and placed in ``signals["colors"]``.  UI source files are parsed for
        className-frequency inferred signals.  All runs at ``explicit=False`` for the
        purpose of the per-field provenance flags, but JSON token files still populate
        the explicit ``colors`` bucket so their tokens outrank inferred ones in normalize.
        """
        signals = _empty_gather_dict()
        components: set[str] = set()
        spacing: set[int] = set()
        shadows: list[str] = []
        inferred_colors: dict[str, str] = {}
        inferred_spacing: set[int] = set()
        inferred_shadows: list[str] = []
        inferred_fonts: list[str] = []
        inferred_components: set[str] = set()
        inference_stats: dict[str, int] = {}

        _design_file_names = frozenset({
            "tailwind.config.ts", "tailwind.config.js", "tailwind.config.mjs",
            "tailwind.config.cjs", "components.json", "tokens.json",
            "style-dictionary.json", "app/globals.css", "src/index.css",
            "src/globals.css", "styles/globals.css", "package.json",
        })

        for path, text in fetched.items():
            lower_path = path.lower()
            # Treat design-token config files as explicit sources.
            if lower_path in _design_file_names or \
               path in _design_file_names:
                signals["files_present"].append(path)
                if lower_path.endswith(".json"):
                    collect_json_signals(text, signals, components, spacing, shadows, hints)
                elif lower_path.endswith((".css",)):
                    collect_css_var_tokens(text, signals, spacing)
                    for value in _FONT_DECL_RE.findall(text):
                        if value:
                            signals["fonts"].append(value.strip())
                    collect_component_hints(text, components, hints)
                elif lower_path.endswith((".js", ".ts", ".mjs", ".cjs")):
                    collect_text_signals(text, signals, components, spacing, shadows, hints)
            else:
                # UI source file → inferred signals.
                file_name = path.rsplit("/", 1)[-1]
                signals["inference_files"].append(path)
                collect_inferred_signals(
                    text, file_name,
                    inferred_colors, inferred_spacing, inferred_shadows,
                    inferred_fonts, inferred_components, inference_stats, hints,
                )

        signals["spacing"] = sorted(spacing)
        signals["shadows"] = shadows[:8]
        signals["components"] = sorted(components)
        signals["inferred_colors"] = inferred_colors
        signals["inferred_spacing"] = sorted(inferred_spacing)
        signals["inferred_radius"] = inference_stats.get("_radius")
        signals["inferred_shadows"] = inferred_shadows[:8]
        signals["inferred_fonts"] = inferred_fonts[:8]
        signals["inferred_components"] = sorted(inferred_components)
        signals["inference_stats"] = inference_stats
        return signals


# ── Module-level registry singleton ───────────────────────────────────────────

# Priority order matters: first match wins.  Tailwind first (highest coverage —
# most repos in the wild + Sprntly's own stack), then CSS custom-properties.
styling_registry = StylingRegistry()
styling_registry.register(TailwindStrategy())
styling_registry.register(CssVarsStrategy())

# The degrade strategy is kept as a module-level singleton for external callers
# but is NOT registered — the registry's detect-miss arm uses it directly.
degrade_strategy = DegradeStrategy()


# ── Entry function (called by GithubExtractor.extract_raw_signals) ─────────────


def gather_github_signals(
    fetched: dict[str, str],
    deps: set[str],
    file_paths: list[str],
    hints: tuple[str, ...],
) -> dict:
    """Detect the styling system and gather design tokens from already-fetched content.

    Parameters
    ----------
    fetched
        ``{path: text}`` map of file bodies already fetched by the adapter.
        Only the winning strategy's files should be in here (the adapter fetches
        only what the detected strategy needs).
    deps
        Set of ``package.json`` dependency names (keys of ``dependencies`` +
        ``devDependencies``).
    file_paths
        Flat list of all file paths returned by the bounded design-file listing
        (used for detection only — no body fetch here).
    hints
        The ``_COMPONENT_HINTS`` tuple from the adapter (passed in to avoid a
        circular import).

    Returns
    -------
    dict
        The gather dict with the key contract documented in this module's docstring.
        Never raises; falls back to graceful degradation on any unrecognized stack.
    """
    strategy = styling_registry.detect(deps, file_paths)
    if strategy is None:
        strategy = degrade_strategy

    try:
        return strategy.gather(fetched, hints)
    except Exception:
        # Last-resort safety net: return a valid empty gather dict rather than
        # propagating an unexpected parse error up through the adapter.
        return _empty_gather_dict()

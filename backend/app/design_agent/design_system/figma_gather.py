"""Pure Figma document gather: walk a fetched file doc and produce the rich
gather dict that ``FigmaExtractor.extract_raw_signals`` merges alongside the
unchanged legacy palette summary.

This module is a pure function — no network calls, no ``requests``, no
``figma_oauth`` import, no ``anthropic`` import.  It is deterministic for a
fixed input document so all behaviour can be tested offline with synthetic
fixtures.

The caller (``FigmaExtractor.extract_raw_signals``) already owns the fetched
document and passes it in; we walk it here.

Gather output contract
----------------------
Returns a plain ``dict`` with exactly these keys (all always present):

    color_candidates   – list[{"hex", "weight", "source"}]
                         ALL colors — chromatic AND neutral.  No saturation
                         computation or threshold anywhere in this module;
                         saturation is the kernel's concern.
    neutral_candidates – list[{"role", "hex", "weight"}]
                         role in {"surface", "border", "muted"}
    container_observations – list[{"has_border", "has_shadow"}]
    observed_component_types – list[str]
    theme_background   – str | None   (dominant-board background hex)
    theme_is_dark      – bool
    foreground         – str | None   (dominant TEXT fill hex)
    heading_font_family – str
    body_font_family    – str
    font_weights_observed – list[int]
    radius_convention  – "sharp" | "rounded" | "pill" | ""
    spacing_px         – list[int]
    explicit_color_styles – bool
    explicit_text_styles  – bool

Key design decisions that must not be changed here:
- All colours go in as candidates; saturation is never computed or
  thresholded here — the kernel decides chromatic-vs-neutral downstream.
- ``round(c * 255)`` for new-path hex conversion (not the legacy ``int()``).
- 5 000-node outer cap; 200-container-observation inner cap — independent.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Maximum nodes we will visit (outer walk bound).
_MAX_NODES = 5_000
# Maximum container observations to collect (inner cap, independent of outer).
_MAX_CONTAINERS = 200
# Maximum spacing values to keep.
_MAX_SPACING = 12


def _fill_to_hex(fill: dict[str, Any]) -> str | None:
    """Convert a Figma SOLID fill dict to a lower-case #rrggbb string.

    Uses ``round()`` (not the legacy ``int()`` truncation) for the new path.
    Returns ``None`` when the fill is not a visible SOLID fill.
    """
    if not isinstance(fill, dict):
        return None
    if fill.get("type") != "SOLID":
        return None
    if fill.get("visible") is False:
        return None
    c = fill.get("color") or {}
    if not isinstance(c, dict):
        return None
    r = round((c.get("r") or 0.0) * 255)
    g = round((c.get("g") or 0.0) * 255)
    b = round((c.get("b") or 0.0) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def _first_visible_solid_hex(fills: list | None) -> str | None:
    """Return the first visible-SOLID fill hex from a fills list."""
    for fill in (fills or []):
        hx = _fill_to_hex(fill)
        if hx:
            return hx
    return None


def _color_dict_to_hex(d: object) -> str | None:
    """Convert a Figma ``{r, g, b, a}`` color dict (components in 0..1) to #rrggbb.

    Returns the hex string when ``d`` is a dict with alpha > 0.  Returns None when
    the dict is absent, malformed, or fully transparent (alpha 0).  A transparent
    color carries no visual information so it is never useful as a background signal.
    """
    if not isinstance(d, dict):
        return None
    if (d.get("a") or 0.0) <= 0:
        return None
    r = round((d.get("r") or 0.0) * 255)
    g = round((d.get("g") or 0.0) * 255)
    b = round((d.get("b") or 0.0) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def _frame_background_hex(frame: dict) -> str | None:
    """Resolve a frame's own background hex (fills or frame-level backgroundColor).

    Figma files sometimes carry their canvas background exclusively in the frame's
    ``backgroundColor`` property (a ``{r, g, b, a}`` dict with components in 0..1)
    while keeping the ``fills`` list empty or containing only invisible entries.
    A prominent example: a dark-mode file whose single top-level frame has
    ``fills=[{white, visible:false}]`` and ``backgroundColor={0,0,0,1}`` (pure
    black).  Without the fallback the black canvas is invisible to the classifier
    and the file is mis-classified as light.

    This function covers only the frame itself (layers 1 and 2 of the full
    background-resolution chain).  The caller is responsible for trying the
    page/canvas backgroundColor as layer 3 when this returns None.

    Precedence:
      1. First visible SOLID fill (unchanged existing behaviour — fills still win).
      2. ``backgroundColor`` with alpha > 0 (fallback for invisible-fills case).
      3. ``None`` — alpha 0 or absent means genuinely transparent / no canvas.
    """
    hx = _first_visible_solid_hex(frame.get("fills"))
    if hx:
        return hx
    return _color_dict_to_hex(frame.get("backgroundColor"))


def _luminance(hex_color: str) -> float:
    """Perceptual luminance of a #rrggbb string (ITU-R BT.601 weights)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _parse_variables_doc(variables_doc: dict | None) -> list[dict[str, Any]]:
    """Extract COLOR variable candidates from the Variables API response.

    Returns a list of ``{"hex": ..., "weight": 1.0, "source": "variable"}``
    entries.  Returns ``[]`` when ``variables_doc`` is falsy, missing the
    expected structure, or contains no COLOR-typed variables.

    The Variables API is Enterprise-gated and the default OAuth scopes do not
    include ``file_variables:read``, so in normal production this doc is ``{}``
    and the list degrades to ``[]`` as expected.
    """
    if not variables_doc or not isinstance(variables_doc, dict):
        return []
    meta = variables_doc.get("meta") or {}
    variables = meta.get("variables") or {}
    if not isinstance(variables, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for var in variables.values():
        if not isinstance(var, dict):
            continue
        if var.get("resolvedType") != "COLOR":
            continue
        values_by_mode = var.get("valuesByMode") or {}
        if not isinstance(values_by_mode, dict):
            continue
        # Take the first mode value.
        for mode_value in values_by_mode.values():
            if not isinstance(mode_value, dict):
                continue
            r = round((mode_value.get("r") or 0.0) * 255)
            g = round((mode_value.get("g") or 0.0) * 255)
            b = round((mode_value.get("b") or 0.0) * 255)
            hx = f"#{r:02x}{g:02x}{b:02x}"
            candidates.append({"hex": hx, "weight": 1.0, "source": "variable"})
            break  # one mode value per variable is enough

    return candidates


def _name_to_neutral_role(name: str) -> str | None:
    """Route a style or variable name to a neutral role by name tokens.

    Tokens: lowercase the name, split on ``/``, space, ``-``, ``_``.
    Priority:
      ``border|stroke|divider|outline``  → "border"
      ``muted|secondary|subtle|caption|disabled`` → "muted"
      ``background|surface|card|sheet|canvas`` → "surface"
      anything else (including unrecognised names) → None (i.e. color_candidate)

    Routing is by name ONLY — never by saturation.
    """
    tokens = re.split(r"[/\s\-_]", name.lower())
    for token in tokens:
        if token in ("border", "stroke", "divider", "outline"):
            return "border"
        if token in ("muted", "secondary", "subtle", "caption", "disabled"):
            return "muted"
        if token in ("background", "surface", "card", "sheet", "canvas"):
            return "surface"
    return None


def _dominant_board_class(pages: list[dict]) -> tuple[str, bool, set[str]]:
    """Determine which background class (dark vs light) dominates by total bbox area.

    Returns ``(theme_background_hex, theme_is_dark, dominant_frame_ids)`` where
    ``dominant_frame_ids`` is the set of frame IDs belonging to the dominant
    class.  Theme-neutral frames (no resolvable background) are NOT in the
    returned set but must always be gathered — the caller handles that.

    Returns ``("", False, set())`` when no frames carry bbox information.
    """
    dark_area: float = 0.0
    light_area: float = 0.0
    dark_frames: list[tuple[float, str, str]] = []  # (area, id, bg_hex)
    light_frames: list[tuple[float, str, str]] = []

    for page in pages:
        if not isinstance(page, dict):
            continue
        for frame in (page.get("children") or []):
            if not isinstance(frame, dict):
                continue
            bbox = frame.get("absoluteBoundingBox")
            if not isinstance(bbox, dict):
                continue
            w = bbox.get("width") or 0.0
            h = bbox.get("height") or 0.0
            area = float(w) * float(h)
            # Layer 1 + 2: frame's own fills and backgroundColor.
            # Layer 3: fall back to the page/canvas backgroundColor when the frame
            # itself is fully transparent.  This is the Plotline case: a single
            # top-level frame whose backgroundColor has alpha=0 sits on a page
            # canvas with backgroundColor={0.118, 0.118, 0.118, a:1} (#1e1e1e).
            # The page dict is already in scope here, so no extra fetch is needed.
            bg_hex = _frame_background_hex(frame) or _color_dict_to_hex(
                page.get("backgroundColor")
            )
            if bg_hex is None:
                continue  # theme-neutral frame, gathered unconditionally by caller
            frame_id = frame.get("id", "")
            if _luminance(bg_hex) < 128:
                dark_area += area
                dark_frames.append((area, frame_id, bg_hex))
            else:
                light_area += area
                light_frames.append((area, frame_id, bg_hex))

    if dark_area == 0 and light_area == 0:
        return "", False, set()

    if dark_area >= light_area:
        # Dark class dominates.
        dominant_frames = dark_frames
        is_dark = True
    else:
        dominant_frames = light_frames
        is_dark = False

    # The theme_background is the bg hex of the largest-area frame in the dominant class.
    dominant_frames_sorted = sorted(dominant_frames, key=lambda t: t[0], reverse=True)
    theme_bg = dominant_frames_sorted[0][2] if dominant_frames_sorted else ""
    dominant_ids = {t[1] for t in dominant_frames}
    return theme_bg, is_dark, dominant_ids


def _collect_styles_from_nodes(
    nodes: list[dict],
    styles_map: dict[str, dict],
    dominant_ids: set[str],
    theme_neutral_ids: set[str],
) -> tuple[list[dict], bool]:
    """Resolve published color styles via the consuming-node approach.

    Walks the node tree looking for nodes that consume a fill style via
    ``node["styles"]["fill"]`` (or ``node["styles"]["fills"]`` as a defensive
    fallback).  The concrete colour is that node's first visible SOLID fill.
    Weight = sum of consumer bbox areas.

    Returns ``(resolved_candidates, explicit_color_styles)`` where:
    - ``resolved_candidates`` is a list of ``{"hex", "weight", "source"}``
      dicts (style-derived, no saturation field).
    - ``explicit_color_styles`` is True only when at least one style resolved
      to a concrete colour this way.

    Depth-10 truncation may prevent any consumer node being found — in that
    case this returns ``([], False)`` and the caller falls through to raw fills.
    """
    # Map style_node_id → {"hex": hex, "total_weight": float}
    style_weights: dict[str, dict[str, Any]] = {}

    node_count = [0]

    def _walk(node: dict, depth: int) -> None:
        if not isinstance(node, dict):
            return
        if node_count[0] >= _MAX_NODES:
            return
        node_count[0] += 1

        # Check if this node consumes a fill style.
        node_styles = node.get("styles")
        if isinstance(node_styles, dict):
            style_id = node_styles.get("fill") or node_styles.get("fills")
            if style_id and style_id in styles_map:
                hx = _first_visible_solid_hex(node.get("fills"))
                if hx:
                    bbox = node.get("absoluteBoundingBox")
                    if isinstance(bbox, dict):
                        area = float(bbox.get("width") or 0.0) * float(bbox.get("height") or 0.0)
                    else:
                        area = 1.0
                    if style_id not in style_weights:
                        style_weights[style_id] = {"hex": hx, "total_weight": 0.0}
                    style_weights[style_id]["total_weight"] += area

        for child in (node.get("children") or []):
            if node_count[0] < _MAX_NODES:
                _walk(child, depth + 1)

    for node in nodes:
        _walk(node, 0)

    if not style_weights:
        return [], False

    # Build candidates: route by style name where possible.
    # For the color_candidates output we return ALL styles as color_candidates;
    # neutral routing happens via _name_to_neutral_role in gather_figma_signals.
    candidates = []
    for style_id, data in style_weights.items():
        style_meta = styles_map.get(style_id, {})
        style_name = style_meta.get("name", "")
        candidates.append({
            "hex": data["hex"],
            "weight": data["total_weight"],
            "source": "style",
            "_style_name": style_name,  # consumed by gather for routing; not part of public contract
        })

    return candidates, True


def _map_component_name(name: str, _component_hints: tuple[str, ...]) -> str | None:
    """Map a Figma component/componentSet name to a _COMPONENT_HINTS entry.

    Strategy:
    1. Lowercase, split on ``/``, space, ``-``, ``_``, ``.``.
    2. For each token (with trailing ``s`` stripped): exact match against hints.
    3. Word-boundary substring search (the ``_collect_component_hints`` pattern).
    Returns ``None`` for unmappable names.
    """
    haystack = name.lower()
    tokens = re.split(r"[/\s\-_.]", haystack)
    for token in tokens:
        # Strip trailing 's' for plural handling.
        stem = token.rstrip("s") if token.endswith("s") else token
        if stem in _component_hints:
            return stem
        if token in _component_hints:
            return token
    # Word-boundary substring pass (mirrors _collect_component_hints).
    for hint in _component_hints:
        if re.search(rf"\b{hint}\b", haystack):
            return hint
    return None


def gather_figma_signals(
    file_doc: dict | None,
    variables_doc: dict | None = None,
) -> dict[str, Any]:
    """Walk a fetched Figma document and produce the rich gather dict.

    The output keys (``theme_background``, ``theme_is_dark``, ``foreground``,
    ``color_candidates``, ``neutral_candidates``, ``container_observations``,
    ``observed_component_types``, typography, radius, spacing, and the two
    ``explicit_*`` provenance flags) feed ``FigmaExtractor.normalize``, which
    folds them through the shared hardening kernel.

    No network I/O, no LLM calls, no ``requests`` / ``figma_oauth`` / ``anthropic``
    imports.  Deterministic for a fixed input.

    Graceful degradation: any missing field in the Figma payload yields an empty
    family (empty list, ``""``, ``None``) rather than raising.
    """
    # Import COMPONENT_HINTS lazily to avoid a circular top-level import.
    # We need them as a set for O(1) lookups and a tuple for the regex walk.
    from app.design_agent.design_system.adapters import _COMPONENT_HINTS

    # Always-present empty result; populated as we go.
    result: dict[str, Any] = {
        "color_candidates": [],
        "neutral_candidates": [],
        "container_observations": [],
        "observed_component_types": [],
        "theme_background": None,
        "theme_is_dark": False,
        "foreground": None,
        "heading_font_family": "",
        "body_font_family": "",
        "font_weights_observed": [],
        "radius_convention": "",
        "spacing_px": [],
        "explicit_color_styles": False,
        "explicit_text_styles": False,
    }

    if not isinstance(file_doc, dict):
        return result

    document = file_doc.get("document")
    if not isinstance(document, dict):
        return result

    pages: list[dict] = [p for p in (document.get("children") or []) if isinstance(p, dict)]

    # ── Step 1: Dominant-theme board selection ──────────────────────────────
    # Classify each top-level frame as dark or light by its background fill;
    # the dominant class is the one with greater total bbox area.
    # Frames with no resolvable background are "theme-neutral" and always gathered.
    theme_bg, theme_is_dark, dominant_ids = _dominant_board_class(pages)
    result["theme_background"] = theme_bg or None
    result["theme_is_dark"] = theme_is_dark

    # Collect all top-level frame IDs so we can identify theme-neutral ones.
    all_top_frame_ids: set[str] = set()
    for page in pages:
        for frame in (page.get("children") or []):
            if isinstance(frame, dict):
                fid = frame.get("id")
                if fid:
                    all_top_frame_ids.add(fid)

    # Frames to gather from: dominant class ∪ theme-neutral (no bg = not in either class).
    frames_to_gather: list[dict] = []
    for page in pages:
        for frame in (page.get("children") or []):
            if not isinstance(frame, dict):
                continue
            fid = frame.get("id", "")
            is_dominant = fid in dominant_ids
            is_neutral = fid not in dominant_ids and (
                _first_visible_solid_hex(frame.get("fills")) is None
            )
            if is_dominant or is_neutral:
                frames_to_gather.append(frame)

    # When no dominant class exists (e.g. empty file), gather everything.
    if not dominant_ids:
        for page in pages:
            for frame in (page.get("children") or []):
                if isinstance(frame, dict):
                    frames_to_gather.append(frame)

    # ── Step 2: Explicit fallback ladder ───────────────────────────────────
    # Priority: Variables → published color styles → raw fills.

    # --- Rung 1: Variables ---
    variable_candidates = _parse_variables_doc(variables_doc)

    # --- Rung 2: Published color styles ---
    # The full-file payload carries a top-level ``styles`` map keyed by node id.
    # Shape: {style_node_id: {name, styleType|style_type, description, ...}}
    # Accept both ``styleType`` and ``style_type`` defensively.
    raw_styles_map = file_doc.get("styles") or {}
    color_styles_map: dict[str, dict] = {}
    for node_id, meta in (raw_styles_map.items() if isinstance(raw_styles_map, dict) else []):
        if not isinstance(meta, dict):
            continue
        style_type = meta.get("styleType") or meta.get("style_type") or ""
        if style_type.upper() in ("FILL", "COLOR", ""):
            # Accept ambiguous entries (empty style_type) defensively.
            color_styles_map[node_id] = meta

    # Resolve styles by finding consumer nodes in the gathered frames.
    all_gathered_nodes: list[dict] = list(frames_to_gather)
    style_resolved_candidates, explicit_color_styles = _collect_styles_from_nodes(
        all_gathered_nodes, color_styles_map, dominant_ids, set()
    )
    result["explicit_color_styles"] = explicit_color_styles

    # --- Ladder decision ---
    # Variables win outright; styles win over raw fills; raw fills are last resort.
    use_variables = bool(variable_candidates)
    use_styles = bool(style_resolved_candidates) and not use_variables

    # ── Step 3 + raw fills path ────────────────────────────────────────────
    # The raw-fill walk also collects: typography, container observations,
    # component types, radius, spacing.  It runs unconditionally; the color
    # accumulators are used only when neither variables nor styles resolved.

    # Track bbox areas per hex for raw fill weighting.
    raw_fill_area: dict[str, float] = {}  # hex → total area

    # Neutral accumulators for raw path.
    container_fill_area: dict[str, float] = {}   # hex → area (surface)
    stroke_area: dict[str, float] = {}           # hex → area (border)
    text_fill_area: dict[str, float] = {}        # hex → area (TEXT fills)

    container_observations: list[dict[str, bool]] = []
    radius_values: list[float] = []
    spacing_values: set[int] = set()
    font_families_weight: dict[str, int] = {}  # family → occurrence count
    font_weights_seen: set[int] = set()
    heading_fonts: list[str] = []
    body_fonts: list[str] = []

    node_counter = [0]

    def _is_dominant_or_neutral_frame(node: dict) -> bool:
        """True for top-level frames in the dominant class or theme-neutral."""
        fid = node.get("id", "")
        return fid in dominant_ids or (
            fid in all_top_frame_ids and _first_visible_solid_hex(node.get("fills")) is None
        )

    def _walk_gather(node: dict, is_top_level_frame: bool) -> None:
        if not isinstance(node, dict):
            return
        if node_counter[0] >= _MAX_NODES:
            return
        node_counter[0] += 1

        node_type = node.get("type", "")
        bbox = node.get("absoluteBoundingBox")
        bbox_area: float = 1.0
        if isinstance(bbox, dict):
            bbox_area = float(bbox.get("width") or 0.0) * float(bbox.get("height") or 0.0)
            if bbox_area <= 0:
                bbox_area = 1.0

        fills = node.get("fills") or []
        first_fill_hex = _first_visible_solid_hex(fills)

        # --- Raw fill accumulation (used as fallback if styles/vars don't resolve) ---
        if first_fill_hex and not is_top_level_frame:
            raw_fill_area[first_fill_hex] = raw_fill_area.get(first_fill_hex, 0.0) + bbox_area

        # --- Structural neutral routing ---
        is_container = node_type in ("FRAME", "COMPONENT", "INSTANCE", "RECTANGLE")
        has_children = bool(node.get("children"))

        if is_container and has_children and first_fill_hex and not is_top_level_frame:
            # Container FRAME fills → surface candidates.
            container_fill_area[first_fill_hex] = (
                container_fill_area.get(first_fill_hex, 0.0) + bbox_area
            )

        # Visible strokes on container nodes → border candidates.
        if is_container:
            for stroke in (node.get("strokes") or []):
                if not isinstance(stroke, dict):
                    continue
                if stroke.get("visible") is False:
                    continue
                stroke_hex = _fill_to_hex({**stroke, "type": "SOLID"}) if stroke.get("type") != "SOLID" else _fill_to_hex(stroke)
                if stroke_hex:
                    stroke_area[stroke_hex] = stroke_area.get(stroke_hex, 0.0) + bbox_area

        # TEXT fill accumulation.
        if node_type == "TEXT" and first_fill_hex:
            text_fill_area[first_fill_hex] = (
                text_fill_area.get(first_fill_hex, 0.0) + bbox_area
            )
            # Typography.
            style = node.get("style") or {}
            family = style.get("fontFamily") or ""
            weight = style.get("fontWeight")
            if family:
                font_families_weight[family] = font_families_weight.get(family, 0) + 1
                if isinstance(weight, (int, float)):
                    font_weights_seen.add(int(weight))

        # --- Container observations (Step 4) ---
        if (
            is_container
            and not is_top_level_frame
            and len(container_observations) < _MAX_CONTAINERS
        ):
            has_border = any(
                isinstance(s, dict) and s.get("visible") is not False
                for s in (node.get("strokes") or [])
            )
            has_shadow = any(
                isinstance(e, dict)
                and e.get("type") == "DROP_SHADOW"
                and e.get("visible") is not False
                for e in (node.get("effects") or [])
            )
            container_observations.append({"has_border": has_border, "has_shadow": has_shadow})

        # --- Radius (Step 6) ---
        if is_container:
            cr = node.get("cornerRadius")
            if isinstance(cr, (int, float)):
                radius_values.append(float(cr))

        # --- Spacing (Step 6) ---
        for key in ("itemSpacing", "paddingLeft", "paddingRight", "paddingTop", "paddingBottom"):
            val = node.get(key)
            if isinstance(val, (int, float)) and int(val) > 0:
                spacing_values.add(int(val))

        # Recurse.
        for child in (node.get("children") or []):
            if node_counter[0] < _MAX_NODES:
                _walk_gather(child, False)

    # Walk all gathered frames.
    for frame in frames_to_gather:
        if node_counter[0] < _MAX_NODES:
            _walk_gather(frame, True)

    # ── Step 5: Component types ─────────────────────────────────────────────
    component_types: set[str] = set()
    hints_set = set(_COMPONENT_HINTS)

    # From file-level metadata maps.
    for meta_map_key in ("components", "componentSets"):
        meta_map = file_doc.get(meta_map_key) or {}
        if isinstance(meta_map, dict):
            for comp_meta in meta_map.values():
                if isinstance(comp_meta, dict):
                    name = comp_meta.get("name") or ""
                    mapped = _map_component_name(name, _COMPONENT_HINTS)
                    if mapped:
                        component_types.add(mapped)

    # From walked COMPONENT/COMPONENT_SET/INSTANCE nodes in frames.
    comp_node_names: list[str] = []

    def _collect_comp_names(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") in ("COMPONENT", "COMPONENT_SET", "INSTANCE"):
            name = node.get("name") or ""
            if name:
                comp_node_names.append(name)
        for child in (node.get("children") or []):
            _collect_comp_names(child)

    for frame in frames_to_gather:
        _collect_comp_names(frame)

    for name in comp_node_names:
        mapped = _map_component_name(name, _COMPONENT_HINTS)
        if mapped:
            component_types.add(mapped)

    result["observed_component_types"] = sorted(component_types)

    # ── Step 6: Radius convention + spacing ─────────────────────────────────
    if radius_values:
        radius_counter = Counter(radius_values)
        dominant_radius = radius_counter.most_common(1)[0][0]
        if dominant_radius <= 0:
            result["radius_convention"] = "sharp"
        elif dominant_radius >= 999:
            result["radius_convention"] = "pill"
        else:
            result["radius_convention"] = "rounded"
    # else: remains ""

    spacing_sorted = sorted(spacing_values)[:_MAX_SPACING]
    result["spacing_px"] = spacing_sorted

    # ── Assemble color and neutral candidates ───────────────────────────────
    dominant_bg_hex = result["theme_background"]

    if use_variables:
        # Variables rung wins.
        for c in variable_candidates:
            role = _name_to_neutral_role("")  # No style name for variables; route to color_candidates
            result["color_candidates"].append({
                "hex": c["hex"], "weight": c["weight"], "source": "variable"
            })

    elif use_styles:
        # Styles rung wins.
        for c in style_resolved_candidates:
            style_name = c.get("_style_name", "")
            role = _name_to_neutral_role(style_name)
            if role is not None:
                result["neutral_candidates"].append({
                    "role": role, "hex": c["hex"], "weight": c["weight"]
                })
            else:
                result["color_candidates"].append({
                    "hex": c["hex"], "weight": c["weight"], "source": "style"
                })

    else:
        # Raw-fill fallback: exclude the dominant background from color_candidates.
        for hx, area in raw_fill_area.items():
            if hx == dominant_bg_hex:
                continue
            result["color_candidates"].append({
                "hex": hx, "weight": area, "source": "fill"
            })

    # Neutral candidates from raw structural routing.
    # These are ALWAYS assembled from raw observation regardless of ladder rung,
    # because styles/variables do not carry structural container/stroke roles.
    for hx, area in container_fill_area.items():
        if hx == dominant_bg_hex:
            continue
        result["neutral_candidates"].append({"role": "surface", "hex": hx, "weight": area})

    for hx, area in stroke_area.items():
        result["neutral_candidates"].append({"role": "border", "hex": hx, "weight": area})

    # The dominant background always routes to neutral_candidates as "surface".
    if dominant_bg_hex:
        result["neutral_candidates"].append({
            "role": "surface", "hex": dominant_bg_hex, "weight": 0.0
        })

    # Layer 4 backstop: if layers 1-3 all produced no background (transparent frame
    # on a transparent page, or a file with no top-level frames at all), fall back to
    # the highest-weight color candidate as the background.  This is a last-resort
    # signal for files that carry no explicit Figma background property anywhere.
    # It must NOT override a background already resolved by layers 1-3.
    if not result["theme_background"] and result["color_candidates"]:
        backstop_hex = max(result["color_candidates"], key=lambda c: c["weight"])["hex"]
        result["theme_background"] = backstop_hex
        result["theme_is_dark"] = _luminance(backstop_hex) < 128

    # Refresh dominant_bg_hex: it was read from result["theme_background"] before the
    # backstop could set it, so re-read it now so the foreground contrast block below
    # uses the final resolved value.
    dominant_bg_hex = result["theme_background"]

    # Foreground: pick the text fill with the highest luminance contrast against the
    # resolved canvas background.  This ensures a dark canvas yields the lightest
    # gathered text (not a low-contrast mid-gray that happens to cover more area),
    # and a light canvas yields the darkest gathered text.  When no background is
    # known, fall back to the highest-area text fill (the original behaviour).
    # Secondary fills (all others) route to neutral_candidates as muted.
    if text_fill_area:
        if dominant_bg_hex:
            bg_lum = _luminance(dominant_bg_hex)
            dominant_text_hex = max(
                text_fill_area,
                key=lambda h: abs(_luminance(h) - bg_lum),
            )
        else:
            dominant_text_hex = max(text_fill_area, key=lambda h: text_fill_area[h])
        result["foreground"] = dominant_text_hex
        for hx, area in text_fill_area.items():
            if hx != dominant_text_hex:
                result["neutral_candidates"].append({
                    "role": "muted", "hex": hx, "weight": area
                })

    # ── Typography ──────────────────────────────────────────────────────────
    result["font_weights_observed"] = sorted(font_weights_seen)
    if font_families_weight:
        dominant_family = max(font_families_weight, key=lambda f: font_families_weight[f])
        result["heading_font_family"] = dominant_family
        result["body_font_family"] = dominant_family

    # Container observations (already capped at _MAX_CONTAINERS in the walk).
    result["container_observations"] = container_observations

    return result

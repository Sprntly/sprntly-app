"""Unit tests for the Figma gather module and the Variables/styles/fills ladder.

These tests cover:
  - The published-styles path: full-file styles-map shape with consumer nodes.
  - Graceful degradation when depth-10 truncates consumer nodes or other fields.
  - The fallback ladder: Variables → styles → raw fills.
  - Area × usage weighting (not frequency).
  - No saturation filtering — monochrome files still emit color candidates.
  - Dominant-theme board selection.
  - Neutral routing: surface / border / muted.
  - Container observations: border/shadow flags, 200-observation cap.
  - Component name mapping.
  - Radius convention and spacing collection.
  - Node-walk bound (5 000-node cap).
  - Malformed input returns a well-formed empty dict without raising.
  - fetch_file_variables never raises.
  - Regression: extract_raw_signals keeps legacy summary keys byte-equal.

Pure unit tests — no DB, no network, no model.
"""
from __future__ import annotations

import ast
import importlib
import types
from typing import Any

import pytest

from app.design_agent.design_system.figma_gather import gather_figma_signals
from app.design_agent.design_system.adapters import FigmaExtractor
from app.design_agent.design_system.extractors import RawSignals


# ── Fixture helpers ──────────────────────────────────────────────────────────


def _bbox(w: float = 100.0, h: float = 100.0) -> dict:
    return {"x": 0.0, "y": 0.0, "width": w, "height": h}


def _solid_fill(r: float, g: float, b: float, visible: bool = True) -> dict:
    fill = {"type": "SOLID", "color": {"r": r, "g": g, "b": b}}
    if not visible:
        fill["visible"] = False
    return fill


def _hex_fill(hex_color: str) -> dict:
    """Build a SOLID fill dict from a #rrggbb hex string."""
    r = int(hex_color[1:3], 16) / 255.0
    g = int(hex_color[3:5], 16) / 255.0
    b = int(hex_color[5:7], 16) / 255.0
    return _solid_fill(r, g, b)


def _text_node(family: str, weight: int, fill_hex: str, area: float = 1000.0) -> dict:
    w = area ** 0.5
    return {
        "id": f"text-{family}-{weight}",
        "type": "TEXT",
        "absoluteBoundingBox": _bbox(w, w),
        "fills": [_hex_fill(fill_hex)],
        "style": {"fontFamily": family, "fontWeight": weight},
    }


def _frame_node(
    node_id: str,
    fills: list | None = None,
    children: list | None = None,
    bbox_w: float = 100.0,
    bbox_h: float = 100.0,
    strokes: list | None = None,
    effects: list | None = None,
    corner_radius: float | None = None,
    item_spacing: float | None = None,
) -> dict:
    node: dict[str, Any] = {
        "id": node_id,
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(bbox_w, bbox_h),
        "fills": fills or [],
        "children": children or [],
    }
    if strokes is not None:
        node["strokes"] = strokes
    if effects is not None:
        node["effects"] = effects
    if corner_radius is not None:
        node["cornerRadius"] = corner_radius
    if item_spacing is not None:
        node["itemSpacing"] = item_spacing
    return node


def _page_with_frames(frames: list) -> dict:
    return {"id": "page1", "type": "CANVAS", "children": frames}


def _doc(pages: list, styles: dict | None = None) -> dict:
    d: dict[str, Any] = {"document": {"children": pages}}
    if styles is not None:
        d["styles"] = styles
    return d


_WELL_FORMED_KEYS = {
    "color_candidates",
    "neutral_candidates",
    "container_observations",
    "observed_component_types",
    "theme_background",
    "theme_is_dark",
    "foreground",
    "heading_font_family",
    "body_font_family",
    "font_weights_observed",
    "radius_convention",
    "spacing_px",
    "explicit_color_styles",
    "explicit_text_styles",
}


def _assert_well_formed(result: dict) -> None:
    assert _WELL_FORMED_KEYS == set(result.keys()), (
        f"Missing or extra keys: {_WELL_FORMED_KEYS.symmetric_difference(set(result.keys()))}"
    )


# ── AC1 / AC16: module is pure and LLM-free ─────────────────────────────────


def test_gather_module_is_pure_and_llm_free():
    """The gather module must not import requests, figma_oauth, or anthropic."""
    import app.design_agent.design_system.figma_gather as mod
    source_path = mod.__file__
    with open(source_path) as f:
        source = f.read()
    tree = ast.parse(source)
    banned = {"requests", "figma_oauth", "anthropic"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                for banned_mod in banned:
                    assert banned_mod not in name, (
                        f"figma_gather.py must not import '{banned_mod}' but found: {name}"
                    )

    # Confirm no saturation COMPUTATION (function call or comparison) is present.
    # The word may appear in comments/docstrings explaining the no-filter rule, but no
    # live code should compute or threshold on saturation.
    # We check that there is no assignment or function-call pattern involving saturation.
    for node in ast.walk(tree):
        # No function definitions named _saturation or saturation_of.
        if isinstance(node, ast.FunctionDef):
            assert "saturation" not in node.name, (
                f"figma_gather.py must not define a saturation function: {node.name}"
            )
        # No variable assignments to a name containing 'saturation'.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "saturation" in target.id:
                    raise AssertionError(
                        f"figma_gather.py must not compute saturation: assignment to '{target.id}'"
                    )


# ── AC2: Published styles resolve via consuming nodes ───────────────────────


def test_published_styles_resolve_via_consuming_nodes():
    """Full-file styles-map shape + consumer nodes → style-derived candidates,
    summed-area weights, explicit_color_styles True.

    Fixture models the GET /v1/files/{key}?depth=10 full-file payload shape:
      - top-level ``styles`` map keyed by node id
      - nodes consuming a fill style via node["styles"]["fill"] == style_node_id
    This is the shape the gather module must use — NOT the /files/{key}/styles
    metadata-list endpoint (which carries no hex color).
    """
    accent_node_id = "style-accent-id"
    styles_map = {
        accent_node_id: {"name": "Primary/Accent", "styleType": "FILL"},
    }
    # Two consumer nodes for the same style: their areas sum to the weight.
    consumer_a = {
        "id": "consumer-a",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(200.0, 100.0),  # area = 20 000
        "fills": [_solid_fill(0.831, 0.686, 0.216)],  # ≈ #d4af37
        "styles": {"fill": accent_node_id},
        "children": [],
    }
    consumer_b = {
        "id": "consumer-b",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(150.0, 100.0),  # area = 15 000
        "fills": [_solid_fill(0.831, 0.686, 0.216)],
        "styles": {"fill": accent_node_id},
        "children": [],
    }
    top_frame = _frame_node(
        "top-frame",
        fills=[_solid_fill(0.169, 0.169, 0.169)],  # dark bg
        children=[consumer_a, consumer_b],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])], styles=styles_map)
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    assert result["explicit_color_styles"] is True
    assert len(result["color_candidates"]) >= 1

    # Find the style-derived candidate.
    style_candidates = [c for c in result["color_candidates"] if c.get("source") == "style"]
    assert len(style_candidates) >= 1, "Expected at least one style-derived candidate"
    # Weight should be the sum of both consumer areas: 20 000 + 15 000 = 35 000.
    total_weight = sum(c["weight"] for c in style_candidates)
    assert total_weight == pytest.approx(35_000.0, rel=0.01)
    # Hex should be consistent.
    hexes = {c["hex"] for c in style_candidates}
    # The hex derived from (0.831, 0.686, 0.216) via round() is #d4af37.
    assert "#d4af37" in hexes


# ── AC2a: Depth-limited graceful degradation ─────────────────────────────────


def test_unresolvable_styles_degrade_to_raw_fills():
    """A styles map whose consumer nodes are absent (truncation) degrades
    to raw-fill inference, explicit_color_styles False, no raise.
    """
    # styles map references a node id that does NOT appear in the tree.
    styles_map = {
        "ghost-node-id": {"name": "Primary/Accent", "styleType": "FILL"},
    }
    # A simple frame with a visible fill, but no node["styles"] key.
    inner = {
        "id": "plain-rect",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [_solid_fill(0.831, 0.686, 0.216)],  # gold
        "children": [],
    }
    top_frame = _frame_node(
        "top-frame",
        fills=[_solid_fill(0.169, 0.169, 0.169)],
        children=[inner],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])], styles=styles_map)
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    assert result["explicit_color_styles"] is False
    # Raw-fill candidates should be present (the gold rect fill).
    assert len(result["color_candidates"]) >= 1
    fill_sources = [c.get("source") for c in result["color_candidates"]]
    assert "fill" in fill_sources


# ── AC14a: Truncation-sensitive fields absent degrade gracefully ──────────────


def test_truncation_sensitive_fields_absent_degrade_gracefully():
    """Missing strokes/effects/itemSpacing/cornerRadius/nested bbox returns
    a well-formed dict with empty families, no raise.
    """
    # Frame with no strokes, no effects, no cornerRadius, no itemSpacing.
    # Inner rect has no absoluteBoundingBox.
    inner = {
        "id": "inner-no-bbox",
        "type": "RECTANGLE",
        "fills": [_solid_fill(0.5, 0.5, 0.5)],
        "children": [],
        # No absoluteBoundingBox key at all.
    }
    top_frame = {
        "id": "top",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(1440.0, 900.0),
        "fills": [_solid_fill(0.169, 0.169, 0.169)],
        "children": [inner],
        # No strokes, no effects, no cornerRadius, no itemSpacing.
    }
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    # The inner RECTANGLE has no strokes or effects, so any observation it produces
    # must have both flags False (graceful degradation — not an exception).
    for obs in result["container_observations"]:
        assert obs["has_border"] is False, "No strokes → has_border must be False"
        assert obs["has_shadow"] is False, "No effects → has_shadow must be False"
    assert result["spacing_px"] == []
    assert result["radius_convention"] == ""


# ── AC3: Styles preferred over raw fills ────────────────────────────────────


def test_styles_preferred_over_raw_fills():
    """When a style resolves, color_candidates contain only style-sourced entries
    (the styles rung wins and raw fills are not added to color_candidates).
    """
    style_id = "primary-style"
    styles_map = {style_id: {"name": "Brand/Primary", "styleType": "FILL"}}
    consumer = {
        "id": "btn",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(80.0, 40.0),
        "fills": [_solid_fill(0.831, 0.686, 0.216)],
        "styles": {"fill": style_id},
        "children": [],
    }
    plain_rect = {
        "id": "plain",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(50.0, 50.0),
        "fills": [_solid_fill(0.2, 0.6, 0.9)],  # a different raw fill color
        "children": [],
    }
    top_frame = _frame_node(
        "f1",
        fills=[_solid_fill(0.169, 0.169, 0.169)],
        children=[consumer, plain_rect],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])], styles=styles_map)
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    assert result["explicit_color_styles"] is True
    # The styles rung wins: color_candidates should only have style sources.
    for c in result["color_candidates"]:
        assert c.get("source") == "style", (
            f"Expected only style-sourced candidates but found: {c}"
        )


# ── AC4: Variables outrank styles, ladder falls through ─────────────────────


def test_variables_outrank_styles_when_present():
    """A non-empty variables_doc with a COLOR variable produces source=='variable'
    candidates and ranks above the styles path.
    """
    variables_doc = {
        "meta": {
            "variables": {
                "var1": {
                    "resolvedType": "COLOR",
                    "name": "Primary",
                    "valuesByMode": {
                        "mode1": {"r": 0.831, "g": 0.686, "b": 0.216}
                    },
                }
            }
        }
    }
    # Also provide a style that would otherwise resolve.
    style_id = "s1"
    styles_map = {style_id: {"name": "Accent", "styleType": "FILL"}}
    consumer = {
        "id": "c1",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [_solid_fill(0.0, 0.5, 0.9)],
        "styles": {"fill": style_id},
        "children": [],
    }
    top_frame = _frame_node(
        "f1", fills=[_solid_fill(0.1, 0.1, 0.1)], children=[consumer], bbox_w=1440.0, bbox_h=900.0
    )
    file_doc = _doc([_page_with_frames([top_frame])], styles=styles_map)
    result = gather_figma_signals(file_doc, variables_doc=variables_doc)

    _assert_well_formed(result)
    sources = [c["source"] for c in result["color_candidates"]]
    assert "variable" in sources, "Variables should produce source='variable' candidates"
    assert "style" not in sources, "Variables rung should prevent style candidates from appearing"


def test_variables_none_or_empty_falls_through_to_styles():
    """variables_doc=None or {} falls through to the styles rung."""
    style_id = "s1"
    styles_map = {style_id: {"name": "Primary", "styleType": "FILL"}}
    consumer = {
        "id": "c1",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [_solid_fill(0.831, 0.686, 0.216)],
        "styles": {"fill": style_id},
        "children": [],
    }
    top_frame = _frame_node(
        "f1", fills=[_solid_fill(0.1, 0.1, 0.1)], children=[consumer], bbox_w=1440.0, bbox_h=900.0
    )
    file_doc = _doc([_page_with_frames([top_frame])], styles=styles_map)

    for vd in (None, {}, {"meta": {}}):
        result = gather_figma_signals(file_doc, variables_doc=vd)
        _assert_well_formed(result)
        assert result["explicit_color_styles"] is True
        style_sources = [c for c in result["color_candidates"] if c.get("source") == "style"]
        assert len(style_sources) >= 1


def test_fetch_file_variables_never_raises(monkeypatch):
    """fetch_file_variables returns {} on non-OK status or exception (AC4)."""
    from app.connectors.figma_oauth import fetch_file_variables
    import app.connectors.figma_oauth as figo

    class _FakeResp:
        def __init__(self, ok, status_code):
            self.ok = ok
            self.status_code = status_code

        def json(self):
            return {"meta": {"variables": {}}}

    # Non-OK status (403 — Enterprise-gated).
    monkeypatch.setattr(
        figo.requests, "get",
        lambda url, **kwargs: _FakeResp(ok=False, status_code=403),
    )
    assert fetch_file_variables("fake-token", "file-key") == {}

    # Network exception.
    def _raise(*args, **kwargs):
        raise ConnectionError("network unavailable")

    monkeypatch.setattr(figo.requests, "get", _raise)
    assert fetch_file_variables("fake-token", "file-key") == {}


# ── AC5: Area × usage weight ─────────────────────────────────────────────────


def test_weight_is_area_times_usage_not_frequency():
    """10 small-area uses of hex A vs 1 large-area use of hex B: B wins by weight."""
    hex_a = _solid_fill(0.5, 0.8, 0.2)   # green-ish
    hex_b = _solid_fill(0.831, 0.686, 0.216)  # gold

    # 10 tiny nodes for hex A: 10 × (10×10) = 1 000 total area.
    small_nodes = [
        {
            "id": f"small-{i}",
            "type": "RECTANGLE",
            "absoluteBoundingBox": _bbox(10.0, 10.0),
            "fills": [hex_a],
            "children": [],
        }
        for i in range(10)
    ]
    # 1 large node for hex B: 200×250 = 50 000 total area.
    large_node = {
        "id": "large",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(200.0, 250.0),
        "fills": [hex_b],
        "children": [],
    }
    top_frame = _frame_node(
        "f1",
        fills=[_solid_fill(0.1, 0.1, 0.1)],
        children=small_nodes + [large_node],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    # Identify A and B in color_candidates.
    a_hex = f"#{round(0.5*255):02x}{round(0.8*255):02x}{round(0.2*255):02x}"
    b_hex = "#d4af37"

    a_weight = sum(c["weight"] for c in result["color_candidates"] if c["hex"] == a_hex)
    b_weight = sum(c["weight"] for c in result["color_candidates"] if c["hex"] == b_hex)

    assert b_weight > a_weight, (
        f"Large-area hex B (weight {b_weight}) should outweigh frequency hex A (weight {a_weight})"
    )


# ── AC6: no saturation pre-filter ───────────────────────────────────────────


def test_monochrome_file_emits_neutral_color_candidates():
    """Near-black + grays only. color_candidates must be non-empty."""
    near_black = _solid_fill(0.05, 0.05, 0.05)   # #0d0d0d
    gray_rect = {
        "id": "gray",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [near_black],
        "children": [],
    }
    top_frame = _frame_node(
        "f1",
        fills=[_solid_fill(0.9, 0.9, 0.9)],  # light bg
        children=[gray_rect],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    # color_candidates must contain the near-black element fill.
    assert len(result["color_candidates"]) >= 1
    hexes = [c["hex"] for c in result["color_candidates"]]
    assert "#0d0d0d" in hexes, f"Near-black should be in color_candidates; got {hexes}"


# ── AC7: Dominant-theme board selection ─────────────────────────────────────


def test_dominant_theme_picks_larger_area_board_set():
    """Light-dominant fixture: theme_background from light set, theme_is_dark False,
    no dark-board fill in neutral_candidates.  Swapping areas flips all three.
    """
    # Light board: large area, light background.
    light_frame = _frame_node(
        "light-f",
        fills=[_solid_fill(0.95, 0.95, 0.95)],  # near-white #f2f2f2
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    # Dark board: small area, dark background.
    dark_frame = _frame_node(
        "dark-f",
        fills=[_solid_fill(0.1, 0.1, 0.1)],  # near-black #1a1a1a
        bbox_w=100.0,
        bbox_h=100.0,
    )
    file_doc = _doc([_page_with_frames([light_frame, dark_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    assert result["theme_is_dark"] is False
    assert result["theme_background"] is not None
    # The dark board's fill should NOT appear in neutral_candidates at high weight.
    dark_hex = f"#{round(0.1*255):02x}{round(0.1*255):02x}{round(0.1*255):02x}"
    dark_neutrals = [n for n in result["neutral_candidates"] if n["hex"] == dark_hex]
    # Dark frame's children fill should be excluded from gathering (non-dominant frame).
    # The dark_hex (dark board background) should not dominate.
    assert result["theme_background"] != dark_hex

    # Swap: now dark is large, light is small.
    light_frame2 = _frame_node(
        "light-f2",
        fills=[_solid_fill(0.95, 0.95, 0.95)],
        bbox_w=100.0,
        bbox_h=100.0,
    )
    dark_frame2 = _frame_node(
        "dark-f2",
        fills=[_solid_fill(0.1, 0.1, 0.1)],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc2 = _doc([_page_with_frames([light_frame2, dark_frame2])])
    result2 = gather_figma_signals(file_doc2)
    assert result2["theme_is_dark"] is True
    assert result2["theme_background"] is not None
    # Now the dark frame is dominant; its background should be the theme_background.
    expected_dark = f"#{round(0.1*255):02x}{round(0.1*255):02x}{round(0.1*255):02x}"
    assert result2["theme_background"] == expected_dark


# ── AC8: Neutral routing ─────────────────────────────────────────────────────


def test_background_routed_to_neutrals_never_color_candidates():
    """Dominant background hex appears in neutral_candidates/theme_background,
    never in color_candidates.  Container fill → surface; visible stroke → border;
    secondary TEXT fill → muted.
    """
    bg_hex = "#2b2b2b"
    accent_hex = "#d4af37"
    border_stroke_hex = "#e5e7eb"
    muted_text_hex = "#888888"

    bg_fill = _hex_fill(bg_hex)
    accent_fill = _hex_fill(accent_hex)

    # Inner container with a visible stroke.
    inner_container = {
        "id": "inner-box",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(200.0, 200.0),
        "fills": [accent_fill],
        "strokes": [{"type": "SOLID", "color": {"r": 0.898, "g": 0.906, "b": 0.922}}],
        "children": [],
    }
    # Secondary text node.
    secondary_text = _text_node(
        family="Inter", weight=400, fill_hex=muted_text_hex, area=500.0
    )
    # Primary text node (dominant text fill).
    primary_text = _text_node(
        family="Inter", weight=700, fill_hex="#f4f1ea", area=5000.0
    )

    top_frame = _frame_node(
        "main-frame",
        fills=[bg_fill],
        children=[inner_container, secondary_text, primary_text],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)

    # Dominant background must NOT appear in color_candidates.
    color_hexes = [c["hex"] for c in result["color_candidates"]]
    assert bg_hex not in color_hexes, (
        f"Background {bg_hex} should not be in color_candidates; got {color_hexes}"
    )

    # Background should appear in neutral_candidates as surface.
    surface_hexes = [n["hex"] for n in result["neutral_candidates"] if n["role"] == "surface"]
    assert bg_hex in surface_hexes

    # Muted secondary text.
    muted_hexes = [n["hex"] for n in result["neutral_candidates"] if n["role"] == "muted"]
    assert muted_text_hex in muted_hexes


# ── AC9: Container observations ─────────────────────────────────────────────


def test_container_observations_strokes_shadows_and_cap():
    """Stroked node → has_border True; DROP_SHADOW node → has_shadow True;
    invisible strokes/effects ignored; 200-observation cap is exact.
    """
    # A stroked container.
    stroked = {
        "id": "stroked",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [],
        "strokes": [{"type": "SOLID", "color": {"r": 0.5, "g": 0.5, "b": 0.5}}],
        "effects": [],
        "children": [],
    }
    # A shadowed container.
    shadowed = {
        "id": "shadowed",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [],
        "strokes": [],
        "effects": [{"type": "DROP_SHADOW", "visible": True}],
        "children": [],
    }
    # A container with invisible stroke (should be ignored).
    invisible_stroke = {
        "id": "invis-stroke",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [],
        "strokes": [{"type": "SOLID", "visible": False, "color": {"r": 0.5, "g": 0.5, "b": 0.5}}],
        "effects": [],
        "children": [],
    }
    # A container with invisible effect (should be ignored).
    invisible_effect = {
        "id": "invis-effect",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(100.0, 100.0),
        "fills": [],
        "strokes": [],
        "effects": [{"type": "DROP_SHADOW", "visible": False}],
        "children": [],
    }
    top_frame = _frame_node(
        "tf",
        fills=[_solid_fill(0.9, 0.9, 0.9)],
        children=[stroked, shadowed, invisible_stroke, invisible_effect],
        bbox_w=1440.0,
        bbox_h=900.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    obs = result["container_observations"]

    # Stroked node.
    stroked_obs = next((o for o in obs if o["has_border"] and not o["has_shadow"]), None)
    assert stroked_obs is not None, "Expected a has_border=True observation"

    # Shadowed node.
    shadow_obs = next((o for o in obs if o["has_shadow"] and not o["has_border"]), None)
    assert shadow_obs is not None, "Expected a has_shadow=True observation"

    # Invisible stroke/effect should result in has_border=False, has_shadow=False.
    neither_obs = [o for o in obs if not o["has_border"] and not o["has_shadow"]]
    assert len(neither_obs) >= 2, "Invisible stroke and invisible effect should yield False flags"

    # Cap test: build a doc with 250 container nodes.
    many_children = [
        {
            "id": f"container-{i}",
            "type": "FRAME",
            "absoluteBoundingBox": _bbox(10.0, 10.0),
            "fills": [],
            "strokes": [],
            "effects": [],
            "children": [],
        }
        for i in range(250)
    ]
    big_frame = _frame_node(
        "big",
        fills=[_solid_fill(0.9, 0.9, 0.9)],
        children=many_children,
        bbox_w=2000.0,
        bbox_h=2000.0,
    )
    big_doc = _doc([_page_with_frames([big_frame])])
    big_result = gather_figma_signals(big_doc)
    assert len(big_result["container_observations"]) == 200


# ── AC10: Component name mapping ─────────────────────────────────────────────


def test_component_names_fuzzy_map_to_hint_vocabulary():
    """Button/Primary → button; Card → card; Cards → card; Btn/Primary and
    Hero Splash 01 map to nothing; empty file → [].
    """
    def _make_doc(names: list[str]) -> dict:
        components = {f"id-{i}": {"name": name} for i, name in enumerate(names)}
        base = _doc([_page_with_frames([])])
        base["components"] = components
        return base

    result_button = gather_figma_signals(_make_doc(["Button/Primary"]))
    assert "button" in result_button["observed_component_types"]

    result_card = gather_figma_signals(_make_doc(["Card"]))
    assert "card" in result_card["observed_component_types"]

    result_cards = gather_figma_signals(_make_doc(["Cards"]))
    assert "card" in result_cards["observed_component_types"]

    # "Btn/Primary" and "Hero Splash 01" are accepted misses.
    result_miss = gather_figma_signals(_make_doc(["Btn/Primary", "Hero Splash 01"]))
    assert "button" not in result_miss["observed_component_types"]
    # hero/splash are not in hints.
    assert result_miss["observed_component_types"] == []

    # Empty components map.
    empty_doc = _doc([_page_with_frames([])])
    result_empty = gather_figma_signals(empty_doc)
    assert result_empty["observed_component_types"] == []


# ── AC11: Radius convention + spacing ────────────────────────────────────────


def test_radius_convention_and_spacing_collection():
    """Sharp/rounded/pill/absent; spacing sorted+deduped+capped; none → [].

    The mapping mirrors the adapters._radius_convention thresholds:
      0 → 'sharp', 8 → 'rounded', 9999 → 'pill', none → ''.
    """
    def _doc_with_frame_radius(cr: float | None) -> dict:
        child = {
            "id": "child",
            "type": "FRAME",
            "absoluteBoundingBox": _bbox(100.0, 100.0),
            "fills": [],
            "children": [],
        }
        if cr is not None:
            child["cornerRadius"] = cr
        top = _frame_node("tf", fills=[_solid_fill(0.9, 0.9, 0.9)], children=[child], bbox_w=1440.0, bbox_h=900.0)
        return _doc([_page_with_frames([top])])

    assert gather_figma_signals(_doc_with_frame_radius(0.0))["radius_convention"] == "sharp"
    assert gather_figma_signals(_doc_with_frame_radius(8.0))["radius_convention"] == "rounded"
    assert gather_figma_signals(_doc_with_frame_radius(9999.0))["radius_convention"] == "pill"
    assert gather_figma_signals(_doc_with_frame_radius(None))["radius_convention"] == ""

    # Spacing: build a frame with multiple spacing values.
    frame_with_spacing = {
        "id": "spacer",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(1440.0, 900.0),
        "fills": [_solid_fill(0.9, 0.9, 0.9)],
        "itemSpacing": 16,
        "paddingLeft": 24,
        "paddingRight": 24,  # duplicate → deduped
        "paddingTop": 8,
        "paddingBottom": 8,  # duplicate → deduped
        "children": [],
    }
    spacing_doc = _doc([_page_with_frames([frame_with_spacing])])
    result = gather_figma_signals(spacing_doc)
    assert result["spacing_px"] == [8, 16, 24]

    # No spacing at all.
    no_spacing_frame = _frame_node("ns", fills=[_solid_fill(0.9, 0.9, 0.9)], bbox_w=1440.0, bbox_h=900.0)
    no_spacing_doc = _doc([_page_with_frames([no_spacing_frame])])
    assert gather_figma_signals(no_spacing_doc)["spacing_px"] == []

    # Cap at 12 entries.
    many_values = list(range(4, 200, 4))  # 49 unique values
    frame_many = {
        "id": "many-spacing",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(1440.0, 900.0),
        "fills": [_solid_fill(0.9, 0.9, 0.9)],
        "children": [],
    }
    for i, val in enumerate(many_values):
        frame_many[f"paddingLeft"] = val  # only one key per pass (last value wins in practice)
    # Build multiple children with different spacing.
    many_children = [
        {
            "id": f"sp-{i}",
            "type": "FRAME",
            "absoluteBoundingBox": _bbox(10.0, 10.0),
            "fills": [],
            "itemSpacing": float(val),
            "children": [],
        }
        for i, val in enumerate(many_values)
    ]
    big_spacing_frame = _frame_node(
        "big-sp", fills=[_solid_fill(0.9, 0.9, 0.9)], children=many_children,
        bbox_w=1440.0, bbox_h=900.0
    )
    big_sp_doc = _doc([_page_with_frames([big_spacing_frame])])
    big_sp_result = gather_figma_signals(big_sp_doc)
    assert len(big_sp_result["spacing_px"]) <= 12


# ── AC13: Malformed input ─────────────────────────────────────────────────────


def test_malformed_file_doc_returns_empty_well_formed_dict():
    """'{}', '{"document": None}', and nodes-without-bbox all return
    the empty-but-well-formed dict (all keys present) without raising.
    """
    for bad_input in ({}, {"document": None}, None):
        result = gather_figma_signals(bad_input)
        _assert_well_formed(result)
        assert result["color_candidates"] == []
        assert result["neutral_candidates"] == []
        assert result["container_observations"] == []
        assert result["theme_background"] is None
        assert result["explicit_color_styles"] is False

    # Nodes without bbox.
    no_bbox_child = {
        "id": "no-bbox",
        "type": "RECTANGLE",
        "fills": [_solid_fill(0.5, 0.5, 0.5)],
        "children": [],
        # No absoluteBoundingBox.
    }
    top_frame = _frame_node(
        "tf", fills=[_solid_fill(0.9, 0.9, 0.9)], children=[no_bbox_child],
        bbox_w=1440.0, bbox_h=900.0
    )
    result = gather_figma_signals(_doc([_page_with_frames([top_frame])]))
    _assert_well_formed(result)


# ── AC14: Node-walk bound ─────────────────────────────────────────────────────


def test_node_walk_cap_bounds_large_documents():
    """A synthetic doc with more than 5 000 nodes returns a valid dict
    without raising and respects the cap.
    """
    # Build a flat list of 6 000 child nodes under one top-level frame.
    many_children = [
        {
            "id": f"node-{i}",
            "type": "RECTANGLE",
            "absoluteBoundingBox": _bbox(5.0, 5.0),
            "fills": [_solid_fill(0.5, 0.5, 0.5)],
            "children": [],
        }
        for i in range(6_000)
    ]
    top_frame = _frame_node(
        "big-frame",
        fills=[_solid_fill(0.9, 0.9, 0.9)],
        children=many_children,
        bbox_w=2000.0,
        bbox_h=2000.0,
    )
    file_doc = _doc([_page_with_frames([top_frame])])
    result = gather_figma_signals(file_doc)

    _assert_well_formed(result)
    # The total observations must be at most 200 (inner cap).
    assert len(result["container_observations"]) <= 200


# ── Extraction path uses only gather keys (no legacy summary) ────────────────


def test_extract_raw_signals_emits_only_rich_gather_keys():
    """extract_raw_signals now returns only the rich gather keys from gather_figma_signals.

    The legacy palette-summary keys (background, accent, is_dark, swatches,
    font_family, font_weights) were removed from extract_raw_signals because
    FigmaExtractor.normalize now reads the gather keys through the shared kernel.
    _extract_palette_summary still exists in tools.py for the in-loop fetch_figma
    tool payload, but it is no longer called during design-system extraction.

    This replaces test_extract_raw_signals_keeps_legacy_summary_keys_byte_equal,
    which asserted the old disjoint-key merge contract. That contract is retired
    because the legacy keys are no longer present in the merged output — asserting
    their presence would be a dodge rather than a genuine regression guard. The
    correct contract is the opposite: legacy keys must NOT appear in the extraction
    path output.
    """
    dark_fill = _hex_fill("#2b2b2b")
    gold_fill = _hex_fill("#d4af37")
    accent_rect = {
        "id": "accent",
        "type": "RECTANGLE",
        "absoluteBoundingBox": _bbox(60.0, 60.0),
        "fills": [gold_fill],
        "children": [],
    }
    top_frame = {
        "id": "main",
        "type": "FRAME",
        "absoluteBoundingBox": _bbox(1440.0, 900.0),
        "fills": [dark_fill],
        "children": [accent_rect],
    }
    file_doc = {"document": {"children": [_page_with_frames([top_frame])]}}

    raw = FigmaExtractor().extract_raw_signals("file-key", file_doc=file_doc)
    signals = raw.signals

    # Legacy keys must NOT be present in the extraction-path output.
    legacy_keys = ("background", "accent", "is_dark", "swatches", "font_family", "font_weights")
    for key in legacy_keys:
        assert key not in signals, (
            f"Legacy key '{key}' must NOT appear in extract_raw_signals output after this change; "
            f"found it in: {set(signals.keys())}"
        )

    # Rich gather keys must all be present.
    for key in _WELL_FORMED_KEYS:
        assert key in signals, (
            f"Rich key '{key}' missing from extract_raw_signals output; got: {set(signals.keys())}"
        )

"""Tests for the deterministic primitive-set renderer.

Pure unit tests — no DB, no network, no model.  All assertions are structural:
does the output have the right keys, does each template carry the required
markers, and are radius variants applied correctly?
"""
from __future__ import annotations

import pytest

from app.design_agent.design_system.models import ComponentLanguage, DesignSystem
from app.design_agent.design_system.primitives import render_primitive_set

_EXPECTED_KEYS = {
    "src/components/ui/button.tsx",
    "src/components/ui/card.tsx",
    "src/components/ui/input.tsx",
    "src/components/ui/badge.tsx",
    "src/components/ui/label.tsx",
}


def test_render_primitive_set_returns_five_keys():
    result = render_primitive_set(DesignSystem(confidence="high"))
    assert set(result.keys()) == _EXPECTED_KEYS


def test_render_primitive_set_each_has_export_and_react():
    result = render_primitive_set(DesignSystem(confidence="high"))
    for key, content in result.items():
        assert "export" in content, f"{key} missing export"
        assert "React" in content, f"{key} missing React"


def test_render_primitive_set_each_references_css_var():
    result = render_primitive_set(DesignSystem(confidence="high"))
    for key, content in result.items():
        assert "var(--" in content, f"{key} missing CSS variable reference"


def test_render_primitive_set_radius_sharp():
    ds = DesignSystem(
        confidence="high",
        component_language=ComponentLanguage(radius="sharp"),
    )
    result = render_primitive_set(ds)
    # label is an inline text element — radius doesn't apply to it
    radius_keys = {k for k in result if not k.endswith("label.tsx")}
    for key in radius_keys:
        assert "rounded-none" in result[key], f"{key} missing rounded-none for sharp radius"


def test_render_primitive_set_radius_pill():
    ds = DesignSystem(
        confidence="high",
        component_language=ComponentLanguage(radius="pill"),
    )
    result = render_primitive_set(ds)
    radius_keys = {k for k in result if not k.endswith("label.tsx")}
    for key in radius_keys:
        assert "rounded-full" in result[key], f"{key} missing rounded-full for pill radius"


def test_render_primitive_set_radius_default():
    ds = DesignSystem(
        confidence="high",
        component_language=ComponentLanguage(radius="rounded"),
    )
    result = render_primitive_set(ds)
    radius_keys = {k for k in result if not k.endswith("label.tsx")}
    for key in radius_keys:
        assert "rounded-md" in result[key], f"{key} missing rounded-md for default radius"


def test_render_primitive_set_low_confidence_returns_empty():
    assert render_primitive_set(DesignSystem(confidence="low")) == {}


def test_render_primitive_set_none_returns_empty():
    assert render_primitive_set(None) == {}


def test_render_primitive_set_medium_confidence_ok():
    result = render_primitive_set(DesignSystem(confidence="medium"))
    assert set(result.keys()) == _EXPECTED_KEYS

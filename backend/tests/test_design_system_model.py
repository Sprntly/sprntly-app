"""Tests for the normalized DesignSystem model.

The model is the source-agnostic shape every design source normalizes into. A
bare instance must be a valid neutral baseline (the deterministic fallback), the
enum-like fields must reject junk, and the whole thing must survive a
dump/validate round-trip.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design_agent.design_system.models import DesignSystem


def test_default_design_system_constructs():
    ds = DesignSystem()
    # The baseline is complete: nested submodels are populated, not None.
    assert ds.tokens.colors.background
    assert ds.tokens.colors.semantic.success
    assert ds.tokens.fonts.weights  # non-empty default weight list
    assert ds.tokens.spacing_scale  # non-empty default spacing scale
    assert ds.component_language.buttons.style == "filled"
    assert ds.component_inventory == []
    assert ds.has_explicit_system is False
    assert ds.confidence == "low"


def test_literal_fields_reject_invalid_values():
    with pytest.raises(ValidationError):
        DesignSystem(confidence="excellent")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DesignSystem(component_language={"radius": "circular"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DesignSystem(component_language={"density": "roomy"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DesignSystem(component_language={"buttons": {"style": "gradient"}})  # type: ignore[arg-type]


def test_round_trips_through_dump_and_validate():
    original = DesignSystem(
        has_explicit_system=True,
        confidence="high",
        component_inventory=["button", "card", "input"],
    )
    dumped = original.model_dump()
    restored = DesignSystem.model_validate(dumped)
    assert restored == original
    # The nested structure survives the round-trip intact.
    assert restored.tokens.colors.semantic.warning == original.tokens.colors.semantic.warning


def test_component_inventory_accepts_a_list_of_type_strings():
    ds = DesignSystem(component_inventory=["button", "modal", "table", "badge"])
    assert ds.component_inventory == ["button", "modal", "table", "badge"]
    assert all(isinstance(name, str) for name in ds.component_inventory)

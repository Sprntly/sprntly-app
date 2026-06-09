"""Tests for the DesignSignals intermediate model.

DesignSignals is the source-agnostic gather output — the seam between
per-source GATHER and the shared HARDEN kernel. A bare instance must be a
valid, honestly-absent baseline; the role enum must reject anything that is
not a weight-ranked neutral; pass-through fields must survive round-trips; and
the module itself must stay pure data (no I/O, no provider imports).
"""
from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.design_agent.design_system.signals import (
    ColorCandidate,
    ContainerObservation,
    DesignSignals,
    FieldFlags,
    NeutralCandidate,
    TypographySignals,
)


def test_default_design_signals_constructs():
    sig = DesignSignals()
    assert sig.chromatic_candidates == []
    assert sig.neutral_candidates == []
    assert sig.container_observations == []
    assert sig.observed_component_types == []
    assert sig.spacing_scale == []
    assert sig.is_dark is False
    assert sig.background_hex == ""
    assert sig.provider == ""
    assert isinstance(sig.typography, TypographySignals)
    assert sig.typography.heading_family == ""
    assert sig.typography.body_family == ""
    assert sig.typography.weights == []
    assert sig.typography.radius_convention == ""


def test_color_candidate_constructs_with_weight_and_saturation():
    c = ColorCandidate(hex="#0e6b4f", weight=1.0, saturation=0.77)
    assert c.hex == "#0e6b4f"
    assert isinstance(c.weight, float)
    assert isinstance(c.saturation, float)
    assert c.weight == 1.0
    assert c.saturation == 0.77


def test_neutral_candidate_constructs_with_role():
    n = NeutralCandidate(role="surface", hex="#fafaf7")
    assert n.role == "surface"
    assert n.hex == "#fafaf7"
    assert n.weight == 0.0


def test_container_observation_defaults_false():
    obs = ContainerObservation()
    assert obs.has_border is False
    assert obs.has_shadow is False


def test_round_trips_through_dump_and_validate():
    original = DesignSignals(
        chromatic_candidates=[
            ColorCandidate(hex="#0e6b4f", weight=1.0, saturation=0.77),
            ColorCandidate(hex="#123456", weight=0.5, saturation=0.4),
        ],
        neutral_candidates=[
            NeutralCandidate(role="surface", hex="#fafaf7", weight=0.9),
            NeutralCandidate(role="border", hex="#dddddd", weight=0.3),
            NeutralCandidate(role="muted", hex="#888888", weight=0.2),
        ],
        container_observations=[
            ContainerObservation(has_border=True, has_shadow=False),
            ContainerObservation(has_border=False, has_shadow=True),
        ],
        observed_component_types=["button", "card", "input"],
        typography=TypographySignals(
            heading_family="Inter",
            body_family="Inter",
            weights=[400, 600, 700],
            radius_convention="rounded",
        ),
        is_dark=True,
        background_hex="#0b0b0b",
        foreground_hex="#f4f1ea",
        spacing_scale=[4, 8, 16, 24],
        gathered=FieldFlags(accent=True, neutrals=True, typography=True),
        explicit=FieldFlags(accent=True),
        provider="web",
    )
    dumped = original.model_dump()
    restored = DesignSignals.model_validate(dumped)
    assert restored == original


def test_passthrough_fields_present_and_default():
    bare = DesignSignals()
    assert bare.background_hex == ""
    assert bare.foreground_hex == ""
    assert bare.spacing_scale == []

    populated = DesignSignals(
        background_hex="#ffffff",
        foreground_hex="#1a1a1a",
        spacing_scale=[4, 8, 16],
    )
    restored = DesignSignals.model_validate(populated.model_dump())
    assert restored.background_hex == "#ffffff"
    assert restored.foreground_hex == "#1a1a1a"
    assert restored.spacing_scale == [4, 8, 16]
    assert restored == populated


def test_neutral_role_excludes_foreground_and_background():
    with pytest.raises(ValidationError):
        NeutralCandidate(role="foreground", hex="#111")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        NeutralCandidate(role="background", hex="#111")  # type: ignore[arg-type]
    # The three accepted roles construct cleanly.
    assert NeutralCandidate(role="surface", hex="#111").role == "surface"
    assert NeutralCandidate(role="border", hex="#111").role == "border"
    assert NeutralCandidate(role="muted", hex="#111").role == "muted"


def test_flags_default_to_absent():
    sig = DesignSignals()
    assert isinstance(sig.gathered, FieldFlags)
    assert isinstance(sig.explicit, FieldFlags)
    for flags in (sig.gathered, sig.explicit):
        assert flags.accent is False
        assert flags.neutrals is False
        assert flags.elevation is False
        assert flags.inventory is False
        assert flags.typography is False


def test_neutral_role_rejects_unknown_value():
    with pytest.raises(ValidationError):
        NeutralCandidate(role="brand", hex="#111")  # type: ignore[arg-type]


def test_module_has_no_io_side_effects():
    module = importlib.import_module("app.design_agent.design_system.signals")
    # The module references no I/O-capable provider library by name.
    referenced = set(vars(module))
    assert "playwright" not in referenced
    assert "requests" not in referenced
    assert "anthropic" not in referenced
    # Importing the module must not transitively pull any of them in. Checked in a
    # FRESH interpreter — asserting against this process's sys.modules would be
    # order-dependent (a sibling test that imports an adapter pollutes the global
    # table), so spawn a clean process that imports only this module and reports.
    backend_dir = pathlib.Path(__file__).resolve().parents[1]
    probe = (
        "import sys, importlib;"
        "importlib.import_module('app.design_agent.design_system.signals');"
        "leaked = [n for n in ('playwright', 'requests', 'anthropic')"
        " if any(m == n or m.startswith(n + '.') for m in sys.modules)];"
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"importing signals leaked: {result.stdout.strip()}"
    # Constructing instances performs no I/O — it simply builds.
    DesignSignals(chromatic_candidates=[ColorCandidate(hex="#0e6b4f")])


def test_no_baked_design_token_colors_in_source():
    source = pathlib.Path(
        importlib.import_module("app.design_agent.design_system.signals").__file__
    ).read_text()
    for literal in ("#2563eb", "#e5e7eb", "#6b7280"):
        assert literal not in source

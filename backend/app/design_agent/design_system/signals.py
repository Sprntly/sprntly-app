"""The normalized, source-agnostic gather output.

This module defines the intermediate shape that sits between per-source
GATHER and the shared HARDEN kernel. Each source (a website, a Figma file,
a code repository) reduces its raw, provider-specific observations into one
`DesignSignals` object: a flat bag of color candidates, neutral candidates,
container observations, typography, and per-field provenance.

`DesignSignals` decides nothing. It carries candidates plus provenance only;
the kernel is the sole consumer that resolves them into a finished design
system. This module is pure data — no fetching, no sampling, no model calls,
no I/O. Every field carries a deterministic default, so a bare instance is a
valid, honestly-absent baseline.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Roles the kernel RESOLVES via pick_neutrals (highest-weight candidate per role).
# Only these three — background and foreground are NON-heuristic pass-throughs
# (background_hex / foreground_hex below), not weight-ranked candidates, so they
# are deliberately NOT in this set.
NeutralRole = Literal["surface", "border", "muted"]


class ColorCandidate(BaseModel):
    """One observed chromatic-or-neutral color with a source-comparable weight."""

    hex: str                      # lower-case #rrggbb (gather layer normalizes)
    weight: float = 0.0           # source-comparable prominence (web: rendered area;
                                  #   figma: bbox-area x usage; github: usage count)
    saturation: float = 0.0       # [0,1] chromatic-ness; kernel drops neutrals by this


class NeutralCandidate(BaseModel):
    """A neutral color observed for a specific semantic role."""

    role: NeutralRole
    hex: str                      # lower-case #rrggbb
    weight: float = 0.0


class ContainerObservation(BaseModel):
    """One sampled container's separation treatment (for elevation derivation)."""

    has_border: bool = False
    has_shadow: bool = False


class TypographySignals(BaseModel):
    """Observed typography. Empty strings / empty list = not gathered."""

    heading_family: str = ""
    body_family: str = ""
    weights: list[int] = Field(default_factory=list)
    radius_convention: str = ""   # "sharp" | "rounded" | "pill" | "" (absent)


class FieldFlags(BaseModel):
    """Per-field provenance. `gathered` = a real candidate was observed for this
    field; `explicit` = it came from a documented system (published Figma styles,
    a real Tailwind/MUI theme, CSS vars) rather than inference. Both default to
    'absent' (False) so a field nobody populated is honestly absent, not assumed.
    """

    accent: bool = False
    neutrals: bool = False
    elevation: bool = False
    inventory: bool = False
    typography: bool = False


class DesignSignals(BaseModel):
    """Normalized, source-agnostic gather output. The seam between per-source
    GATHER and the shared HARDEN kernel. Decides nothing; carries candidates +
    provenance only.
    """

    chromatic_candidates: list[ColorCandidate] = Field(default_factory=list)
    neutral_candidates: list[NeutralCandidate] = Field(default_factory=list)
    container_observations: list[ContainerObservation] = Field(default_factory=list)
    observed_component_types: list[str] = Field(default_factory=list)
    typography: TypographySignals = Field(default_factory=TypographySignals)
    is_dark: bool = False
    # Pass-through fields — NON-heuristic signals the kernel maps straight onto the
    # DesignSystem without deciding anything. They live here (not in per-source
    # post-decoration of normalize) so harden() is the SOLE assembler of the
    # DesignSystem — otherwise web/figma/github each re-implement the same
    # post-decoration, the exact 3x duplication this workstream removes.
    background_hex: str = ""       # surface/page background; "" = absent
    foreground_hex: str = ""       # primary text color; "" = absent. NON-heuristic
                                   #   pass-through (web derives it from is_dark today:
                                   #   "#f4f1ea" if dark else "#1a1a1a"). Also fed to
                                   #   pick_neutrals as the border-derivation relative.
    spacing_scale: list[int] = Field(default_factory=list)  # px scale; [] = absent
    gathered: FieldFlags = Field(default_factory=FieldFlags)
    explicit: FieldFlags = Field(default_factory=FieldFlags)
    provider: str = ""            # "web" | "figma" | "github" — provenance only

"""The normalized, source-agnostic design-system shape.

Every design source — a connected Figma file, a code repository, a live
website — gets reduced to one common `DesignSystem` object so the rest of the
product can reason about a brand's look-and-feel without caring where it came
from. Source-specific extraction produces a raw bag of signals; normalization
folds that bag into this shape.

Every field carries a deterministic default, so a bare `DesignSystem()` is a
valid, neutral baseline. That baseline is the fallback used whenever a source
is missing, unreadable, or only partially understood — callers can always rely
on a complete object rather than guarding for missing keys.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SemanticColors(BaseModel):
    """Status colors used for success / error / warning states."""

    success: str = "#16a34a"
    error: str = "#dc2626"
    warning: str = "#d97706"


class Colors(BaseModel):
    """The core palette: surfaces, text, and the brand/accent colors."""

    background: str = "#ffffff"
    foreground: str = "#111111"
    # The card / raised-surface color. Defaults to the background so a bare
    # baseline reads as a single flat surface; sources that distinguish a
    # secondary surface (e.g. a Figma file's second-most-common fill) set it.
    surface: str = "#ffffff"
    primary: str = "#2563eb"
    accent: str = "#2563eb"
    muted: str = "#6b7280"
    border: str = "#e5e7eb"
    semantic: SemanticColors = Field(default_factory=SemanticColors)


class Fonts(BaseModel):
    """Typeface choices, available weights, and the relative type scale."""

    heading_family: str = "system-ui, sans-serif"
    body_family: str = "system-ui, sans-serif"
    weights: list[int] = Field(default_factory=lambda: [400, 600, 700])
    type_scale: str = "default"


class Tokens(BaseModel):
    """Design tokens: color, type, spacing, and surface conventions."""

    colors: Colors = Field(default_factory=Colors)
    is_dark: bool = False
    fonts: Fonts = Field(default_factory=Fonts)
    spacing_scale: list = Field(default_factory=lambda: [4, 8, 12, 16, 24, 32, 48])
    radius_convention: str = "rounded"
    elevation_style: str = "shadows"


class Buttons(BaseModel):
    """How buttons read: their fill style, corner radius, and text weight."""

    style: Literal["filled", "outline", "ghost"] = "filled"
    radius: str = "rounded"
    weight: str = "medium"


class ComponentLanguage(BaseModel):
    """The qualitative feel of the UI — the vocabulary a designer would use to
    describe it (how round, how dense, how it separates surfaces, etc.)."""

    radius: Literal["sharp", "rounded", "pill"] = "rounded"
    density: Literal["compact", "comfortable", "spacious"] = "comfortable"
    separation: Literal["borders", "shadows", "both"] = "shadows"
    buttons: Buttons = Field(default_factory=Buttons)
    accent_usage: Literal["heavy", "restrained"] = "restrained"
    brief: str = ""


class DesignSystem(BaseModel):
    """The complete normalized design system for one source.

    A default instance is a neutral, light-mode baseline that any caller can use
    as-is when no real source has been extracted yet.
    """

    tokens: Tokens = Field(default_factory=Tokens)
    component_language: ComponentLanguage = Field(default_factory=ComponentLanguage)
    # Component TYPES present in the source (e.g. "button", "card", "input") —
    # never component code.
    component_inventory: list[str] = Field(default_factory=list)
    has_explicit_system: bool = False
    confidence: Literal["high", "medium", "low"] = "low"

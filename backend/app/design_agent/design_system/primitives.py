"""Deterministic TSX primitive generator for the Design Agent.

Given a normalized `DesignSystem`, produces ready-to-use TypeScript/React
component files for the five standard UI primitives: button, card, input,
badge, and label.

Every generated component:
- Imports only React (no third-party dependencies)
- Uses CSS variables emitted by the prototype runner for brand colours
- Uses Tailwind utility classes for layout/sizing
- Applies the design system's radius token

Returns an empty dict when the design system is ``None`` or its confidence
level is ``"low"`` — callers must hold the result until a sufficiently
confident extraction is available.
"""
from __future__ import annotations

from app.design_agent.design_system.models import DesignSystem

_RADIUS_CLASS: dict[str, str] = {
    "sharp": "rounded-none",
    "pill": "rounded-full",
}
_RADIUS_DEFAULT = "rounded-md"


def _radius(ds: DesignSystem) -> str:
    return _RADIUS_CLASS.get(ds.component_language.radius, _RADIUS_DEFAULT)


def _button(radius_class: str) -> str:
    return f"""\
import React from "react";
interface ButtonProps {{ children?: React.ReactNode; onClick?: () => void; className?: string; }}
export function Button({{ children, onClick, className = "" }}: ButtonProps) {{
  return (
    <button
      onClick={{onClick}}
      className={{`inline-flex items-center justify-center px-4 py-2 text-sm font-medium {radius_class} transition-colors ${{className}}`}}
      style={{{{ background: "var(--primary)", color: "var(--background)" }}}}
    >
      {{children}}
    </button>
  );
}}
"""


def _card(radius_class: str) -> str:
    return f"""\
import React from "react";
interface CardProps {{ children?: React.ReactNode; className?: string; }}
export function Card({{ children, className = "" }}: CardProps) {{
  return (
    <div
      className={{`p-6 {radius_class} border ${{className}}`}}
      style={{{{ background: "var(--surface)", borderColor: "var(--border)", color: "var(--foreground)" }}}}
    >
      {{children}}
    </div>
  );
}}
"""


def _input(radius_class: str) -> str:
    return f"""\
import React from "react";
interface InputProps {{ placeholder?: string; value?: string; onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void; className?: string; }}
export function Input({{ placeholder, value, onChange, className = "" }}: InputProps) {{
  return (
    <input
      placeholder={{placeholder}}
      value={{value}}
      onChange={{onChange}}
      className={{`w-full px-3 py-2 text-sm border {radius_class} outline-none focus:ring-2 ${{className}}`}}
      style={{{{ background: "var(--background)", borderColor: "var(--border)", color: "var(--foreground)" }}}}
    />
  );
}}
"""


def _badge(radius_class: str) -> str:
    return f"""\
import React from "react";
interface BadgeProps {{ children?: React.ReactNode; className?: string; }}
export function Badge({{ children, className = "" }}: BadgeProps) {{
  return (
    <span
      className={{`inline-flex items-center px-2 py-0.5 text-xs font-medium {radius_class} ${{className}}`}}
      style={{{{ background: "var(--primary)", color: "var(--background)" }}}}
    >
      {{children}}
    </span>
  );
}}
"""


def _label(radius_class: str) -> str:  # noqa: ARG001 — radius not used on label but kept for uniform signature
    return """\
import React from "react";
interface LabelProps { children?: React.ReactNode; htmlFor?: string; className?: string; }
export function Label({ children, htmlFor, className = "" }: LabelProps) {
  return (
    <label
      htmlFor={htmlFor}
      className={`text-sm font-medium leading-none ${className}`}
      style={{ color: "var(--foreground)" }}
    >
      {children}
    </label>
  );
}
"""


def render_primitive_set(ds: DesignSystem | None) -> dict[str, str]:
    """Return a mapping of ``src/components/ui/<name>.tsx`` → TSX content.

    Produces exactly five keys for the standard primitive components.  Returns
    an empty dict when *ds* is ``None`` or its ``confidence`` is ``"low"`` —
    callers should treat an empty result as "no design-system available yet".
    """
    if ds is None or ds.confidence == "low":
        return {}

    rc = _radius(ds)
    return {
        "src/components/ui/button.tsx": _button(rc),
        "src/components/ui/card.tsx": _card(rc),
        "src/components/ui/input.tsx": _input(rc),
        "src/components/ui/badge.tsx": _badge(rc),
        "src/components/ui/label.tsx": _label(rc),
    }

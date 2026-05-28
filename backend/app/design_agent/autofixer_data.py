"""Known-good lists for the static AST autofixer (P1-10).

Plain Python data structures — no runtime configuration system, no schema
validation (per P1-10 AC13). These lists change independently of the
validator logic: a new shadcn component installs -> add to SHADCN_REGISTRY;
a new prototype dependency is allowed -> add to KNOWN_PACKAGES; a new shadcn
semantic colour token -> add to TAILWIND_SEMANTIC_TOKENS. Keeping them here
(separate from autofixer.py and autofixer.js) makes the diff for "I'm
allowing X" trivially small and clearly reviewable.

Single source of truth: `payload_data()` serialises these for the Node
companion (autofixer.js) via the stdin payload, so the JS holds no hardcoded
lists and the Python + Node sides can never drift.
"""
from __future__ import annotations

# shadcn/ui registry — component names importable as `@/components/ui/<name>`.
# An import of `@/components/ui/<name>` where <name> is NOT here is flagged as a
# hallucinated component (the agent invented a component that was never installed).
SHADCN_REGISTRY: frozenset[str] = frozenset({
    "accordion", "alert", "alert-dialog", "aspect-ratio", "avatar", "badge",
    "breadcrumb", "button", "calendar", "card", "carousel", "checkbox",
    "collapsible", "command", "context-menu", "dialog", "drawer",
    "dropdown-menu", "form", "hover-card", "input", "input-otp", "label",
    "menubar", "navigation-menu", "pagination", "popover", "progress",
    "radio-group", "resizable", "scroll-area", "select", "separator", "sheet",
    "sidebar", "skeleton", "slider", "sonner", "switch", "table", "tabs",
    "textarea", "toast", "toaster", "toggle", "toggle-group", "tooltip",
})

# Prototype dependency allowlist — bare npm packages the generated prototype is
# permitted to import. The output stack is React + Vite + TypeScript + Tailwind
# + shadcn/ui only (AD3). Any `@radix-ui/*` subpackage is allowed via prefix
# match in the validator (shadcn primitives pull many radix subpackages); it is
# NOT enumerated here. An import of a bare package outside this set (and not a
# radix subpackage) is flagged as a hallucinated import.
KNOWN_PACKAGES: frozenset[str] = frozenset({
    "react", "react-dom", "react-router-dom",
    "clsx", "tailwind-merge", "class-variance-authority",
    "lucide-react", "date-fns", "zod",
    "@radix-ui/react-slot",
})

# shadcn/ui semantic colour tokens. These look like Tailwind colour utilities
# (`bg-foreground`, `text-primary`, `bg-primary-100`) but do NOT exist in
# vanilla Tailwind without a `tailwind.config` theme extension — they are the
# single most common Tailwind hallucination from shadcn muscle memory. A colour
# utility (`bg-`, `text-`, `border-`, ...) whose colour segment is one of these
# is flagged. Real palette colours (`slate`, `blue`, ...) are intentionally NOT
# validated against a positive list — the fixer is deliberately permissive
# (per P1-10 Implementation Notes) to avoid false positives on the vast,
# config-extensible Tailwind class space; it targets the known failure mode.
TAILWIND_SEMANTIC_TOKENS: frozenset[str] = frozenset({
    "background", "foreground",
    "primary", "primary-foreground",
    "secondary", "secondary-foreground",
    "muted", "muted-foreground",
    "accent", "accent-foreground",
    "destructive", "destructive-foreground",
    "popover", "popover-foreground",
    "card", "card-foreground",
    "border", "input", "ring",
})


def payload_data() -> dict[str, list[str]]:
    """Serialise the known-good lists for the Node companion's stdin payload.

    Sorted for deterministic output (stable suggestions, reproducible tests).
    """
    return {
        "shadcn_registry": sorted(SHADCN_REGISTRY),
        "known_packages": sorted(KNOWN_PACKAGES),
        "semantic_tokens": sorted(TAILWIND_SEMANTIC_TOKENS),
    }

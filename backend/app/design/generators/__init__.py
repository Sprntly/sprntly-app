"""Prototype generators — one module per scenario.

  figma_generator    — Scenario A: design tokens + frames from Figma
  website_generator  — Scenario B: style inference from public HTML
  codebase_generator — Scenario C: stub, Post-V1

Each module exposes a single entrypoint returning a JSON skeleton:

    {
        "pages": [...],      # route / page tree
        "components": [...], # component inventory
        "style": {           # design tokens
            "colors": [...],
            "fonts":  [...],
        },
        "meta": {            # provenance for the UI / debugging
            "scenario": "...",
            "source":   "...",
        }
    }

The real Next.js codegen lands in a follow-up PR (Jide). The skeletons
are intentionally weak so the lifecycle, KG events, and the route
contract can land + ship indefinitely.
"""
from app.design.generators.codebase_generator import generate_from_codebase
from app.design.generators.figma_generator import generate_from_figma
from app.design.generators.website_generator import generate_from_website

__all__ = [
    "generate_from_figma",
    "generate_from_website",
    "generate_from_codebase",
]

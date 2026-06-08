"""Source-agnostic design-system extraction, normalization, and caching.

Importing this package registers the concrete source adapters (Figma, website)
in the shared `registry`, so any caller can resolve an adapter by provider name
without importing each adapter module explicitly.
"""
from app.design_agent.design_system import adapters as _adapters  # noqa: F401 — import side-effect: registers adapters

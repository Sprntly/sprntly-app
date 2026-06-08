"""The extraction contract every design source plugs into.

A source adapter (Figma, a code repository, a website) does two things:

  1. Reports a cheap *version* signal for a given source reference, so callers
     can tell whether a previously-cached design system is still current
     without doing the full, expensive extraction.
  2. Pulls the source's raw, provider-specific signals into a `RawSignals` bag.

A shared `normalize` step then folds any source's `RawSignals` into the common
`DesignSystem` shape, so the rest of the product never depends on which source
produced the result.

Adapters register themselves in the module-level `registry` and are looked up
by provider name. No adapters are registered here yet — the concrete Figma,
repository, and website adapters register on import in their own modules.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.design_agent.design_system.models import DesignSystem


class RawSignals(BaseModel):
    """A provider-specific bag of signals, captured before normalization.

    Deliberately permissive: each adapter stows whatever it pulled from its
    source under `signals`, plus enough provenance to trace it back. The shared
    `normalize` step is the only thing that interprets the contents, so the
    shape stays loose until a concrete adapter pins down what it collects.
    """

    provider: str = ""
    ref: str = ""
    signals: dict = Field(default_factory=dict)


@runtime_checkable
class DesignSystemExtractor(Protocol):
    """Interface a design source adapter implements.

    `category` is the broad kind of source ("design_tool", "codebase",
    "website"); `provider` is the specific source ("figma", "github", "web").
    The methods are intentionally split so a staleness check can stay cheap and
    independent of a full extraction.
    """

    category: str
    provider: str

    def current_version(self, ref: str) -> str:
        """Return a cheap version marker for `ref` (last-modified time, commit
        SHA, ETag, etc.) used to detect when a cached design system is stale."""
        ...

    def extract_raw_signals(self, ref: str) -> RawSignals:
        """Pull the source's raw, provider-specific signals for `ref`."""
        ...


def normalize(raw: RawSignals) -> DesignSystem:
    """Fold a source's raw signals into the common `DesignSystem` shape.

    Dispatches to the registered adapter for `raw.provider`, which owns the
    per-source mapping. When no adapter is registered for the provider (or the
    adapter does not expose its own `normalize`), this returns the deterministic
    baseline `DesignSystem`, so callers always get a complete, valid object.

    The optional model-written `brief` is layered in later; this step is
    deterministic mapping only.
    """
    adapter = registry.get(raw.provider)
    adapter_normalize = getattr(adapter, "normalize", None)
    if adapter_normalize is not None:
        return adapter_normalize(raw)
    return DesignSystem()


class ExtractorRegistry:
    """A small provider-name → adapter lookup.

    Adapters register on import; callers resolve one by provider name. A miss
    returns None so the caller can fall back to the baseline design system
    rather than raising.
    """

    def __init__(self) -> None:
        self._by_provider: dict[str, DesignSystemExtractor] = {}

    def register(self, extractor: DesignSystemExtractor) -> None:
        """Register `extractor` under its `provider` name (last write wins)."""
        self._by_provider[extractor.provider] = extractor

    def get(self, provider: str) -> DesignSystemExtractor | None:
        """Return the adapter registered for `provider`, or None if none is."""
        return self._by_provider.get(provider)


# Module-level singleton the adapters register against.
registry = ExtractorRegistry()

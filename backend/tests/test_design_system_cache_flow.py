"""Unit tests for the design-system cache + staleness flow in runner.py.

Covers _resolve_design_system:

  1. Cache HIT + version unchanged  → raw factory NOT called, upsert NOT called,
     cached design system returned.
  2. Cache HIT + version changed    → raw factory called once, upsert called once
     with source_version == current, FRESH design system returned.
  3. Cache HIT + version_factory returns None / raises
                                   → raw factory NOT called, cached design system
                                     returned (no churn on undeterminable probe).
  4. Cache HIT + stale but re-extract yields nothing (None raw)
                                   → upsert NOT called, cached design system
                                     returned (transient failure fallback).
  5. Cache MISS                     → raw factory called, upsert called with
                                     source_version == version_factory() value.
  6. Cache MISS + version_factory is None
                                   → upsert called with source_version=None.

All tests are hermetic: no network, no DB — lookup / upsert / normalize /
registry are all replaced by controlled fakes.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Import RawSignals from the REAL module before any patching happens.
from app.design_agent.design_system.extractors import RawSignals
from app.design_agent.runner import _resolve_design_system


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_ds(background: str = "#1a1a1a"):
    """Build a minimal DesignSystem-like object accepted by model_validate."""
    from app.design_agent.design_system.models import Colors, DesignSystem, Tokens

    return DesignSystem(
        tokens=Tokens(colors=Colors(background=background)),
        has_explicit_system=True,
        confidence="high",
    )


def _cached_row(ds, stored_version: str | None) -> dict:
    """Fake lookup_design_system return value with the fields the function reads."""
    return {
        "data": ds.model_dump(),
        "source_version": stored_version,
        "source_category": "design_tool",
        "source_provider": "figma",
        "source_ref": "file-key",
    }


@contextmanager
def _patched_internals(monkeypatch, *, cached_row, upsert_return, normalize_return):
    """Context manager that installs module-level fakes for the three heavy
    imports _resolve_design_system makes at runtime, then restores them cleanly.

    Uses monkeypatch to restore originals at the end of each test so that
    subsequent tests (including test_design_systems_db.py's fixture) see the
    real modules again.
    """
    fake_lookup = MagicMock(return_value=cached_row)
    fake_upsert = MagicMock(return_value=upsert_return or {})

    fake_adapter = SimpleNamespace(category="design_tool")
    fake_registry = SimpleNamespace(get=MagicMock(return_value=fake_adapter))
    fake_normalize = MagicMock(return_value=normalize_return)

    # Save originals so we can restore them.
    orig_db_mod = sys.modules.get("app.db.design_systems")
    orig_ext_mod = sys.modules.get("app.design_agent.design_system.extractors")
    orig_da_mod = sys.modules.get("app.design_agent.design_system")

    # Build thin stub modules for the two that _resolve_design_system imports at
    # runtime.  We keep the existing DesignSystem model module untouched so
    # model_validate works.
    fake_db_mod = types.ModuleType("app.db.design_systems")
    fake_db_mod.lookup_design_system = fake_lookup
    fake_db_mod.upsert_design_system = fake_upsert
    monkeypatch.setitem(sys.modules, "app.db.design_systems", fake_db_mod)

    fake_ext_mod = types.ModuleType("app.design_agent.design_system.extractors")
    fake_ext_mod.normalize = fake_normalize
    fake_ext_mod.registry = fake_registry
    # Preserve RawSignals so any test that already imported it keeps working.
    fake_ext_mod.RawSignals = RawSignals
    monkeypatch.setitem(
        sys.modules, "app.design_agent.design_system.extractors", fake_ext_mod
    )

    # The function does `import app.design_agent.design_system` for the adapter-
    # registration side effect; register a no-op stub if not already present.
    if "app.design_agent.design_system" not in sys.modules:
        monkeypatch.setitem(
            sys.modules,
            "app.design_agent.design_system",
            types.ModuleType("app.design_agent.design_system"),
        )

    fakes = SimpleNamespace(
        lookup=fake_lookup,
        upsert=fake_upsert,
        normalize=fake_normalize,
        registry=fake_registry,
    )
    yield fakes
    # monkeypatch handles teardown — setitem entries are restored after each test.


# ─── Test 1: cache HIT + version unchanged ────────────────────────────────────


def test_cache_hit_version_unchanged_returns_cached_no_extract_no_upsert(monkeypatch):
    """Cache HIT + current == stored → raw factory is NOT called, upsert is NOT
    called, and the cached design system is returned."""
    cached_ds = _make_ds("#111111")
    row = _cached_row(cached_ds, stored_version="v42")

    raw_calls = []
    raw_factory = lambda: (raw_calls.append(1), None)[1]  # noqa: E731

    version_calls = []
    version_factory = lambda: (version_calls.append(1), "v42")[1]  # noqa: E731

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#999999"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#111111"  # from cache, not normalize
    assert len(raw_calls) == 0, "raw factory must NOT be called when version matches"
    assert fakes.upsert.call_count == 0, "upsert must NOT be called when version matches"
    assert len(version_calls) == 1, "version factory called exactly once"


# ─── Test 2: cache HIT + version changed ──────────────────────────────────────


def test_cache_hit_version_changed_re_extracts_and_upserts_with_current_version(monkeypatch):
    """Cache HIT + current != stored → raw factory called once, upsert called
    once with source_version == current, freshly-normalized design system returned."""
    cached_ds = _make_ds("#111111")
    fresh_ds = _make_ds("#aabbcc")
    row = _cached_row(cached_ds, stored_version="v1")

    raw_calls = []
    fake_raw = RawSignals(provider="figma", ref="file-key", signals={"background": "#aabbcc"})

    def raw_factory():
        raw_calls.append(1)
        return fake_raw

    version_factory = lambda: "v2"  # noqa: E731

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#aabbcc"  # freshly normalized
    assert len(raw_calls) == 1, "raw factory must be called exactly once on stale HIT"
    assert fakes.upsert.call_count == 1, "upsert must be called exactly once"
    call_kwargs = fakes.upsert.call_args.kwargs
    assert call_kwargs["source_version"] == "v2"


# ─── Test 3: cache HIT + version_factory returns None ─────────────────────────


def test_cache_hit_version_factory_returns_none_no_churn(monkeypatch):
    """Cache HIT + version_factory() returns None (undeterminable) → raw factory
    NOT called, upsert NOT called, cached design system returned as-is."""
    cached_ds = _make_ds("#333333")
    row = _cached_row(cached_ds, stored_version="v5")

    raw_calls = []
    raw_factory = lambda: (raw_calls.append(1), None)[1]  # noqa: E731
    version_factory = lambda: None  # noqa: E731

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#333333"
    assert len(raw_calls) == 0
    assert fakes.upsert.call_count == 0


def test_cache_hit_version_factory_raises_no_churn(monkeypatch):
    """Cache HIT + version_factory() raises → treated as undeterminable, raw
    factory NOT called, upsert NOT called, cached design system returned."""
    cached_ds = _make_ds("#444444")
    row = _cached_row(cached_ds, stored_version="v9")

    raw_calls = []
    raw_factory = lambda: (raw_calls.append(1), None)[1]  # noqa: E731

    def version_factory():
        raise RuntimeError("probe network failure")

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#444444"
    assert len(raw_calls) == 0
    assert fakes.upsert.call_count == 0


# ─── Test 4: cache HIT + stale + re-extract yields None ───────────────────────


def test_cache_hit_stale_re_extract_none_falls_back_to_cached(monkeypatch):
    """Cache HIT + stale version + raw_signals_factory returns None → upsert NOT
    called (nothing to store), cached design system returned to avoid discarding
    a good cached row on a transient extraction failure."""
    cached_ds = _make_ds("#555555")
    row = _cached_row(cached_ds, stored_version="v1")

    raw_calls = []

    def raw_factory():
        raw_calls.append(1)
        return None  # extraction failed / empty

    version_factory = lambda: "v2"  # noqa: E731

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#555555"  # fell back to cache
    assert len(raw_calls) == 1, "raw factory was tried"
    assert fakes.upsert.call_count == 0, "upsert skipped when raw is None"


# ─── Test 5: cache MISS + version_factory returns a value ─────────────────────


def test_cache_miss_upserts_with_current_version(monkeypatch):
    """Cache MISS → raw factory called, normalize called, upsert called with
    source_version equal to what version_factory() returned."""
    fresh_ds = _make_ds("#aabbcc")

    raw_calls = []
    fake_raw = RawSignals(provider="figma", ref="file-key", signals={"background": "#aabbcc"})

    def raw_factory():
        raw_calls.append(1)
        return fake_raw

    version_factory = lambda: "v10"  # noqa: E731

    with _patched_internals(
        monkeypatch,
        cached_row=None,  # simulate a cache miss
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
        )

    assert result is not None
    assert result.tokens.colors.background == "#aabbcc"
    assert len(raw_calls) == 1
    assert fakes.upsert.call_count == 1
    call_kwargs = fakes.upsert.call_args.kwargs
    assert call_kwargs["source_version"] == "v10"


# ─── Test 6: cache MISS + version_factory is None ─────────────────────────────


def test_cache_miss_version_factory_none_upserts_with_source_version_none(monkeypatch):
    """Cache MISS + no version_factory → upsert called with source_version=None
    (defensive: the caller passed None when no version probe is available)."""
    fresh_ds = _make_ds("#112233")
    fake_raw = RawSignals(provider="web", ref="https://example.com", signals={})

    raw_calls = []

    def raw_factory():
        raw_calls.append(1)
        return fake_raw

    with _patched_internals(
        monkeypatch,
        cached_row=None,
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="web",
            source_ref="https://example.com",
            raw_signals_factory=raw_factory,
            version_factory=None,
        )

    assert result is not None
    assert len(raw_calls) == 1
    assert fakes.upsert.call_count == 1
    call_kwargs = fakes.upsert.call_args.kwargs
    assert call_kwargs["source_version"] is None


def test_force_re_extracts_without_lookup_and_upserts_with_current_version(monkeypatch):
    """Force refresh bypasses lookup, extracts once, and stores the fresh result
    with the probed source version."""
    fresh_ds = _make_ds("#abcdef")
    fake_raw = RawSignals(provider="figma", ref="file-key", signals={"background": "#abcdef"})

    raw_calls = []
    version_calls = []

    def raw_factory():
        raw_calls.append(1)
        return fake_raw

    def version_factory():
        version_calls.append(1)
        return "v-force"

    with _patched_internals(
        monkeypatch,
        cached_row=_cached_row(_make_ds("#111111"), stored_version="v-old"),
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
            force=True,
        )

    assert result is not None
    assert result.tokens.colors.background == "#abcdef"
    assert len(version_calls) == 1
    assert len(raw_calls) == 1
    assert fakes.lookup.call_count == 0
    assert fakes.upsert.call_count == 1
    call_kwargs = fakes.upsert.call_args.kwargs
    assert call_kwargs["company_id"] == "co-1"
    assert call_kwargs["source_category"] == "design_tool"
    assert call_kwargs["source_provider"] == "figma"
    assert call_kwargs["source_ref"] == "file-key"
    assert call_kwargs["source_version"] == "v-force"
    assert call_kwargs["data"] == fresh_ds.model_dump()
    assert call_kwargs["has_explicit_system"] == fresh_ds.has_explicit_system
    assert call_kwargs["confidence"] == fresh_ds.confidence
    assert call_kwargs["extracted_at"] is None


def test_force_returns_none_without_upsert_when_raw_signals_are_missing(monkeypatch):
    """Force refresh leaves cache untouched when there is nothing to extract."""
    raw_calls = []
    version_calls = []

    def raw_factory():
        raw_calls.append(1)
        return None

    def version_factory():
        version_calls.append(1)
        return "v-force"

    with _patched_internals(
        monkeypatch,
        cached_row=_cached_row(_make_ds("#111111"), stored_version="v-old"),
        upsert_return={},
        normalize_return=_make_ds("#abcdef"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=raw_factory,
            version_factory=version_factory,
            force=True,
        )

    assert result is None
    assert len(version_calls) == 1
    assert len(raw_calls) == 1
    assert fakes.lookup.call_count == 0
    assert fakes.upsert.call_count == 0


def test_force_does_not_bypass_missing_source_guard(monkeypatch):
    """Force refresh still requires a company, provider, and source reference."""
    raw_calls = []
    version_calls = []

    with _patched_internals(
        monkeypatch,
        cached_row=None,
        upsert_return={},
        normalize_return=_make_ds(),
    ) as fakes:
        result = _resolve_design_system(
            company_id=None,
            provider="figma",
            source_ref="file-key",
            raw_signals_factory=lambda: raw_calls.append(1) or None,
            version_factory=lambda: version_calls.append(1) or "v1",
            force=True,
        )

    assert result is None
    assert len(raw_calls) == 0
    assert len(version_calls) == 0
    assert fakes.lookup.call_count == 0
    assert fakes.upsert.call_count == 0


# ─── Test 7: no provider → returns None early (no DB / factory calls) ─────────


def test_no_provider_returns_none_without_calling_factories(monkeypatch):
    """When company_id/provider/source_ref is absent the function returns None
    immediately without touching the DB or any factory."""
    raw_calls = []
    version_calls = []

    with _patched_internals(
        monkeypatch,
        cached_row=None,
        upsert_return={},
        normalize_return=_make_ds(),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider=None,
            source_ref=None,
            raw_signals_factory=lambda: raw_calls.append(1) or None,
            version_factory=lambda: version_calls.append(1) or "v1",
        )

    assert result is None
    assert len(raw_calls) == 0
    assert len(version_calls) == 0
    assert fakes.lookup.call_count == 0


# ─── Test 8: _design_source_for_generation returns 4-tuple ────────────────────


def test_design_source_for_generation_figma_returns_four_tuple(monkeypatch):
    """Figma branch returns a 4-tuple and version_factory uses a FRESH instance
    with the access token set (never the registry singleton)."""
    from app.design_agent.runner import _design_source_for_generation

    fresh_instances: list = []

    class _FakeFigmaExtractor:
        def __init__(self):
            fresh_instances.append(self)
            self.access_token = None

        def current_version(self, ref: str) -> str:
            return f"v-{ref}"

        def extract_raw_signals(self, ref, file_doc=None):
            return RawSignals(provider="figma", ref=ref, signals={})

    import app.design_agent.design_system.adapters as _adapters_mod

    monkeypatch.setattr(_adapters_mod, "FigmaExtractor", _FakeFigmaExtractor)

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key="my-file",
        figma_access_token="tok-abc",
        website_url=None,
        website_sample=None,
    )

    assert provider == "figma"
    assert source_ref == "my-file"
    assert raw_factory is not None
    assert version_factory is not None

    # Calling version_factory must create a fresh instance and set the token.
    version = version_factory()
    assert len(fresh_instances) >= 1
    last = fresh_instances[-1]
    assert last.access_token == "tok-abc"
    assert version == "v-my-file"


def test_design_source_for_generation_website_returns_four_tuple():
    """Website branch returns a 4-tuple (no token needed)."""
    from app.design_agent.runner import _design_source_for_generation

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url="https://example.com",
        website_sample=None,
    )

    assert provider == "web"
    assert source_ref == "https://example.com"
    assert raw_factory is not None
    assert version_factory is not None


def test_design_source_for_generation_github_preserves_source_ref_and_installation(monkeypatch):
    """GitHub branch returns a stable source_ref and binds installation context into
    fresh extractor instances for both extraction and version probing."""
    from app.design_agent.runner import _design_source_for_generation

    calls: list[tuple[str, int, str]] = []

    class _FakeGithubExtractor:
        def __init__(self, installation_id):
            self.installation_id = installation_id

        def current_version(self, ref: str) -> str:
            calls.append(("version", self.installation_id, ref))
            return f"sha-for-{ref}"

        def extract_raw_signals(self, ref: str):
            calls.append(("raw", self.installation_id, ref))
            return RawSignals(provider="github", ref=ref, signals={"files_present": []})

    import app.design_agent.design_system.adapters as _adapters_mod

    monkeypatch.setattr(_adapters_mod, "GithubExtractor", _FakeGithubExtractor)

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url=None,
        website_sample=None,
        github_repo="org/repo@develop",
        github_installation_id=987,
    )

    assert provider == "github"
    assert source_ref == "org/repo@develop"
    assert raw_factory is not None
    assert version_factory is not None
    assert version_factory() == "sha-for-org/repo@develop"
    raw = raw_factory()
    assert raw.provider == "github"
    assert calls == [
        ("version", 987, "org/repo@develop"),
        ("raw", 987, "org/repo@develop"),
    ]


def test_github_cache_miss_upserts_codebase_category_source_ref_and_version(monkeypatch):
    """Cache MISS for provider=github stores source_category=codebase, stable
    source_ref, and the probed GitHub source_version."""
    fresh_ds = _make_ds("#223344")
    fake_raw = RawSignals(provider="github", ref="org/repo@main", signals={"files_present": []})

    raw_calls = []

    def raw_factory():
        raw_calls.append(1)
        return fake_raw

    class _FakeGithubAdapter:
        category = "codebase"

    with _patched_internals(
        monkeypatch,
        cached_row=None,
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        fakes.registry.get.return_value = _FakeGithubAdapter()
        result = _resolve_design_system(
            company_id="co-1",
            provider="github",
            source_ref="org/repo@main",
            raw_signals_factory=raw_factory,
            version_factory=lambda: "sha-123",
        )

    assert result is not None
    assert len(raw_calls) == 1
    assert fakes.upsert.call_count == 1
    call_kwargs = fakes.upsert.call_args.kwargs
    assert call_kwargs["company_id"] == "co-1"
    assert call_kwargs["source_category"] == "codebase"
    assert call_kwargs["source_provider"] == "github"
    assert call_kwargs["source_ref"] == "org/repo@main"
    assert call_kwargs["source_version"] == "sha-123"
    assert call_kwargs["data"] == fresh_ds.model_dump()


def test_github_cache_hit_unchanged_skips_extraction(monkeypatch):
    cached_ds = _make_ds("#334455")
    row = _cached_row(cached_ds, stored_version="sha-unchanged")
    row["source_provider"] = "github"
    row["source_category"] = "codebase"
    row["source_ref"] = "org/repo"

    raw_calls = []

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="github",
            source_ref="org/repo",
            raw_signals_factory=lambda: raw_calls.append(1) or RawSignals(provider="github"),
            version_factory=lambda: "sha-unchanged",
        )

    assert result is not None
    assert result.tokens.colors.background == "#334455"
    assert raw_calls == []
    assert fakes.upsert.call_count == 0


def test_github_cache_hit_changed_re_extracts_and_updates_version(monkeypatch):
    cached_ds = _make_ds("#334455")
    fresh_ds = _make_ds("#abcdef")
    row = _cached_row(cached_ds, stored_version="sha-old")
    row["source_provider"] = "github"
    row["source_category"] = "codebase"
    row["source_ref"] = "org/repo"
    raw_calls = []

    class _FakeGithubAdapter:
        category = "codebase"

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=fresh_ds,
    ) as fakes:
        fakes.registry.get.return_value = _FakeGithubAdapter()
        result = _resolve_design_system(
            company_id="co-1",
            provider="github",
            source_ref="org/repo",
            raw_signals_factory=lambda: raw_calls.append(1) or RawSignals(provider="github"),
            version_factory=lambda: "sha-new",
        )

    assert result is not None
    assert result.tokens.colors.background == "#abcdef"
    assert raw_calls == [1]
    assert fakes.upsert.call_count == 1
    assert fakes.upsert.call_args.kwargs["source_version"] == "sha-new"
    assert fakes.upsert.call_args.kwargs["source_category"] == "codebase"


def test_github_cache_hit_version_probe_failure_uses_cached_without_extract(monkeypatch):
    cached_ds = _make_ds("#445566")
    row = _cached_row(cached_ds, stored_version="sha-cached")
    row["source_provider"] = "github"
    row["source_category"] = "codebase"
    row["source_ref"] = "org/repo"
    raw_calls = []

    with _patched_internals(
        monkeypatch,
        cached_row=row,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="github",
            source_ref="org/repo",
            raw_signals_factory=lambda: raw_calls.append(1) or RawSignals(provider="github"),
            version_factory=lambda: (_ for _ in ()).throw(RuntimeError("github down")),
        )

    assert result is not None
    assert result.tokens.colors.background == "#445566"
    assert raw_calls == []
    assert fakes.upsert.call_count == 0


def test_github_cache_miss_raw_failure_returns_none_without_upsert(monkeypatch):
    with _patched_internals(
        monkeypatch,
        cached_row=None,
        upsert_return={},
        normalize_return=_make_ds("#ffffff"),
    ) as fakes:
        result = _resolve_design_system(
            company_id="co-1",
            provider="github",
            source_ref="org/repo",
            raw_signals_factory=lambda: None,
            version_factory=lambda: "sha-123",
        )

    assert result is None
    assert fakes.upsert.call_count == 0


def test_design_source_for_generation_none_branch_returns_four_none_tuple():
    """No source → 4-tuple of Nones."""
    from app.design_agent.runner import _design_source_for_generation

    result = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url=None,
        website_sample=None,
    )

    assert result == (None, None, None, None)

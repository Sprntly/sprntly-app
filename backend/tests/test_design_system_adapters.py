"""Tests for the Figma + website design-source adapters and the unified pre-seed.

These cover the source-specific mapping into the common `DesignSystem` shape and
the source-agnostic CSS pre-seed:

  * a Figma palette summary maps onto the expected tokens (and registers itself),
  * a website sample maps onto the expected tokens (colors, radius, spacing),
  * a low-confidence website sample (the extractor's `None` sentinel) falls back
    to the neutral baseline,
  * the unified renderer reproduces the long-standing Figma palette CSS exactly,
  * the website pre-seed reaches the same tokens the design-system path expects.

Pure unit tests — no DB, no browser, no model. The cache flow is exercised in
test_design_system_cache_flow.py.
"""
from __future__ import annotations

from app.connectors import figma_oauth
from app.design_agent.design_system.adapters import FigmaExtractor, WebExtractor
from app.design_agent.design_system.extractors import RawSignals, normalize, registry
from app.design_agent.design_system.models import DesignSystem
from app.design_agent.runner import (
    _design_source_for_generation,
    _render_design_system_css,
    _render_palette_css,
    _should_pre_seed,
)


# A charcoal / gold / cream Figma palette — the long-standing regression shape.
_FIGMA_PALETTE = {
    "background": "#2b2b2b",   # charcoal
    "accent": "#d4af37",       # gold
    "is_dark": True,
    "swatches": ["#2b2b2b", "#3a3a3a", "#f4f1ea"],  # charcoal / surface / cream
    "font_family": "Inter",
    "font_weights": [400, 700],
}


# ─── Registration ────────────────────────────────────────────────────────


def test_adapters_register_themselves_on_import():
    assert isinstance(registry.get("figma"), FigmaExtractor)
    assert isinstance(registry.get("web"), WebExtractor)
    assert registry.get("figma").category == "design_tool"
    assert registry.get("web").category == "website"


def test_module_normalize_dispatches_by_provider():
    raw = RawSignals(provider="figma", ref="k", signals=_FIGMA_PALETTE)
    ds = normalize(raw)
    assert ds.tokens.colors.background == "#2b2b2b"
    # An unknown provider falls back to the neutral baseline (no adapter).
    assert normalize(RawSignals(provider="nope", ref="x")) == DesignSystem()


def test_generation_source_selection_preserves_figma_website_github_precedence():
    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key="figma-file",
        figma_access_token="figma-token",
        website_url="https://brand.example",
        website_sample={},
        github_repo="org/repo",
        github_installation_id=123,
    )
    assert provider == "figma"
    assert source_ref == "figma-file"
    assert raw_factory is not None
    assert version_factory is not None

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url="https://brand.example",
        website_sample={},
        github_repo="org/repo",
        github_installation_id=123,
    )
    assert provider == "web"
    assert source_ref == "https://brand.example"
    assert raw_factory is not None
    assert version_factory is not None

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url=None,
        website_sample=None,
        github_repo="org/repo",
        github_installation_id=123,
    )
    assert provider == "github"
    assert source_ref == "org/repo"
    assert raw_factory is not None
    assert version_factory is not None

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=None,
        figma_access_token=None,
        website_url=None,
        website_sample=None,
        github_repo="org/repo",
        github_installation_id=None,
    )
    assert (provider, source_ref, raw_factory, version_factory) == (None, None, None, None)


# ─── Figma mapping ───────────────────────────────────────────────────────


def test_figma_signals_map_to_expected_tokens():
    ds = FigmaExtractor().normalize(
        RawSignals(provider="figma", ref="file-key", signals=_FIGMA_PALETTE)
    )
    c = ds.tokens.colors
    assert c.background == "#2b2b2b"
    assert c.accent == "#d4af37"
    assert c.primary == "#d4af37"
    assert c.surface == "#3a3a3a"   # swatch index 1
    assert c.muted == "#f4f1ea"     # swatch index 2
    assert ds.tokens.is_dark is True
    assert c.foreground == "#f4f1ea"  # light text on a dark background
    assert ds.tokens.fonts.heading_family == "Inter"
    assert ds.tokens.fonts.weights == [400, 700]
    assert ds.has_explicit_system is False
    assert ds.confidence != "low"
    assert ds.confidence == "high"   # palette + accent + font = rich signal


def test_figma_without_usable_background_falls_back_to_baseline():
    ds = FigmaExtractor().normalize(
        RawSignals(provider="figma", ref="k", signals={"background": None})
    )
    assert ds == DesignSystem()
    assert ds.has_explicit_system is False


def test_figma_current_version_returns_none_without_token():
    assert FigmaExtractor().current_version("file-key") is None


# ─── Website mapping ─────────────────────────────────────────────────────


def _web_sample(**over) -> dict:
    base = {
        "primary_color": "rgb(37,99,235)",
        "background_color": "#0b0f19",
        "heading_font_family": "Inter",
        "body_font_family": "Roboto",
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["16px 24px", "8px"],
        "logo_url": "https://cdn.example.com/logo.png",
    }
    base.update(over)
    return base


def test_website_signals_map_to_expected_tokens():
    ds = WebExtractor().normalize(
        RawSignals(provider="web", ref="https://acme.com", signals=_web_sample())
    )
    c = ds.tokens.colors
    assert c.primary == "#2563eb"    # rgb() folded to hex
    assert c.accent == "#2563eb"
    assert c.background == "#0b0f19"
    assert ds.tokens.is_dark is True
    assert ds.tokens.fonts.heading_family == "Inter"
    assert ds.tokens.fonts.body_family == "Roboto"
    assert ds.tokens.radius_convention == "rounded"
    assert ds.tokens.spacing_scale == [8, 16, 24]
    assert ds.has_explicit_system is False
    assert ds.confidence != "low"
    assert ds.confidence == "medium"


def test_website_transparent_primary_is_dropped():
    ds = WebExtractor().normalize(
        RawSignals(provider="web", ref="x", signals=_web_sample(primary_color="rgba(0,0,0,0)"))
    )
    # A zero-alpha primary never reaches the tokens — primary stays the default.
    assert ds.tokens.colors.primary == DesignSystem().tokens.colors.primary
    # ... and with no usable brand color the source is not "explicit".
    assert ds.has_explicit_system is False


def test_website_radius_conventions():
    web = WebExtractor()

    def radius(value):
        return web.normalize(
            RawSignals(provider="web", ref="x", signals=_web_sample(border_radius_convention=value))
        ).tokens.radius_convention

    assert radius("0px") == "sharp"
    assert radius("8px") == "rounded"
    assert radius("9999px") == "pill"
    assert radius("50%") == "pill"


def test_website_none_sample_falls_back_to_baseline():
    # The extractor's None sentinel is captured as an empty bag → baseline.
    raw = WebExtractor().extract_raw_signals("https://acme.com", sample=None)
    ds = WebExtractor().normalize(raw)
    assert ds == DesignSystem()
    assert ds.has_explicit_system is False
    assert ds.confidence == "low"


class _FakeWebResp:
    def __init__(self, headers: dict[str, str] | None = None, ok: bool = True):
        self.ok = ok
        self.headers = headers or {}


def test_website_current_version_returns_etag(monkeypatch):
    calls: list[str] = []

    def _fake_head(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 10
        assert kwargs["allow_redirects"] is True
        return _FakeWebResp(
            {"ETag": '"site-v1"', "Last-Modified": "Sun, 07 Jun 2026 12:00:00 GMT"}
        )

    monkeypatch.setattr(figma_oauth.requests, "head", _fake_head)

    assert WebExtractor().current_version("https://acme.com") == '"site-v1"'
    assert calls == ["https://acme.com"]


def test_website_current_version_returns_last_modified_without_etag(monkeypatch):
    def _fake_head(url, **kwargs):
        return _FakeWebResp({"Last-Modified": "Sun, 07 Jun 2026 12:00:00 GMT"})

    monkeypatch.setattr(figma_oauth.requests, "head", _fake_head)

    assert (
        WebExtractor().current_version("https://acme.com")
        == "Sun, 07 Jun 2026 12:00:00 GMT"
    )


def test_website_current_version_returns_stable_ttl_marker_without_headers(monkeypatch):
    def _fake_head(url, **kwargs):
        return _FakeWebResp({})

    monkeypatch.setattr(figma_oauth.requests, "head", _fake_head)

    first = WebExtractor().current_version("https://acme.com")
    second = WebExtractor().current_version("https://acme.com")

    assert first is not None
    assert first.startswith("ttl-")
    assert second == first


def test_website_current_version_blank_ref_returns_none_without_http(monkeypatch):
    calls: list[str] = []

    def _fake_head(url, **kwargs):
        calls.append(url)
        return _FakeWebResp({"ETag": '"site-v1"'})

    monkeypatch.setattr(figma_oauth.requests, "head", _fake_head)

    assert WebExtractor().current_version("   ") is None
    assert calls == []


def test_website_current_version_returns_none_when_http_raises(monkeypatch):
    def _fake_head(url, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(figma_oauth.requests, "head", _fake_head)

    assert WebExtractor().current_version("https://acme.com") is None


# ─── Unified pre-seed equivalence ────────────────────────────────────────


def test_should_pre_seed_uses_confidence_not_explicit_system():
    assert _should_pre_seed(None) is False
    assert _should_pre_seed(DesignSystem(confidence="low")) is False
    assert _should_pre_seed(DesignSystem(confidence="medium")) is True
    assert _should_pre_seed(DesignSystem(confidence="high")) is True


def test_unified_render_matches_legacy_figma_palette_css_byte_for_byte():
    """The whole point of folding Figma behind the adapter: the rendered CSS must
    stay identical to the long-standing Figma palette pre-seed."""
    legacy = _render_palette_css(_FIGMA_PALETTE)
    ds = FigmaExtractor().normalize(
        RawSignals(provider="figma", ref="k", signals=_FIGMA_PALETTE)
    )
    assert _render_design_system_css(ds) == legacy


def test_unified_render_matches_legacy_for_no_font_and_nongoogle_font():
    cases = [
        {"background": "#101820", "accent": "#ff5a36", "is_dark": True,
         "swatches": ["#101820", "#1c2630"], "font_family": None, "font_weights": []},
        {"background": "#ffffff", "accent": "#2563eb", "is_dark": False,
         "swatches": ["#ffffff", "#f3f4f6", "#9ca3af"],
         "font_family": "Helvetica Neue", "font_weights": [400, 600]},
    ]
    for palette in cases:
        ds = FigmaExtractor().normalize(
            RawSignals(provider="figma", ref="k", signals=palette)
        )
        assert _render_design_system_css(ds) == _render_palette_css(palette)


def test_website_charcoal_palette_yields_dark_index_css():
    """A website-sourced dark palette pre-seeds the same dark CSS a Figma source
    would — the Scenario B parity win."""
    ds = WebExtractor().normalize(
        RawSignals(provider="web", ref="x",
                   signals=_web_sample(background_color="#2b2b2b", primary_color="#d4af37"))
    )
    css = _render_design_system_css(ds)
    assert "--background: #2b2b2b;" in css
    assert "--accent: #d4af37;" in css
    assert "--foreground: #f4f1ea;" in css  # light text derived from dark bg

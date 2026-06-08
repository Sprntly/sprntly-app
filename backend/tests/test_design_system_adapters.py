"""Tests for the Figma + website + GitHub design-source adapters and the unified pre-seed.

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

import base64
import socket

import pytest

from app.connectors import figma_oauth
from app.design_agent.design_system.adapters import FigmaExtractor, GithubExtractor, WebExtractor
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
    assert isinstance(registry.get("github"), GithubExtractor)
    assert registry.get("figma").category == "design_tool"
    assert registry.get("web").category == "website"
    assert registry.get("github").category == "codebase"


def test_module_normalize_dispatches_by_provider():
    raw = RawSignals(provider="figma", ref="k", signals=_FIGMA_PALETTE)
    ds = normalize(raw)
    assert ds.tokens.colors.background == "#2b2b2b"
    gh = normalize(
        RawSignals(
            provider="github",
            ref="org/repo",
            signals={
                "files_present": ["tokens.json"],
                "colors": {"primary": "#123456"},
                "fonts": [],
            },
        )
    )
    assert gh.tokens.colors.primary == "#123456"
    assert gh.has_explicit_system is True
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


class _FakeResp:
    def __init__(self, payload=None, ok=True, status_code=200):
        self._payload = payload or {}
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


def _contents(text: str) -> dict:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"encoding": "base64", "content": encoded, "size": len(text)}


def test_github_current_version_uses_default_branch_sha(monkeypatch):
    calls: list[str] = []

    def fake_headers(installation_id):
        assert installation_id == 987
        return {"Authorization": "Bearer install-token"}

    def fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["Authorization"] == "Bearer install-token"
        assert kwargs["timeout"] == 15
        if url.endswith("/repos/org/repo"):
            return _FakeResp({"default_branch": "main", "pushed_at": "fallback"})
        if url.endswith("/repos/org/repo/commits/main"):
            return _FakeResp({"sha": "abc123"})
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr("app.connectors.github_app.headers_for_installation", fake_headers)
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    assert GithubExtractor(installation_id=987).current_version("org/repo") == "abc123"
    assert calls == [
        "https://api.github.com/repos/org/repo",
        "https://api.github.com/repos/org/repo/commits/main",
    ]


def test_github_current_version_uses_explicit_branch_and_falls_back_to_pushed_at(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/repos/org/repo"):
            return _FakeResp({"default_branch": "main", "pushed_at": "2026-06-08T00:00:00Z"})
        if url.endswith("/repos/org/repo/commits/feature%2Fbranch"):
            return _FakeResp(ok=False, status_code=503)
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    assert (
        GithubExtractor(installation_id=987).current_version("org/repo@feature/branch")
        == "2026-06-08T00:00:00Z"
    )
    assert calls == [
        "https://api.github.com/repos/org/repo",
        "https://api.github.com/repos/org/repo/commits/feature%2Fbranch",
    ]


def test_github_current_version_api_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )
    monkeypatch.setattr(
        "app.connectors.github_app.requests.get",
        lambda url, **kwargs: _FakeResp(ok=False, status_code=500),
    )

    assert GithubExtractor(installation_id=987).current_version("org/repo") is None
    assert GithubExtractor(installation_id=None).current_version("org/repo") is None


def test_codebase_current_version_returns_branch_head_sha(monkeypatch):
    """The GitHub extractor's current_version probe fetches the default branch
    from the repo endpoint and then retrieves the HEAD commit SHA from the
    commits endpoint — it does NOT enumerate the full file tree."""
    calls: list[str] = []

    def fake_headers(installation_id):
        return {"Authorization": "Bearer install-token"}

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/repos/owner/codebase-repo"):
            return _FakeResp({"default_branch": "main", "pushed_at": "fallback"})
        if url.endswith("/repos/owner/codebase-repo/commits/main"):
            return _FakeResp({"sha": "deadbeef123"})
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr("app.connectors.github_app.headers_for_installation", fake_headers)
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    sha = GithubExtractor(installation_id=42).current_version("owner/codebase-repo")
    assert sha == "deadbeef123"
    # Exactly two calls: repo metadata + branch HEAD. No tree/blob traversal.
    assert calls == [
        "https://api.github.com/repos/owner/codebase-repo",
        "https://api.github.com/repos/owner/codebase-repo/commits/main",
    ]


def test_github_extracts_explicit_tailwind_css_and_token_files(monkeypatch):
    files = {
        "tailwind.config.ts": """
            export default {
              theme: {
                colors: {
                  background: "#0b1120",
                  primary: "#38bdf8",
                  border: "#1e293b"
                },
                fontFamily: { sans: ["Inter", "system-ui"] },
                borderRadius: { lg: "12px" },
                spacing: { 4: "16px", 6: "24px" },
                boxShadow: { card: "0 12px 32px rgba(15,23,42,0.18)" }
              }
            }
        """,
        "tokens.json": '{"colors":{"surface":{"value":"#111827"}}}',
        "components.json": '{"aliases":{"button":"components/ui/button","card":"components/ui/card"}}',
        "app/globals.css": ':root { --muted: #64748b; --radius: 10px; } body { font-family: "Inter", sans-serif; }',
    }

    def fake_headers(installation_id):
        assert installation_id == 321
        return {"Authorization": "Bearer install-token"}

    def fake_get(url, **kwargs):
        path = url.split("/contents/", 1)[1]
        assert kwargs["headers"]["Authorization"] == "Bearer install-token"
        assert kwargs["timeout"] == 15
        if path in files:
            return _FakeResp(_contents(files[path]))
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr("app.connectors.github_app.headers_for_installation", fake_headers)
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    assert raw.provider == "github"
    assert set(raw.signals["files_present"]) == {
        "tailwind.config.ts", "components.json", "tokens.json", "app/globals.css",
    }
    ds = normalize(raw)
    assert ds.tokens.colors.background == "#0b1120"
    assert ds.tokens.colors.primary == "#38bdf8"
    assert ds.tokens.colors.accent == "#38bdf8"
    assert ds.tokens.colors.surface == "#111827"
    assert ds.tokens.colors.muted == "#64748b"
    assert ds.tokens.colors.border == "#1e293b"
    assert ds.tokens.is_dark is True
    assert ds.tokens.fonts.heading_family == "Inter"
    assert ds.tokens.fonts.body_family == "Inter"
    assert ds.tokens.radius_convention == "rounded"
    assert ds.tokens.spacing_scale == [16, 24]
    assert ds.tokens.elevation_style == "shadows"
    assert {"button", "card"}.issubset(set(ds.component_inventory))
    assert ds.has_explicit_system is True
    assert ds.confidence == "high"


def test_github_missing_or_unreadable_files_fall_back_to_baseline(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )
    monkeypatch.setattr(
        "app.connectors.github_app.requests.get",
        lambda url, **kwargs: _FakeResp(ok=False, status_code=404),
    )

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    assert raw.signals["files_present"] == []
    assert normalize(raw) == DesignSystem()

    raw = GithubExtractor(installation_id=321).extract_raw_signals("")
    assert normalize(raw) == DesignSystem()


def test_github_infers_from_bounded_component_tailwind_patterns(monkeypatch):
    directory = [
        {"type": "file", "name": "button.tsx", "path": "components/ui/button.tsx"},
        {"type": "file", "name": "card.tsx", "path": "components/ui/card.tsx"},
    ]
    files = {
        "components/ui/button.tsx": """
            export function Button() {
              return <button className="bg-blue-600 text-white border-blue-700
                rounded-lg px-4 py-2 gap-2 shadow-md font-semibold text-sm" />
            }
        """,
        "components/ui/card.tsx": """
            export const Card = () => (
              <section className="bg-blue-50 border-blue-200 rounded-lg p-6
                shadow-lg text-slate-900" />
            )
        """,
    }

    def fake_headers(installation_id):
        assert installation_id == 321
        return {"Authorization": "Bearer install-token"}

    def fake_get(url, **kwargs):
        path = url.split("/contents/", 1)[1]
        if path == "components/ui":
            return _FakeResp(directory)
        if path in files:
            return _FakeResp(_contents(files[path]))
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr("app.connectors.github_app.headers_for_installation", fake_headers)
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    assert raw.signals["files_present"] == []
    assert raw.signals["inference_files"] == [
        "components/ui/button.tsx",
        "components/ui/card.tsx",
    ]

    ds = normalize(raw)
    assert ds.tokens.colors.primary == "#3b82f6"
    assert ds.tokens.colors.accent == "#3b82f6"
    assert ds.tokens.colors.foreground == "#ffffff"
    assert ds.tokens.radius_convention == "rounded"
    assert ds.tokens.spacing_scale == [8, 16, 24]
    assert ds.tokens.elevation_style == "shadows"
    assert {"button", "card"}.issubset(set(ds.component_inventory))
    assert ds.has_explicit_system is False
    assert ds.confidence == "medium"


def test_github_explicit_tokens_override_inferred_patterns(monkeypatch):
    files = {
        "tokens.json": '{"primary":{"value":"#ef4444"},"background":{"value":"#ffffff"}}',
        "components/ui/button.tsx": (
            'export function Button(){ return <button className="bg-blue-600 bg-blue-700 '
            'rounded-full p-8 shadow-lg" /> }'
        ),
    }

    def fake_get(url, **kwargs):
        path = url.split("/contents/", 1)[1]
        if path == "components/ui":
            return _FakeResp([
                {"type": "file", "name": "button.tsx", "path": "components/ui/button.tsx"},
            ])
        if path in files:
            return _FakeResp(_contents(files[path]))
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    ds = normalize(raw)
    assert ds.tokens.colors.primary == "#ef4444"
    assert ds.tokens.colors.background == "#ffffff"
    # Explicit token files remain authoritative for explicit-system status even
    # when inference contributes radius/elevation/component inventory.
    assert ds.has_explicit_system is True
    assert ds.tokens.radius_convention == "pill"
    assert "button" in ds.component_inventory


def test_github_inference_directory_errors_and_oversized_files_degrade(monkeypatch):
    def fake_get(url, **kwargs):
        path = url.split("/contents/", 1)[1]
        if path == "components/ui":
            return _FakeResp([
                {"type": "file", "name": "button.tsx", "path": "components/ui/button.tsx"},
            ])
        if path == "components/ui/button.tsx":
            return _FakeResp({
                "encoding": "base64",
                "content": base64.b64encode(b"className='bg-blue-600'").decode("ascii"),
                "size": 200_000,
            })
        return _FakeResp(ok=False, status_code=500)

    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    assert raw.signals["inference_files"] == []
    assert normalize(raw) == DesignSystem()


def test_github_inference_caps_primitive_file_reads(monkeypatch):
    read_paths: list[str] = []
    directory = [
        {"type": "file", "name": f"button{i}.tsx", "path": f"components/ui/button{i}.tsx"}
        for i in range(20)
    ]
    # Use primitive file names so all entries are eligible until the cap is hit.
    for i, item in enumerate(directory):
        item["name"] = "button.tsx"
        item["path"] = f"components/ui/button-{i}.tsx"

    def fake_get(url, **kwargs):
        path = url.split("/contents/", 1)[1]
        if path == "components/ui":
            return _FakeResp(directory)
        if path.startswith("components/ui/button-"):
            read_paths.append(path)
            return _FakeResp(_contents('export function Button(){ return <button className="rounded-md" /> }'))
        return _FakeResp(ok=False, status_code=404)

    monkeypatch.setattr(
        "app.connectors.github_app.headers_for_installation",
        lambda installation_id: {"Authorization": "Bearer install-token"},
    )
    monkeypatch.setattr("app.connectors.github_app.requests.get", fake_get)

    raw = GithubExtractor(installation_id=321).extract_raw_signals("org/repo")
    assert len(read_paths) == 12
    assert len(raw.signals["inference_files"]) == 12


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
        self.is_redirect = False


@pytest.fixture(autouse=True)
def _allow_public_dns(monkeypatch):
    """WebExtractor.current_version now runs the SSRF guard (app.net_guard),
    which resolves the website host via getaddrinfo before the HEAD. These tests
    use ``acme.com`` and don't mock DNS, so stub resolution to a public IP. The
    guard's reject/allow logic itself is covered in test_net_guard.py. Harmless
    to the Figma/GitHub adapter tests, which never call the guard."""
    def _public(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("app.net_guard.socket.getaddrinfo", _public)


def test_website_current_version_returns_etag(monkeypatch):
    calls: list[str] = []

    def _fake_head(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 10
        # SSRF guard: auto-redirect is disabled so each hop can be re-validated.
        assert kwargs["allow_redirects"] is False
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

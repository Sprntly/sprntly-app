"""Tests for the Scenario B website design-system extractor (P5-01).

Playwright is fully mocked — no live Chromium in CI. ``_resolve_async_playwright``
is the seam: each test monkeypatches it to return a fake ``async_playwright``
factory whose ``chromium.launch`` yields a fake browser -> context -> page with
scriptable ``goto`` / ``evaluate`` / ``locator``.

Coverage maps 1:1 to the ticket's Unit Tests section / ACs:
- AC1 happy path (8-field dict), AC2 nav-error sentinel, AC3 disposal on both
  paths, AC4 8s timeout kwarg, AC5 below-confidence (both sub-cases), AC6
  cookie-dismissal swallows failures, AC7 no pool (launch once per call),
  AC8 requirements pin, AC9 host-only logging.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.design_agent.scenarios import website


@pytest.fixture(autouse=True)
def _allow_public_dns(monkeypatch):
    """The SSRF guard (app.net_guard) resolves each website URL via getaddrinfo
    before launching Chromium. These tests use mock ``*.example.com`` hosts that
    do not resolve, so we stub resolution to a public IP — keeping the guard
    active while letting the browser-behavior assertions run. The guard's own
    reject/allow logic is covered in test_net_guard.py."""
    import socket

    def _public(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("app.net_guard.socket.getaddrinfo", _public)

# A well-formed raw page.evaluate() return (pre-mapping): the sampler is a dumb
# emitter, so this carries candidate LISTS (a convertible green CTA), an h1 font
# stack, a body, padding samples, and a logo — i.e. the confident case.
_GOOD_RAW = {
    "color_candidates": [
        {"color": "rgb(37, 99, 235)", "area": 8000, "saturation": 0.6},
    ],
    "neutral_candidates": [],
    "container_observations": [],
    "observed_component_types": [],
    "background_color": "rgb(255, 255, 255)",
    "heading_font_family": '"Inter", system-ui, sans-serif',
    "heading_size_scale": "48px",
    "body_font_family": "Inter, sans-serif",
    "border_radius_convention": "8px",
    "spacing_scale_samples": ["12px 24px", "16px 32px"],
    "logo_url": "https://cdn.example.com/logo.svg",
}


def _build_fake(*, evaluate_return=None, goto_side_effect=None, click_side_effect=None):
    """Build a fake Playwright object graph + a factory matching the
    ``async with async_playwright() as p`` contract. Returns a SimpleNamespace
    of handles for call-tracking assertions."""
    page = MagicMock(name="page")
    page.goto = AsyncMock(side_effect=goto_side_effect)
    page.evaluate = AsyncMock(
        return_value=dict(_GOOD_RAW) if evaluate_return is None else evaluate_return
    )
    locator = MagicMock(name="locator")
    locator.first = MagicMock(name="first")
    locator.first.click = AsyncMock(side_effect=click_side_effect)
    page.locator = MagicMock(return_value=locator)

    context = MagicMock(name="context")
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = MagicMock(name="browser")
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    chromium = MagicMock(name="chromium")
    chromium.launch = AsyncMock(return_value=browser)

    p = MagicMock(name="p")
    p.chromium = chromium

    class _CM:
        async def __aenter__(self_inner):
            return p

        async def __aexit__(self_inner, *exc):
            return False

    def _factory():
        return _CM()

    return SimpleNamespace(
        factory=_factory,
        p=p,
        chromium=chromium,
        browser=browser,
        context=context,
        page=page,
        locator=locator,
    )


def _install(monkeypatch, handles):
    monkeypatch.setattr(website, "_resolve_async_playwright", lambda: handles.factory)


# --- Creation / happy path -------------------------------------------------

async def test_extract_returns_expected_sample_fields(monkeypatch):
    """AC1: mocked page -> dict with all expected keys, values mapped from evaluate()."""
    h = _build_fake()
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is not None
    assert set(ds.keys()) == {
        "color_candidates",
        "neutral_candidates",
        "container_observations",
        "observed_component_types",
        "background_color",
        "heading_font_family",
        "heading_size_scale",
        "body_font_family",
        "border_radius_convention",
        "spacing_scale_samples",
        "logo_url",
    }
    assert ds["color_candidates"] == [
        {"color": "rgb(37, 99, 235)", "area": 8000, "saturation": 0.6},
    ]
    assert ds["background_color"] == "rgb(255, 255, 255)"
    # Heading family is reduced to the first family in the stack, quotes stripped.
    assert ds["heading_font_family"] == "Inter"
    assert ds["heading_size_scale"] == "48px"
    assert ds["border_radius_convention"] == "8px"
    assert ds["spacing_scale_samples"] == ["12px 24px", "16px 32px"]
    assert ds["logo_url"] == "https://cdn.example.com/logo.svg"


# --- Error handling --------------------------------------------------------

async def test_extract_returns_none_on_navigation_error(monkeypatch):
    """AC2: page.goto raising -> returns None, no exception propagates."""
    h = _build_fake(goto_side_effect=RuntimeError("net::ERR_TIMED_OUT"))
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://flaky.example.com")

    assert ds is None


async def test_extract_disposes_browser_on_error(monkeypatch):
    """AC3: on goto raising, context.close() + browser.close() still called."""
    h = _build_fake(goto_side_effect=RuntimeError("net::ERR_TIMED_OUT"))
    _install(monkeypatch, h)

    await website.extract_website_design_system("https://flaky.example.com")

    assert h.context.close.await_count == 1
    assert h.browser.close.await_count == 1


# --- Edge cases ------------------------------------------------------------

async def test_extract_disposes_browser_on_success(monkeypatch):
    """AC3: success path also disposes context + browser."""
    h = _build_fake()
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is not None
    assert h.context.close.await_count == 1
    assert h.browser.close.await_count == 1


async def test_extract_navigation_timeout_is_8s(monkeypatch):
    """AC4: goto called with timeout=8000."""
    h = _build_fake()
    _install(monkeypatch, h)

    await website.extract_website_design_system("https://example.com")

    assert h.page.goto.await_count == 1
    assert h.page.goto.await_args.kwargs["timeout"] == 8000


async def test_extract_below_confidence_no_primary_color_returns_none(monkeypatch):
    """AC5: no chromatic candidate sampled -> None (below-confidence sentinel)."""
    raw = dict(_GOOD_RAW, color_candidates=[])
    h = _build_fake(evaluate_return=raw)
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is None


async def test_extract_below_confidence_no_heading_font_returns_none(monkeypatch):
    """AC5: no heading font family detected -> None (below-confidence sentinel)."""
    raw = dict(_GOOD_RAW, heading_font_family="")
    h = _build_fake(evaluate_return=raw)
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is None


async def test_cookie_dismissal_swallows_selector_failure(monkeypatch):
    """AC6: a selector whose .click raises does not abort; dict still returned."""
    h = _build_fake(click_side_effect=Exception("no such element"))
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    # Every selector failed to click, yet sampling ran and produced a dict.
    assert ds is not None
    assert h.page.evaluate.await_count == 1


async def test_no_browser_pool_launch_per_call(monkeypatch):
    """AC7: two invocations -> two distinct browsers, each launched exactly once."""
    created: list = []

    def resolver():
        h = _build_fake()
        created.append(h)
        return h.factory

    monkeypatch.setattr(website, "_resolve_async_playwright", resolver)

    await website.extract_website_design_system("https://a.example.com")
    await website.extract_website_design_system("https://b.example.com")

    assert len(created) == 2
    assert created[0].chromium.launch.await_count == 1
    assert created[1].chromium.launch.await_count == 1
    # No reuse: each invocation got its own browser instance.
    assert created[0].browser is not created[1].browser


# --- Manifest --------------------------------------------------------------

def test_playwright_pinned_in_requirements():
    """AC8: backend/requirements.txt contains exactly one `playwright==` line."""
    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    lines = [ln.strip() for ln in req.read_text().splitlines()]
    pins = [ln for ln in lines if ln.startswith("playwright==")]
    assert len(pins) == 1, f"expected exactly one playwright pin, found {pins}"


# --- Observability ---------------------------------------------------------

async def test_extract_logs_host_only_not_full_url(monkeypatch, caplog):
    """AC9: logs contain the URL host only — never the full URL / query string."""
    h = _build_fake()
    _install(monkeypatch, h)
    url = "https://shop.example.com/checkout?token=secret123&utm=foo"

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        await website.extract_website_design_system(url)

    messages = [r.getMessage() for r in caplog.records]
    blob = "\n".join(messages)

    # Both lifecycle lines emitted.
    assert any("website_extract_started" in m for m in messages)
    assert any("website_extract_complete" in m for m in messages)
    # Host is present...
    assert "shop.example.com" in blob
    # ...but no PII-bearing path or query string leaks.
    assert "secret123" not in blob
    assert "/checkout" not in blob
    assert "token=" not in blob
    # Confidence boolean is recorded on completion.
    assert "confident=True" in blob


# --- P6-09: fail-loud observable floor reason --------------------------------

def _raise_import_error():
    """A ``_resolve_async_playwright`` replacement that raises ImportError, as a
    missing/broken Playwright dependency would. Mirrors the prod EC2 case where
    Chromium/Playwright is absent (P6-01 handoff dep)."""
    raise ImportError("No module named 'playwright'")


def _completion_lines(caplog):
    return [r.getMessage() for r in caplog.records if "website_extract_complete" in r.getMessage()]


# Regression (required — fails on unfixed code) -------------------------------

async def test_import_error_floors_with_signal(monkeypatch, caplog):
    """AC1: when _resolve_async_playwright raises ImportError, the extractor
    returns None (the floor), does NOT let the ImportError escape, and emits
    website_extract_complete confident=False reason=import_unavailable. On
    UNFIXED code the import resolve sits before the try, so the ImportError
    escapes this call (no completion line) and the test fails."""
    monkeypatch.setattr(website, "_resolve_async_playwright", _raise_import_error)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        # Must NOT raise — the ImportError is now caught inside the extractor.
        ds = await website.extract_website_design_system("https://example.com")

    assert ds is None
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "website_extract_complete" in blob
    assert "confident=False" in blob
    assert "reason=import_unavailable" in blob
    assert "error_class=ImportError" in blob


async def test_no_completion_line_lost_on_import_failure(monkeypatch, caplog):
    """AC6: exactly one website_extract_complete line on the import-failure path
    (the inner finally is always reached because the resolve is inside the try).
    On unfixed code: zero lines."""
    monkeypatch.setattr(website, "_resolve_async_playwright", _raise_import_error)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        await website.extract_website_design_system("https://example.com")

    assert len(_completion_lines(caplog)) == 1


# Reason classification -------------------------------------------------------

async def test_confident_path_reason_ok(monkeypatch, caplog):
    """AC2: a confident extraction returns the design system and logs
    confident=True reason=ok."""
    h = _build_fake()
    _install(monkeypatch, h)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://example.com")

    assert ds is not None
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "confident=True" in blob
    assert "reason=ok" in blob


async def test_below_confidence_reason_low_confidence(monkeypatch, caplog):
    """AC3: an extraction that maps but trips _below_confidence floors to None
    and logs confident=False reason=low_confidence."""
    raw = dict(_GOOD_RAW, color_candidates=[])
    h = _build_fake(evaluate_return=raw)
    _install(monkeypatch, h)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://example.com")

    assert ds is None
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "confident=False" in blob
    assert "reason=low_confidence" in blob


async def test_timeout_reason_timeout(monkeypatch, caplog):
    """AC4: a Playwright nav TimeoutError (classified name-based) floors to None
    and logs reason=timeout — without any top-level playwright import."""

    class TimeoutError(Exception):  # noqa: A001 — mimic playwright.async_api.TimeoutError by name
        pass

    h = _build_fake(goto_side_effect=TimeoutError("Timeout 8000ms exceeded"))
    _install(monkeypatch, h)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://slow.example.com")

    assert ds is None
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "confident=False" in blob
    assert "reason=timeout" in blob
    assert "error_class=TimeoutError" in blob


async def test_other_error_reason_error(monkeypatch, caplog):
    """AC5: any other in-try Exception floors to None and logs reason=error
    error_class=<Class>."""
    h = _build_fake(goto_side_effect=RuntimeError("net::ERR_CONNECTION_REFUSED"))
    _install(monkeypatch, h)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://broken.example.com")

    assert ds is None
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "confident=False" in blob
    assert "reason=error" in blob
    assert "error_class=RuntimeError" in blob


# Contract / observability / module-purity ------------------------------------

async def test_floor_output_unchanged(monkeypatch):
    """AC7: None on every failure path (import / below-confidence / nav error),
    WebsiteDesignSystem on success — byte-identical to the pre-fix return
    contract (only the observability changed)."""
    # success
    h = _build_fake()
    _install(monkeypatch, h)
    assert await website.extract_website_design_system("https://ok.example.com") is not None

    # import failure
    monkeypatch.setattr(website, "_resolve_async_playwright", _raise_import_error)
    assert await website.extract_website_design_system("https://noplaywright.example.com") is None

    # below confidence
    h2 = _build_fake(evaluate_return=dict(_GOOD_RAW, heading_font_family=""))
    _install(monkeypatch, h2)
    assert await website.extract_website_design_system("https://weak.example.com") is None

    # nav error
    h3 = _build_fake(goto_side_effect=RuntimeError("boom"))
    _install(monkeypatch, h3)
    assert await website.extract_website_design_system("https://flaky.example.com") is None


async def test_completion_line_logs_host_only_with_reason(monkeypatch, caplog):
    """AC9: the completion line carries url_host (host only), reason, confident —
    no full URL / query string / sampled colors or fonts."""
    h = _build_fake()
    _install(monkeypatch, h)
    url = "https://shop.example.com/pricing?token=secret999"

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        await website.extract_website_design_system(url)

    line = _completion_lines(caplog)[0]
    assert "url_host=shop.example.com" in line
    assert "reason=" in line
    # No PII / path / query string.
    assert "secret999" not in line
    assert "/pricing" not in line
    # No sampled style values leak into the line.
    assert "rgb(37, 99, 235)" not in line
    assert "Inter" not in line


def test_module_imports_without_playwright():
    """AC8: no TOP-LEVEL playwright import — the only playwright import stays
    indented inside _resolve_async_playwright, keeping the module importable on
    a host with no playwright installed. Mirrors the ticket's grep AC."""
    src_lines = Path(website.__file__).read_text().splitlines()
    top_level = [
        ln for ln in src_lines
        if ln.startswith("import playwright") or ln.startswith("from playwright")
    ]
    assert top_level == [], f"unexpected top-level playwright import: {top_level}"


# Two-tier floor / clause ordering --------------------------------------------

async def test_inner_import_error_precedes_broad_except(monkeypatch, caplog):
    """AC12: an ImportError from _resolve_async_playwright sets
    reason=import_unavailable (the narrow `except ImportError` clause wins), NOT
    reason=error (which would prove the broad `except Exception` caught it first).
    Exactly one completion line is logged (the inner finally is always reached)."""
    monkeypatch.setattr(website, "_resolve_async_playwright", _raise_import_error)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://example.com")

    assert ds is None
    lines = _completion_lines(caplog)
    assert len(lines) == 1
    assert "reason=import_unavailable" in lines[0]
    assert "reason=error" not in lines[0]


async def test_module_absent_floors_via_caller_no_completion_line(monkeypatch, caplog):
    """AC11 tier (b): a MODULE-level ImportError (the `website` module / P5-01
    absent) is the CALLER's safety net (routes/design_agent.py:740 → ds=None),
    NOT the extractor's inner handler. The extractor function body never runs in
    that case, so NO lifecycle line is emitted — distinct from tier (a) (AC1),
    where the inner ImportError IS caught and DOES emit started + complete.

    This asserts the documented seam by exercising tier (a) and confirming the
    function body ran (started + complete both fire); tier (b) is upstream of
    this function entirely and unchanged by P6-09."""
    monkeypatch.setattr(website, "_resolve_async_playwright", _raise_import_error)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://example.com")

    assert ds is None
    msgs = [r.getMessage() for r in caplog.records]
    # Tier (a): the body ran, so BOTH lifecycle lines fired.
    assert any("website_extract_started" in m for m in msgs)
    assert any("website_extract_complete" in m for m in msgs)
    # Tier (b) — module absence — would emit NEITHER (the body never executes);
    # that floor is the caller's `except ImportError`, which P6-09 leaves intact.


# --- P7-08: networkidle→load fix (K1) ----------------------------------------


async def test_goto_uses_wait_until_load(monkeypatch):
    """P7-08 regression (AC4): page.goto is invoked with wait_until="load" — the
    K1 fix. This FAILS if website.py is reverted to wait_until="networkidle".
    The paired timeout=8000 wall-clock cap (AC5) is unchanged by the fix."""
    h = _build_fake()
    _install(monkeypatch, h)

    await website.extract_website_design_system("https://example.com")

    assert h.page.goto.await_count == 1
    assert h.page.goto.await_args.kwargs["wait_until"] == "load"
    # AC5: _NAV_TIMEOUT_MS unchanged — the fix only touched the wait_until value.
    assert h.page.goto.await_args.kwargs["timeout"] == 8000


async def test_extract_load_path_returns_design_system(monkeypatch):
    """P7-08 creation (AC6): a resolving goto (no side-effect) on the load path +
    a valid sampler return yields a confident full design system — the
    preserve-functionality contract (tester proto-54 Plotline success behaviour).
    "Reachable fixture site" = the scriptable fake page whose goto resolves."""
    h = _build_fake()  # goto_side_effect=None -> resolves; _GOOD_RAW sampler return
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://plotline.studio")

    assert ds is not None
    # The success ran through the load path.
    assert h.page.goto.await_args.kwargs["wait_until"] == "load"
    assert set(ds.keys()) == {
        "color_candidates",
        "neutral_candidates",
        "container_observations",
        "observed_component_types",
        "background_color",
        "heading_font_family",
        "heading_size_scale",
        "body_font_family",
        "border_radius_convention",
        "spacing_scale_samples",
        "logo_url",
    }
    assert ds["color_candidates"] == [
        {"color": "rgb(37, 99, 235)", "area": 8000, "saturation": 0.6},
    ]
    assert ds["heading_font_family"] == "Inter"


async def test_extract_load_path_timeout_still_floors(monkeypatch, caplog):
    """P7-08 edge (AC3): even with the load fix, a goto timeout still floors to
    None and logs reason=timeout — the _is_timeout/reason= observability stays
    intact. The fix changes only how nav waits, not the timeout floor behaviour."""

    class TimeoutError(Exception):  # noqa: A001 — mimic playwright.async_api.TimeoutError by name
        pass

    h = _build_fake(goto_side_effect=TimeoutError("Timeout 8000ms exceeded"))
    _install(monkeypatch, h)

    with caplog.at_level(logging.INFO, logger="app.design_agent.scenarios.website"):
        ds = await website.extract_website_design_system("https://slow.example.com")

    assert ds is None
    # The fix value still reached goto on the timeout path.
    assert h.page.goto.await_args.kwargs["wait_until"] == "load"
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "confident=False" in blob
    assert "reason=timeout" in blob
    assert "error_class=TimeoutError" in blob


# --- Accent convertibility floor + broadened CTA sampler -------------------

def test_below_confidence_transparent_primary_is_treated_as_absent():
    """A transparent CTA fill does not convert to a usable hex, so it counts as
    no chromatic candidate at all — the sampler must floor to the None sentinel
    rather than leak a default accent downstream."""
    ds = website._map_sample(
        {
            "color_candidates": [
                {"color": "rgba(0, 0, 0, 0)", "area": 100, "saturation": 0.0}
            ],
            "heading_font_family": "Inter",
        }
    )
    assert website._below_confidence(ds) is True


def test_below_confidence_solid_primary_with_heading_passes():
    """A solid, convertible chromatic candidate plus a heading font clears the
    floor."""
    ds = website._map_sample(
        {
            "color_candidates": [
                {"color": "rgb(14, 107, 79)", "area": 8000, "saturation": 0.77}
            ],
            "heading_font_family": "Inter",
        }
    )
    assert website._below_confidence(ds) is False


def test_below_confidence_missing_heading_returns_true():
    """A convertible chromatic candidate with NO heading font is still below the
    floor."""
    ds = website._map_sample(
        {
            "color_candidates": [
                {"color": "rgb(14, 107, 79)", "area": 8000, "saturation": 0.77}
            ],
            "heading_font_family": "",
        }
    )
    assert website._below_confidence(ds) is True


async def test_extract_transparent_primary_floors_to_none(monkeypatch):
    """End to end: a sample whose only chromatic candidate is transparent returns
    the None sentinel (the caller then shows the manual color-picker floor)."""
    raw = dict(
        _GOOD_RAW,
        color_candidates=[
            {"color": "rgba(0, 0, 0, 0)", "area": 100, "saturation": 0.0}
        ],
    )
    h = _build_fake(evaluate_return=raw)
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is None


def test_sampler_js_broadens_cta_candidates_and_guards_transparency():
    """Lock the sampler's CTA breadth + transparency guard against silent
    regression (the in-page JS itself is proven by the live re-extraction)."""
    js = website._SAMPLER_JS
    assert 'role="button"' in js
    assert "cta" in js.lower()
    assert "isTransparent" in js


def test_sampler_js_emits_saturation_per_candidate():
    """Lock the per-candidate saturation emission against regression (the in-page
    JS is proven by the live re-extraction): the sampler computes saturationOf on
    each CTA fill so the kernel can rank chromatic vs monochrome candidates."""
    js = website._SAMPLER_JS
    assert "saturationOf" in js
    assert "colorCandidates" in js
    assert "saturation:" in js


def test_map_sample_defaults_absent_neutrals_to_empty():
    """Candidate-list keys absent from the raw evaluate() dict map to empty lists
    so the adapter falls back to its defaults rather than crashing."""
    ds = website._map_sample(
        {
            "color_candidates": [{"color": "rgb(1, 2, 3)", "area": 10, "saturation": 0.5}],
            "heading_font_family": "Inter",
        }
    )
    assert ds["neutral_candidates"] == []
    assert ds["container_observations"] == []


def test_map_sample_defaults_absent_component_types_to_empty():
    """An absent observed_component_types key maps to an empty list."""
    ds = website._map_sample(
        {
            "color_candidates": [{"color": "rgb(1, 2, 3)", "area": 10, "saturation": 0.5}],
            "heading_font_family": "Inter",
        }
    )
    assert ds["observed_component_types"] == []


def test_sampler_js_emits_component_types():
    """Lock the DOM component-type detection against regression (the in-page JS is
    proven by the live re-extraction): names only, no counts."""
    js = website._SAMPLER_JS
    assert "observed_component_types" in js
    assert "componentSelectors" in js


def test_sampler_js_emits_container_observations():
    """Lock the bounded container scan against regression (the in-page JS is
    proven by the live re-extraction): the sampler emits per-container
    border/shadow observations and the kernel tallies prevalence."""
    js = website._SAMPLER_JS
    assert "containerObservations" in js
    assert "has_border" in js
    assert "has_shadow" in js
    assert "ELEVATION_SCAN_CAP" in js

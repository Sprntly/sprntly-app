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

# A well-formed raw page.evaluate() return (pre-mapping): a button color, an h1
# font stack, a body, padding samples, and a logo — i.e. the confident case.
_GOOD_RAW = {
    "primary_color": "rgb(37, 99, 235)",
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

async def test_extract_returns_eight_field_design_system(monkeypatch):
    """AC1: mocked page -> dict with all 8 keys, values mapped from evaluate()."""
    h = _build_fake()
    _install(monkeypatch, h)

    ds = await website.extract_website_design_system("https://example.com")

    assert ds is not None
    assert set(ds.keys()) == {
        "primary_color",
        "background_color",
        "heading_font_family",
        "heading_size_scale",
        "body_font_family",
        "border_radius_convention",
        "spacing_scale_samples",
        "logo_url",
    }
    assert ds["primary_color"] == "rgb(37, 99, 235)"
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
    """AC5: no primary color sampled -> None (below-confidence sentinel)."""
    raw = dict(_GOOD_RAW, primary_color="")
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

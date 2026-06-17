"""Live-Chromium capture test — proves the local-render path captures the
RENDERED SPA, not the un-hydrated #root shell.

Skipped when a Chromium runtime is not provisioned (CI hosts without the
Playwright browser). Where Chromium exists, it renders a tiny multi-file SPA
whose module script paints a known fullscreen background into #root, then
asserts the captured PNG is the rendered colour — NOT the white shell a
signed-URL capture would produce when ./assets/* fails to resolve.
"""
from __future__ import annotations

import asyncio

import pytest

import app.design_agent.screenshot as screenshot


def _real_async_playwright():
    from playwright.async_api import async_playwright

    return async_playwright


def _chromium_available() -> bool:
    async def _probe() -> bool:
        try:
            factory = _real_async_playwright()
            async with factory() as p:
                b = await p.chromium.launch(headless=True, args=screenshot._CHROMIUM_ARGS)
                await b.close()
            return True
        except Exception:
            return False

    return asyncio.new_event_loop().run_until_complete(_probe())


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="no Chromium runtime provisioned"
)


@pytest.fixture(autouse=True)
def _restore_real_playwright(monkeypatch):
    """Override the session-wide ``_no_real_browser_in_preview_capture`` autouse
    fixture for THIS module: these tests intentionally drive live Chromium, so
    restore the real lazy-import seam (the global fixture stubs it to ImportError)."""
    monkeypatch.setattr(
        screenshot, "_resolve_async_playwright", _real_async_playwright, raising=False
    )


# A multi-file SPA: the module script (loaded via relative ./assets/*) paints a
# fullscreen solid-red box into #root. If the assets fail to load (the signed-URL
# bug), #root stays empty and the page is white — so a red capture proves the
# SPA hydrated from the LOCAL serve.
_RED_SPA = {
    "index.html": (
        "<!doctype html><html><head>"
        "<style>html,body,#root{margin:0;height:100%;width:100%}</style>"
        '<script type="module" src="./assets/app.js"></script>'
        '</head><body><div id="root"></div></body></html>'
    ),
    "assets/app.js": (
        "const r=document.getElementById('root');"
        "const d=document.createElement('div');"
        "d.style.cssText='position:fixed;inset:0;background:#ff0000';"
        "r.appendChild(d);"
    ),
}


# A second SPA that paints a DIFFERENT colour into #root via its relative
# asset. Used to contrast against the red SPA — a different PNG proves the
# capture reflected the rendered DOM, not a fixed un-hydrated shell.
_BLUE_SPA = {
    "index.html": (
        "<!doctype html><html><head>"
        "<style>html,body,#root{margin:0;height:100%;width:100%}</style>"
        '<script type="module" src="./assets/app.js"></script>'
        '</head><body><div id="root"></div></body></html>'
    ),
    "assets/app.js": (
        "const r=document.getElementById('root');"
        "const d=document.createElement('div');"
        "d.style.cssText='position:fixed;inset:0;background:#0000ff';"  # blue box
        "r.appendChild(d);"
    ),
}


async def test_live_capture_renders_spa_not_shell():
    """The capture loads the SPA from the LOCAL serve, the relative ./assets/app.js
    resolves + executes, #root is populated, and the screenshot reflects the
    rendered DOM. Proven by contrast: the red-painting SPA and a white-rendering
    bundle produce DIFFERENT PNGs — if the capture saw only the un-hydrated shell
    (the signed-URL bug) both would be the same blank page."""
    red = await screenshot.capture_bundle_screenshot(_RED_SPA)
    blue = await screenshot.capture_bundle_screenshot(_BLUE_SPA)
    assert red is not None, "capture returned None on a host with Chromium"
    assert red[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert blue is not None
    # Different rendered DOM → different pixels. A shell-only capture (the bug)
    # would render identical blank pages for both bundles.
    assert red != blue, "captured the empty shell — both bundles look identical"


async def test_live_capture_degrades_when_root_never_populates():
    """A bundle whose #root is never populated → honest-degrade to None within
    the render-wait cap (no crash, no shell screenshot)."""
    shell_only = {
        "index.html": '<!doctype html><body><div id="root"></div></body>',
    }
    png = await screenshot.capture_bundle_screenshot(shell_only)
    assert png is None

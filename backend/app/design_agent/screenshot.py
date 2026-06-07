"""Best-effort prototype preview screenshot.

Render a staged prototype bundle once in headless Chromium and return the PNG
bytes, so the preview card can show a real, lightweight thumbnail instead of a
heavy live iframe or a neutral placeholder.

Reuses the same Playwright dependency the website design-system extractor already
vendors — no new dependency. The Playwright import is deferred behind the
``_resolve_async_playwright`` seam so this module imports cleanly on hosts where
Chromium is not provisioned (tests monkeypatch the seam; the real import runs
only at capture time).

Capture is HONEST-DEGRADE: ``capture_bundle_screenshot`` returns ``None`` on ANY
failure (no Playwright, launch failure, navigation error, timeout) and NEVER
raises to its caller. The caller treats ``None`` as "no thumbnail" and completes
the prototype anyway — a flaky or absent browser must never block completion, and
no fake/placeholder image is ever substituted. The browser is disposed per call
(no pool), on every path.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Navigation wall-clock cap (ms). Matches the website extractor: a slow or hung
# page must not hold the completion hook longer than this before degrading.
_NAV_TIMEOUT_MS = 8000

# Fixed thumbnail viewport. Viewport-only screenshot (full_page off) keeps the
# PNG small — the card downscales it further client-side.
_VIEWPORT = {"width": 1280, "height": 800}

# Standard headless-CI Chromium flags for bounded container memory, identical to
# the website extractor: --disable-dev-shm-usage avoids /dev/shm exhaustion in
# small containers; --no-sandbox is required when Chromium runs as root in CI/EC2.
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--no-sandbox"]


def _resolve_async_playwright():
    """Lazy-import indirection for ``playwright.async_api.async_playwright``.

    Kept as a seam so this module imports cleanly on hosts without Playwright
    (tests monkeypatch this to inject a fake factory; the real import only runs at
    capture time on a host that has the dependency). Mirrors the website
    extractor's seam so the two share the same import posture.
    """
    from playwright.async_api import async_playwright

    return async_playwright


async def capture_bundle_screenshot(bundle_url: str) -> bytes | None:
    """Render ``bundle_url`` in headless Chromium and return PNG bytes.

    Returns the screenshot PNG bytes on success, or ``None`` on ANY failure —
    Playwright not installed (ImportError), Chromium launch failure, navigation
    error, or navigation timeout. NEVER raises to the caller: capture is
    best-effort and must never block prototype completion. The browser is disposed
    per call on every path, including the error path.

    The ``bundle_url`` is intentionally not logged here — it may be a signed
    storage URL. Observability (with stable identifiers) is the caller's job.
    """
    try:
        async_playwright = _resolve_async_playwright()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            context = await browser.new_context(viewport=dict(_VIEWPORT))
            try:
                page = await context.new_page()
                await page.goto(bundle_url, wait_until="load", timeout=_NAV_TIMEOUT_MS)
                return await page.screenshot()
            finally:
                # Dispose per call even on the error path (no browser pool).
                await context.close()
                await browser.close()
    except Exception:  # noqa: BLE001 — honest-degrade: a capture failure is never fatal.
        # No URL / no bytes in the log line; error_class is surfaced by the caller
        # alongside the prototype identifiers it owns.
        return None

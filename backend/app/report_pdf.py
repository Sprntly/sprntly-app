"""Render a captured report's HTML document to PDF bytes in headless Chromium.

PDF is the ONLY download format for reports (product decision): these documents
are typography- and layout-led — a table of problems, a radar chart, sized theme
cards — and a DOCX/markdown flattening would lose the thing that makes them
readable. Chromium is the renderer because the documents already carry their own
`@media print` rules (hide the print button, drop page padding, and
`break-inside: avoid` on cards/rows/charts so a card never splits across pages),
so a browser print IS the intended output. `page.pdf()` uses print media, so
those rules apply without any extra emulation.

Reuses the `playwright` dependency the Design Agent already vendors — no new
dependency — behind the same `_resolve_async_playwright` lazy seam
(app/design_agent/screenshot.py), so this module imports cleanly on hosts where
Chromium is not provisioned and tests can inject a fake factory.

UNLIKE the screenshot capture, this does NOT honest-degrade to a placeholder: the
user clicked Download, so a failure returns None and the route answers 503 rather
than handing back a blank or half-rendered file.

Two deliberate hardening choices, both matching how the in-app viewer already
treats these documents (HtmlReportView renders them in an iframe sandboxed
WITHOUT allow-scripts):

  - JAVASCRIPT OFF. The scripts these reports carry are interaction handlers
    (toggle the metadata panel, disable a button on click) — nothing that renders
    content — so a PDF loses nothing by not running them, and an LLM-authored
    document never gets to execute server-side.
  - SUBRESOURCE ALLOWLIST. Rendering server-side means every URL in the document
    would otherwise be fetched by our own host — an SSRF surface (cloud metadata
    endpoints, internal services) driven by generated content. Only the Google
    Fonts hosts the report skills actually use are allowed; every other request is
    aborted. Fonts matter enough to allow because these are typographic documents
    and a fallback font visibly changes them.

Every rendered PDF carries the Sprntly wordmark (app/watermark.py), stamped in
this module so both the authed download and the public shared-link download are
marked by construction rather than by each route remembering to.
"""
from __future__ import annotations

import asyncio
import logging

from app.watermark import PDF_HEADER_TEMPLATE, pdf_footer_template, watermark_html

logger = logging.getLogger(__name__)

# Standard headless-CI Chromium flags, identical to the screenshot capture:
# --disable-dev-shm-usage avoids /dev/shm exhaustion in small containers;
# --no-sandbox is required when Chromium runs as root in CI/EC2.
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--no-sandbox"]

# Hosts a report document may load subresources from. Everything else is aborted
# — see the module docstring (SSRF). The report skills load Newsreader + Inter
# from Google Fonts; nothing else.
_ALLOWED_SUBRESOURCE_HOSTS = frozenset({
    "fonts.googleapis.com",
    "fonts.gstatic.com",
})

# Wall-clock caps (ms). A hung font fetch or a pathological document must not
# hold a request open indefinitely.
_CONTENT_TIMEOUT_MS = 8000
_FONT_SETTLE_TIMEOUT_MS = 3000
# Seconds, and applied with asyncio.wait_for rather than a Playwright kwarg:
# `page.pdf()` takes no `timeout` argument (unlike set_content / wait_for_*), so
# the cap has to be imposed from outside the call.
_PDF_TIMEOUT_S = 20.0

# The documents set `.page{max-width:100%;padding:0}` under @media print and
# expect the printer margins to supply the whitespace, so the margins here are
# load-bearing rather than cosmetic.
_PDF_MARGIN = {"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"}


def _resolve_async_playwright():
    """Lazy-import indirection for ``playwright.async_api.async_playwright``.

    Kept as a seam so this module imports cleanly on hosts without Playwright
    (tests monkeypatch this to inject a fake factory; the real import only runs at
    render time). Mirrors app/design_agent/screenshot.py's seam.
    """
    from playwright.async_api import async_playwright

    return async_playwright


def _is_allowed_subresource(url: str) -> bool:
    """Allow http(s) requests to the font hosts and inline `data:` URIs only.

    Everything else — another host, `file://`, a private/loopback address, a
    redirect target outside the allowlist — is refused. Host matching is exact
    (no suffix match), so `fonts.googleapis.com.evil.test` does not pass.
    """
    if url.startswith("data:"):
        return True
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _ALLOWED_SUBRESOURCE_HOSTS


async def render_report_pdf(html: str) -> bytes | None:
    """Render the self-contained report document `html` → PDF bytes.

    Returns None on ANY failure (Playwright missing, Chromium launch failure,
    timeout, render error) and never raises — the route turns None into a 503 so
    the user gets a clear "couldn't generate the PDF", never a corrupt file.
    Empty/blank input returns None rather than a one-blank-page PDF.
    """
    if not html or not html.strip():
        return None
    try:
        async_playwright = _resolve_async_playwright()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            # JS off for the whole context — see the module docstring.
            context = await browser.new_context(java_script_enabled=False)
            try:
                page = await context.new_page()

                async def _gate(route):
                    if _is_allowed_subresource(route.request.url):
                        await route.continue_()
                    else:
                        logger.info(
                            "report pdf: blocked subresource host=%s",
                            route.request.url.split("/")[2:3] or ["?"],
                        )
                        await route.abort()

                await page.route("**/*", _gate)
                # Stamped here rather than at the two routes so both the authed
                # download and the public /r/<token> one are marked by
                # construction — there is no way to reach page.pdf() unmarked.
                await page.set_content(
                    watermark_html(html), timeout=_CONTENT_TIMEOUT_MS
                )
                # Bounded settle so webfonts land before layout is measured; a
                # slow/blocked font falls through to system fonts rather than
                # stalling the download.
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=_FONT_SETTLE_TIMEOUT_MS
                    )
                except Exception:  # noqa: BLE001 — settle is best-effort.
                    pass
                return await asyncio.wait_for(
                    page.pdf(
                        format="A4",
                        # The cards, badges and chart fills ARE the document;
                        # without this Chromium drops every background in print
                        # media.
                        print_background=True,
                        margin=dict(_PDF_MARGIN),
                        # The sprntly.ai footer, painted into the bottom margin
                        # band on every page — see app/watermark.py for why the
                        # footer cannot ride in the document body like the
                        # wordmark does.
                        display_header_footer=True,
                        header_template=PDF_HEADER_TEMPLATE,
                        footer_template=pdf_footer_template(),
                    ),
                    timeout=_PDF_TIMEOUT_S,
                )
            finally:
                # Dispose per call on every path, including errors (no pool).
                await context.close()
                await browser.close()
    except Exception:  # noqa: BLE001 — surfaced to the caller as None → 503.
        logger.exception("report pdf render failed")
        return None

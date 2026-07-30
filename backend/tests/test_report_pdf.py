"""Unit tests for the report PDF renderer's guard rails.

The renderer fetches subresources from OUR host, driven by LLM-authored HTML, so
the allowlist is the SSRF boundary and is tested directly rather than only
through the route.
"""
from __future__ import annotations

import pytest

from app import report_pdf as rp


@pytest.mark.parametrize("url", [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400",
    "https://fonts.gstatic.com/s/inter/v13/font.woff2",
    "http://fonts.googleapis.com/css2?family=Inter",
    "data:image/svg+xml;base64,PHN2Zy8+",
])
def test_allows_the_font_hosts_and_inline_data_uris(url):
    assert rp._is_allowed_subresource(url)


@pytest.mark.parametrize("url", [
    # Cloud metadata — the canonical server-side-request-forgery target.
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:8000/v1/reports/1",
    "http://localhost/admin",
    "http://10.0.0.5/internal",
    "https://evil.test/exfiltrate?data=x",
    # Suffix trickery: the check is an exact host match, not endswith.
    "https://fonts.googleapis.com.evil.test/css",
    "https://notfonts.googleapis.com/css",
    # Non-http schemes must never resolve server-side.
    "file:///etc/passwd",
    "ftp://fonts.gstatic.com/font.woff2",
])
def test_blocks_everything_else(url):
    assert not rp._is_allowed_subresource(url)


async def test_blank_document_renders_nothing_rather_than_a_blank_page():
    assert await rp.render_report_pdf("") is None
    assert await rp.render_report_pdf("   \n  ") is None


async def test_missing_playwright_returns_none_instead_of_raising(monkeypatch):
    """conftest already stubs the seam to raise ImportError; assert the contract
    explicitly since the route depends on None (→ 503) rather than an exception."""
    def _boom():
        raise ImportError("no playwright here")

    monkeypatch.setattr(rp, "_resolve_async_playwright", _boom)
    assert await rp.render_report_pdf("<!DOCTYPE html><html></html>") is None

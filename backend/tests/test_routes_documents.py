"""Tests for POST /v1/documents/pdf — the PRD / Evidence PDF download.

This route renders CALLER-SUPPLIED HTML in headless Chromium on our own host, so
its guards are the point: authentication, a body cap, and a per-user render
budget. (The SSRF boundary — JS off, subresource allowlist — belongs to the
renderer and is tested directly in test_report_pdf.py.)
"""
from __future__ import annotations

import pytest

from tests._company_helpers import company_client

DOC = "<!DOCTYPE html><html><body><h1>Handoff Threshold PRD</h1></body></html>"


@pytest.fixture
def fake_render(monkeypatch):
    """Stand in for Chromium; records what was handed to the renderer."""
    seen: dict = {}

    async def _render(html: str):
        seen["html"] = html
        return b"%PDF-1.4 fake"

    from app.routes import documents

    monkeypatch.setattr(documents, "render_report_pdf", _render)
    return seen


def test_requires_auth(unauth_client, isolated_settings):
    r = unauth_client.post("/v1/documents/pdf", json={"html": DOC, "filename": "x"})
    assert r.status_code == 401


def test_renders_the_supplied_document_as_an_attachment(
    isolated_settings, monkeypatch, fake_render
):
    ctx = company_client(monkeypatch)

    r = ctx.client.post(
        "/v1/documents/pdf",
        json={"html": DOC, "filename": "Handoff Threshold PRD"},
    )

    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake"
    assert r.headers["content-type"] == "application/pdf"
    assert fake_render["html"] == DOC, "the document reaches the renderer verbatim"
    # Slugified server-side — a caller-supplied name never lands in a header raw.
    assert r.headers["content-disposition"] == 'attachment; filename="handoff-threshold-prd.pdf"'
    # Per-tenant authenticated content; no shared cache may hold it.
    assert r.headers["cache-control"] == "private, no-store"


def test_a_hostile_filename_cannot_break_out_of_the_header(
    isolated_settings, monkeypatch, fake_render
):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/documents/pdf",
        json={"html": DOC, "filename": 'evil"; attachment; filename="pwned'},
    )
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    # The slugifier strips every character that could terminate the value or
    # start a second directive, so the header stays one quoted filename.
    assert disposition.count('filename="') == 1
    assert disposition.count('"') == 2
    assert disposition.count(";") == 1
    assert disposition == 'attachment; filename="evil-attachment-filename-pwned.pdf"'


def test_an_unrenderable_document_503s_rather_than_saving_a_broken_file(
    isolated_settings, monkeypatch
):
    async def _fails(html: str):
        return None

    from app.routes import documents

    monkeypatch.setattr(documents, "render_report_pdf", _fails)
    ctx = company_client(monkeypatch)

    r = ctx.client.post("/v1/documents/pdf", json={"html": DOC, "filename": "x"})
    assert r.status_code == 503


def test_an_empty_document_is_rejected_before_the_renderer(
    isolated_settings, monkeypatch, fake_render
):
    ctx = company_client(monkeypatch)
    assert ctx.client.post("/v1/documents/pdf", json={"html": "", "filename": "x"}).status_code == 422
    assert "html" not in fake_render


def test_an_oversized_document_is_rejected(isolated_settings, monkeypatch, fake_render):
    """A render is seconds of CPU; the body cap bounds what one call can cost."""
    from app.routes.documents import _MAX_HTML_CHARS

    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/documents/pdf",
        json={"html": "x" * (_MAX_HTML_CHARS + 1), "filename": "x"},
    )
    assert r.status_code == 422
    assert "html" not in fake_render


def test_the_render_budget_is_per_user(isolated_settings, monkeypatch, fake_render):
    """Keyed by user, not company, so one person looping downloads cannot lock
    their colleagues out."""
    from app.routes.documents import PDF_LIMITER

    PDF_LIMITER._events.clear()  # type: ignore[attr-defined]
    ctx = company_client(monkeypatch)

    codes = [
        ctx.client.post("/v1/documents/pdf", json={"html": DOC, "filename": "x"}).status_code
        for _ in range(PDF_LIMITER._max + 1)
    ]
    assert codes[:-1] == [200] * PDF_LIMITER._max
    assert codes[-1] == 429
    PDF_LIMITER._events.clear()  # type: ignore[attr-defined]

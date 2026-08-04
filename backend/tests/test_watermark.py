"""Tests for the Sprntly wordmark stamped behind outgoing documents.

Two layers. The pure ones cover the injection contract (placement, idempotence,
the fragment documents that have no <body>). The last one renders a forced
three-page document through REAL Chromium and reads the mark back out of the
resulting PDF — because the whole design rests on Chromium repeating a
`position:fixed` layer onto every page, and a stubbed browser cannot tell us
whether that is still true.
"""
from __future__ import annotations

import pytest

from app import report_pdf as rp
from app import watermark as wm


# ── injection contract ───────────────────────────────────────────────────────

def test_stamps_the_wordmark_inside_the_body():
    out = wm.watermark_html("<html><body><h1>Report</h1></body></html>")
    assert wm.WORDMARK in out
    assert wm.MARKER_CLASS in out
    # Inside the body, not after it — content outside </body> is at the mercy of
    # the parser's error recovery.
    assert out.index(wm.MARKER_CLASS) < out.index("</body>")


def test_keeps_the_document_and_its_own_styles_intact():
    doc = "<html><head><style>.page{background:#fff}</style></head><body><p>x</p></body></html>"
    out = wm.watermark_html(doc)
    assert ".page{background:#fff}" in out
    assert "<p>x</p>" in out
    # Ours lands after the document's own stylesheet, so equal-specificity rules
    # resolve our way on source order alone.
    assert out.index(".page{background:#fff}") < out.index(f".{wm.MARKER_CLASS}{{")


def test_fragment_documents_without_a_body_still_get_marked():
    """The evidence brief and some report templates open with <meta><style> and
    never emit <body> — appending is the fallback, not a skip."""
    out = wm.watermark_html("<meta charset='utf-8'><style>p{color:red}</style><p>hi</p>")
    assert wm.MARKER_CLASS in out
    assert "<p>hi</p>" in out


def test_stamping_twice_does_not_double_the_overlay():
    """Two overlays at 8% read as 16% — a visibly darker document. The capture →
    render → export chain can hand the same HTML through more than one path."""
    once = wm.watermark_html("<html><body>x</body></html>")
    assert wm.watermark_html(once) == once
    assert once.count(f'<div class="{wm.MARKER_CLASS}"') == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_input_is_returned_untouched(blank):
    assert wm.watermark_html(blank) == blank


def test_the_mark_text_is_escaped():
    """The default is a constant, but the seam takes a string — keep it from
    being an injection point if a caller ever passes one through."""
    out = wm.watermark_html("<html><body>x</body></html>", "</span><script>evil()</script>")
    assert "<script>evil()</script>" not in out
    assert "&lt;script&gt;" in out


def test_the_overlay_cannot_swallow_clicks_or_selection():
    """It sits ON TOP of the content (see the module docstring), so it has to be
    inert — on the shared page the document underneath is still interactive."""
    css = wm.watermark_layer()
    assert "pointer-events:none" in css
    assert "user-select:none" in css


def test_the_overlay_is_hidden_from_assistive_tech():
    assert 'aria-hidden="true"' in wm.watermark_layer()


# ── footer ───────────────────────────────────────────────────────────────────

def test_the_footer_band_carries_the_url():
    tpl = wm.pdf_footer_template()
    assert wm.FOOTER_URL in tpl
    assert "https://www.sprntly.ai/" == wm.FOOTER_URL


def test_the_footer_band_styles_itself_explicitly():
    """Chromium renders header/footer templates against its own stylesheet at
    font-size 0 — an unstyled template prints as an invisible band."""
    tpl = wm.pdf_footer_template()
    assert "font-size:8px" in tpl
    assert "print-color-adjust:exact" in tpl


def test_the_footer_url_is_escaped():
    tpl = wm.pdf_footer_template("https://x.test/?a=1&b=2<script>")
    assert "<script>" not in tpl
    assert "&amp;" in tpl


def test_the_header_band_is_blank():
    """Chromium draws a header band whenever header/footer are enabled; left to
    its default it prints a title/date line we never asked for."""
    assert wm.PDF_HEADER_TEMPLATE == "<div></div>"


def test_the_footer_does_not_ride_in_the_document_body():
    """The wordmark repeats per page as a fixed layer, but a footer cannot: at
    bottom:0 it collides with the last line of a full page, and pushed into the
    margin it stops repeating and prints once, on the last page (measured). So
    the document body carries the mark only — the footer comes from Chromium."""
    out = wm.watermark_html("<html><body>x</body></html>")
    assert wm.FOOTER_URL not in out


# ── the part a stub cannot prove ─────────────────────────────────────────────

_PAGE = "<div style='{brk}height:600px;font:14px sans-serif'>Page {n}</div>"
_THREE_PAGE_DOC = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
    + _PAGE.format(brk="", n="one")
    + _PAGE.format(brk="break-before:page;page-break-before:always;", n="two")
    + _PAGE.format(brk="break-before:page;page-break-before:always;", n="three")
    + "</body></html>"
)


def _real_playwright_seam():
    from playwright.async_api import async_playwright

    return async_playwright


async def test_every_page_of_a_multi_page_pdf_carries_the_mark(monkeypatch):
    """Chromium repeats `position:fixed` onto each page in paged media, and
    paints the native footer band on each page too. Both are the entire reason
    one document-level element can mark a twelve-page report, so both are
    asserted against a real browser: if a Chromium upgrade drops either, this
    fails here instead of shipping reports marked only on page one.

    Skipped (not failed) where Chromium is not provisioned — the same
    honest-degrade posture the renderer itself takes.
    """
    pypdf = pytest.importorskip("pypdf", reason="pypdf is needed to read the PDF back")
    # Undo conftest's autouse stub: this one test wants the real browser.
    monkeypatch.setattr(rp, "_resolve_async_playwright", _real_playwright_seam)

    pdf = await rp.render_report_pdf(_THREE_PAGE_DOC)
    if pdf is None:
        pytest.skip("Chromium is not available in this environment")

    import io

    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 3, "the fixture should force exactly three pages"
    for i, page in enumerate(reader.pages, start=1):
        # letter-spacing puts gaps between the glyphs, so compare unspaced.
        text = "".join(page.extract_text().split())
        assert wm.WORDMARK in text, f"page {i} of the PDF is missing the watermark"
        assert wm.FOOTER_URL in text, f"page {i} of the PDF is missing the footer"


async def test_the_renderer_stamps_before_it_prints(monkeypatch):
    """The stamp lives in render_report_pdf so neither download route can reach
    page.pdf() unmarked. Capture what actually reaches the page."""
    seen: dict[str, str] = {}

    class _Page:
        async def route(self, *_a, **_k): pass
        async def set_content(self, html, **_k): seen["html"] = html
        async def wait_for_load_state(self, *_a, **_k): pass
        async def pdf(self, **_k): return b"%PDF-1.4 fake"

    class _Ctx:
        async def new_page(self): return _Page()
        async def close(self): pass

    class _Browser:
        async def new_context(self, **_k): return _Ctx()
        async def close(self): pass

    class _Chromium:
        async def launch(self, **_k): return _Browser()

    class _PW:
        chromium = _Chromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False

    monkeypatch.setattr(rp, "_resolve_async_playwright", lambda: (lambda: _PW()))

    assert await rp.render_report_pdf("<html><body><h1>Q3</h1></body></html>") == b"%PDF-1.4 fake"
    assert wm.MARKER_CLASS in seen["html"]
    assert "<h1>Q3</h1>" in seen["html"]

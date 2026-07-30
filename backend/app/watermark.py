"""Stamp the Sprntly wordmark and footer on any document that leaves the app.

Two marks, one layer: the wordmark rotated behind the content, and
``https://www.sprntly.ai/`` on the foot of every page. The footer is the part a
reader is meant to actually read and act on, so unlike the wordmark it is fully
opaque and sits in the bottom margin rather than over the text.

Applied to documents on their way OUT — a downloaded PDF, a file handed to Word,
a page shown to someone outside the workspace. Never to what a signed-in member
reads in the app: they know whose product it is, and a mark over the working copy
is just noise.

The mark is a text wordmark, always on. There is deliberately no plan/role/flag
that removes it — a watermark whose presence depends on state is a watermark you
have to reason about at every call site, and the one that matters is the copy
that got forwarded.

WHY AN OVERLAY AND NOT A BACKGROUND. The obvious "behind the content" reading is
``background-image`` on the page element, but these documents each paint their
own opaque surface (``prd.css`` gives ``.page`` a white background over a white
``body``), so a background sits UNDER that surface and never shows. Turning those
surfaces transparent would mean editing every skill template and would flatten
the card look the documents are designed around. Instead this is a top layer held
at ~8% alpha, which reads as ink in the paper while leaving the text underneath
fully legible — the look of "behind" without fighting each document's paint
order. ``pointer-events:none`` keeps it from swallowing clicks on the surfaces
that are still interactive.

WHY ``position:fixed``. In paged media a fixed element is laid out against the
PAGE box, and Chromium paints it on EVERY page — so one element marks a
twelve-page report without knowing where the breaks fall. This is load-bearing:
``backend/tests/test_watermark.py`` renders a forced three-page document through
real Chromium and asserts the mark lands on all three, so a Chromium change that
drops the repeat fails the suite instead of quietly shipping unmarked pages.

The style block is emitted with the layer at the END of the body, after the
document's own ``<style>`` in ``<head>``. Equal specificity, so source order
decides and ours wins — no ``!important`` and no edits to the skill templates.
"""
from __future__ import annotations

import re
from html import escape

#: The mark itself. Text, not the logo — it survives PDF, Word and print alike.
WORDMARK = "SPRNTLY"

#: Footer line, repeated on every page beneath the content.
FOOTER_URL = "https://www.sprntly.ai/"

#: Class on the injected layer, and the idempotence check. Re-stamping a document
#: that already carries the mark is a no-op rather than a second, darker overlay
#: (the capture → render → export chain can hand the same HTML through more than
#: one stamping path).
MARKER_CLASS = "sprntly-watermark"

# Brand accent (var(--accent) in the web app, --green in the PRD stylesheet). At
# 8% it tints rather than colours, and stays neutral on the cream/white surfaces
# every one of these documents uses.
_INK = "#179463"

# The footer is a real line of text meant to be read, not a tint — a mid grey
# that stays legible on white without competing with the document's own type.
_FOOTER_INK = "#8C8A84"

_CSS = f"""
.{MARKER_CLASS}{{
  position:fixed;top:0;left:0;right:0;bottom:0;
  z-index:2147483000;
  pointer-events:none;
  display:flex;align-items:center;justify-content:center;
  overflow:hidden;
  margin:0;padding:0;border:0;background:none;
}}
.{MARKER_CLASS} span{{
  display:block;
  font:700 120px/1 'Inter',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
  /* .1em of left indent offsets the trailing letter-space so the rotated mark
     sits optically centred. 120px is the practical ceiling: at -30deg the run
     spans ~5.7x the font size, and A4 inside 12mm margins is ~703px. */
  letter-spacing:.2em;text-indent:.1em;
  white-space:nowrap;
  color:{_INK};
  opacity:.08;
  transform:rotate(-30deg);
  -webkit-user-select:none;user-select:none;
  -webkit-print-color-adjust:exact;print-color-adjust:exact;
}}
"""

# Chromium's own footer band, for `page.pdf(display_header_footer=True)`.
#
# The report PDF uses THIS rather than the CSS footer above, because only the
# native band paints inside the page MARGIN. A `position:fixed` element repeats
# per page only while it lies within the page box; pushed into the margin with a
# negative offset it stops repeating and prints once, on the last page — measured,
# not assumed. Keeping it at `bottom:0` inside the box would repeat correctly but
# would collide with the last line of a full page, so the margin band is the only
# place a footer can be both per-page and out of the way.
#
# Chromium renders this template with its own default stylesheet at font-size 0,
# so every value here has to be explicit.
_PDF_FOOTER_TEMPLATE = (
    '<div style="width:100%;font-family:Helvetica,Arial,sans-serif;font-size:8px;'
    f"font-weight:500;letter-spacing:.04em;color:{_FOOTER_INK};text-align:center;"
    'padding:0 12mm;-webkit-print-color-adjust:exact;print-color-adjust:exact;">'
    "{url}</div>"
)
# Chromium always draws a header band when header/footer are enabled; an empty
# div keeps it blank rather than falling back to its default title/date line.
PDF_HEADER_TEMPLATE = "<div></div>"


def pdf_footer_template(footer: str = FOOTER_URL) -> str:
    """The footer band Chromium paints on every page of the report PDF."""
    return _PDF_FOOTER_TEMPLATE.format(url=escape(footer))

_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def watermark_layer(text: str = WORDMARK) -> str:
    """The `<style>` + layer markup on its own, for callers that assemble a
    document themselves rather than patching a finished one.

    Wordmark only — the footer for this path comes from
    :func:`pdf_footer_template`, not from the document body.
    """
    return (
        f"<style>{_CSS}</style>"
        f'<div class="{MARKER_CLASS}" aria-hidden="true"><span>{escape(text)}</span></div>'
    )


def watermark_html(html: str, text: str = WORDMARK) -> str:
    """Return *html* with the wordmark layer stamped behind its content.

    Inserted before ``</body>`` when there is one, appended otherwise (the
    evidence brief and some report templates are fragment documents with no
    ``<body>``). Blank input and already-stamped input are returned untouched.
    Never raises: a document that cannot be stamped is still a document the user
    asked for, and losing the download to a formatting edge case would be the
    worse failure.
    """
    if not html or not html.strip():
        return html
    if MARKER_CLASS in html:
        return html
    layer = watermark_layer(text)
    if _BODY_CLOSE_RE.search(html):
        return _BODY_CLOSE_RE.sub(lambda m: layer + m.group(0), html, count=1)
    return html + layer

"""A report's body, as HTML.

A report is a rich document now — read in the panel, edited there under the same
toolbar the PRD and the team document use, rendered to PDF and served behind a
public share link. All four of those want HTML. The pipelines that WRITE reports
answer in markdown (the pinned HTML templates went with #1024 and are not coming
back — a report is prose the model composes, not a form it fills), so the two
ends are converted here rather than each consumer sniffing and guessing.

WHERE THE CONVERSION HAPPENS. At capture, so a report is STORED as HTML and
every reader downstream gets the same bytes. Rows written before this — the
scheduled monthly runs, and everything captured since #1024 — hold markdown, and
`to_html` converts them on read so they render and edit identically. They are
rewritten as HTML the first time someone saves an edit, which is the only moment
a one-way conversion is something the user asked for.

WHAT COUNTS AS ALREADY-HTML. Any body whose first non-space character opens a
tag. This is DELIBERATELY wider than `report_capture.looks_like_html_report`,
which matches only a self-contained document (`<!doctype`, `<html>`, `<div>`,
`<style>`, `<meta>`) and is the sniff CHAT uses to choose an iframe. A converted
report starts `<h1>` or `<p>` — a fragment, not a document — so the narrow test
would call it markdown and hand it back to a markdown renderer, which prints the
tags at the reader. Two questions, two tests: "is this a whole HTML document?"
and "is this HTML at all?".

SANITISED ON THE WAY OUT. The markdown being converted is model output, and a
model that emits raw `<script>` in its answer would otherwise have it survive
conversion into a body the app renders. `sanitize_artifact_html` is the same
allow-list the team documents are stored under, so a report and a document can
hold exactly the same tags — which is what lets one editor serve both.
"""
from __future__ import annotations

import logging
import re

from app.custom_artifact_html import sanitize_artifact_html
from app.html_report import looks_like_html_report

logger = logging.getLogger(__name__)

#: Any body that OPENS with a tag is already HTML. Anchored and non-greedy: a
#: markdown report that merely mentions `<script>` mid-sentence is still
#: markdown, and converting it is what escapes that mention safely.
_OPENS_WITH_TAG = re.compile(r"^\s*<[a-zA-Z!/]")

#: `tables` because every report writes one (RICE grids, prevalence counts) and
#: without it the pipes render as literal text; `fenced_code` because the
#: engines fence their examples; `sane_lists` so a list interrupted by prose
#: does not silently renumber from 1.
_EXTENSIONS = ["tables", "fenced_code", "sane_lists"]


def is_html_body(body: str | None) -> bool:
    """Is this report body HTML rather than markdown?

    See the module note on why this is wider than the document sniff.
    """
    return bool(_OPENS_WITH_TAG.match(body or ""))


def to_html(body: str | None) -> str:
    """A report's body as sanitised HTML. Already-HTML passes through.

    NEVER RAISES. A conversion failure returns the body untouched: the reader
    then sees markdown source rather than a rendered document, which is worse
    than the alternative but is not a 500 on a report they can otherwise read.
    """
    text = body or ""
    if not text.strip():
        return ""
    if looks_like_html_report(text):
        # A SELF-CONTAINED document -- doctype, head, its own <style>. It is
        # served into a sandboxed iframe precisely because it owns its own
        # rendering, and the fragment allow-list below would strip the head and
        # the styles and leave a naked body. Untouched, as it has always been.
        return text
    if is_html_body(text):
        # An HTML FRAGMENT -- what this module produces, and what the panel's
        # editor saves. Sanitised on the same allow-list team documents use.
        return sanitize_artifact_html(text)
    try:
        import markdown as md

        return sanitize_artifact_html(md.markdown(text, extensions=_EXTENSIONS))
    except Exception:  # noqa: BLE001 — a readable report beats a 500
        logger.exception("report markdown→HTML conversion failed")
        return text

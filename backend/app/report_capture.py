"""Capture a chat-generated report as a durable `reports` artifact.

A report answer gets kept. The chat thread renders it and then, until this
module existed, lost it: the document lived only in `ask_jobs.response`, so it
was never listed on /artifacts, could not be downloaded, and could not be
shared. This is the capture side, writing the document to `reports` so it
becomes a listable, downloadable, shareable artifact.

WHAT COUNTS AS A REPORT — two shapes, one for each era of this product:

  1. `_report: True` on the payload. The report ENGINES stamp it on the one
     return that is an actual document (`competitive_intel`, `market_intel`,
     `public_feedback`, `call_digest`'s VoC synthesis, `company_research`'s
     findings) and deliberately NOT on their degraded apologies, which carry
     `_skill` all the same. It is the same marker `monthly_reports._is_report`
     already gates the scheduled runs on — one definition of "here is the
     report" rather than a second one to keep in sync.
  2. A self-contained HTML DOCUMENT (`_HTML_DOC_RE`, the sniff the renderer
     uses). The pinned report templates are gone, so nothing produces this
     shape today; reports captured back when they did still render, and any
     future HTML-shaped answer is still captured.

Shape 1 exists because of a regression this module shipped: the policy USED to
be the HTML sniff alone, and when the pinned templates were removed (#1024,
"answer directly — delete the built-in skill layer and the pinned report
formats") every report pipeline started answering in markdown. The sniff simply
stopped matching, capture self-disabled for all of them, and chat-generated
reports stopped reaching the library entirely — no artifacts row, and (because
the row is also what carries `conversation_id`) no way back to the thread that
produced one. The self-disabling was recorded here as a feature at the time.

BEST-EFFORT BY CONTRACT. Capture runs after the answer is finished, so it can
only ever add an artifact — it must never break, delay, or alter the reply the
user is reading. `capture_report` therefore swallows everything it raises and
returns None; the call site needs no guard of its own.

ATTACHMENT. A report is generated somewhere and that provenance is part of what
it is, so the originating ask's `conversation_id` / `prd_id` ride along: set
when the ask carried them, NULL when it didn't. Presence of the id IS the
attachment — there is no separate "attached" flag to keep in sync. It is what
lets /artifacts open a report over the chat that produced it, and what the
thread's own Reports panel lists on.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from html import unescape

from app import report_markdown
from app.labels import humanize_label
from app.llm import strip_code_fence

logger = logging.getLogger(__name__)

# Same sniff as the frontend's `looksLikeHtmlBrief` (web/app/lib/htmlBrief.ts):
# a document answer opens with one of these tags. Kept deliberately identical —
# if the two ever disagree, chat renders a report the library doesn't hold (or
# the reverse).
_HTML_DOC_RE = re.compile(r"^\s*<(?:!doctype|meta|html|div|style)\b", re.IGNORECASE)

# Skills whose HTML output ALREADY has an artifact home. Capturing these would
# list the same document twice on /artifacts, under two different types:
#   prd-author     → `prds`
#   evidence-brief → `evidences`
# Neither is reachable from a chat turn any more (both are bound by name from
# their own pipelines — prd_runner / evidence_kg — and chat can no longer route
# to a built-in at all), so this frozenset is belt-and-braces rather than a live
# gate. Kept because the exclusion is about the ARTIFACT TYPE, not about who can
# reach it: if either pipeline ever routes an answer through capture, listing
# the same document under two types is still the wrong outcome.
SKILLS_WITH_OWN_ARTIFACT: frozenset[str] = frozenset({"prd-author", "evidence-brief"})

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# A markdown report's title is its first heading — the shape every engine opens
# with ("# Voice of customer · 1–30 Jun"). Checked only after the HTML patterns
# miss, so an HTML document's <title> still wins.
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Long enough for "Voice of Customer Report · 1 Mar – 30 Jun 2026", short enough
# that a runaway <h1> can't become the artifacts row label.
_TITLE_MAX = 200


def looks_like_html_report(answer: str | None) -> bool:
    """Is this answer a self-contained HTML document (rather than markdown)?

    Unwraps a stray ```html fence first, exactly as the renderer does — models
    occasionally fence the document despite being told not to.
    """
    return bool(_HTML_DOC_RE.match(strip_code_fence(answer or "")))


def is_report_payload(payload: dict) -> bool:
    """Is this payload a finished report document?

    `_report` is the engines' own marker for "here is the report", stamped on
    the ONE return that is a document and never on the degraded apologies —
    those carry `_skill` too, which is why `_skill` alone cannot decide this
    (`competitive_intel.py`'s comment on the flag records the same reasoning).
    The HTML sniff stays as the second shape: reports captured before the
    pinned templates were removed, and any future document-shaped answer.
    """
    return (
        payload.get("_report") is True
        or looks_like_html_report(payload.get("answer"))
    )


def report_title(doc: str, skill: str) -> str:
    """The report's display title: its <title>, else its first <h1>, else its
    first markdown heading, else the skill's humanised label ("Voice of
    customer report").

    Denormalised into the row so listing artifacts never has to parse the
    document.
    """
    for pattern in (_TITLE_RE, _H1_RE):
        m = pattern.search(doc)
        if not m:
            continue
        text = unescape(_TAG_RE.sub(" ", m.group(1)))
        text = " ".join(text.split())
        if text:
            return text[:_TITLE_MAX]
    m = _MD_HEADING_RE.search(doc)
    if m:
        text = " ".join(m.group(1).split())
        if text:
            return text[:_TITLE_MAX]
    return humanize_label(skill)


def capture_report(
    payload: dict,
    *,
    company_id: str,
    question: str = "",
    workspace_id: str | None = None,
    ask_id: int | None = None,
    conversation_id: int | None = None,
    prd_id: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> int | None:
    """Persist `payload`'s answer as a report when it is one; return its id.

    Returns None — never raises — when the answer isn't a report document, when
    the skill owns another artifact type, when routing produced no skill to
    attribute it to, when the ask was cancelled, or when the save itself fails.

    `is_cancelled` is consulted only once the answer is known to be a report, so
    an ordinary markdown ask never pays for the check: a Stop during the final,
    un-interruptible LLM call leaves `complete_ask_job` a no-op and the answer
    discarded, and a report captured from it would be an artifact for a reply the
    user never saw.
    """
    try:
        skill = payload.get("_skill")
        # No routed skill means nothing to badge or label the artifact with, and
        # no report skill produced it — not a report.
        if not skill or skill in SKILLS_WITH_OWN_ARTIFACT:
            return None
        if not is_report_payload(payload):
            return None
        if is_cancelled is not None and is_cancelled():
            logger.info("report capture skipped — ask cancelled ask_id=%s", ask_id)
            return None

        # Store the unwrapped document: the viewer, the PDF render and the share
        # link all serve this column directly, and a stray fence would leak into
        # every one of them. Markdown is stored VERBATIM — `ReportsTab` and the
        # public `/r/<token>` viewer both sniff the stored body and render a
        # non-HTML one through react-markdown (no `rehype-raw`), so escaping
        # here would print the escapes, and wrapping it would defeat the sniff.
        # A report is STORED AS HTML: it is a rich document now, read in the
        # panel and edited there under the same toolbar the PRD and the team
        # document use, and rendered to PDF and behind a public link. The
        # pipelines answer in markdown, so the conversion happens once, here,
        # rather than in each of the four readers. Sanitised inside `to_html`
        # with the same allow-list team documents are stored under — which is
        # what lets one editor serve both.
        doc = report_markdown.to_html(strip_code_fence(payload.get("answer") or ""))

        from app import db

        report_id = db.save_report(
            company_id,
            skill=skill,
            title=report_title(doc, skill),
            html=doc,
            question=question,
            workspace_id=workspace_id,
            ask_id=ask_id,
            conversation_id=conversation_id,
            prd_id=prd_id,
        )
        logger.info(
            "report captured id=%s skill=%s company=%s conversation=%s prd=%s",
            report_id, skill, company_id, conversation_id, prd_id,
        )
        return report_id
    except Exception:  # noqa: BLE001 — the answer already rendered; only the
        # artifacts library is degraded, and one lost row must not surface as a
        # failed ask.
        logger.exception("report capture failed for ask_id=%s", ask_id)
        return None

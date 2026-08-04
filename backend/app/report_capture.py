"""Capture a chat-generated HTML report as a durable `reports` artifact.

An answer that is a SELF-CONTAINED HTML DOCUMENT rather than markdown gets kept.
The chat thread already detects that and renders it in a sandboxed iframe
(web/app/components/shared/HtmlReportView.tsx via `looksLikeHtmlBrief`); until
this module existed nothing kept it, so the document died with the `ask_jobs`
row. This is the capture side: the same sniff, server-side, writing the document
to `reports` so it becomes a listable, downloadable, shareable artifact.

`_HTML_DOC_RE` IS THE WHOLE POLICY, and it is why nothing here needed changing
when the pinned report templates were removed. VoC, competitive-intelligence and
public-feedback answers used to be rendered HTML and were captured; they are
markdown now, so the sniff simply stops matching and capture self-disables for
them — no allow-list to prune, no dead branch. Reports already in the library
keep rendering, and any future HTML-shaped answer is still captured. The
`reports` table, its routes, sharing and PDF are all untouched.

BEST-EFFORT BY CONTRACT. Capture runs after the answer is finished, so it can
only ever add an artifact — it must never break, delay, or alter the reply the
user is reading. `capture_report` therefore swallows everything it raises and
returns None; the call site needs no guard of its own.

ATTACHMENT. A report is generated somewhere and that provenance is part of what
it is, so the originating ask's `conversation_id` / `prd_id` ride along: set
when the ask carried them, NULL when it didn't. Presence of the id IS the
attachment — there is no separate "attached" flag to keep in sync.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from html import unescape

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

# Long enough for "Voice of Customer Report · 1 Mar – 30 Jun 2026", short enough
# that a runaway <h1> can't become the artifacts row label.
_TITLE_MAX = 200


def looks_like_html_report(answer: str | None) -> bool:
    """Is this answer a self-contained HTML document (rather than markdown)?

    Unwraps a stray ```html fence first, exactly as the renderer does — models
    occasionally fence the document despite being told not to.
    """
    return bool(_HTML_DOC_RE.match(strip_code_fence(answer or "")))


def report_title(html_doc: str, skill: str) -> str:
    """The report's display title: its <title>, else its first <h1>, else the
    skill's humanised label ("Voice of customer report").

    Denormalised into the row so listing artifacts never has to parse HTML.
    """
    for pattern in (_TITLE_RE, _H1_RE):
        m = pattern.search(html_doc)
        if not m:
            continue
        text = unescape(_TAG_RE.sub(" ", m.group(1)))
        text = " ".join(text.split())
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

    Returns None — never raises — when the answer isn't an HTML document, when
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
        answer = payload.get("answer") or ""
        if not looks_like_html_report(answer):
            return None
        if is_cancelled is not None and is_cancelled():
            logger.info("report capture skipped — ask cancelled ask_id=%s", ask_id)
            return None

        # Store the unwrapped document: the viewer, the PDF render and the share
        # link all serve this column directly, and a stray fence would leak into
        # every one of them.
        doc = strip_code_fence(answer)

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

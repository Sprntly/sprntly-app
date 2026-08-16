"""Write a custom artifact — a document of whatever kind the user asked for.

The generator behind "draft a leadership update on the Q3 reliability work".
There is no skill, no template and no fixed section list here, and that is the
design rather than an omission: the product promise is a document for ANYTHING,
so the shape has to come from the request. A PRD generator can hard-code its
sections because every PRD has them; "leadership update", "launch plan",
"postmortem", "customer FAQ" and "board memo" share nothing except being prose
a person will read.

WHAT IS FIXED is therefore only the two things the app depends on:

  * the OUTPUT FORMAT — an HTML fragment in the same small vocabulary the
    editor and the sanitizer speak, so a generated document is immediately
    editable rather than a blob the toolbar cannot manipulate. The LLM already
    writes HTML for evidence briefs (#1108), so this reuses a working contract;
  * the GROUNDING RULE — the document may only assert what the supplied context
    supports, and says so plainly when the context is thin. A leadership update
    that invents a revenue number is worse than no leadership update, because
    it is the artifact most likely to be forwarded without being checked.

GROUNDING SOURCE. Context comes from the caller (the chat turn that asked),
not from a fresh retrieval pass here. The chat already resolved what the thread
is about — that is what the planner's `task` brief is — and re-running
retrieval would answer a different question from the one the user watched being
answered. A generation started from the library with no chat behind it simply
has less context, and the prompt's honesty rule covers that case.

LIFECYCLE mirrors ticket sets: the row is created `generating` BEFORE the
multi-minute call so the panel has an id to open and poll, and this module
flips it to `ready` or `failed`. A backend restart mid-generation orphans the
row — a documented, recurring event here — so `sweep_orphan_generating()` runs
at startup and fails them honestly rather than leaving a document that spins
forever.
"""
from __future__ import annotations

import logging
import math

from app.custom_artifact_html import sanitize_artifact_html
from app.db.client import require_client, retry_on_disconnect
from app.db.custom_artifacts import fail_artifact, finish_artifact
from app.graph.gateway import llm_call
from app.llm import LONG_REQUEST_TIMEOUT_S, MAX_ATTEMPTS, strip_code_fence

logger = logging.getLogger(__name__)

_AGENT = "chat"
# Answer-tier sonnet, matching the model-tiering policy's default. This writes
# prose a human forwards to their leadership; it is not a classification.
_MODEL = "claude-sonnet-4-6"
_PROMPT_VERSION = "custom-artifact-v1"

# Long enough for a real document, bounded so a runaway generation cannot write
# a megabyte into the shared database.
_MAX_TOKENS = 8_000

# The tag vocabulary is stated to the model AND enforced by the sanitizer, so a
# stray tag is dropped rather than rendered. Keeping the two lists in sight of
# each other is deliberate: a model told it may use <h2> while the sanitizer
# strips <h2> produces documents that silently lose their headings.
_SYSTEM = """You write documents for a product team inside Sprntly.

The user names the KIND of document they want (a leadership update, a launch \
plan, a postmortem, a customer FAQ, a board memo — anything). There is no fixed \
template. Work out what that kind of document should contain for THIS audience \
and THIS subject, and write that.

OUTPUT
Return an HTML fragment and nothing else. No <html>, <head>, <body> or <style> \
wrapper, no markdown, no code fence, no commentary before or after.

Use only these tags: <h1> <h2> <h3> <p> <strong> <em> <u> <ul> <ol> <li> \
<blockquote> <table> <thead> <tbody> <tr> <th> <td> <a href> <code> <hr> <br>.
Anything else is stripped before storage, so a heading in an unsupported tag \
is a heading the user loses.

Open with a single <h1> naming the document. Then write it.

GROUNDING — THIS IS THE PART THAT MATTERS
Everything factual must come from the CONTEXT below. This document will be \
forwarded to people who were not in the conversation and who will not check it.

  * Never invent a number, a date, a customer name, a metric or a quote. If the \
context does not contain it, the document does not claim it.
  * Where the context is thin, say so in the document, in the user's own \
register — "we do not yet have adoption numbers for this" reads as competence. \
A confident fabrication does not.
  * Do not pad. A short document that is true beats a long one that is padded \
with generic advice. If there is only enough material for four paragraphs, \
write four paragraphs.
  * No placeholder text. Never write [Author name], [insert metric], TBD or \
similar — an unfilled placeholder is the single most common way a generated \
document embarrasses the person who forwards it. If you do not know the \
author, do not write an author line at all."""


def _render_input(*, kind: str, task: str, context: str) -> str:
    kind_line = kind.strip() or "document"
    parts = [
        f"DOCUMENT KIND: {kind_line}",
        "",
        "WHAT THE USER ASKED FOR:",
        task.strip() or f"Write a {kind_line}.",
    ]
    if context.strip():
        parts += ["", "CONTEXT (the only facts you may assert):", context.strip()]
    else:
        # Said explicitly rather than left as an empty section, so the model
        # treats "no context" as a stated condition to write honestly under
        # rather than as an accident it should paper over.
        parts += [
            "",
            "CONTEXT: none was supplied. Write the document's structure and "
            "the parts that follow from the request alone, and state plainly "
            "inside the document which sections need facts the team must fill "
            "in — describing what is missing, never leaving a placeholder token.",
        ]
    return "\n".join(parts)


def _title_from(html: str, fallback: str) -> str:
    """The document's own <h1> is its title, so the library row and the first
    line of the document cannot disagree. Falls back to the requested kind."""
    from bs4 import BeautifulSoup

    h1 = BeautifulSoup(html, "html.parser").find("h1")
    text = h1.get_text().strip() if h1 else ""
    return (text or fallback or "Untitled document")[:300]


def generate_into(
    *,
    company_id: str,
    artifact_id: int,
    kind: str,
    task: str,
    context: str = "",
) -> None:
    """Write the document and land it on the row. Never raises.

    Total by contract: every failure path records `failed` on the row, because
    the panel polls that row and an exception escaping here would leave it
    spinning on a generation that is not running. The stored error string is
    for operators; the web maps failures onto its own recovery copy.
    """
    try:
        result = llm_call(
            enterprise_id=company_id,
            agent=_AGENT,
            purpose="custom_artifact",
            system=_SYSTEM,
            input=_render_input(kind=kind, task=task, context=context),
            prompt_version=_PROMPT_VERSION,
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            long_output=True,
        )
        html = sanitize_artifact_html(strip_code_fence(result.text or ""))
        if not html.strip():
            # A generation that produced nothing is a failure, not an empty
            # document: an empty document looks like the user's own blank page
            # and hides the fact that a call was made and came back with
            # nothing.
            fail_artifact(company_id, artifact_id, "generation returned no content")
            return
        finish_artifact(
            company_id, artifact_id, title=_title_from(html, kind), body_html=html
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring's total contract
        logger.exception("custom artifact %s generation failed", artifact_id)
        fail_artifact(company_id, artifact_id, str(exc))


# How long a document may sit in `generating` before a sweep calls it orphaned.
#
# DERIVED FROM THE CALL'S OWN CEILING rather than picked, because the two must
# not drift: `custom_artifacts` rows carry NO HEARTBEAT (unlike `ask_jobs`,
# which bump theirs precisely so a sweep cannot fail a long-but-healthy job),
# so `updated_at` is stamped once at creation and age is the ONLY signal. A gate
# shorter than the longest possible healthy run therefore does not just fail
# early — it stamps `failed` on a generation that is still writing, shows the
# user a failure, and then `finish_artifact` lands the document afterwards and
# flips the row to `ready`. Telling someone their document died and then
# silently producing it is worse than either outcome alone.
#
# A single call can take MAX_ATTEMPTS × LONG_REQUEST_TIMEOUT_S (4 × 600s = 40
# minutes) before backoff, and it also queues on the process-wide `_llm_gate`
# behind every other generation. The doubling plus headroom covers the queue.
#
# THIS MATTERS MORE NOW THAN IT DID: while this sweep only ran at startup, a
# too-short gate was mostly theoretical (the process that owned the generation
# had died by then, or it would not be sweeping). Running it every 5 minutes
# points it at LIVE generations owned by this very process.
#
# The cost of the wider gate is honest and small: a genuinely orphaned document
# now spins for up to 90 minutes instead of 30 before it is marked failed. A
# late true failure beats a prompt false one.
_MAX_CALL_MINUTES = math.ceil(MAX_ATTEMPTS * LONG_REQUEST_TIMEOUT_S / 60)  # 40
ORPHAN_AFTER_MINUTES = _MAX_CALL_MINUTES * 2 + 10  # 90
ORPHAN_ERROR = "interrupted by a server restart"


@retry_on_disconnect
def sweep_orphan_generating(older_than_minutes: int = ORPHAN_AFTER_MINUTES) -> int:
    """Fail documents abandoned in `generating` by a dead worker; return how many.

    When the process dies mid-generation the owning task goes with it, so
    nothing will ever move the row to a terminal state. The panel polls that
    row, so an interrupted generation spins forever with no error to explain it
    and no way to retry — the failure `invalidate_orphan_generating_prds` and
    `fail_orphan_generating_ask_jobs` exist to prevent for their own tables.

    IMPORTANT — why an AGE CUTOFF rather than "fail everything generating":
    STAGING AND PROD SHARE ONE SUPABASE PROJECT, so both environments' rows live
    in this table. A blanket sweep at staging startup would fail documents prod
    was writing at that moment, and vice versa. Age is the only signal that
    separates "the owner is dead" from "the owner is another live process",
    because rows carry no owner or heartbeat column. This is exactly the
    reasoning `fail_orphan_generating_ask_jobs` records; the PRD sweep predates
    the shared-project setup and is the pattern NOT to copy.

    The status filter is repeated on the UPDATE so a row that finished between
    the select and the write is not stamped failed over its own good content.
    """
    from datetime import datetime, timedelta, timezone

    # Second precision, matching `db.client.utc_now()` — the format every row's
    # `updated_at` is written in. A cutoff carrying microseconds compares
    # lexically against a timestamp that does not, which is a difference that
    # only shows up at the boundary and only outside Postgres.
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).replace(microsecond=0).isoformat()
    c = require_client()
    rows = (
        c.table("custom_artifacts").select("id")
        .eq("status", "generating")
        .lt("updated_at", cutoff)
        .execute().data or []
    )
    ids = [r["id"] for r in rows]
    if ids:
        c.table("custom_artifacts").update(
            {"status": "failed", "error": ORPHAN_ERROR}
        ).in_("id", ids).eq("status", "generating").execute()
    return len(ids)

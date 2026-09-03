"""Apply a chat instruction to the REPORT or DOCUMENT open beside the chat.

`goal_report_chat_edit.py`'s shape, applied to the two artifacts that had no
edit path at all. Read that file first: the split it makes — a writer that owns
the tenant gate, a target that never comes from the model, an edit that is live
on call — is the part being reused, and the reasons are the same ones. What is
NOT reused is its prompt: that one is tuned for a Goal Analysis run (unsized
findings, coverage notes, an HTML fragment), and pointing it at a markdown
voice-of-customer report would edit a document by rules written about a
different one.

WHY THIS EXISTS. A report or a document could be generated, read, shared and
downloaded — and not changed. Asked to "convert the RICE section into a table",
chat did the only thing it could: it wrote the table into the conversation and
told the user to paste it in themselves. The document sitting in the panel two
inches away was not editable by anything except hand.

★ THE TARGET IS NEVER THE MODEL'S ★

Both writers take an id from the SURFACE — the artifact the user has open — and
an instruction. There is no id in any prompt or tool schema, so there is no id
for a model to get wrong, and a prompt-injected instruction inside a customer's
own document cannot name someone else's report. That is the `edit_prd` rule,
here for the reason it is there.

★ SHAPE IS PRESERVED, NOT CHOSEN ★

A report's stored body is markdown today and was a self-contained HTML document
before the pinned templates were removed; a team document is sanitized HTML.
The editor is told to return the document in the SHAPE it was given, because
both readers sniff the stored body to decide how to render it
(`ReportsTab`/`PublicReportViewer`) — an edit that quietly converted markdown to
HTML would change how the whole document renders, which is not what "make that
section a table" asked for.

★ AN EDIT THAT ISN'T ONE WRITES NOTHING ★

"What does this say about pricing?" is a question, not an edit. The editor
answers with an empty `sections_changed`, and both writers then leave the stored
row untouched — no version bump, no `updated_at`, nothing for a reader to
notice. A no-op save is not free: on a document it moves the version a
colleague's editor is holding.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.db.custom_artifacts import (
    BodyTooLarge,
    VersionConflict,
    get_artifact,
    update_artifact,
)
from app.db.reports import get_report, update_report_body
from app.graph.gateway import llm_call
from app.custom_artifact_html import sanitize_artifact_html
from app import report_markdown

logger = logging.getLogger(__name__)

_AGENT = "qa"
#: v2: the FORMAT rule became per-artifact, so an evidence page — a whole
#: HTML document with its own stylesheet and hand-authored SVG charts — is
#: no longer edited under a rule written for prose in a dozen tags.
EDIT_PROMPT_VERSION = "artifact-chat-edit-v2"

#: The stored body of a report or a document, and how big an edit may return.
#: Matches the full-emit budget the Goal Analysis editor uses — the two are the
#: same job on a different document, and a report that does not fit in one
#: re-emit could not have been written by one either.
_MAX_TOKENS = 32000

_EDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "document": {"type": "string"},
        "sections_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["document", "sections_changed", "summary"],
}

#: The FORMAT half of the editor's contract, and the one thing that is not
#: the same for every artifact. A report and a team document are prose in a
#: small tag vocabulary; an evidence page is a complete, self-contained
#: visual brief with its own stylesheet, layout classes and hand-authored
#: SVG charts. Handing the prose rule to an evidence edit is how "add a
#: chart to the evidence" comes back as a page stripped of everything that
#: made it one.
_SHAPE_PROSE = """\
FORMAT. Return the document in the SAME FORMAT you were given it. Markdown in, \
markdown out; HTML in, HTML out. The product decides how to render this \
document by looking at the stored text, so a format change is a rendering \
change nobody asked for. When the document is HTML, use only these tags: h1, \
h2, h3, h4, p, strong, em, u, s, ul, ol, li, blockquote, code, pre, a, br, hr, \
table, thead, tbody, tr, th, td — everything else is stripped on save."""

_SHAPE_EVIDENCE = """\
FORMAT. This is a COMPLETE, SELF-CONTAINED HTML PAGE \
— its own <style> block, its own layout classes, and charts \
hand-authored as inline SVG. Return the WHOLE page, and keep all of \
that: the stylesheet, the `wrap` container, the class names, the \
section order, and every chart already in it. Add nothing the page \
cannot render on its own — no <script>, no chart library, no \
external stylesheet, no remote image. The page renders with scripts \
disabled, so each of those is a blank space where a reader expects \
content.

A NEW CHART IS HAND-AUTHORED SVG, in the visual language of the charts \
already there: the same class names, the same type sizes, the same \
palette. Plot only numbers already in this page or in the instruction \
— a chart is the most believable thing on a page, so an invented \
data point does more damage here than an invented sentence would. \
Caption it with the takeaway as a complete sentence, the way the \
captions already there do. If the numbers for the chart you were asked \
for are not in the page, change NOTHING and say so in `summary` rather \
than drawing a plausible one."""


_EDIT_SYSTEM_TEMPLATE = """\
You are Sprntly's document editor. You are given ONE document the user has open \
beside their chat, and ONE edit instruction they typed ("convert the RICE \
section into a table", "cut the appendix", "rewrite the summary for an exec", \
"add a risks section"). Apply the instruction with the MINIMAL change necessary \
and return the whole document back.

{shape_rule}

RULES
- Change ONLY what the instruction affects. Leave every other section \
byte-for-byte as it was, in the same order. A reformat of one section is not \
licence to re-word the rest.
- INVENT NOTHING. No new findings, numbers, quotes, sources or conclusions. \
This document reports work that was already done; anything not already in it is \
a fabrication carrying the credibility of the real research around it. \
Restructuring existing content into a table is a formatting change and is \
allowed; filling an empty cell with a plausible number is not.
- Keep every source, citation and attribution attached to the claim it \
supports. Do not drop them to tighten prose.
- If the instruction does not actually ask for a change — it is a question \
about the document, or a comment — return the document UNCHANGED, with an \
empty `sections_changed` and a `summary` that says no edit was needed.

Return the FULL updated document in `document`, the human-readable names of the \
sections you changed in `sections_changed`, and a one-line `summary` of what \
you did."""

#: Built once per shape rather than per call: the system block is
#: cache-controlled, and a string rebuilt per call would fragment that
#: cache.
_EDIT_SYSTEM = _EDIT_SYSTEM_TEMPLATE.format(shape_rule=_SHAPE_PROSE)
_EDIT_SYSTEM_EVIDENCE = _EDIT_SYSTEM_TEMPLATE.format(shape_rule=_SHAPE_EVIDENCE)

_EDIT_USER = """\
Apply this edit instruction to the {label} below.

INSTRUCTION: {instruction}

{label_upper} (edit and return the whole thing, in the same format):
{document}
"""


def apply_edit(
    document: str, instruction: str, *, enterprise_id: str,
    label: str = "document", shape: str = "prose",
) -> dict:
    """Run the editor over one document. `{"document", "sections_changed", "summary"}`.

    Raises RuntimeError when the model returns nothing usable, so the caller
    leaves the stored row untouched rather than writing an empty document over
    a report someone waited minutes for.

    `label` only names the artifact in the prompt ("report" / "document" /
    "evidence page"). `shape` picks the FORMAT rule, and that is the one
    thing which genuinely differs: "prose" for a report or a team document,
    "evidence" for the self-contained visual brief, whose stylesheet,
    classes and inline SVG charts a prose rule would tell the model to throw
    away. Every other rule is identical for all three, because the thing
    being protected — the provenance of work already done — is the same in
    each.
    """
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose="apply_artifact_chat_edit",
        prompt_version=EDIT_PROMPT_VERSION,
        system=_EDIT_SYSTEM_EVIDENCE if shape == "evidence" else _EDIT_SYSTEM,
        input=_EDIT_USER.format(
            label=label, label_upper=label.upper(), instruction=instruction,
            document=document,
        ),
        json_schema=_EDIT_SCHEMA,
        max_tokens=_MAX_TOKENS,
        # A report runs to tens of thousands of characters and comes back whole;
        # a non-streamed call of that size hits the Anthropic read timeout.
        long_output=True,
    )
    # `.output`, NOT `.text` — `LLMResult` carries `output`, and reading `.text`
    # is the bug (#1188) that made every chat-written document raise
    # AttributeError for three days while twenty tests passed.
    out = result.output if isinstance(result.output, dict) else {}
    edited = (out.get("document") or "").strip()
    if not edited:
        raise RuntimeError("the edit returned an empty document")
    sections = out.get("sections_changed") or []
    return {
        "document": edited,
        "sections_changed": [s for s in sections if isinstance(s, str)],
        "summary": (out.get("summary") or "").strip(),
    }


def _actor(company) -> str:
    """Who to record as `updated_by`. Total — never raises on a context object
    carrying neither field (the `project_chat_edit._actor` chain)."""
    return (
        getattr(company, "user_id", None)
        or getattr(company, "user_email", None)
        or "auto"
    )


def edit_report_scoped(report_id: int, instruction: str, company) -> dict:
    """Apply a chat instruction to a captured REPORT. `{"report", "sections_changed", "summary"}`.

    TENANCY: `get_report` filters `company_id` in the query, so a foreign id
    reads as absent and becomes a 404 — never a 403. "Exists but not yours" must
    be indistinguishable from "was never issued".

    NO VERSION COMPARE-AND-SET, unlike the document writer below, and the
    difference is the table rather than a decision: `reports` has no `version`
    column and no editor UI writing to it concurrently — a report is written
    once by a pipeline and read after. The last-writer-wins window is two chat
    edits of the same report racing each other, which needs both to be in flight
    at once on one thread. If reports ever gain a hand editor, they need a
    version column first, and this is the call site that would use it.
    """
    row = get_report(report_id, company.company_id)
    if row is None:
        raise HTTPException(404, "Report not found")

    # HTML whatever the row holds: a report is a rich document now, and the
    # legacy markdown rows convert on the way in exactly as they do for every
    # other reader. Editing one therefore rewrites it as HTML, which is the only
    # moment that one-way conversion is something the user asked for.
    body = report_markdown.to_html(row.get("html")).strip()
    if not body:
        raise HTTPException(409, "This report has no content to edit yet")

    try:
        edit = apply_edit(
            body, instruction, enterprise_id=company.company_id, label="report",
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if not edit["sections_changed"]:
        # A question, not an edit. Nothing is written.
        return {"report": row, "sections_changed": [], "summary": edit["summary"]}

    # Sanitised on the way back in: an edit is model output like any other, and
    # this body is rendered in the app, in the PDF and behind a public link.
    # `to_html` first, so a model that answered in markdown despite being handed
    # HTML still stores a document rather than source text.
    text = sanitize_artifact_html(report_markdown.to_html(edit["document"]))

    saved = update_report_body(report_id, company.company_id, html=text)
    if saved is None:
        # The row went away between the read and the write (a delete in another
        # tab). Same 404 as absent, for the same reason.
        raise HTTPException(404, "Report not found")
    logger.info(
        "report edited id=%s company=%s sections=%s",
        report_id, company.company_id, edit["sections_changed"],
    )
    return {
        "report": saved,
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }


def edit_evidence_scoped(
    evidence_id: int, instruction: str, company, *, workspace_id=None
) -> dict:
    """Apply a chat instruction to an EVIDENCE PAGE (an `evidences` row).
    `{"evidence", "sections_changed", "summary"}`.

    WHY IT EXISTS. Asked to "improve the evidence with an analytical chart of
    the evidence", the chat drew the chart into the conversation — because
    evidence was the one artifact in the panel with no edit path at all. A PRD
    has one, a report has one, a team document has one, and the document the
    request was actually about did not, so the only thing the chat could do was
    answer beside it.

    TENANCY IS THE SAME CHAIN THE ROUTE USES, not a new one:
    `require_owned_evidence` resolves evidence → brief → dataset → company (and
    workspace when given) and raises 404 on a mismatch. A foreign id reads as
    absent, never as forbidden — the rule every ownership check here follows.

    THE SHAPE IS THE DIFFERENCE. An evidence page is not prose: it is a whole
    self-contained HTML document with its own stylesheet, layout classes and
    hand-authored SVG charts. It is edited under `_SHAPE_EVIDENCE`, and it goes
    back through `normalize_evidence_html` — the SAME normaliser the generator
    uses — rather than `sanitize_artifact_html`, whose dozen-tag allow-list
    would strip the page down to its headings and throw away every chart on it,
    including the one the user just asked for. Scripts are stripped there and
    the canonical stylesheet is re-injected, so an edited page is stored under
    exactly the contract a generated one is.

    NO VERSION COMPARE-AND-SET, for the reason `edit_report_scoped` gives:
    `evidences` has no version column. The losing window is two chat edits of
    one page racing on a single thread.
    """
    from app.deps.ownership import require_owned_evidence
    from app.db import complete_evidence
    from app.evidence_html import EvidenceHtmlError, normalize_evidence_html
    from app.skills.loader import get_skill

    row = require_owned_evidence(evidence_id, company.company_id, workspace_id)

    body = (row.get("payload_md") or "").strip()
    if not body:
        # Still generating, or generated and failed. Editing an empty page
        # would write the model's idea of one over a row the reader is
        # watching fill in.
        raise HTTPException(409, "This evidence page has no content to edit yet")

    try:
        edit = apply_edit(
            body, instruction, enterprise_id=company.company_id,
            label="evidence page", shape="evidence",
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if not edit["sections_changed"]:
        # A question about the evidence, or a chart whose numbers are not in
        # the page — the editor is told to change nothing and say so. Either
        # way the row is untouched and the chat answers instead of claiming an
        # edit it did not make.
        return {"evidence": row, "sections_changed": [], "summary": edit["summary"]}

    try:
        html = normalize_evidence_html(
            edit["document"], get_skill("evidence-brief").assets["evidence.css"]
        )
    except EvidenceHtmlError as exc:
        # The editor returned something that is not a document. Refusing is the
        # only safe answer: the alternative is storing it and rendering a blank
        # panel where a finished brief used to be.
        raise HTTPException(502, f"The edited evidence page was unusable: {exc}")

    complete_evidence(evidence_id, row.get("title") or "", html)
    logger.info(
        "evidence edited id=%s company=%s sections=%s",
        evidence_id, company.company_id, edit["sections_changed"],
    )
    return {
        # The stored row, re-read the way the other two writers return theirs,
        # so the caller renders what was actually saved rather than what was
        # sent.
        "evidence": {**row, "payload_md": html, "status": "ready", "error": None},
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }


def edit_document_scoped(artifact_id: int, instruction: str, company) -> dict:
    """Apply a chat instruction to a team DOCUMENT (a `custom_artifacts` row).
    `{"artifact", "sections_changed", "summary"}`.

    The same two gates `goal_report_chat_edit.apply_report_edit_scoped` applies,
    minus its KIND gate — which is the whole point of this writer existing
    beside that one. That gate refuses anything that is not a Goal Analysis
    report, deliberately, so a tool aimed at the analysis panel can only ever
    touch the analysis panel's document. This writer's caller is the chat, whose
    target is whatever the user has open, so the gate here is TENANCY plus the
    version compare-and-set — and the editor prompt is the document-agnostic one
    above rather than the run-report one.

    409 on a lost compare-and-set: a colleague, or the user's own editor tab,
    saved between the read and the write. Overwriting them silently is the
    failure `version` exists to prevent.
    """
    row = get_artifact(company.company_id, artifact_id)
    if row is None:
        raise HTTPException(404, "Document not found")

    body = (row.get("body_html") or "").strip()
    if not body:
        raise HTTPException(409, "This document has no content to edit yet")

    try:
        edit = apply_edit(
            body, instruction, enterprise_id=company.company_id, label="document",
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if not edit["sections_changed"]:
        return {"artifact": row, "sections_changed": [], "summary": edit["summary"]}

    try:
        saved = update_artifact(
            company.company_id,
            artifact_id,
            body_html=sanitize_artifact_html(edit["document"]),
            base_version=int(row.get("version") or 1),
            updated_by=_actor(company),
        )
    except BodyTooLarge:
        raise HTTPException(413, "The edited document is too large to store")
    except VersionConflict:
        # Someone saved between the read and the write, so the instruction was
        # applied to text that is no longer current. The edit is REFUSED rather
        # than replayed onto a document it was not written about.
        raise HTTPException(
            409, "Someone else saved this document while the edit was running"
        )
    if saved is None:
        raise HTTPException(404, "Document not found")
    logger.info(
        "document edited id=%s company=%s sections=%s",
        artifact_id, company.company_id, edit["sections_changed"],
    )
    return {
        "artifact": saved,
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }

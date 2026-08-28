"""The chat-driven edit of a Goal Analysis report — tool, writer, and refusal.

This is `project_chat_edit.py`'s shape applied to a different document. Read
that file first: the split it makes (a TOOL DEFINITION that refuses to accept a
target, a HANDLER that closes over the real one, and ONE writer that owns the
tenant gate and the version) is the part being reused, and the reasons are the
same ones.

★ THE TARGET IS NEVER THE MODEL'S ★

`EDIT_GOAL_REPORT_TOOL` takes `instruction` and nothing else, with
`additionalProperties: False`. There is no id in the schema, so there is no id
for a model to get wrong, and no prompt-injected instruction in a customer's
own documents can name someone else's report. The id comes from the surface —
the artifact the user has OPEN beside the chat — and reaches the writer through
a closure the server builds. That is the `edit_prd` rule, and it is here for
the reason it is there: a document the user is not looking at must not change
under them.

★ THE EDIT IS LIVE ON CALL ★

No propose/confirm gate. That gate was deliberately retired for PRDs in
e05577dc, and re-adding it on this surface alone would put two documents that
sit in the same panel on two different contracts — the divergence #1272 fixed.
The edit is versioned and the pre-edit body is recoverable from the version
counter's conflict payload, so "undo" is a real answer where "are you sure?"
was only a delay.

★ AN EDIT DETACHES THE REPORT, AND NOTHING HERE HAS TO REMEMBER TO SAY SO ★

The run keeps `report_body_hash` — the fingerprint of the body as rendered.
This writer changes the body and does NOT touch that column, so the report
reads as detached from the next request onward. Deriving it rather than setting
a flag is what makes the hand-edit path (a plain `PATCH /v1/custom-artifacts`
autosave, which knows nothing about Goal Analysis) detach too.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import HTTPException

from app.crucible.report import ARTIFACT_KIND
from app.db import crucible_runs as runs_db
from app.db.custom_artifacts import (
    BodyTooLarge,
    VersionConflict,
    get_artifact,
    update_artifact,
)
from app.graph.gateway import llm_call
from app import targeted_edit

logger = logging.getLogger(__name__)

_AGENT = "crucible"

EDIT_PROMPT_VERSION = "goal-report-chat-edit-v1"

#: The in-band chat tool. Its DESCRIPTION carries the contract, because the
#: description is the only part of this the model reads.
EDIT_GOAL_REPORT_TOOL = {
    "name": "edit_goal_report",
    "description": (
        "Edit the Goal Analysis report currently open beside this chat. Call "
        "this when the latest turn asks to change, add to, update, remove "
        "from, tighten, or rewrite part of the report. Pass a plain-language "
        "`instruction` describing the change in the team's own words. You do "
        "NOT choose or pass a report id — the target is whichever report the "
        "team has open beside this chat; if none is open, tell them to open "
        "one first. Editing the report does NOT change the analysis it came "
        "from: the run, its findings and its ledger are immutable, and an "
        "edited report stops being regenerated from them — say so if the team "
        "seems to expect otherwise. The edit applies IMMEDIATELY when you call "
        "this — there is no confirmation step. After calling this, tell the "
        "team what you changed in past tense (the change is already applied)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": (
                    "the change to make to the report, in plain language"
                ),
            },
        },
        "required": ["instruction"],
        # NO ID, AND NO ROOM FOR ONE. `additionalProperties: False` is the
        # structural half of "the model never picks the target" — the prose
        # above is the half a model can ignore.
        "additionalProperties": False,
    },
}

_EDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "html": {"type": "string"},
        "sections_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["html", "sections_changed", "summary"],
}

_EDIT_SYSTEM = """\
You are Sprntly's Goal Analysis report editor. You are given a report about a \
completed analysis run, as an HTML fragment, and ONE edit instruction the user \
typed in chat ("cut the ruled-out list", "put the limits section first", \
"rewrite the summary for an exec"). Apply the instruction with the MINIMAL \
change necessary.

WHAT THIS DOCUMENT IS. It reports a deterministic run over the company's own \
evidence. Its credibility rests on three things, and an edit must not quietly \
remove any of them:
- an unsized finding says "Could not be sized". It is NOT zero, and rewriting \
it as 0, "none", or "no impact" asserts a measurement nobody made.
- the source documents beside a finding are how a reader checks it. Do not \
drop them to tighten prose.
- "What this cannot tell you" and the coverage notes are the report's own \
statement of its limits. A shorter report may compress them; it may not delete \
them. If the instruction asks you to remove them outright, do it — the user is \
allowed to — but say so plainly in `summary`.

Rules:
- Change ONLY what the instruction affects. Leave every untouched section \
byte-for-byte as it was, in the same order.
- Invent NO findings, numbers, sources or conclusions. This document reports a \
run; anything not derived from what is already in it is a fabrication with a \
provenance trail behind it that does not exist.
- Use only these HTML tags: h1, h2, h3, h4, p, strong, em, u, s, ul, ol, li, \
blockquote, code, pre, a, br, hr, table, thead, tbody, tr, th, td. Everything \
else is stripped on save. No class or data attributes, no inline styles beyond \
font/colour/alignment, no script or style blocks.
- If the instruction does not actually request a change (it is a question or a \
comment), return the document UNCHANGED with an empty `sections_changed` and a \
`summary` saying no edit was needed.

Return the FULL updated HTML in `html`, the human-readable section names you \
changed in `sections_changed`, and a one-line `summary` of the edit."""

_EDIT_USER = """\
Apply this edit instruction to the Goal Analysis report below.

INSTRUCTION: {instruction}

REPORT (HTML — edit and return the whole thing):
{report_html}
"""


def _full_emit_report(*, system: str, user: str, enterprise_id: str) -> dict:
    """Today's `apply_report_edit` body verbatim: the full-document re-emit call.

    This is BOTH the flag-off path AND the fallback when a targeted splice is
    rejected by any validation gate. The return shape
    (`{html, sections_changed, summary}`) is the contract the caller consumes.

    NO `model=` ARGUMENT: the gateway's default is sonnet, which is the tiering
    policy's default for product code. Opus is reserved for the three surfaces
    that were measured to need it, and a targeted HTML edit is not one of them.
    """
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose="apply_goal_report_chat_edit",
        prompt_version=EDIT_PROMPT_VERSION,
        system=system,
        input=user,
        json_schema=_EDIT_SCHEMA,
        max_tokens=32000,
        long_output=True,
    )
    # `.output`, NOT `.text`. `LLMResult` carries `output`; reading `.text` is
    # the bug that made every chat-written document raise AttributeError for
    # three days (#1188), and it passed twenty tests because the double defined
    # the interface the code was wrong about.
    out = result.output if isinstance(result.output, dict) else {}
    html = (out.get("html") or "").strip()
    if not html:
        raise RuntimeError("scoped Goal Analysis report edit returned no HTML")
    sections = out.get("sections_changed") or []
    return {
        "html": html,
        "sections_changed": [s for s in sections if isinstance(s, str)],
        "summary": (out.get("summary") or "").strip(),
    }


def _targeted_or_fallback_report(
    *, report_html: str, system: str, user: str, enterprise_id: str
) -> dict:
    """Run the targeted-ops editor over the stored report body, splice + validate
    via the shared primitive, and fall back to `_full_emit_report` (the proven
    path) on ANY gate failure. Only reached when the goal-report sub-gate is ON.

    `report_html` is the already-sanitized stored `body_html` (the writer
    re-sanitizes on save), so the splice operates on stable anchors.
    """
    t_result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose="apply_goal_report_chat_edit",
        prompt_version=f"{EDIT_PROMPT_VERSION}-targeted",
        system=targeted_edit.targeted_system(
            system, targeted_edit.GOALREPORT_SECTION_MODEL
        ),
        input=user,
        json_schema=targeted_edit.TARGETED_EDIT_SCHEMA,
        max_tokens=32000,
        long_output=True,
    )
    out = t_result.output if isinstance(t_result.output, dict) else {}
    summary = (out.get("summary") or "").strip()
    try:
        html, sections = targeted_edit.interpret(
            out,
            stored_doc=report_html,
            model=targeted_edit.GOALREPORT_SECTION_MODEL,
            # Goal-report `html` was never code-fenced (unlike PRD), so identity —
            # do not strip anything the model returns.
            strip_fence=lambda s: s,
        )
    except targeted_edit.FallbackNeeded as exc:
        logger.warning(
            "targeted goal-report edit falling back to full-emit: %s", exc
        )
        return _full_emit_report(system=system, user=user, enterprise_id=enterprise_id)
    return {
        "html": html,
        "sections_changed": [s for s in sections if isinstance(s, str)],
        "summary": summary,
    }


def apply_report_edit(report_html: str, instruction: str, enterprise_id: str) -> dict:
    """Run the scoped editor. `{"html", "sections_changed", "summary"}`.

    Raises RuntimeError when the model returns no usable HTML, so the caller
    leaves the stored document untouched rather than writing an empty one over
    a report someone may have spent an afternoon on.

    Behind `TARGETED_EDIT_GOALREPORT_ENABLED` (default OFF): flag-off is the
    current full-document re-emit, byte-identical to today. Flag-on asks the
    model for only the changed `<h2>` sections and splices them into the stored
    body, validating against the six gates and falling back to the full-emit
    call on any failure.
    """
    user = _EDIT_USER.format(instruction=instruction, report_html=report_html)
    if not targeted_edit.goalreport_enabled():
        return _full_emit_report(
            system=_EDIT_SYSTEM, user=user, enterprise_id=enterprise_id
        )
    return _targeted_or_fallback_report(
        report_html=report_html,
        system=_EDIT_SYSTEM,
        user=user,
        enterprise_id=enterprise_id,
    )


def _actor(company) -> str:
    """Who to record as `updated_by`. Total — never raises on a context object
    that carries neither field (the `project_chat_edit._actor` chain)."""
    return (
        getattr(company, "user_id", None)
        or getattr(company, "user_email", None)
        or "auto"
    )


def apply_report_edit_scoped(
    artifact_id: int,
    instruction: str,
    company,
) -> dict:
    """THE ONE WRITER. Apply a chat instruction to a Goal Analysis report.

    `company` is a `WorkspaceContext`/`CompanyContext`-shaped object carrying
    `.company_id`. Returns `{"artifact", "sections_changed", "summary"}`.

    TWO GATES, in this order, BEFORE anything is read or written:

      1. TENANCY — `get_artifact` filters `company_id` in the query, so a
         foreign id reads as absent and becomes a 404. Never a 403: "exists but
         not yours" must be indistinguishable from "was never issued".
      2. KIND — the document must be a Goal Analysis report on one of THIS
         company's runs. This is not belt-and-braces. Without it, a caller who
         names any of their own documents gets it rewritten by a prompt tuned
         for a report — no template checks, no per-surface rules — through a
         tool the user thought only touched the analysis panel. The gate is a
         reverse lookup on the run, so it also hands us the run the report
         belongs to, which is what the narration needs.

    409 on a lost compare-and-set, carrying nothing but the fact: a colleague
    (or the user's own editor tab) saved between the read and the write, and
    silently overwriting them is the failure `version` exists to prevent.
    """
    row = get_artifact(company.company_id, artifact_id)
    if row is None:
        raise HTTPException(404, "Report not found")
    run = runs_db.get_by_artifact(artifact_id, company.company_id)
    if run is None or (row.get("kind") or "") != ARTIFACT_KIND:
        # Deliberately the SAME 404 as absent. A caller probing ids learns
        # nothing about which of their documents are reports.
        raise HTTPException(404, "Report not found")

    report_html = (row.get("body_html") or "").strip()
    if not report_html:
        raise HTTPException(409, "This report has no content to edit yet")

    try:
        edit = apply_report_edit(
            report_html, instruction, enterprise_id=company.company_id
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if not edit["sections_changed"]:
        # The editor judged the instruction was not an edit. NOTHING IS
        # WRITTEN — not even a no-op save, which would bump the version and
        # detach a report nobody actually changed.
        return {
            "artifact": row,
            "sections_changed": [],
            "summary": edit["summary"],
            "run_id": run.get("id"),
        }

    try:
        saved = update_artifact(
            company.company_id,
            artifact_id,
            body_html=edit["html"],
            base_version=int(row.get("version") or 1),
            updated_by=_actor(company),
        )
    except BodyTooLarge:
        raise HTTPException(413, "The edited report is too large to store")
    except VersionConflict:
        raise HTTPException(
            409,
            "This report was saved by someone else while the edit was being "
            "written. Reopen it and ask again.",
        )
    if saved is None:
        raise HTTPException(404, "Report not found")

    # The run's `report_body_hash` is deliberately LEFT ALONE. It still holds
    # the fingerprint of the rendered report, so from here on the body no
    # longer matches it and the report reads as detached — which is exactly
    # what just became true.
    return {
        "artifact": saved,
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
        "run_id": run.get("id"),
    }


#: What the agent says when the tool fires with nothing open. Authored HERE and
#: returned as the tool result, so the model's own final turn is grounded in
#: the real outcome (`qa_agent`'s edit_prd narration rule): a model that
#: invents "done!" over a refusal is the failure that rule exists to catch.
NO_REPORT_OPEN = (
    "I can't edit a Goal Analysis report unless one is open beside this chat. "
    "Open the report you want changed and ask me again."
)


def make_edit_goal_report_handler(
    artifact_id: Optional[int], company
) -> Callable[[dict], "tuple[str, None]"]:
    """Build the in-band tool handler for ONE turn, closed over ONE target.

    `artifact_id` is the report the SURFACE says is open — never a model
    argument. `None` means nothing is open, and the handler then refuses
    without reading or writing anything, which is the whole reason the target
    is a closure rather than a tool field.

    The second element of the return is always `None`: the edit is applied by
    the time the narration exists, so there is no pending mutation to ride out.
    The shape matches `SurfaceScope.edit_prd_handler` so the tool loop needs no
    second calling convention.
    """

    def handle(tool_input: dict) -> "tuple[str, None]":
        instruction = (tool_input or {}).get("instruction") or ""
        instruction = instruction.strip() if isinstance(instruction, str) else ""
        if artifact_id is None:
            return NO_REPORT_OPEN, None
        if not instruction:
            return (
                "I didn't catch what to change in the report — tell me what "
                "you'd like different and I'll apply it.",
                None,
            )
        try:
            result = apply_report_edit_scoped(artifact_id, instruction, company)
        except HTTPException as exc:
            # NEVER RAISES INTO THE TOOL LOOP. `run_tool_loop` wants a normal
            # tool_result string; an exception here would abort the turn and
            # the user would see nothing at all, which reads as the chat being
            # broken rather than as one edit being refused.
            logger.info(
                "edit_goal_report refused artifact_id=%s status=%s",
                artifact_id, exc.status_code,
            )
            return f"I couldn't apply that edit: {exc.detail}", None
        except Exception:  # noqa: BLE001 — same reason as above
            logger.exception("edit_goal_report failed artifact_id=%s", artifact_id)
            return (
                "Something went wrong applying that edit, so the report is "
                "unchanged.",
                None,
            )
        if not result["sections_changed"]:
            return (
                result["summary"]
                or "That didn't read as a change to the report, so I left it as it is.",
                None,
            )
        changed = ", ".join(result["sections_changed"])
        return (
            f"Updated the report ({changed}). "
            f"{result['summary']} It is now an edited document — the run "
            f"behind it is unchanged, and this report is no longer "
            f"regenerated from it.",
            None,
        )

    return handle

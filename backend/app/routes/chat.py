"""The chat surfaces' two out-of-band decisions, either side of a message.

POST /v1/chat/intent — the action-envelope decision for a chat message.
POST /v1/chat/suggestions — next-prompt suggestions once an answer has landed.

Both are read-only, both load the conversation server-side (ownership-scoped),
and neither is on the answer path: `/intent` runs before the dispatch and
`/suggestions` after the answer is already on screen, so the suggestion call
can be slow, fail, or never return without the user losing anything.

The single backend entry the chat surfaces call BEFORE dispatching a message:
it loads the conversation history server-side, resolves the target PRD (the
active tab's, or the one this conversation is bound to), and returns the
action envelope from app.chat_intent. The client becomes a reducer over
envelopes — it maps `intent` onto the existing executor endpoints
(generate-from-task / chat-edit / stories/generate / design-agent / ask)
instead of deciding intent itself with regexes.

Read-only: this route never kicks a job. Executors stay where they are, with
their own auth/ownership/clarify flows, so adopting the envelope is a
frontend dispatch swap — not a behavior change to any generation pipeline.

Fail-open by construction: on any resolver failure the envelope is
{intent: "answer", confidence: 0} and the client sends the message down
today's ask path unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext

# The envelope's render-data legs (open lookup + conversation stamps,
# artifact rows/counts) live in app.chat_envelope so the project chat
# surfaces attach the SAME enrichment. The underscore names are re-imported
# here on purpose: routes/projects.py and the chat suites import them from
# this module, and that surface stays stable across the extraction.
from app.chat_envelope import (  # noqa: F401 — re-exported for existing importers
    _MAX_CHAT_ARTIFACTS,
    _attach_open_conversations,
    _chat_artifact_counts,
    _chat_artifact_list,
    _dataset_for,
    enrich_chat_envelope,
    project_prd_edit_target,
)
from app.chat_intent import resolve_chat_intent
from app.chat_suggestions import suggest_next_prompts
from app.db.conversations import get_conversation_prd_id, get_conversation_project_id
from app.deps.ownership import require_owned_prd
from app.entitlements import require_agents_module
from app.routes.ask import _load_history

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatIntentIn(BaseModel):
    # Same ceiling as AskIn: the message may carry an inlined attachment block.
    message: str = Field(..., min_length=1, max_length=120_000)
    # When set, prior turns are loaded (ownership-checked) so deictic messages
    # ("draft it up") resolve against the thread, and the conversation's bound
    # PRD backstops a tab that lost its local prd_id.
    conversation_id: int | None = None
    # The active tab's open PRD, when there is one. Ownership-gated below.
    prd_id: int | None = Field(default=None, ge=1)
    has_attachments: bool = False
    # Optional pluggable context source: `{"kind": str, "params": dict}`,
    # accepted so a surface that brings its own context can carry it symmetric
    # with `/v1/ask`. `/intent` runs BEFORE dispatch and is not on the answer
    # path, but a `{"kind": "project", ...}` source DOES scope the classify
    # envelope's render-data legs (artifact list / counts / open lookup) to the
    # project via `enrich_chat_envelope(project_id=...)`, so the cards a project
    # chat renders match its project-scoped prose. No source (every main-chat
    # client) ⇒ workspace-wide listing, unchanged.
    context_source: dict | None = None
    # What the tab has OPEN in its side panel besides a PRD: the report, the
    # team document or the evidence page the user is looking at, as
    # `{"kind": "report"|"document"|"evidence", "id": int, "title": str}`. The planner is TOLD about it
    # (`ask_planner._open_artifact_block`), which is what gives "convert that
    # section into a table" a referent instead of an answer that prints the
    # rewritten section into the chat. Ownership-resolved below, never trusted:
    # its TITLE reaches a prompt and its id gates an edit action.
    open_artifact: dict | None = None


def _resolve_open_artifact(raw: dict | None, company) -> dict | None:
    """The report/document the tab has open, re-read under this company's scope.

    Returns `{"kind", "id", "title"}` or None — None for anything absent,
    malformed, of an unknown kind, or belonging to another company. The kinds
    are the ones an edit can act on: a report, a team document, and an
    evidence page. Never
    raises: a panel pointer that no longer resolves is a message with no open
    artifact, not a failed send, and the classify endpoint's whole contract is
    that it fails open to `answer`.

    RE-READ RATHER THAN ECHOED, for two reasons that are both about trust:
    the title lands inside the planner's prompt, so a caller could otherwise
    write arbitrary text into it; and the id becomes the target of an edit
    action, so accepting one unchecked would let a request name a document its
    sender does not own.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    try:
        artifact_id = int(raw.get("id"))
    except (TypeError, ValueError):
        return None
    try:
        if kind == "report":
            from app.db import get_report

            row = get_report(artifact_id, company.company_id)
            title = (row or {}).get("title") or ""
        elif kind == "document":
            from app.db.custom_artifacts import get_artifact

            row = get_artifact(company.company_id, artifact_id)
            title = (row or {}).get("title") or ""
        elif kind == "evidence":
            # Resolved through the SAME ownership chain the evidence routes
            # use (evidence → brief → dataset → company), which raises rather
            # than returning None — caught below with every other lookup
            # failure, because a pointer that does not resolve is a message
            # with no open artifact, never a failed send.
            from app.deps.ownership import require_owned_evidence

            row = require_owned_evidence(
                artifact_id, company.company_id, company.workspace_id
            )
            title = (row or {}).get("title") or ""
        else:
            return None
    except Exception:  # noqa: BLE001 — a lookup failure is "nothing open"
        logger.exception("open-artifact lookup failed kind=%s id=%s", kind, artifact_id)
        return None
    if row is None:
        return None
    return {"kind": kind, "id": artifact_id, "title": title}


def _thread_edit_target(conversation_id: int | None, company) -> dict | None:
    """The report or document an edit acts on when the PANEL names none.

    "Add a risks section to that report" was answered instead of applied, with
    the report unchanged — reported as "it cannot edit a report based on a
    prompt; tried making reference to a report several times and it did not get
    it". The reference was fine. The TARGET was only ever read from the side
    panel (`_resolve_open_artifact`), so with the panel closed, or showing a
    list with nothing picked, `edit_artifact` had nothing to act on and
    `chat_intent` correctly downgraded it to `answer` — which then printed the
    rewritten section into the chat and told the reader to paste it in.

    The thread is the missing referent. A report generated in this conversation
    belongs to it whether or not the panel happens to be showing it, and
    someone typing "that report" in the chat that produced it means that one.

    THE NEWEST, and the panel still wins — this is only consulted when the
    panel named nothing. That is the same resolution `project_prd_edit_target`
    already uses for the same shape of problem ("the common single-PRD project
    has exactly one answer, and with several the newest matches the recency
    collapse the open-resolver and listing legs already use"). A thread with
    one report — the ordinary case — is unambiguous either way.

    Scoped by company AND conversation on both reads, so a guessed id returns
    nothing rather than another tenant's document. Never raises: no target is
    a recoverable `answer` that can ask which document, a failed classify is
    not.
    """
    if conversation_id is None:
        return None
    rows: list[dict] = []
    try:
        from app.db import list_reports_for_conversation

        rows += [
            {"kind": "report", "id": r["id"], "title": r.get("title") or "",
             "created_at": r.get("created_at") or ""}
            for r in list_reports_for_conversation(conversation_id, company.company_id)
            if r.get("id") is not None
        ]
    except Exception:  # noqa: BLE001 — one source failing is not a failed send
        logger.exception(
            "thread report target lookup failed conversation=%s", conversation_id
        )
    try:
        from app.db.custom_artifacts import list_artifacts_for_conversation

        rows += [
            {"kind": "document", "id": r["id"], "title": r.get("title") or "",
             "created_at": r.get("created_at") or ""}
            for r in list_artifacts_for_conversation(
                company.company_id, conversation_id
            )
            # A document still being written is not something to edit.
            if r.get("id") is not None and r.get("status") != "generating"
        ]
    except Exception:  # noqa: BLE001
        logger.exception(
            "thread document target lookup failed conversation=%s", conversation_id
        )
    if not rows:
        return None
    rows.sort(key=lambda r: str(r["created_at"]), reverse=True)
    newest = rows[0]
    # `origin` is what keeps the planner's prompt honest: this document is in
    # the thread, NOT on screen, and the line rendered for it says so
    # (`ask_planner._open_artifact_block`).
    return {
        "kind": newest["kind"], "id": newest["id"], "title": newest["title"],
        "origin": "thread",
    }


@router.post("/intent")
def chat_intent(
    body: ChatIntentIn,
    # The chat surface is the Agents module — same gate as /v1/ask.
    company: CompanyContext = Depends(require_agents_module),
):
    """Decide the action envelope for one chat message.

    Returns {intent, confidence, task, instruction, artifact_type,
    artifact_query, reason, source, prd_id, prd_title} — prd_id/prd_title are
    the resolved TARGET (tab-sent or conversation-bound), echoed so the reducer
    acts on the same document the decision was grounded on.

    An `open_artifact` envelope carries one extra key, `open`:
    {status: "resolved"|"ambiguous"|"not_found", artifact_type, query,
    artifact, candidates} — the LOOKUP result. Still read-only: naming a
    document is not opening it, and nothing here generates anything.
    """
    prd_id = body.prd_id
    prd_row: dict | None = None
    if prd_id is not None:
        # Explicit tab context: a foreign prd_id is a hard 404 (same posture
        # as the ask route — no cross-tenant existence disclosure).
        prd_row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
    elif body.conversation_id is not None:
        # Fallback target: the PRD this conversation produced. Best-effort —
        # the binding was ownership-scoped when written, but re-verify and
        # degrade to no-target rather than failing the send.
        bound = get_conversation_prd_id(
            body.conversation_id, company.company_id, company.user_id
        )
        if bound:
            try:
                prd_row = require_owned_prd(
                    bound, company.company_id, company.workspace_id
                )
                prd_id = bound
            except HTTPException:
                prd_id = None

    # Resolve the project scope BEFORE classify: a project-bound conversation
    # scopes both the render-data legs (below) AND the act-on-PRD target here.
    # Server-derived from the conversation's project binding first (a
    # `conversations` row with a non-null `project_id`), so it holds even when
    # the client sent no `context_source`; the client source is the fallback for
    # a first-turn classify with no conversation row yet. A main-chat row carries
    # `project_id = NULL`, so it derives None and stays workspace-scoped.
    project_id = None
    if body.conversation_id is not None:
        project_id = get_conversation_project_id(
            body.conversation_id, company.company_id
        )
    if project_id is None and (
        isinstance(body.context_source, dict)
        and body.context_source.get("kind") == "project"
    ):
        params = body.context_source.get("params") or {}
        raw = params.get("project_id")
        if raw is not None:
            project_id = int(raw)

    # In a project chat, an act-on-PRD intent ("make the PRD shorter") targets
    # the project's OWN PRD even when the user hasn't opened it — otherwise
    # `chat_intent` finds no target `prd_id` and silently downgrades the edit to
    # a summary (`_NEEDS_PRD` → answer). The client's open-panel / conversation-
    # bound PRD, resolved above, still wins; this only fills the gap when neither
    # is present. The write itself stays gated by `project_prd_gate`.
    if prd_id is None and project_id is not None:
        target = project_prd_edit_target(company, project_id)
        if target is not None:
            try:
                prd_row = require_owned_prd(
                    target, company.company_id, company.workspace_id
                )
                prd_id = target
            except HTTPException:
                prd_id = None

    history = _load_history(body.conversation_id, company.company_id, company.user_id)
    prd_title = (prd_row or {}).get("title") or None

    from app.timing import timed

    with timed("route:chat_intent.resolve"):
        # The open report/document, RE-READ from the DB under this company's
        # scope. The client says WHICH artifact its panel is showing; the title
        # that reaches the planner's prompt and the id that gates an edit come
        # from the row, never from the request — a client (or anything that can
        # forge one) must not be able to name a document it does not own, nor
        # write its own text into a prompt through the title.
        open_artifact = _resolve_open_artifact(body.open_artifact, company)
        # …and, when it is showing nothing, the document THIS THREAD made. Kept
        # as its own value rather than folded into `open_artifact` so the
        # planner's prompt can stay literally true about which is which: one
        # says "is open beside this chat", the other "was produced in this
        # chat", and both are a referent an edit can act on.
        thread_artifact = (
            _thread_edit_target(body.conversation_id, company)
            if open_artifact is None
            else None
        )
        envelope = resolve_chat_intent(
            company.company_id,
            body.message,
            history,
            prd_id=prd_id,
            prd_title=prd_title,
            has_attachments=body.has_attachments,
            open_artifact=open_artifact,
            thread_artifact=thread_artifact,
        )
    envelope["prd_id"] = prd_id
    envelope["prd_title"] = prd_title
    # The render-data legs (open lookup + conversation stamps, artifact
    # rows/counts) — the SHARED enrichment the project chat surfaces also
    # run, so a card main chat can render always has the same data there.
    # No dataset is passed: it resolves per leg inside the enrichment,
    # exactly where this route resolved it before the extraction.
    #
    # A project chat's listing / open legs must resolve against THAT project's
    # own artifacts, never the whole workspace's — otherwise "open the PRD" in a
    # project chat goes ambiguous against every workspace PRD, and "which PRDs
    # exist?" returns the workspace's newest rows instead of the project's. The
    # `project_id` was derived above (server-side from the conversation binding,
    # client `context_source` as fallback); main-chat rows carry None and stay
    # workspace-scoped, byte-identical to today.
    enrich_chat_envelope(envelope, company, project_id=project_id)
    return envelope


class ChatSuggestionsIn(BaseModel):
    # The thread to continue. Required: suggestions are only meaningful for a
    # conversation that has an answered turn in it, and the history is read
    # server-side (ownership-checked) rather than trusted from the client.
    conversation_id: int
    # The active tab's open PRD, when there is one. Ownership-gated below.
    prd_id: int | None = Field(default=None, ge=1)


@router.post("/suggestions")
def chat_suggestions(
    body: ChatSuggestionsIn,
    company: CompanyContext = Depends(require_agents_module),
):
    """0-3 next prompts continuing this conversation — or, very often, none.

    Called AFTER an answer has rendered, never before or during: this is a
    separate round trip precisely so it cannot delay, block or fail the answer
    stream. A late or missing response costs the user nothing, which is why the
    contract is `{suggestions: [...]}` with `[]` as an ordinary success rather
    than an error anywhere.

    Silence is the designed default — see app.chat_suggestions for the four
    abstention layers. This route adds no suggestion logic of its own; it
    resolves ownership, loads the thread, and hands over.
    """
    prd_id = body.prd_id
    prd_title: str | None = None
    if prd_id is not None:
        # Same posture as /intent: a foreign prd_id is a hard 404.
        prd_row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
        prd_title = (prd_row or {}).get("title") or None

    # Ownership-scoped: `_load_history` returns [] for a conversation that is
    # not the CALLER's, and an empty thread abstains — so a crafted
    # conversation_id yields silence, never another user's turns.
    history = _load_history(body.conversation_id, company.company_id, company.user_id)
    return {
        "suggestions": suggest_next_prompts(
            company.company_id, history, prd_id=prd_id, prd_title=prd_title
        )
    }

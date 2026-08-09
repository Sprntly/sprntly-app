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

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.artifact_open import resolve_open_artifact
from app.auth import CompanyContext
from app.chat_intent import resolve_chat_intent
from app.chat_suggestions import suggest_next_prompts
from app.db.conversations import get_conversation_prd_id
from app.deps.ownership import require_owned_prd
from app.entitlements import require_agents_module
from app.routes.ask import _load_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["chat"])


def _dataset_for(company) -> str:
    """The dataset slug backing the caller's active workspace ("" if none).

    Datasets are per-workspace ('{company}--{workspace}') except the DEFAULT
    workspace, which keeps the bare company slug and — for companies predating
    the workspace binding — often has no `datasets.workspace_id` at all. The
    company-slug fallback covers exactly that legacy case and is scoped to the
    default workspace on purpose: applying it to a non-default workspace with
    no dataset of its own would search the default workspace's artifacts from
    inside a workspace that must not see them.

    Returning "" (never a guess) is what makes an unresolvable workspace a
    clean `not_found` instead of a lookup against the wrong slug.
    """
    from app.db.companies import slug_for_company_id
    from app.db.workspaces import dataset_slug_for_workspace

    workspace_id = getattr(company, "workspace_id", None)
    if workspace_id:
        bound = dataset_slug_for_workspace(workspace_id)
        if bound:
            return bound
        if not getattr(company, "workspace_is_default", False):
            return ""
    return slug_for_company_id(company.company_id) or ""


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

    history = _load_history(body.conversation_id, company.company_id, company.user_id)
    prd_title = (prd_row or {}).get("title") or None

    envelope = resolve_chat_intent(
        company.company_id,
        body.message,
        history,
        prd_id=prd_id,
        prd_title=prd_title,
        has_attachments=body.has_attachments,
    )
    envelope["prd_id"] = prd_id
    envelope["prd_title"] = prd_title
    if envelope.get("intent") == "open_artifact":
        # The resolver named a SUBJECT; the lookup happens here, where the
        # tenant scope lives. Same posture as the rest of this route: read-only
        # and scoped to the caller's workspace, so a phrase can only ever
        # resolve to a document this caller already owns.
        envelope["open"] = resolve_open_artifact(
            artifact_type=envelope.get("artifact_type") or "prd",
            query=envelope.get("artifact_query") or "",
            dataset=_dataset_for(company),
        )
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

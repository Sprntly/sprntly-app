"""POST /v1/chat/intent — the action-envelope decision for a chat message.

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

from app.auth import CompanyContext
from app.chat_intent import resolve_chat_intent
from app.db.conversations import get_conversation_prd_id
from app.deps.ownership import require_owned_prd
from app.entitlements import require_agents_module
from app.routes.ask import _load_history

logger = logging.getLogger(__name__)

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


@router.post("/intent")
def chat_intent(
    body: ChatIntentIn,
    # The chat surface is the Agents module — same gate as /v1/ask.
    company: CompanyContext = Depends(require_agents_module),
):
    """Decide the action envelope for one chat message.

    Returns {intent, confidence, task, instruction, reason, source, prd_id,
    prd_title} — prd_id/prd_title are the resolved TARGET (tab-sent or
    conversation-bound), echoed so the reducer acts on the same document the
    decision was grounded on.
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
    return envelope

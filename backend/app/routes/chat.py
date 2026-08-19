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
)
from app.chat_intent import resolve_chat_intent
from app.chat_suggestions import suggest_next_prompts
from app.db.conversations import get_conversation_prd_id
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

    from app.timing import timed

    with timed("route:chat_intent.resolve"):
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
    # The render-data legs (open lookup + conversation stamps, artifact
    # rows/counts) — the SHARED enrichment the project chat surfaces also
    # run, so a card main chat can render always has the same data there.
    # No dataset is passed: it resolves per leg inside the enrichment,
    # exactly where this route resolved it before the extraction.
    #
    # When a project context-source rides this classify call
    # (`{"kind": "project", "params": {"project_id": N}}`), forward its
    # `project_id` so the listing / open legs resolve against THAT project's
    # own artifacts — making the intent envelope's cards and counts agree with
    # the project-scoped prose the answer path produces, instead of showing the
    # whole workspace's. Naturally gated: no project context-source ⇒ no
    # `project_id` ⇒ the workspace-wide listing is byte-identical to today.
    project_id = None
    if (
        isinstance(body.context_source, dict)
        and body.context_source.get("kind") == "project"
    ):
        params = body.context_source.get("params") or {}
        raw = params.get("project_id")
        if raw is not None:
            project_id = int(raw)
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

"""Conversation history endpoints — persist chat threads to Supabase.

  GET    /v1/conversations               -> list the CALLER'S conversations
  POST   /v1/conversations               -> create a new conversation (stamped with the caller)
  PATCH  /v1/conversations/{id}          -> update title/reply/pinned/prd_id
  DELETE /v1/conversations/{id}          -> delete a conversation
  DELETE /v1/conversations/{id}/turns/{turn_id}
                                         -> rewind to just before a user turn

Chats are PER-USER: every row is stamped with the creating member's user_id
and only that member can list/read/update/delete it — teammates in the same
workspace never see each other's chats (PRD chats included). Only artifacts
(PRDs, prototypes, evidence) are workspace-shared.

Legacy rows created before stamping (user_id IS NULL) cannot be attributed to
an owner, so they are hidden from everyone — strict per-user privacy beats
resurfacing chats whose author is unknown.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from app import attachments_storage
from app.auth import (
    CompanyContext,
    WorkspaceContext,
    require_company,
    require_workspace,
)
from app.db.client import require_client, utc_now
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
from app.realtime import publish_broadcast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    title: str = Field(..., min_length=1)
    preview: str = ""
    agent_type: str = "ask"
    query: str = ""
    reply: str = ""
    pinned: bool = False
    # The PRD this conversation is about, when opened from a PRD tab. Lets a
    # reopened PRD rehydrate its earlier chat turns via GET /by-prd/{prd_id}.
    prd_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    preview: str | None = None
    query: str | None = None
    reply: str | None = None
    pinned: bool | None = None
    # Back-patched once known: command flows (import a doc / "generate a PRD for
    # X") create the conversation from the seed turn BEFORE the async generate
    # returns the prd_id, so it's first stored as null. Setting it here lets a
    # reopened-from-history chat rebind to its PRD (by-prd lookup + panel reopen).
    prd_id: int | None = None


def _get_owned_conversation(
    c: Any, conversation_id: int, company: CompanyContext
) -> dict[str, Any] | None:
    """The conversation iff it belongs to this company AND the caller owns it.
    None otherwise (including legacy user_id-NULL rows) — callers 404."""
    resp = (
        c.table("conversations")
        .select("*")
        .eq("id", conversation_id)
        .eq("company_id", company.company_id)
        .eq("user_id", company.user_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@router.get("")
def list_conversations(
    company: WorkspaceContext = Depends(require_workspace),
):
    """List the CALLER'S conversations in the ACTIVE WORKSPACE, newest first."""
    c = require_client()
    resp = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("workspace_id", company.workspace_id)
        .eq("user_id", company.user_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"conversations": resp.data or []}


@router.post("")
def create_conversation(
    body: ConversationIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Create a new conversation, owned by the calling user, in the active
    workspace."""
    c = require_client()
    row: dict[str, Any] = {
        "company_id": company.company_id,
        "workspace_id": company.workspace_id,
        # Chats are per-user: stamp the creator so list/read stay private.
        "user_id": company.user_id,
        "title": body.title,
        "preview": body.preview,
        "agent_type": body.agent_type,
        "query": body.query,
        "reply": body.reply,
        "pinned": body.pinned,
    }
    if body.prd_id is not None:
        row["prd_id"] = body.prd_id
    resp = c.table("conversations").insert(row).execute()
    return resp.data[0] if resp.data else {}


# ── Attachment files (the ORIGINAL uploaded document, not just extracted text) ──
# Declared BEFORE the /{conversation_id} routes: the file is staged on SEND, before
# its turn (and often its conversation) exists, so these are workspace-scoped, not
# conversation-scoped. Storing the raw file lets a reopened chat render the real
# document — PDF/image inline, everything downloadable — via a short-lived signed
# URL (routes/attachments_storage), the same Bearer-authed-endpoint→public-URL
# pattern the OAuth start + bundle share use.


@router.post(
    "/attachments",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def upload_attachment(
    file: UploadFile = File(...),
    company: WorkspaceContext = Depends(require_workspace),
):
    """Stage an uploaded chat file; return its storage key + sniffed metadata.

    Empty → 400, oversize → 413, unsupported extension → 422 (mirrors the
    screenshot/PRD-import upload guards)."""
    ext = attachments_storage.ext_of(file.filename or "")
    if not attachments_storage.is_supported_ext(ext):
        raise HTTPException(422, "Unsupported file type.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > attachments_storage.MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")
    key = await attachments_storage.stage_attachment(
        workspace_id=company.workspace_id, data=data, ext=ext
    )
    return {
        "key": key,
        "name": file.filename or f"file.{ext}",
        "mime": attachments_storage.media_type_for_key(key),
        "size": len(data),
    }


@router.get("/attachments/sign")
def sign_attachment(
    key: str,
    name: str = "",
    company: WorkspaceContext = Depends(require_workspace),
):
    """Mint fresh signed (view + download) URLs for a stored attachment key.

    The key embeds the workspace prefix; a key outside the caller's workspace is
    refused (404 — never leak that it exists). Re-signed on every viewer open so a
    permanent chat always resolves a live URL after the short TTL elapses."""
    try:
        urls = attachments_storage.attachment_urls(
            workspace_id=company.workspace_id, key=key, filename=name,
        )
    except ValueError:
        raise HTTPException(404, "Attachment not found")
    return {**urls, "mime": attachments_storage.media_type_for_key(key)}


@router.get("/by-prd/{prd_id}")
def get_conversation_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Return the CALLER'S most recent conversation for a PRD (plus its turns),
    so a reopened PRD tab can rehydrate their prior chat. PRD chats are
    per-user — a teammate reopening the same PRD gets their own (or no)
    conversation, never someone else's. Empty (not 404) when the caller has no
    saved conversation for the PRD yet. (Company-scoped: the PRD id itself is
    already workspace-gated where it's read, and chats stay per-user.)"""
    c = require_client()
    conv = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("prd_id", prd_id)
        .eq("user_id", company.user_id)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    conversation = conv.data[0] if conv.data else None
    if conversation is None:
        return {"conversation": None, "turns": []}
    turns = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation["id"])
        .order("created_at")
        .execute()
    )
    return {"conversation": conversation, "turns": turns.data or []}


@router.get("/by-evidence/{evidence_id}")
def get_conversation_by_evidence(
    evidence_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Return the CALLER'S most recent conversation for an Evidence doc (plus
    its turns) — the Evidence mirror of GET /by-prd/{prd_id}. Same per-user
    scoping and same "empty (not 404) when the caller has no saved conversation
    for it yet" contract; see get_conversation_by_prd for the full rationale."""
    c = require_client()
    conv = (
        c.table("conversations")
        .select("*")
        .eq("company_id", company.company_id)
        .eq("evidence_id", evidence_id)
        .eq("user_id", company.user_id)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    conversation = conv.data[0] if conv.data else None
    if conversation is None:
        return {"conversation": None, "turns": []}
    turns = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation["id"])
        .order("created_at")
        .execute()
    )
    return {"conversation": conversation, "turns": turns.data or []}


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: int,
    body: ConversationUpdate,
    company: CompanyContext = Depends(require_company),
):
    """Update a conversation (title, reply, pinned, etc.) — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    patch: dict[str, Any] = {"updated_at": utc_now()}
    if body.title is not None:
        patch["title"] = body.title
    if body.preview is not None:
        patch["preview"] = body.preview
    if body.query is not None:
        patch["query"] = body.query
    if body.reply is not None:
        patch["reply"] = body.reply
    if body.pinned is not None:
        patch["pinned"] = body.pinned
    if body.prd_id is not None:
        patch["prd_id"] = body.prd_id
    resp = (
        c.table("conversations")
        .update(patch)
        .eq("id", conversation_id)
        .eq("company_id", company.company_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "Conversation not found")
    return resp.data[0]


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Delete a conversation (turns cascade-delete via FK) — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    c.table("conversations").delete().eq(
        "id", conversation_id
    ).eq("company_id", company.company_id).execute()
    return {"ok": True}


# ── Turns (messages within a conversation) ──


class TurnAttachment(BaseModel):
    """Extracted text of a file the user attached to this turn. Persisted so a
    reloaded thread (and the chat→PRD flow) can still see documents attached
    earlier in the conversation — content caps mirror the ask path's clamps.

    `content` may be EMPTY: a document imported straight to a PRD (the "generate a
    PRD" command over a file) has no in-chat extracted text — the file BECOMES the
    PRD — but its name is still persisted as a name-only chip so the reopened
    thread shows what the user attached beside their command. Empty-content
    attachments are skipped by the chat→PRD grounding (frontend conversationPrdDocs).

    `key`/`mime` point at the ORIGINAL file stashed in storage (POST
    /v1/conversations/attachments) so a reopened chat can render the real document
    (PDF/image inline, everything downloadable) — not just the extracted text.
    Null on legacy turns and on text pasted without an upload."""
    name: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., max_length=60_000)
    key: str | None = Field(default=None, max_length=400)
    mime: str | None = Field(default=None, max_length=200)
    size: int | None = Field(default=None, ge=0)

    # This field is THE extracted document text, so it is the likeliest place a
    # NUL enters the system at all — and it lands in a JSON column, which
    # refuses U+0000 exactly as a `text` column does. Same strip, and the same
    # reasoning, as TurnIn.content below.
    @field_validator("content")
    @classmethod
    def _drop_nul(cls, v: str) -> str:
        from app.ingest import strip_nul

        return strip_nul(v)


#: Ceiling on a persisted structured reply, measured on its serialized JSON.
#: The payload this exists for is small and bounded — an answer plus at most
#: `chat_envelope._MAX_CHAT_ARTIFACTS` listing rows — so anything near this is
#: not a reply, and the column is writable by any signed-in caller on their own
#: conversation. Rejected outright rather than truncated: half a payload
#: restores as half a thread, which is worse than restoring from `content`.
_MAX_TURN_REPLY_BYTES = 64_000


class TurnIn(BaseModel):
    role: str = "user"  # "user" or "assistant"
    content: str = Field(..., min_length=1)
    attachments: list[TurnAttachment] | None = Field(default=None, max_length=8)
    #: The FULL structured reply on an ASSISTANT turn — the answer payload plus
    #: the listing's own rows (`artifact_list`) — persisted onto
    #: `conversation_turns.reply` (jsonb, migration 20260816160000).
    #:
    #: Why it exists at all: `content` is a STRING, so everything a turn showed
    #: beyond prose was dropped the moment it was saved. "Show me the PRDs I
    #: created" rendered twelve clickable rows live and, reopened from Chat
    #: history, rendered the sentence announcing them over empty space — the
    #: answer promised "click one to open it" and there was nothing to click.
    #:
    #: The COLUMN already existed: it was added for the group project chat,
    #: which hit this same defect first and was itself removed later
    #: (520c12cc), taking its writer with it. The main chat kept the bug and
    #: inherits the column.
    #:
    #: `content` still carries the plain answer text and is still the fallback:
    #: null here (every row written before this, and every non-structured
    #: writer) restores exactly as it did before.
    reply: dict[str, Any] | None = None

    #: Client-issued idempotency key for an ASSISTANT turn on an INDIVIDUAL
    #: PROJECT chat only (`add_turn`'s idempotent-upsert branch below) — a
    #: retry/double-submit of the SAME completed ask carrying the SAME key
    #: collapses to one row instead of inserting a second. Ignored on main
    #: chat and on a user turn: only the narrow gate below reads it, so
    #: every existing caller that omits it is byte-identical to before.
    client_message_id: str | None = None

    @field_validator("reply")
    @classmethod
    def _bounded(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        import json

        if len(json.dumps(v, default=str)) > _MAX_TURN_REPLY_BYTES:
            raise ValueError("reply payload too large")
        return v

    # The same NUL guard `AskIn.question` carries, and this model needs it for
    # the same reason: a turn's content is the message the composer built, which
    # for an attached document includes its extracted text. Postgres `text`
    # cannot store `U+0000` (SQLSTATE 22P05), and `add_turn` was observed dying
    # on exactly that alongside the ask. See `ingest.strip_nul` for why a
    # character-level check never catches it and why the SQLite test fake
    # cannot reproduce it.
    @field_validator("content")
    @classmethod
    def _drop_nul(cls, v: str) -> str:
        from app.ingest import strip_nul

        return strip_nul(v)


@router.get("/{conversation_id}/turns")
def list_turns(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """List all turns in a conversation, oldest first — owner only."""
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    resp = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {"turns": resp.data or []}


def _catalog_turn_attachments(
    turn: dict[str, Any], body: TurnIn, company: CompanyContext
) -> None:
    """Register this turn's attachments in the document catalog, session-scoped.

    Chat attachments are PRIVATE to their conversation, so each row carries
    `conversation_id` + `user_id` (both from the verified CompanyContext, never
    from the request body) and is only readable back by that same triple.

    `external_id` is the synthetic `turn:{turn_id}:attachment:{index}` id the
    ask path's document manifest already uses, so the catalog and the manifest
    name the same document the same way.

    `body_text` stays NULL: the extracted text is already on the turn row and
    reaches the model through folded history. A document's text is never
    stored twice.

    Never raises. A turn that saves today must still save if cataloguing
    fails — this runs after the turn is already persisted, and its failure is
    logged and dropped."""
    turn_id = turn.get("id")
    if not turn_id or not body.attachments:
        return
    try:
        from app import document_catalog
    except Exception:  # noqa: BLE001 — never break a turn save on an import
        logger.warning("document catalog unavailable; turn not catalogued",
                       exc_info=True)
        return
    for index, attachment in enumerate(body.attachments):
        content = attachment.content or ""
        try:
            document_catalog.register_document(
                company.company_id,
                provider=document_catalog.PROVIDER_CHAT_ATTACHMENT,
                external_id=f"turn:{turn_id}:attachment:{index}",
                title=attachment.name,
                content_hash=document_catalog.content_hash_for(content),
                doc_date=turn.get("created_at"),
                conversation_id=turn.get("conversation_id"),
                user_id=company.user_id,
                get_text=lambda text=content: text,
                background=True,
            )
        except Exception:  # noqa: BLE001 — cataloguing must never fail a turn save
            logger.warning(
                "document catalog registration failed for turn %s attachment %s",
                turn_id, index, exc_info=True,
            )


# The exact `conversation_turns` read-DTO key set published on a fresh turn —
# same whitelist as `routes/projects.py::_TURN_CREATED_DTO_KEYS` (AD-P21
# no-schema-coupling). Kept as its own copy here rather than importing that
# module's private helper: this is a routes<->routes cross-import that would
# otherwise couple two independently-owned route files over a private name.
_TURN_CREATED_DTO_KEYS = ("id", "role", "content", "created_at")


@router.post("/{conversation_id}/turns")
def add_turn(
    conversation_id: int,
    body: TurnIn,
    company: CompanyContext = Depends(require_company),
):
    """Add a turn (message) to a conversation — owner only.

    Realtime fan-out (best-effort, AD-P22): this is the SHARED write path for
    every conversation turn (main chat AND the individual project chat's own
    plain-ask flow both persist here client-side — see
    `web/.../projects/useProjectConversation.ts`'s `chatPersistence`). A
    `turn.created` broadcast fires ONLY when the conversation just written to
    is an INDIVIDUAL PROJECT chat (`kind == "individual" and project_id is
    not None`) — every other conversation (main chat, non-project, legacy
    group) is byte-identical: no publish, no added query, no added latency
    beyond the cheap in-memory gate check on the row this route already
    fetched for the ownership check below.

    Idempotent-upsert branch (individual project chat, assistant turn, a
    `client_message_id` present): the project chat's ask-completion persist
    can legitimately fire twice for the SAME logical answer — the SAME
    conversation-scoped ask can be independently "resumed" by a second mount
    of this chat (a second browser tab open on it, or a navigate-away-and-
    back while the ask is still in flight — see `useProjectConversation.ts`'s
    resume effect), each of which persists the settled reply through this
    SAME route. Routing that one case through
    `db.conversations.post_owned_individual_assistant_turn`'s existing
    `(conversation_id, role='assistant', client_message_id)` upsert collapses
    a same-key double-submit to ONE row. Every other caller (main chat, a
    user turn, or an assistant turn with no key) takes the ORIGINAL insert
    path below, byte-identical to before."""
    c = require_client()
    conversation = _get_owned_conversation(c, conversation_id, company)
    if conversation is None:
        raise HTTPException(404, "Conversation not found")

    is_individual_project = (
        conversation.get("kind") == "individual"
        and conversation.get("project_id") is not None
    )

    if is_individual_project and body.role == "assistant" and body.client_message_id:
        from app.db import conversations as conversations_db

        # `post_owned_individual_assistant_turn` resolves its OWN conversation
        # server-side from `(project_id, user_id)` — the same single row
        # `_get_owned_conversation` above already confirmed the caller owns,
        # so this can never write anywhere but the conversation the caller
        # just posted to. It also advances the caller's own read cursor and
        # bumps `conversations.updated_at` itself, so nothing further is
        # needed on this path (no attachments, no `reply` jsonb — the
        # assistant-turn callers that carry a client_message_id never send
        # either today; adding one back would need this branch reconsidered).
        turn = conversations_db.post_owned_individual_assistant_turn(
            project_id=conversation["project_id"],
            user_id=conversation["user_id"],
            content=body.content,
            client_message_id=body.client_message_id,
        )
    else:
        row: dict[str, Any] = {
            "conversation_id": conversation_id,
            "role": body.role,
            "content": body.content,
        }
        if body.attachments:
            # exclude_none keeps the stored shape minimal — a text-only attachment
            # stays {name, content}; key/mime/size appear only when a file was stored.
            row["attachments"] = [a.model_dump(exclude_none=True) for a in body.attachments]
        # ASSISTANT TURNS ONLY. `reply` is what the product said, in the shape it
        # said it; a user turn has no structured reply and a client sending one is
        # describing a turn that does not exist, so the field is dropped rather
        # than stored. `list_turns` selects `*`, so a row written here comes back
        # to the client with no read-side change.
        if body.reply is not None and body.role == "assistant":
            row["reply"] = body.reply
        resp = c.table("conversation_turns").insert(row).execute()
        turn = resp.data[0] if resp.data else {}
        _catalog_turn_attachments(turn, body, company)
        # Update conversation preview + timestamp. Only overwrite preview on user
        # turns — assistant turns should NOT blank out the last user message shown
        # in the chat-history list (ChatsScreen).
        patch: dict[str, Any] = {"updated_at": utc_now()}
        if body.role == "user":
            patch["preview"] = body.content[:200]
        c.table("conversations").update(patch).eq("id", conversation_id).execute()

    # ── Realtime gate: individual project chat ONLY ─────────────────────────
    # Defense-in-depth (matches the client's own `parseRealtimeTurnPayload`
    # blank-content guard): a blank-content row is never worth a broadcast —
    # the row is still persisted above exactly as before, only the publish
    # is skipped, so a client that somehow still receives it can never
    # render an empty/phantom bubble from THIS row.
    if is_individual_project and turn and (turn.get("content") or "").strip():
        try:
            # The conversation is private to its own `user_id` — that's the
            # owner uid the per-user topic keys on, never the acting request
            # user (same row already fetched above; no second query, mirrors
            # `get_individual_conversation_owner`'s own resolution).
            owner_uid = conversation.get("user_id")
            if owner_uid is not None:
                publish_broadcast(
                    f"project:{conversation['project_id']}:user:{owner_uid}",
                    "turn.created",
                    {k: turn[k] for k in _TURN_CREATED_DTO_KEYS if k in turn},
                )
        except Exception:  # noqa: BLE001 — best-effort, AD-P22: never mask a successful write
            logger.warning(
                "realtime_publish_prep_failed topic=project:%s:user:? event=turn.created "
                "conversation_id=%s",
                conversation.get("project_id"), conversation_id,
            )

    return turn


@router.delete("/{conversation_id}/turns/{turn_id}")
def rewind_to_turn(
    conversation_id: int,
    turn_id: int,
    company: CompanyContext = Depends(require_company),
):
    """REWIND the conversation to just before `turn_id` — owner only.

    Deletes that turn and every turn after it. One flow needs this: editing or
    retrying a past question. The client rewinds the thread on screen to the
    point being re-asked, and the record has to follow, or the same
    conversation reopened from history would show the old question, its old
    answer, AND the new pair — the second copy of a conversation nobody had.

    This is the only endpoint that removes anything from a conversation, so the
    two rules it keeps are what make it safe to have:

      * `role='user'` only. The anchor is always a QUESTION — you rewind to a
        thing you said, never into the middle of an answer. So an assistant
        turn can never be deleted while the question it answered survives, and
        the product's own words are never edited out from under it.
      * SUFFIX only. It cuts from a point to the end, never a hole in the
        middle, so whatever remains is a coherent prefix of the conversation
        that actually happened — every surviving question keeps its answer.

    A turn id that isn't in this conversation (or isn't a user turn) is 409,
    not 404: the caller is a client whose thread has moved on, and the honest
    answer is "that isn't a thing you can rewind to", not "no such row". A
    conversation that isn't the caller's own still 404s exactly like every
    other route here — a foreign tenant must not learn it exists.
    """
    c = require_client()
    if _get_owned_conversation(c, conversation_id, company) is None:
        raise HTTPException(404, "Conversation not found")
    resp = (
        c.table("conversation_turns")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    turns = resp.data or []
    cut = next(
        (i for i, t in enumerate(turns) if int(t.get("id") or 0) == turn_id),
        -1,
    )
    if cut == -1:
        raise HTTPException(409, "That turn is not in this conversation")
    if turns[cut].get("role") != "user":
        raise HTTPException(409, "A conversation can only be rewound to a user turn")

    for doomed in turns[cut:]:
        c.table("conversation_turns").delete().eq("id", doomed.get("id")).execute()
    # Roll the list preview back to whatever the last SURVIVING user turn said,
    # so the chat-history row doesn't keep advertising a message that no longer
    # exists. Empty when the rewind took the whole conversation.
    prior = next(
        (t.get("content") or "" for t in reversed(turns[:cut]) if t.get("role") == "user"),
        "",
    )
    c.table("conversations").update(
        {"preview": prior[:200], "updated_at": utc_now()}
    ).eq("id", conversation_id).execute()
    return {"ok": True, "removed": len(turns) - cut}

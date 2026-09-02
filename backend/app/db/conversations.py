"""Conversation ↔ PRD binding.

The chat surface opens a PRD command tab (import a document, "generate a PRD
for X") and persists the seed turn as a conversation BEFORE the PRD exists —
the prd_id simply isn't known until the generate/import call returns. The
client used to back-patch the link afterwards from a React effect, which meant
navigating away (or reloading) mid-generation left the conversation with
prd_id=NULL forever: reopening it from history came back as a plain, PRD-less
chat with no way to reach the document it had just produced.

Binding server-side at PRD-creation time closes that window: once the route has
both ids it writes the link itself, so the browser is free to leave.
"""
from __future__ import annotations

import logging
from typing import Any

from postgrest.exceptions import APIError

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception, index_name: str) -> bool:
    """Same posture as `db/asks.py::claim_retry_attempt`'s race-loser check
    (Postgres 23505 on the partial-unique index): a concurrent writer won
    the race for this key, and this call just lost it."""
    return getattr(exc, "code", None) == _UNIQUE_VIOLATION or index_name in str(exc)


def conversation_belongs_to_company(conversation_id: int, company_id: str) -> bool:
    """Whether this conversation exists AND belongs to this company.

    Conversation ids are sequential integers, so any route that accepts one from
    the client has to prove ownership before storing it — otherwise a caller can
    stamp their own artifact with a foreign tenant's conversation id and read
    that chat's title back out of the artifacts listing. Callers turn False into
    404 (never 403): "exists but not yours" and "doesn't exist" must be
    indistinguishable.
    """
    rows = (
        require_client().table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def bind_conversation_to_prd(
    conversation_id: int,
    prd_id: int,
    company_id: str,
    user_id: str | None,
) -> bool:
    """Point `conversation_id` at `prd_id`. True if a row was updated.

    Ownership-scoped exactly like the conversations routes (per-user chats
    within a company), so a caller can never bind someone else's conversation
    by guessing an id. Only fills a NULL prd_id — a conversation already bound
    to a PRD is left alone, so a re-issued command can't silently repoint an
    existing chat at a different document.

    Best-effort by design: this runs inside the fire-and-forget generate/import
    routes, where the PRD itself is what the caller is waiting on. A failure
    here is logged and swallowed rather than failing the generation — the
    client's own back-patch remains as a fallback.
    """
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .update({"prd_id": prd_id})
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .is_("prd_id", "null")
        )
        # Legacy rows predate user stamping (user_id IS NULL) and are hidden from
        # everyone, so there is nothing to bind when the caller has no user id.
        if user_id:
            q = q.eq("user_id", user_id)
        resp: Any = q.execute()
        return bool(resp.data)
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to bind conversation %s to PRD %s", conversation_id, prd_id,
            exc_info=True,
        )
        return False


def conversations_for_prds(
    prd_ids: list[int], company_id: str
) -> dict[int, dict]:
    """`{prd_id: {"id", "title"}}` — the newest conversation bound to each PRD.

    The REVERSE of `get_conversation_prd_id`, for the surfaces that start from
    the artifact and want its chat back: the chat's artifact list and the
    open-with-thread flow both need to know which thread produced a PRD so a
    click can resume the conversation instead of opening a bare document.

    COMPANY-scoped, deliberately not user-scoped — the same posture as the
    Artifacts listing's conversation-title joins (db/artifacts.py): an
    artifact library is shared across the company, so the thread behind a
    teammate's PRD is as openable as the PRD itself. Newest binding wins when
    several chats point at one PRD (regeneration re-binds are fill-only-NULL,
    but imports/deep-links can produce more than one row).

    Best-effort: any failure returns {} and the caller renders artifacts
    without thread affordances rather than failing the listing.
    """
    if not prd_ids:
        return {}
    try:
        rows = (
            require_client()
            .table("conversations")
            .select("id, title, prd_id")
            .in_("prd_id", prd_ids)
            .eq("company_id", company_id)
            .order("id", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — a listing enrichment, never the listing
        logger.warning("conversations_for_prds failed", exc_info=True)
        return {}
    out: dict[int, dict] = {}
    for r in rows:
        pid = r.get("prd_id")
        # Newest-first order + first-write-wins keeps the latest thread.
        if isinstance(pid, int) and pid not in out:
            out[pid] = {"id": r.get("id"), "title": r.get("title") or ""}
    return out


def bind_conversation_to_project(
    conversation_id: int,
    project_id: int,
    company_id: str,
    user_id: str | None,
) -> bool:
    """Point `conversation_id` at `project_id`. True if a row was updated.

    Mirrors `bind_conversation_to_prd` exactly — same ownership scoping (a
    caller can never bind someone else's conversation by guessing an id),
    same fill-only-NULL semantics (a conversation already bound to a
    project is left alone — first-write-wins, so a re-issued ask can't
    silently repoint an existing chat at a different project), same
    best-effort contract (this runs inside `/v1/ask`, where the answer
    itself is what the caller is waiting on; a failure here is logged and
    swallowed rather than failing the ask).
    """
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .update({"project_id": project_id})
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .is_("project_id", "null")
        )
        # Legacy rows predate user stamping (user_id IS NULL) and are hidden
        # from everyone, so there is nothing to bind when the caller has no
        # user id.
        if user_id:
            q = q.eq("user_id", user_id)
        resp: Any = q.execute()
        return bool(resp.data)
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to bind conversation %s to project %s", conversation_id, project_id,
            exc_info=True,
        )
        return False


def get_conversation_prd_id(
    conversation_id: int,
    company_id: str,
    user_id: str | None,
) -> int | None:
    """The PRD this conversation is bound to, or None.

    The read half of bind_conversation_to_prd, with the same ownership scoping
    (per-user chats within a company). Used by the chat intent dispatcher to
    resolve "make it shorter" to the PRD this thread produced when the client
    didn't send an explicit prd_id (e.g. a resumed chat whose tab lost its
    local state). Best-effort: any error → None."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .select("prd_id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .limit(1)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        rows: Any = q.execute()
        if rows.data:
            return rows.data[0].get("prd_id")
        return None
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to read PRD binding for conversation %s", conversation_id,
            exc_info=True,
        )
        return None


def get_conversation_project_id(
    conversation_id: int,
    company_id: str,
) -> int | None:
    """The project a chat conversation belongs to, or None.

    A project-bound conversation is a `conversations` row with a non-null
    `project_id`; `kind` is `individual` (the private per-user project chat) or
    the legacy shared-thread value the old group chat wrote (still readable —
    no DB rows were removed when the group surface was retired). A MAIN-CHAT
    row shares `kind`'s `individual` default but carries `project_id = NULL`,
    so it returns None and stays workspace-scoped — `project_id` is the real
    discriminator, and the `kind` guard only fences off any future non-project
    kind. Company-scoped only — no per-user gate, because a legacy shared-kind
    row is owned by its creator, not the member currently classifying a
    message; the caller already reached this conversation_id through an
    ownership/membership-checked surface,
    and the value only NARROWS the read-only artifact listing to that project's
    own documents (never widens it, never mutates), so the company scope is the
    boundary that matters. Best-effort: any error → None.

    Used by the chat-intent route to resolve a project chat's `open_artifact` /
    `list_artifacts` legs against THE PROJECT's own artifacts even when the
    client did not send a `context_source` — closing the class where a project
    chat silently answers workspace-wide.
    """
    try:
        c = require_client()
        rows: Any = (
            c.table("conversations")
            .select("project_id, kind")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        if not rows.data:
            return None
        row = rows.data[0]
        if row.get("kind") not in ("individual", "group"):
            return None
        return row.get("project_id")
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to read project binding for conversation %s", conversation_id,
            exc_info=True,
        )
        return None


# ── Evidence half of the binding (mirrors the PRD pair above exactly) ───────
#
# Extends the SAME mechanism to Evidence rather than inventing a parallel one
# (conversations.evidence_id, added alongside conversations.prd_id's existing
# column — see the 20260731 migration): the chat-task command's Evidence
# artifact (generate_task_evidence) gets the identical "bind the commanding
# chat to the artifact server-side, before the caller could navigate away"
# treatment PRDs already had.


def bind_conversation_to_evidence(
    conversation_id: int,
    evidence_id: int,
    company_id: str,
    user_id: str | None,
) -> bool:
    """Point `conversation_id` at `evidence_id`. True if a row was updated.

    Same ownership scoping and "only fills a NULL" semantics as
    bind_conversation_to_prd — see its docstring."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .update({"evidence_id": evidence_id})
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .is_("evidence_id", "null")
        )
        if user_id:
            q = q.eq("user_id", user_id)
        resp: Any = q.execute()
        return bool(resp.data)
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to bind conversation %s to evidence %s", conversation_id, evidence_id,
            exc_info=True,
        )
        return False


def get_conversation_evidence_id(
    conversation_id: int,
    company_id: str,
    user_id: str | None,
) -> int | None:
    """The Evidence doc this conversation is bound to, or None. Read half of
    bind_conversation_to_evidence — see get_conversation_prd_id."""
    try:
        c = require_client()
        q = (
            c.table("conversations")
            .select("evidence_id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .limit(1)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        rows: Any = q.execute()
        if rows.data:
            return rows.data[0].get("evidence_id")
        return None
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to read evidence binding for conversation %s", conversation_id,
            exc_info=True,
        )
        return None


# ── Individual project chat ("My chat with Sprntly") ─────────────────────
#
# ADDITIVE ONLY: ONE `kind='individual'` conversation per (project_id,
# user_id), rather than per project. This is what gives
# `ProjectIndividualChat.tsx` a real, reusable `conversation_id` to thread
# into `/v1/ask` — before this helper existed, every turn from that surface
# POSTed a fresh, unbound ask, so `ask_job_runner._run_sync`'s memory-
# promotion gate (`project_id is not None and conversation_id is not None`)
# could never fire for it, no matter how durable an insight the turn
# produced.
#
# This does NOT add a partial unique index as a concurrency backstop:
# `get_conversation_by_prd`/`get_conversation_by_evidence` above already
# tolerate this exact same "select the most recent, else create" shape
# without a DB-level guarantee, and a lost race here self-heals the same
# way — a rare double-create on two simultaneous first opens just means a
# later get-or-create (any subsequent mount) converges on the
# most-recently-created row; the extra row sits unused and is never read
# again. Flagged for the planner as the durable-vs-per-mount call this
# ticket made (see the dispatch report), not silently decided.


def get_individual_project_chat(project_id: int, user_id: str) -> dict[str, Any] | None:
    """This caller's `kind='individual'` conversation for `project_id`,
    most recently created first, or None if they haven't sent a message in
    this project's individual chat yet. Read-only counterpart of
    `create_individual_project_chat`."""
    rows = (
        require_client()
        .table("conversations")
        .select("*")
        .eq("project_id", project_id)
        .eq("kind", "individual")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def create_individual_project_chat(project_id: int, user_id: str) -> dict[str, Any]:
    """Create-if-absent: THIS caller's one individual project chat.
    Idempotent (best-effort — see the module-level note above) — a second
    call for the same (project, caller) pair returns the SAME row."""
    existing = get_individual_project_chat(project_id, user_id)
    if existing:
        return existing

    from app.db.projects import get_project  # local import: avoid a load-order cycle

    project = get_project(project_id)
    if not project:
        raise ValueError(f"create_individual_project_chat: project {project_id} not found")

    client = require_client()
    row = (
        client.table("conversations")
        .insert(
            {
                "company_id": project["company_id"],
                "workspace_id": project["workspace_id"],
                "user_id": user_id,
                "project_id": project_id,
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    return row


@retry_on_disconnect
def post_individual_turn(conversation_id: int, role: str, content: str) -> dict[str, Any]:
    """Write a turn into an individual project conversation and touch
    updated_at. The cross-user brief is always role='assistant',
    author_user_id=NULL — the agent never writes a 'user' turn as a person.

    An individual project conversation is single-owner (the assignee), so
    there is no second human author to attribute a turn to — every row this
    helper writes is the agent's, delivered cross-user (build spec
    AD-P16/AD-P19). Returns the inserted turn row (incl. `id`) so the
    caller captures `delivered_turn_id`."""
    client = require_client()
    resp = (
        client.table("conversation_turns")
        .insert(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "author_user_id": None,
            }
        )
        .execute()
    )
    client.table("conversations").update({"updated_at": utc_now()}).eq(
        "id", conversation_id
    ).execute()
    return resp.data[0]


def get_individual_conversation_owner(conversation_id: int) -> str | None:
    """The `user_id` that OWNS an individual project chat — resolved from the
    conversation row itself, never from the caller's own identity. Needed by
    a cross-user writer (`post_individual_turn`, the agent's own async reply)
    that has a `conversation_id` but not necessarily the OWNER's uid: the
    acting caller and the conversation's owner are not guaranteed to be the
    same person (a delegate/execute-task reply can land in a teammate's
    individual chat). Returns None when the row is missing or not
    `kind='individual'` — callers treat that as "nothing to publish to"
    rather than guessing a topic."""
    rows = (
        require_client()
        .table("conversations")
        .select("user_id, kind")
        .eq("id", conversation_id)
        .eq("kind", "individual")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["user_id"] if rows else None


def list_individual_turns(
    conversation_id: int, user_id: str, since: int | None = None
) -> list[dict[str, Any]]:
    """Turns in an INDIVIDUAL project chat the CALLER OWNS, ascending, after
    the `since` id cursor (AD-P4 poll read). Returns `[]` (never another
    user's turns) when the conversation is not `kind='individual'` OR its
    `user_id` != the caller — the read-side counterpart of the delegation
    cross-user write gate (`post_individual_turn` is reachable cross-user by
    design; this reader never is). Single-owner individual chats don't need
    `author_user_id` in the payload (the owner is implied), so the row shape
    is `{id, role, content, created_at}` — no `profiles` join needed."""
    client = require_client()
    conv = (
        client.table("conversations")
        .select("id, user_id, kind")
        .eq("id", conversation_id)
        .eq("kind", "individual")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    if not conv:
        return []

    q = client.table("conversation_turns").select(
        "id, role, content, created_at, client_message_id"
    ).eq("conversation_id", conversation_id)
    if since is not None:
        q = q.gt("id", since)
    return q.order("id").execute().data or []


def _owned_conversation_id(project_id: int, user_id: str) -> int:
    """Resolve THIS caller's individual project chat server-side
    (create-if-absent), never a client-supplied `conversation_id` — the
    ownership spine both owned writers below share (AC6)."""
    conversation = get_individual_project_chat(project_id, user_id)
    if conversation is None:
        conversation = create_individual_project_chat(project_id, user_id)
    return conversation["id"]


def _advance_own_cursor(conversation_id: int, user_id: str, turn_id: int) -> None:
    """Best-effort: advance the caller's own read cursor to the turn they
    just wrote, so writing your own turn and leaving does not flip your own
    chat to unread (AC8). A cursor miss is benign — it just leaves a
    stale-but-harmless unread dot; it must never break the write. Local
    import avoids a load-order cycle (`conversation_read_cursors` already
    imports `list_individual_turns` from this module)."""
    try:
        from app.db import conversation_read_cursors

        conversation_read_cursors.set_cursor(conversation_id, user_id, turn_id)
    except Exception:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "failed to advance own read cursor conversation_id=%s turn_id=%s",
            conversation_id, turn_id, exc_info=True,
        )


def _update_owned_turn_content(turn_id: int, conversation_id: int, content: str) -> dict[str, Any]:
    """Idempotent-key hit, but the incoming content differs from what's
    already stored — a two-phase flow (park an interim answer under a
    `client_message_id`, then re-persist the SAME key once the flow settles
    on its real, final answer) reusing the key on purpose. Updates the
    existing row's content in place and returns it, rather than the
    read-check silently discarding the new content. Only called when a
    content mismatch is already confirmed by the caller, so a same-key/
    same-content retry never reaches here and never issues this write."""
    client = require_client()
    row = (
        client.table("conversation_turns")
        .update({"content": content})
        .eq("id", turn_id)
        .execute()
        .data[0]
    )
    client.table("conversations").update({"updated_at": utc_now()}).eq(
        "id", conversation_id
    ).execute()
    return row


def _find_owned_turn(conversation_id: int, role: str, **key: Any) -> dict[str, Any] | None:
    """Read-check for the idempotent writers below: the existing row for
    this `(conversation_id, role, key)`, or None. `key` is exactly one of
    `client_message_id=...` / `ask_job_id=...`."""
    q = (
        require_client()
        .table("conversation_turns")
        .select("id, role, content, created_at, client_message_id, ask_job_id, author_user_id")
        .eq("conversation_id", conversation_id)
        .eq("role", role)
    )
    for col, val in key.items():
        q = q.eq(col, val)
    rows = q.limit(1).execute().data
    return rows[0] if rows else None


@retry_on_disconnect
def post_owned_individual_user_turn(
    *, project_id: int, user_id: str, content: str, client_message_id: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write the CALLER'S OWN user turn into THEIR individual project chat —
    the owned, idempotent counterpart of `post_individual_turn` (the
    cross-user brief writer, left unchanged). Resolves the conversation
    SERVER-side from `(project_id, user_id)` — never a client-supplied
    `conversation_id` — so a caller can only ever write into their own
    individual chat (AC6). Idempotent on `(conversation_id, role='user',
    client_message_id)` (AC4): a retry/double-submit with the SAME key
    returns the SAME row rather than inserting a second one. Advances the
    author's own read cursor (AC8). Returns the row (incl. `id`).

    `attachments` (optional): the resolved structured attachment texts
    ([{name, content, …}]) riding this send, written to the EXISTING
    `conversation_turns.attachments` column (no new column) when truthy —
    so `_load_history` folds them onto the answer's context on a follow-up.
    The default (None) leaves the insert byte-identical to the
    pre-attachment write, so every existing call site is unaffected."""
    conversation_id = _owned_conversation_id(project_id, user_id)

    existing = _find_owned_turn(
        conversation_id, "user", client_message_id=client_message_id
    )
    if existing is not None:
        _advance_own_cursor(conversation_id, user_id, existing["id"])
        return existing

    client = require_client()
    insert_payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": content,
        "author_user_id": user_id,
        "client_message_id": client_message_id,
    }
    if attachments:
        insert_payload["attachments"] = attachments
    try:
        row = (
            client.table("conversation_turns")
            .insert(insert_payload)
            .execute()
            .data[0]
        )
    except APIError as exc:
        if not _is_unique_violation(exc, "conversation_turns_client_msg_uidx"):
            raise
        # A concurrent send with the SAME client_message_id won the race —
        # the §A partial-unique backstop (AC4). Re-read the row it wrote.
        existing = _find_owned_turn(
            conversation_id, "user", client_message_id=client_message_id
        )
        if existing is None:
            raise
        _advance_own_cursor(conversation_id, user_id, existing["id"])
        return existing

    client.table("conversations").update({"updated_at": utc_now()}).eq(
        "id", conversation_id
    ).execute()
    _advance_own_cursor(conversation_id, user_id, row["id"])
    return row


@retry_on_disconnect
def post_owned_individual_assistant_turn(
    *,
    project_id: int,
    user_id: str,
    content: str,
    client_message_id: str | None = None,
    ask_job_id: int | None = None,
) -> dict[str, Any]:
    """Write the ASSISTANT'S turn into the CALLER'S individual project chat
    (`author_user_id: None` — the agent, not the caller, said this; `user_id`
    only resolves WHICH owned conversation to write into). Same server-side
    ownership resolution as the user-turn writer (AC6). Idempotent-keyed
    (AC4/AC5) on `(conversation_id, role='assistant', ask_job_id)` when
    `ask_job_id` is given (the `/v1/ask` answer — the durable run link a
    resumed poll reuses), else on `(conversation_id, role='assistant',
    client_message_id)`. Exactly one of the two keys must be given — a
    caller passing neither (or both) is a bug (asserted).

    Upsert-on-content: a key hit whose stored content already matches is a
    true no-op (no write, same invariant as before — a retry/double-submit
    never inserts a second row and never needlessly writes). A key hit whose
    content DIFFERS updates that row's content in place instead of
    discarding it — the two-phase private-clarify flow parks an interim
    answer under a `client_message_id` and then re-persists the SAME key
    once generation settles on the real, final answer; without this, the
    final answer was silently dropped and a reload showed the stale interim
    text forever. Advances the caller's own read cursor (AC8) either way.
    Returns the row (incl. `id`)."""
    if (client_message_id is None) == (ask_job_id is None):
        raise ValueError(
            "post_owned_individual_assistant_turn requires exactly one of "
            "client_message_id or ask_job_id"
        )
    conversation_id = _owned_conversation_id(project_id, user_id)

    key_col, key_val, index_name = (
        ("ask_job_id", ask_job_id, "conversation_turns_ask_job_uidx")
        if ask_job_id is not None
        else ("client_message_id", client_message_id, "conversation_turns_client_msg_uidx")
    )

    existing = _find_owned_turn(conversation_id, "assistant", **{key_col: key_val})
    if existing is not None:
        if existing["content"] != content:
            existing = _update_owned_turn_content(existing["id"], conversation_id, content)
        _advance_own_cursor(conversation_id, user_id, existing["id"])
        return existing

    client = require_client()
    try:
        row = (
            client.table("conversation_turns")
            .insert(
                {
                    "conversation_id": conversation_id,
                    "role": "assistant",
                    "content": content,
                    "author_user_id": None,
                    "client_message_id": client_message_id,
                    "ask_job_id": ask_job_id,
                }
            )
            .execute()
            .data[0]
        )
    except APIError as exc:
        if not _is_unique_violation(exc, index_name):
            raise
        # A concurrent write won the race for this key (§A partial-unique
        # backstop) — re-read what it wrote and apply the same
        # upsert-on-content rule as the pre-check above.
        existing = _find_owned_turn(conversation_id, "assistant", **{key_col: key_val})
        if existing is None:
            raise
        if existing["content"] != content:
            existing = _update_owned_turn_content(existing["id"], conversation_id, content)
        _advance_own_cursor(conversation_id, user_id, existing["id"])
        return existing

    client.table("conversations").update({"updated_at": utc_now()}).eq(
        "id", conversation_id
    ).execute()
    _advance_own_cursor(conversation_id, user_id, row["id"])
    return row


@retry_on_disconnect
def latest_conversation_at(company_id: str) -> str | None:
    """When this company last held a conversation, or None if it never has.

    Every `conversations` row is created by a human sending a message — the
    scheduler, the warm fan-out and the startup pass never write one — so the
    newest row is the cheapest honest answer to "has anybody actually opened
    this workspace lately?". Used by `app.warm_gate` to decide whether
    pre-generating a brief's drill-downs is worth paying for.

    `llm_usage_events.user_id` is the obvious alternative and is NOT usable:
    no caller currently populates `usage_scope(user_id=...)`, so the column is
    NULL on every row — including unambiguously interactive ones like
    `apply_prd_chat_edit`. Worth fixing on its own, but it cannot back this.
    """
    rows = (
        require_client().table("conversations")
        .select("created_at")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return (rows[0].get("created_at") if rows else None) or None

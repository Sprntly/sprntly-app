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

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)


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


# ── Group chat (build spec §4.4/§4.5, AD-P2) ──────────────────────────────
#
# ADDITIVE ONLY. Everything below is a NEW authorization/read/write path used
# exclusively when `conversations.kind='group'` — it never touches
# `conversation_belongs_to_company` or any per-user helper above. A group
# chat's `user_id` is its creator (for symmetry with the existing schema);
# real authorization is `project_chat_members` (v1: seeded 1:1 from
# `project_members` — see build spec §4.5), read via the helpers below only.
#
# Every helper here that accepts a bare `conversation_id` re-checks
# `kind='group'` before reading/writing it — a private single-owner
# conversation id can never be read or written through this path, even if a
# caller upstream forgets the project-membership gate (isolation regression,
# R4/§9).


AGENT_AUTHOR_NAME = "Sprntly"  # constant label for assistant turns (author_user_id=NULL)


def get_group_chat(project_id: int) -> dict[str, Any] | None:
    """The project's single `kind='group'` conversation row, or None if it
    hasn't been created yet. Read-only counterpart of `create_group_chat`."""
    rows = (
        require_client()
        .table("conversations")
        .select("*")
        .eq("project_id", project_id)
        .eq("kind", "group")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def _seed_roster_from_members(client: Any, conversation_id: int, project_id: int) -> None:
    """Populate `project_chat_members` from the project's CURRENT
    `project_members` at group-chat creation time (v1: the group chat is
    open to all members, build spec §4.5). Upserted (not inserted) so a
    re-run after the race backstop in `create_group_chat` can never raise a
    duplicate-PK error."""
    members = (
        client.table("project_members")
        .select("user_id")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    if not members:
        return
    client.table("project_chat_members").upsert(
        [{"conversation_id": conversation_id, "user_id": m["user_id"]} for m in members],
        on_conflict="conversation_id,user_id",
    ).execute()


@retry_on_disconnect
def create_group_chat(project_id: int, creator_id: str) -> dict[str, Any]:
    """Create-if-absent: the project's ONE `kind='group'` conversation.
    Idempotent — a second call for the same project returns the SAME row.

    The schema's partial unique index (`uq_one_group_chat_per_project`) is
    the concurrency backstop: if two requests race to create the group chat,
    exactly one INSERT wins and the other raises a unique-violation here —
    caught below, re-reading and returning the WINNER's row rather than
    erroring the loser's caller (build spec §5.3).

    Seeds `project_chat_members` from `project_members` on the winning
    creation only (never on an idempotent no-op return, and never again on
    the losing side of a race)."""
    existing = get_group_chat(project_id)
    if existing:
        return existing

    from app.db.projects import get_project  # local import: avoid a load-order cycle

    project = get_project(project_id)
    if not project:
        raise ValueError(f"create_group_chat: project {project_id} not found")

    client = require_client()
    try:
        row = (
            client.table("conversations")
            .insert(
                {
                    "company_id": project["company_id"],
                    "workspace_id": project["workspace_id"],
                    "user_id": creator_id,
                    "project_id": project_id,
                    "kind": "group",
                }
            )
            .execute()
            .data[0]
        )
    except Exception:
        # Race backstop — see docstring. Only re-raise if the re-read also
        # comes back empty (a real failure, not a lost race).
        existing = get_group_chat(project_id)
        if existing:
            return existing
        raise

    _seed_roster_from_members(client, row["id"], project_id)
    return row


def user_in_group_roster(conversation_id: int, user_id: str) -> bool:
    """Whether `user_id` is seeded into this group chat's roster
    (`project_chat_members`). v1: membership is effectively project
    membership (the roster is seeded from it 1:1 at creation, build spec
    §4.5) — routes gate on project membership directly; this helper exists
    for the forward-compatible explicit-roster check (e.g. a future
    topic-chat whose roster is a SUBSET of project members)."""
    rows = (
        require_client()
        .table("project_chat_members")
        .select("conversation_id")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def _author_display(
    author_user_id: str | None, profiles_by_id: dict[str, dict[str, Any]]
) -> tuple[str | None, str | None]:
    """(author_name, author_job_role) for one turn. Agent turns
    (author_user_id=None) get the constant Sprntly label with no job role;
    human turns resolve their name the same way `db.projects.list_members`
    does (full_name, else first+last, else None) and `job_role` from
    `profiles.role` (AD-P5 — the person's own job designation)."""
    if not author_user_id:
        return AGENT_AUTHOR_NAME, None
    prof = profiles_by_id.get(author_user_id) or {}
    full = (prof.get("full_name") or "").strip()
    first = (prof.get("first_name") or "").strip()
    last = (prof.get("last_name") or "").strip()
    name = full or (f"{first} {last}".strip() if (first or last) else None) or None
    return name, prof.get("role")


def list_group_turns(conversation_id: int, since: int | None = None) -> list[dict[str, Any]]:
    """Turns in a group chat, ascending, after the `since` cursor (a turn
    id — AD-P4 poll read, mirrors the `prototype_comments` refetch
    posture). Each turn carries `author_name`/`author_job_role` (joined
    from `profiles`; agent turns get the constant Sprntly label, no job
    role — build spec §5.3/§5.4).

    Refuses (returns []) when `conversation_id` does not resolve to a
    `kind='group'` row — the group path can never read an individual
    chat's turns, even if a caller forgets to resolve the id via
    `get_group_chat` first (isolation regression, R4/§9)."""
    client = require_client()
    conv = (
        client.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("kind", "group")
        .limit(1)
        .execute()
        .data
    )
    if not conv:
        return []

    q = client.table("conversation_turns").select("*").eq("conversation_id", conversation_id)
    if since is not None:
        q = q.gt("id", since)
    turns = q.order("id").execute().data or []
    if not turns:
        return []

    author_ids = {t["author_user_id"] for t in turns if t.get("author_user_id")}
    profiles_by_id: dict[str, dict[str, Any]] = {}
    if author_ids:
        profiles_by_id = {
            p["id"]: p
            for p in (
                client.table("profiles")
                .select("id, full_name, first_name, last_name, role")
                .in_("id", list(author_ids))
                .execute()
                .data
                or []
            )
        }

    out = []
    for t in turns:
        author_id = t.get("author_user_id")
        name, job_role = _author_display(author_id, profiles_by_id)
        out.append(
            {
                "id": t["id"],
                "role": t["role"],
                "content": t["content"],
                "author_user_id": author_id,
                "author_name": name,
                "author_job_role": job_role,
                "created_at": t["created_at"],
            }
        )
    return out


def post_group_turn(
    conversation_id: int,
    author_user_id: str | None,
    content: str,
    *,
    role: str = "user",
) -> dict[str, Any] | None:
    """Insert one turn into a group chat. Human turn: pass the poster's
    `author_user_id` (role defaults to 'user'). Agent turn (on an
    `@Sprntly` mention): pass `author_user_id=None, role='assistant'` — the
    ONLY conversation_turns rows with `author_user_id` set at all are group
    turns (single-owner individual chats never needed it, build spec §4.5).

    Refuses (returns None, no write) when `conversation_id` does not
    resolve to a `kind='group'` row — mirrors `list_group_turns`'
    isolation guard (R4/§9)."""
    client = require_client()
    conv = (
        client.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("kind", "group")
        .limit(1)
        .execute()
        .data
    )
    if not conv:
        return None

    resp = (
        client.table("conversation_turns")
        .insert(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "author_user_id": author_user_id,
            }
        )
        .execute()
    )
    client.table("conversations").update({"updated_at": utc_now()}).eq(
        "id", conversation_id
    ).execute()
    return resp.data[0] if resp.data else None


# ── Individual project chat ("My chat with Sprntly") ─────────────────────
#
# ADDITIVE ONLY, mirrors the group-chat pair (`get_group_chat`/
# `create_group_chat`) one level down: ONE `kind='individual'` conversation
# per (project_id, user_id), rather than per project. This is what gives
# `ProjectIndividualChat.tsx` a real, reusable `conversation_id` to thread
# into `/v1/ask` — before this helper existed, every turn from that surface
# POSTed a fresh, unbound ask, so `ask_job_runner._run_sync`'s memory-
# promotion gate (`project_id is not None and conversation_id is not None`)
# could never fire for it, no matter how durable an insight the turn
# produced.
#
# Unlike `create_group_chat`, this does NOT add a partial unique index as a
# concurrency backstop: `get_conversation_by_prd`/`get_conversation_by_evidence`
# above already tolerate this exact same "select the most recent, else
# create" shape without a DB-level guarantee, and a lost race here
# self-heals the same way — a rare double-create on two simultaneous first
# opens just means a later get-or-create (any subsequent mount) converges on
# the most-recently-created row; the extra row sits unused and is never
# read again. Flagged for the planner as the durable-vs-per-mount call this
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

    Mirrors `post_group_turn` one level down (individual rather than
    group), minus the author_user_id parameter: an individual project
    conversation is single-owner (the assignee), so there is no second
    human author to attribute a turn to — every row this helper writes is
    the agent's, delivered cross-user (build spec AD-P16/AD-P19). Returns
    the inserted turn row (incl. `id`) so the caller captures
    `delivered_turn_id`."""
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

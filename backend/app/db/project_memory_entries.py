"""Project memory — discrete, provenance-tagged entries (the source of
truth) plus the read-only cached synthesized summary on top of them.

Layering (build spec AD-P3): `project_memory_entries` rows are either
user-authored (`author_user_id` set, `promoted_by` NULL — "Added by
David") or agent-promoted (`author_user_id` NULL, `promoted_by='agent'` —
"Promoted by Sprntly"); the schema's XOR check enforces exactly one is
set. `add_entry` writes the user-authored shape; `add_agent_promoted_entry`
(the agent-promotion writer's own insert, called from `project_memory.py`'s
`maybe_promote_turn`) writes the agent-promoted shape — the two stay
separate functions deliberately, so neither can accidentally set both
provenance fields.

`project_memory_summary` is a cached materialization, never written by
this module beyond flipping its `stale` flag on entry mutation (AD-P7):
regenerating it is a bounded LLM call, out of scope here. `get_summary`
serves the cached/seeded row read-only, or a computed fallback when none
exists yet — it never calls an LLM.
"""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect, utc_now


@retry_on_disconnect
def list_entries(project_id: int) -> list[dict]:
    """Memory entries for this project, most-recently-updated first. Each
    row is annotated with `source_conversation_kind`
    (`"group" | "individual" | None`) — the KIND of the conversation
    `source_conversation_id` points at (`conversations.kind`, additive
    group-chat column), batch-resolved in one extra query (never N+1, same
    posture as `list_members`'s profile join).

    An agent-promoted entry's `source_conversation_id` is set from BOTH the
    project's group chat (an `@Sprntly` mention / smart-interjection reply)
    AND a member's individual chat (a cross-chat promotion,
    `app/project_memory.py`'s `maybe_promote_turn`) — the id alone can't
    tell those apart, which is exactly the mislabeling this annotation
    fixes: a caller must read `source_conversation_kind`, never assume
    "group" just because the id is set."""
    client = require_client()
    entries = (
        client.table("project_memory_entries")
        .select("*")
        .eq("project_id", project_id)
        .order("updated_at", desc=True)
        .execute()
        .data
        or []
    )
    if not entries:
        return entries

    conv_ids = {
        e["source_conversation_id"]
        for e in entries
        if e.get("source_conversation_id") is not None
    }
    kind_by_id: dict[int, str] = {}
    if conv_ids:
        conv_rows = (
            client.table("conversations")
            .select("id, kind")
            .in_("id", list(conv_ids))
            .execute()
            .data
            or []
        )
        kind_by_id = {row["id"]: row["kind"] for row in conv_rows}

    for entry in entries:
        source_id = entry.get("source_conversation_id")
        entry["source_conversation_kind"] = kind_by_id.get(source_id) if source_id is not None else None
    return entries


@retry_on_disconnect
def add_entry(project_id: int, *, body: str, author_user_id: str) -> dict:
    """Insert a user-authored entry (`author_user_id` set, `promoted_by`
    left NULL by omission) and flip an existing summary's `stale` flag —
    the entries changed, so the cached synthesis is out of date until the
    Phase-2 writer regenerates it. Never calls an LLM."""
    client = require_client()
    row = (
        client.table("project_memory_entries")
        .insert(
            {
                "project_id": project_id,
                "body": body,
                "author_user_id": author_user_id,
            }
        )
        .execute()
        .data[0]
    )
    _flip_summary_stale(client, project_id)
    return row


@retry_on_disconnect
def add_agent_promoted_entry(
    project_id: int, *, body: str, source_conversation_id: int
) -> dict:
    """Insert an agent-promoted entry (`promoted_by='agent'`, `author_user_id`
    left unset/NULL by omission) — the ONLY caller of this shape; `add_entry`
    above stays the user-authored shape, kept as two separate functions so
    neither can accidentally set both provenance fields and trip the
    `pme_one_provenance` XOR check. Flips an existing summary's `stale` flag,
    same as `add_entry` — but, unlike `add_entry`, this write happens OUTSIDE
    an HTTP route handler (from `app/project_memory.py::maybe_promote_turn`),
    so the caller is responsible for calling `schedule_regen(project_id)`
    itself; flipping `stale` alone never regenerates the summary. Never calls
    an LLM."""
    client = require_client()
    row = (
        client.table("project_memory_entries")
        .insert(
            {
                "project_id": project_id,
                "body": body,
                "promoted_by": "agent",
                "source_conversation_id": source_conversation_id,
            }
        )
        .execute()
        .data[0]
    )
    _flip_summary_stale(client, project_id)
    return row


@retry_on_disconnect
def update_agent_promoted_entry(
    project_id: int, entry_id: int, *, body: str, source_conversation_id: int
) -> dict | None:
    """Revise an EXISTING agent-promoted entry in place — the semantic-dedup
    "update" branch's own write (`app/project_memory.py::maybe_promote_turn`'s
    third outcome, alongside the unchanged skip/new paths). Scoped to
    `(id, project_id, promoted_by='agent')` in the WHERE clause itself, so
    this can NEVER touch a user-authored row (`author_user_id` set,
    `promoted_by` NULL) even if the caller mis-targets one — the guardrail
    lives in the query, not only in the caller's own check. Touches
    `updated_at` explicitly (this table has no update trigger for it, unlike
    `connections`/`workspaces`) so the revised entry surfaces first in
    `list_entries`'s recency order and in `get_latest_insight`. Flips
    summary `stale`, same as the other writers below. Never calls an LLM.
    Returns None (no raise) when no row matched the scoped WHERE — the
    caller treats that as a fail-safe skip rather than trusting an
    unvalidated target_entry_id."""
    client = require_client()
    rows = (
        client.table("project_memory_entries")
        .update(
            {
                "body": body,
                "source_conversation_id": source_conversation_id,
                "updated_at": utc_now(),
            }
        )
        .eq("id", entry_id)
        .eq("project_id", project_id)
        .eq("promoted_by", "agent")
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    _flip_summary_stale(client, project_id)
    return rows[0]


@retry_on_disconnect
def update_entry(project_id: int, entry_id: int, *, body: str) -> dict | None:
    """Edit an entry's body — scoped to `(id, project_id)` so an entry_id
    from another project can never be edited through this project (404 at
    the route layer when this returns None). Flips summary `stale`."""
    client = require_client()
    rows = (
        client.table("project_memory_entries")
        .update({"body": body})
        .eq("id", entry_id)
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    _flip_summary_stale(client, project_id)
    return rows[0]


@retry_on_disconnect
def delete_entry(project_id: int, entry_id: int) -> bool:
    """Remove an entry — scoped to `(id, project_id)`, same isolation as
    `update_entry`. True iff a row was actually deleted. Flips summary
    `stale` only when a row existed to remove."""
    client = require_client()
    resp = (
        client.table("project_memory_entries")
        .delete()
        .eq("id", entry_id)
        .eq("project_id", project_id)
        .execute()
    )
    deleted = bool(resp.count) if resp.count is not None else bool(resp.data)
    if deleted:
        _flip_summary_stale(client, project_id)
    return deleted


@retry_on_disconnect
def get_summary(project_id: int) -> dict:
    """The cached `project_memory_summary` row, read-only, when one
    exists; otherwise a computed fallback `{summary_md: None, entry_count,
    stale: False}`. Never issues an LLM call in either branch — synthesis
    is a Phase 2 writer, this endpoint only reads (AD-P7/§5.4)."""
    client = require_client()
    rows = (
        client.table("project_memory_summary")
        .select("*")
        .eq("project_id", project_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return rows[0]

    count = (
        client.table("project_memory_entries")
        .select("id")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    return {"summary_md": None, "entry_count": len(count), "stale": False}


@retry_on_disconnect
def get_latest_insight(project_id: int) -> dict | None:
    """The single most-recently-updated agent-promoted entry, shaped for
    the individual chat's cross-chat INSIGHT turn — `{"by": "Sprntly",
    "text": <body>, "source_kind": <"group"|"individual"|None>}`, or `None`
    when the project has no agent-promoted entry yet (user-authored entries
    alone never produce an insight, build spec AD-P3). Reuses
    `list_entries`'s existing updated_at-desc ordering AND its
    `source_conversation_kind` annotation, rather than adding a second
    ordering/lookup convention — `source_kind` is what lets the caller
    render "from the group chat" vs "from a chat with Sprntly" instead of
    assuming group whenever a source conversation is set (the bug this
    field exists to fix). Attribution is fixed at "Sprntly" (v1) — the
    schema records `source_conversation_id`, not the seeding human, so
    per-teammate attribution is a flagged follow-on, not guessed here.
    Never calls an LLM."""
    for entry in list_entries(project_id):
        if entry.get("promoted_by") == "agent":
            return {
                "by": "Sprntly",
                "text": entry["body"],
                "source_kind": entry.get("source_conversation_kind"),
            }
    return None


def _flip_summary_stale(client, project_id: int) -> None:
    """Set `stale=true` on the project's summary row if one exists. A
    plain `UPDATE ... WHERE project_id = ?` is a no-op when no row is
    present, so this needs no existence check first."""
    client.table("project_memory_summary").update({"stale": True}).eq(
        "project_id", project_id
    ).execute()

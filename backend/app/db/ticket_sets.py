"""ticket_sets — tickets generated from a chat with NO PRD behind them.

One row per generated set. The tickets themselves are elements of the `stories`
jsonb array, exactly as `prd_tickets.stories` holds a PRD's — the same
`Story.to_dict()` payload, so every downstream reader works on both unmodified.

Lifecycle mirrors `prototypes` (db/artifacts.py): the row is created
`status='generating'` at KICK-OFF, before the multi-minute LLM call starts, and
the background job flips it to `ready` (or `failed`) when it lands. Creating the
row up front is what makes double-generation structurally impossible — the
client never posts stories back, so a double-poll or a StrictMode double-effect
has nothing to write with.

Reads filter by `company_id`, so every workspace in a company shares one ticket
library (the `reports` / `custom_skills` posture); `workspace_id` records which
workspace generated it and is nullable because generation runs in a background
job that may carry no workspace context.

TENANCY. `get_set` filters company_id IN THE QUERY rather than fetching by id
and comparing after — a foreign id returns None, which the route turns into 404.
There is no database safety net here: the backend holds the service-role key, so
RLS is bypassed and this filter IS the tenant boundary.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

# Columns a listing returns. `stories` is deliberately absent — a list of N sets
# must not carry N full ticket arrays (the same posture that keeps `html` out of
# the reports listing in db/artifacts.py). The count is derived where needed.
_LIST_COLUMNS = (
    "id, title, source_text, status, created_at, conversation_id, workspace_id"
)


@retry_on_disconnect
def create_set(
    company_id: str,
    *,
    workspace_id: str | None = None,
    conversation_id: int | None = None,
    source_text: str = "",
) -> int:
    """Create a `generating` set and return its id.

    Called BEFORE generation is scheduled so the id exists for the response —
    the client opens the panel against it immediately and polls, rather than
    waiting for a set id that only exists once a multi-minute call finishes.
    """
    resp = (
        require_client().table("ticket_sets")
        .insert(
            {
                "company_id": company_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "source_text": source_text,
                "status": "generating",
            }
        )
        .execute()
    )
    rows = resp.data or []
    if not rows:
        raise RuntimeError("ticket_sets insert returned no row")
    return int(rows[0]["id"])


@retry_on_disconnect
def finish_set(set_id: int, *, title: str, stories: list[dict]) -> None:
    """Flip a set to `ready` with its generated tickets.

    An EMPTY `stories` is allowed here, unlike the PRD path. `prd_tickets` is a
    cache keyed by a content hash: an empty row there wedges the Tickets tab on
    "0 tickets" forever because the next open compares hashes and serves the
    cached emptiness, so that path deliberately skips the write and lets the
    next open regenerate. A `ticket_sets` row is not a cache — it IS the
    artifact, one-shot, with nothing that re-kicks it — so "the run came back
    with nothing" has to be recorded rather than skipped, or the panel spins on
    a run that finished. The web renders ready-with-zero as its own empty state
    carrying a retry.
    """
    require_client().table("ticket_sets").update(
        {
            "title": title,
            "stories": stories,
            "status": "ready",
            "error": None,
            "updated_at": utc_now(),
        }
    ).eq("id", set_id).execute()


@retry_on_disconnect
def fail_set(set_id: int, error: str) -> None:
    """Record a failed run. The stored message is for operators — the web maps
    failures onto its own recovery copy and never renders this string."""
    require_client().table("ticket_sets").update(
        {"status": "failed", "error": (error or "")[:500], "updated_at": utc_now()}
    ).eq("id", set_id).execute()


@retry_on_disconnect
def get_set(company_id: str, set_id: int) -> dict[str, Any] | None:
    """One set, or None when it does not exist OR belongs to another company.

    The two cases are deliberately indistinguishable to the caller so the route
    can 404 both — a foreign tenant must not be able to tell "exists but not
    yours" from "doesn't exist".
    """
    rows = (
        require_client().table("ticket_sets")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", set_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def list_sets_for_company(company_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """This company's sets, newest first, without the `stories` payload."""
    return (
        require_client().table("ticket_sets")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_sets_for_conversation(
    company_id: str, conversation_id: int
) -> list[dict[str, Any]]:
    """The sets born in one chat, newest first (the thread-resume read).

    Company-scoped as well as conversation-scoped: conversation ids are
    sequential integers, so filtering on the id alone would hand a foreign
    tenant's set to anyone who guessed one.
    """
    return (
        require_client().table("ticket_sets")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
        .eq("conversation_id", conversation_id)
        .order("id", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def get_set_stories(company_id: str, set_id: int) -> list[dict]:
    """Just the `stories` array for one set ([] when absent/foreign)."""
    row = get_set(company_id, set_id)
    if row is None:
        return []
    return [s for s in (row.get("stories") or []) if isinstance(s, dict)]


@retry_on_disconnect
def find_set_story(
    company_id: str, story_ref: str
) -> tuple[dict | None, int | None]:
    """Locate one generated ticket by its stable id across ALL of a company's
    sets. Returns (story, set_id) or (None, None).

    Mirrors db/prd_tickets.find_ticket_story — tickets have no rows of their
    own, they are elements of each set's `stories` array, so a key lookup is a
    scan over the company's sets.
    """
    rows = (
        require_client().table("ticket_sets")
        .select("id, stories")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    for row in rows:
        for story in row.get("stories") or []:
            if isinstance(story, dict) and story.get("id") == story_ref:
                return story, row.get("id")
    return None, None

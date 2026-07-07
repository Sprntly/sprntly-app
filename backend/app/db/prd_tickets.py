"""Persisted user-story tickets for a PRD — backed by the `prd_tickets` table.

One row per PRD (unique prd_id), upserted on each (re)generation. Each row keeps
the generated stories as JSON plus a `content_hash` of the rendered PRD they were
produced from. The Tickets tab reads this first: when the hash matches the PRD's
current rendered content it serves the cached stories (no LLM call); only when the
PRD content has changed (or no row exists yet) does it regenerate.

Edits to a PRD update `payload_md`/`title` in place (no version bump) and applied
prd_patches fold into payload_md at read time, so hashing the *rendered* content
is the reliable "did the PRD change" signal — not generated_at or a row count.
"""
import hashlib
import logging
from datetime import datetime, timezone

from app.db.client import require_client, retry_on_disconnect
from app.db.prds import get_prd_rendered

logger = logging.getLogger(__name__)


def hash_prd_row(prd: dict) -> str:
    """SHA-256 over a (rendered) PRD row's content: title + Part A + Part B.

    The \\x1f field separator keeps the three parts from colliding across
    boundaries. Callers that already hold the rendered row (e.g. the generator)
    use this directly to avoid a second DB read.
    """
    basis = "\x1f".join([
        str(prd.get("title") or ""),
        str(prd.get("payload_md") or ""),
        str(prd.get("llm_part") or ""),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def prd_content_hash(prd_id: int) -> str | None:
    """The content hash of a PRD by id (renders + folds patches first).

    Returns None when the PRD does not exist. Used by the freshness check on read.
    """
    prd = get_prd_rendered(prd_id)
    if prd is None:
        return None
    return hash_prd_row(prd)


@retry_on_disconnect
def get_tickets(company_id: str, prd_id: int) -> dict | None:
    """The persisted ticket row for a PRD, or None. Tenant-scoped."""
    c = require_client()
    resp = (
        c.table("prd_tickets")
        .select("*")
        .eq("company_id", company_id)
        .eq("prd_id", prd_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def find_ticket_story(
    company_id: str, ticket_key: str
) -> tuple[dict | None, int | None]:
    """Locate one generated ticket (a `stories` element) by its stable id
    across ALL of a company's PRDs. Returns (story, prd_id) or (None, None).

    Tickets have no standalone rows — they're elements of each PRD's
    `prd_tickets.stories` array. `get_ticket` uses this to merge the generated
    base content (title, body, acceptance criteria, scope, …) with the
    per-ticket overrides so a developer sees the full ticket, not just edits."""
    c = require_client()
    rows = (
        c.table("prd_tickets")
        .select("prd_id, stories")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    for row in rows:
        for story in row.get("stories") or []:
            if isinstance(story, dict) and story.get("id") == ticket_key:
                return story, row.get("prd_id")
    return None, None


def save_tickets(
    company_id: str, prd_id: int, content_hash: str, stories: list[dict]
) -> None:
    """Upsert the generated stories for a PRD (one row per prd_id)."""
    c = require_client()
    c.table("prd_tickets").upsert(
        {
            "company_id": company_id,
            "prd_id": prd_id,
            "content_hash": content_hash,
            "stories": stories,
            "status": "ready",
            "error": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="prd_id",
    ).execute()

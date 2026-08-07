"""What a set of tickets BELONGS to — a PRD, or a standalone ticket set.

Tickets used to have exactly one owner, so the whole sync engine took a bare
`prd_id: int`. Standalone sets (tickets generated from a chat with no PRD)
introduced a second owner with identical downstream behaviour, and the choice
was either to fork the engine or to give it an owner it can carry around. This
is that owner.

A scope answers the only three questions the engine ever asks about ownership:

  * which stories are mine        -> `stories(company_id)`
  * what do my ticket keys start with -> `key_prefix`
  * which sync-destination row is mine -> `column` / `filter_sync`

Everything else in app/stories/sync.py — the `_Tracker` adapter, push, status
pull-back, comments, removal — already works off stories and ticket keys and
needed no change at all. The Sprntly-ticket -> tracker-issue mapping
(jira_issue_map / clickup_task_map / asana_task_map) was likewise already
PRD-free: it keys on the story's content-derived `stable_id`.

DELIBERATELY NOT a class hierarchy. Two kinds, a handful of branches, and the
kind string IS the ticket-key prefix — a frozen dataclass keeps the whole thing
readable in one screen and makes scopes hashable/comparable for free.

This module imports nothing from the app so both the db layer
(app/db/ticket_sync.py) and the engine (app/stories/sync.py) can depend on it
without a cycle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The two ticket owners. The value is literally the ticket-key prefix, so
#: `prd-12-abc` and `set-12-abc` name their owner in the key itself.
PRD = "prd"
SET = "set"
KINDS = (PRD, SET)

#: Any ticket key: `<kind>-<owner id>-<story ref>`. Anchored and requiring a
#: digit run for the id, so a bare legacy story id ("a1b2c3") does not match and
#: falls through to the legacy resolution path instead of being mis-parsed.
TICKET_KEY_RX = re.compile(r"^(prd|set)-(\d+)-(.+)$")


def title_slug(title: str | None) -> str:
    """The web's legacy ticket-key slug fallback (`ticketKeyFor` mirror):
    lowercase, non-alphanumeric runs -> '-', trimmed, first 60 chars.

    Only ever reached for stories generated before ids existed. Generated
    stories — on BOTH the PRD and the insight path — always carry `id` from
    `Story.to_dict()`, which stamps the content-derived `stable_id()`.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "ticket").lower()).strip("-")[:60]
    return slug or "ticket"


@dataclass(frozen=True)
class TicketScope:
    """The artifact a set of tickets belongs to: ('prd', 41) or ('set', 7)."""

    kind: str
    id: int

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown ticket scope kind {self.kind!r}")

    # ── Identity ──

    @property
    def is_prd(self) -> bool:
        return self.kind == PRD

    @property
    def is_set(self) -> bool:
        return self.kind == SET

    @property
    def key_prefix(self) -> str:
        """The `LIKE` prefix every one of this scope's ticket keys starts with.

        The trailing '-' matters: without it `prd-1-%` would also match
        `prd-12-...`, so one PRD's pass would pick up another's edits and
        comments.
        """
        return f"{self.kind}-{self.id}-"

    def ticket_key(self, story: dict[str, Any]) -> str:
        """The composed ticket key every ticket_edits / ticket_comments /
        ticket_attachments row for this story is stored under — the same format
        the web's `ticketKeyFor`/`ticketKeyForSet` and the MCP surface compose."""
        sid = story.get("id")
        if sid:
            return f"{self.key_prefix}{sid}"
        return f"{self.key_prefix}{title_slug(story.get('title'))}"

    # ── Persistence ──

    @property
    def column(self) -> str:
        """The `prd_ticket_sync` column that owns this scope's destination row."""
        return "prd_id" if self.is_prd else "ticket_set_id"

    def filter_sync(self, query):
        """Narrow a prd_ticket_sync query to this scope's row.

        Filters on BOTH owner columns — `.eq(mine, id).is_(theirs, None)` — not
        just on mine. The table's two unique indexes rely on NULL-distinctness,
        so `prd_id = 12` alone is already exact today; pinning the other column
        to NULL keeps it exact if a row ever acquires both, which is precisely
        the state the `prd_ticket_sync_one_owner` check exists to forbid. Cheap
        belt-and-braces on the query that decides which customer tracker gets
        written to.
        """
        other = "ticket_set_id" if self.is_prd else "prd_id"
        return query.eq(self.column, self.id).is_(other, "null")

    def sync_keys(self) -> dict[str, Any]:
        """The owner columns for an INSERT/UPSERT of this scope's sync row.

        Writes the other column as an explicit NULL rather than omitting it, so
        an upsert that lands on DO UPDATE cannot leave a stale owner behind.
        """
        return {
            "prd_id": self.id if self.is_prd else None,
            "ticket_set_id": self.id if self.is_set else None,
        }

    # ── Stories ──

    def stories(self, company_id: str) -> list[dict[str, Any]]:
        """This scope's stored base stories ([] when it has none yet).

        Imported lazily: the db modules pull in the Supabase client, and this
        module is imported by the db layer itself.
        """
        if self.is_prd:
            from app.db.prd_tickets import get_tickets

            row = get_tickets(company_id, self.id)
        else:
            from app.db.ticket_sets import get_set

            row = get_set(company_id, self.id)
        raw = (row.get("stories") if row else None) or []
        return [s for s in raw if isinstance(s, dict)]


def prd_scope(prd_id: int) -> TicketScope:
    return TicketScope(PRD, int(prd_id))


def set_scope(set_id: int) -> TicketScope:
    return TicketScope(SET, int(set_id))


def scope_from_key(ticket_key: str) -> TicketScope | None:
    """The scope a composed ticket key belongs to, or None when the key is
    malformed / a bare legacy story id.

    None is the FAIL-CLOSED answer every caller wants: an unparseable key means
    "I cannot tell which artifact this is", and every caller responds by doing
    nothing rather than guessing an owner and writing to the wrong tracker.
    """
    m = TICKET_KEY_RX.match(ticket_key or "")
    if not m:
        return None
    return TicketScope(m.group(1), int(m.group(2)))


def split_key(ticket_key: str) -> tuple[TicketScope | None, str]:
    """`(scope, story_ref)` for a composed key; `(None, key)` for a bare legacy
    story id, which still resolves its base story by scanning."""
    m = TICKET_KEY_RX.match(ticket_key or "")
    if not m:
        return None, ticket_key
    return TicketScope(m.group(1), int(m.group(2))), m.group(3)

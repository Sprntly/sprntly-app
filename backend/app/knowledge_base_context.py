"""What Sprntly has LEARNED for this company, as a context block.

The fifth member of the own-records family (`skills_context`, `team_context`,
`projects_context`, the backlog block), and it exists for the same reason each
of those does: the answer path could reason WITH this material and could not
answer a question ABOUT it.

Reported (2026-09-03): "the chat system does not understand KG". Asked what
its knowledge base is, or what is in it, the assistant had nothing — every
retrieval path pulls signals RELEVANT TO A QUESTION, and none of them can say
how many there are, where they came from, or when they last changed. The graph
answers "what do we know about exports"; nothing answered "what do you know at
all".

WHAT THE READER CALLS IT. Internally this is the knowledge graph. To the PM it
is their product memory — `prompts.VOICE_GUARD` bans our plumbing vocabulary
from user-facing output, and that ban stands here: a customer who types
"knowledge graph" gets an answer about their product memory, not a lesson in
our architecture. The block below says so in the same breath as the numbers, so
the model neither parrots the internal term nor pretends the question was
unintelligible.

COUNTS, NOT ROWS. Every number here is a COUNT query with `limit(1)` — no
signal content crosses the wire. A question about the size of something must
not cost what reading it would.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: The `kg_signal.source_type` vocabulary (the CHECK constraint in
#: `20260603120000_kg_foundation.sql`), paired with what a PM would call it.
#: Counted one at a time so the breakdown costs counts rather than rows.
_SOURCE_LABELS: tuple[tuple[str, str], ...] = (
    ("customer_voice", "customer conversations"),
    ("communication", "team conversations"),
    ("project_mgmt", "tickets and project tools"),
    ("analytics", "product analytics"),
    ("revenue", "revenue and CRM"),
    ("pm_manual", "things your team told us directly"),
    ("verbal_claim", "claims made in meetings"),
    ("agent_inferred", "conclusions Sprntly drew itself"),
    ("outcome_measured", "measured outcomes"),
)

#: Entity types worth naming. The `kg_entity.type` label is emergent, so this
#: is a display order for the ones that recur, not a closed set — anything else
#: is folded into the total rather than dropped.
_ENTITY_LABELS: tuple[tuple[str, str], ...] = (
    ("theme", "themes"),
    ("account", "accounts"),
    ("product_area", "product areas"),
    ("competitor", "competitors"),
    ("goal", "goals"),
    ("kpi", "metrics"),
    ("hypothesis", "hypotheses"),
    ("decision", "decisions"),
    ("outcome", "outcomes"),
)


def _count(table: str, company_id: str, **eq) -> int:
    """One exact COUNT, tenant-scoped. 0 on any failure — a number we could not
    read must never become a number we made up."""
    from app.db.client import require_client

    try:
        q = require_client().table(table).select("id", count="exact").eq(
            "enterprise_id", company_id
        )
        for col, val in eq.items():
            q = q.eq(col, val)
        return int(getattr(q.limit(1).execute(), "count", 0) or 0)
    except Exception:  # noqa: BLE001 — an unreadable count degrades, never lies
        logger.exception(
            "knowledge base: count failed table=%s company=%s", table, company_id
        )
        return 0


def _newest_signal_date(company_id: str) -> str:
    """The date of the most recent thing learned, or "". One row, one column."""
    from app.db.client import require_client

    try:
        rows = (
            require_client().table("kg_signal").select("transaction_at")
            .eq("enterprise_id", company_id)
            .order("transaction_at", desc=True).limit(1).execute().data or []
        )
        return str(rows[0].get("transaction_at") or "")[:10] if rows else ""
    except Exception:  # noqa: BLE001
        logger.exception("knowledge base: newest-signal read failed for %s", company_id)
        return ""


def knowledge_base_block(company_id: Optional[str]) -> str:
    """What this company's product memory HOLDS, as a context section.

    Empty string for no tenant. A company whose memory is genuinely empty still
    gets a block: "nothing yet, here is what fills it" is the answer that
    question deserves, and silence would leave the model to guess.
    """
    if not company_id:
        return ""

    signals = _count("kg_signal", company_id)
    entities = _count("kg_entity", company_id)

    by_source = [
        (label, _count("kg_signal", company_id, source_type=key))
        for key, label in _SOURCE_LABELS
    ]
    by_source = [(label, n) for label, n in by_source if n]

    by_entity = [
        (label, _count("kg_entity", company_id, type=key))
        for key, label in _ENTITY_LABELS
    ]
    by_entity = [(label, n) for label, n in by_entity if n]

    parts = [
        "=== WHAT SPRNTLY HAS LEARNED FOR THIS COMPANY ===",
        "This is Sprntly's product memory for this workspace: everything it has "
        "extracted from the sources this company connected — customer "
        "conversations, team chat, tickets, analytics, revenue and documents — "
        "kept as dated, sourced facts and the themes, accounts and decisions "
        "they connect to. It is what lets the assistant answer from what this "
        "company already knows rather than from the open internet.",
        "THE READER MAY CALL IT SOMETHING ELSE. Some people ask about their "
        "\"knowledge graph\", \"KG\", \"knowledge base\", \"memory\" or \"what "
        "you know about us\" — every one of those is this. Answer the question "
        "they asked, in their terms, but describe it as their product memory or "
        "what Sprntly has learned; never explain Sprntly's internals or use our "
        "architecture vocabulary back at them.",
        "The counts below were read just now and are exact. Never estimate a "
        "number that is not here, and never describe what is in the memory "
        "beyond what this block and your other context actually show.",
        "",
        f"Facts learned: {signals:,}",
        f"Things they are about (themes, accounts, product areas, …): {entities:,}",
    ]

    if by_source:
        parts.append("")
        parts.append("Where the facts came from:")
        parts += [f"- {label}: {n:,}" for label, n in by_source]
    if by_entity:
        parts.append("")
        parts.append("What they are about:")
        parts += [f"- {label}: {n:,}" for label, n in by_entity]

    newest = _newest_signal_date(company_id)
    if newest:
        parts.append("")
        parts.append(f"Most recently learned something: {newest}")

    if not signals and not entities:
        parts.append("")
        parts.append(
            "NOTHING HAS BEEN LEARNED YET. This workspace's memory is empty — "
            "no sources have been connected and synced, or none have produced "
            "anything yet. Say so plainly and point them at Sources to connect "
            "their tools; do not describe contents that are not there."
        )
    return "\n".join(parts) + "\n"

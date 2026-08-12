"""Standalone-artifact chat grounding — evidence and ticket-set tabs.

`prd_context.build_prd_context` gives a PRD-tab chat its document. These two
builders do the same job for the tabs that hold an artifact WITHOUT a PRD: an
evidence report opened from a Top Insights card, and a standalone ticket set
written from a chat request. Before this, a question typed next to either —
"what's the strongest signal here?", "which ticket covers the export flow?" —
was answered from the general corpus, sounding right while never reading the
document on screen.

Same contract as prd_context, deliberately:

- best-effort by construction: a missing row, a foreign tenant, or any read
  error collapses to '' and the ask degrades to the plain corpus+KG answer
- ownership is re-checked INSIDE the builder even though the route already
  gated the id — the builder is also reachable from the background worker,
  and a second cheap check is the difference between "the route forgot" and
  "a tenant's document leaked into another tenant's prompt"
- the block rides the same `prd_context` parameter through qa_agent →
  compose_ask_answer, so grounding behaviour (corpus+KG skipped, the
  PRD-addendum grounding rules, cacheability) is identical for every kind of
  open artifact
"""
from __future__ import annotations

import logging

# Same noise-strip and cap the PRD block uses — one sanitiser for every
# document that enters a prompt, not three drifting copies.
from app.prd_context import _cap, _strip_noise

logger = logging.getLogger(__name__)

# Generous enough for a full evidence page next to the corpus bundle; the
# ticket cap covers a large set (a set is titles + bodies + acceptance
# criteria, far denser than an HTML report).
_EVIDENCE_CAP = 30_000
_TICKETS_CAP = 16_000

_EVIDENCE_HEADER = (
    "=== CURRENT EVIDENCE CONTEXT ===\n"
    "The user has this evidence report open next to the chat. Questions like "
    "\"this evidence\", \"this report\", \"point 2 here\", or unqualified asks "
    "about findings/signals/sources refer to it. Document bodies below may be "
    "HTML — read the content, ignore the markup.\n"
)

_TICKET_SET_HEADER = (
    "=== CURRENT TICKET SET CONTEXT ===\n"
    "The user has this set of tickets open next to the chat. Questions like "
    "\"these tickets\", \"ticket 2\", or unqualified asks about scope/"
    "acceptance criteria refer to them.\n"
)


def build_evidence_context(
    enterprise_id: str | None, evidence_id: int | None
) -> str:
    """The "CURRENT EVIDENCE CONTEXT" block for an evidence-tab ask, or ''."""
    if not enterprise_id or not evidence_id:
        return ""
    try:
        from app.db import get_evidence
        from app.deps.ownership import require_owned_evidence

        # Ownership first — raises 404 for a foreign/missing id, which the
        # except below converts to the empty-block degrade.
        require_owned_evidence(int(evidence_id), enterprise_id)
        row = get_evidence(int(evidence_id))
        if not row:
            return ""
        body = _strip_noise(row.get("payload_md") or "")
        if not body:
            return ""
        lines = [
            _EVIDENCE_HEADER,
            f"## The evidence report (id {row['id']})",
            f"Title: {row.get('title') or '(untitled)'}",
            f"Status: {row.get('status') or 'unknown'}",
            "",
            _cap(body, _EVIDENCE_CAP),
        ]
        return "\n".join(lines).strip()
    except Exception:  # noqa: BLE001 — grounding is best-effort, never fatal
        logger.warning(
            "evidence context build failed for evidence %s", evidence_id, exc_info=True
        )
        return ""


def build_ticket_set_context(
    enterprise_id: str | None, ticket_set_id: int | None
) -> str:
    """The "CURRENT TICKET SET CONTEXT" block for a ticket-set-tab ask, or ''."""
    if not enterprise_id or not ticket_set_id:
        return ""
    try:
        from app.db.ticket_sets import get_set

        # get_set filters company_id IN THE QUERY (see its docstring) — the
        # lookup itself is the tenancy check, so a foreign id is a None here,
        # never a readable row.
        row = get_set(enterprise_id, int(ticket_set_id))
        if not row:
            return ""
        stories = row.get("stories") or []
        parts = [
            _TICKET_SET_HEADER,
            f"## The ticket set (id {row['id']})",
            f"Title: {row.get('title') or '(untitled)'}",
            f"Status: {row.get('status') or 'unknown'}",
            f"Tickets: {len(stories)}",
        ]
        for i, story in enumerate(stories, start=1):
            body = (story.get("body") or "").strip()
            criteria = story.get("acceptance_criteria") or []
            block = [f"\n### Ticket {i}: {story.get('title') or '(untitled)'}"]
            if story.get("priority"):
                block.append(f"Priority: {story['priority']}")
            if body:
                block.append(body)
            if criteria:
                block.append(
                    "Acceptance criteria:\n"
                    + "\n".join(f"- {c}" for c in criteria if c)
                )
            parts.append("\n".join(block))
        return _cap("\n".join(parts).strip(), _TICKETS_CAP)
    except Exception:  # noqa: BLE001 — grounding is best-effort, never fatal
        logger.warning(
            "ticket-set context build failed for set %s", ticket_set_id, exc_info=True
        )
        return ""

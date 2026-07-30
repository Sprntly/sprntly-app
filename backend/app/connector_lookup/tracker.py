"""The tracker fast-path — "show me my open tickets" against whichever tracker
the company actually uses.

Why this module exists: intent detection has been tracker-AGNOSTIC since the Jira
lookup shipped (skill_router._stateless_tracker_lookup fires on a read verb + a
PM noun, no "jira" needed — deliberately, so future trackers route the same
way), but EXECUTION was Jira-only. A ClickUp-only company asking "show my open
tickets" therefore got "connect Jira" — advice to install a tracker they don't
use, about tickets we could already read. That was the latent bug; this picker is
the fix.

Order of preference, and why:
1. the tracker the question NAMES, when it's connected — the user is explicit;
2. Jira — the richest surface (reads plus the propose→confirm write card);
3. ClickUp — read-only;
4. neither → one honest message naming both, plus what IS connected.

Asana is deliberately absent: there is no Asana read client (asana_oauth.py says
so), so it falls to registry.DEFERRED copy rather than pretending.
"""
from __future__ import annotations

import logging
import re

from app.connector_lookup import answer as connector_answer

logger = logging.getLogger(__name__)

#: Trackers with a live-read adapter, in preference order.
TRACKERS: tuple[str, ...] = ("jira", "clickup")

_NAMES = {
    "jira": re.compile(r"\b(jira|atlassian)\b", re.I),
    "clickup": re.compile(r"\bclick\s?up\b", re.I),
}


def _has_connection(enterprise_id: str, provider: str) -> bool:
    """True when the company holds a connection row for `provider`.

    Row presence, not a live session: opening a session costs an API round trip,
    and a present-but-stale credential must still reach that provider's own
    reconnect copy rather than being reported as "not connected".
    """
    from app import db

    try:
        return db.get_connection(enterprise_id, provider) is not None
    except Exception:  # noqa: BLE001 — degrade to "not connected"
        logger.warning(
            "tracker-lookup: connection check failed for %s/%s",
            enterprise_id, provider, exc_info=True,
        )
        return False


def pick(enterprise_id: str, question: str) -> str | None:
    """Which tracker should serve this question, or None when none is connected."""
    connected = [t for t in TRACKERS if _has_connection(enterprise_id, t)]
    if not connected:
        return None
    for tracker in connected:
        if _NAMES[tracker].search(question or ""):
            return tracker
    return connected[0]


def answer(
    *, enterprise_id: str, question: str, history: list[dict] | None = None
) -> dict:
    """Answer a tracker read against the company's connected tracker."""
    tracker = pick(enterprise_id, question)
    if tracker == "jira":
        # Unchanged path: the Jira lookup owns its own session handling, connect/
        # reconnect copy, propose→confirm card and decision-log row.
        from app import jira_lookup

        return jira_lookup.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )
    if tracker == "clickup":
        from app.connector_lookup.clickup import PROVIDER

        return connector_answer.answer(
            enterprise_id=enterprise_id,
            question=question,
            history=history,
            providers=[PROVIDER],
            skill_action="ClickUp lookup",
        )
    return connector_answer.plain_payload(
        "I can read your tickets live — status, assignees, comments — but no "
        "tracker is connected yet. Connect **Jira** or **ClickUp** in Settings → "
        "Connectors and ask me again."
        + connector_answer.connected_sources_sentence(enterprise_id),
        skill_action="Tracker lookup",
    )

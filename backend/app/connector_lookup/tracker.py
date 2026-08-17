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
_DISPLAY = {"jira": "Jira", "clickup": "ClickUp"}
#: How far back a tracker thread stays "the tracker we're talking about" — same
#: 8-turn window skill_router uses for tracker-thread stickiness.
_THREAD_WINDOW = 8


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


def named_trackers(text: str) -> list[str]:
    """Trackers explicitly named in a message, in TRACKERS order."""
    return [t for t in TRACKERS if _NAMES[t].search(text or "")]


def any_connected(enterprise_id: str) -> bool:
    """True when at least one tracker (Jira or ClickUp) is connected for this
    company. Mirrors the `connected` comprehension inside `pick`, exposed so a
    capability-gate caller (qa_agent's tracker-lookup interceptor) can test
    connectivity without pulling in `pick`'s full resolution logic."""
    return any(_has_connection(enterprise_id, t) for t in TRACKERS)


def pick(
    enterprise_id: str, question: str, history: list[dict] | None = None
) -> str | None:
    """Which tracker should serve this question, or None when none is connected.

    Order of evidence:
      1. a tracker the question NAMES and the company has connected;
      2. a tracker an earlier turn of THIS thread named and the company has
         connected — without which a follow-up carrying no name of its own
         ("yes", "more details on that") silently jumps trackers on a
         dual-tracker tenant: the thread was about ClickUp, the follow-up names
         nothing, and step 3 would hand it to Jira and answer about the wrong
         workspace;
      3. the default preference order (Jira first: reads plus propose→confirm).
    """
    connected = [t for t in TRACKERS if _has_connection(enterprise_id, t)]
    if not connected:
        return None
    for tracker in named_trackers(question):
        if tracker in connected:
            return tracker
    # Walk the thread newest-first so the most recent tracker mentioned wins.
    for turn in reversed((history or [])[-_THREAD_WINDOW:]):
        for tracker in named_trackers(turn.get("content") or ""):
            if tracker in connected:
                return tracker
    return connected[0]


def _adapter_for(name: str):
    """The LookupProvider for a tracker key, imported lazily — mirroring the
    connected branches, whose adapters pull in their OAuth clients."""
    if name == "clickup":
        from app.connector_lookup.clickup import PROVIDER

        return PROVIDER
    if name == "jira":
        from app.connector_lookup import jira as jira_adapter

        return jira_adapter.PROVIDER
    return None


def _kg_fallback(
    *,
    enterprise_id: str,
    question: str,
    history: list[dict] | None,
    trackers: list[str],
) -> dict:
    """Answer a tracker question from the knowledge graph when no live tracker
    session is available.

    The 20-minute connector sync keeps every tracker's tasks fresh in the graph,
    so an absent or failed live session is no longer a dead end: `connector_answer`
    degrades to a KG-only tool loop (its nothing-connected branch, with
    `include_knowledge_graph=True`) instead of returning the connect copy. The KG
    reader is tenant-scoped and tracker-agnostic — which tracker's adapter carries
    us into that branch does not change what the graph returns — so the adapters
    are passed only so the loop's shared plumbing (and its honest degradation)
    still applies.
    """
    providers = [p for p in (_adapter_for(t) for t in trackers) if p is not None]
    return connector_answer.answer(
        enterprise_id=enterprise_id,
        question=question,
        history=history,
        providers=providers,
        include_knowledge_graph=True,
        skill_action="Tracker lookup",
    )


def answer(
    *, enterprise_id: str, question: str, history: list[dict] | None = None
) -> dict:
    """Answer a tracker read against the company's connected tracker."""
    # The user named a tracker and it is NOT connected. We used to dead-end here
    # with "connect X" — but the same tracker's tasks are synced into the
    # knowledge graph, so read those instead of false-denying. This does NOT
    # answer out of a different tracker's LIVE data (the old worry): the KG reader
    # is tenant-scoped and reads Sprntly's own extracted signals, not the other
    # tracker's session.
    named = named_trackers(question)
    if named and not any(_has_connection(enterprise_id, t) for t in named):
        return _kg_fallback(
            enterprise_id=enterprise_id, question=question,
            history=history, trackers=named,
        )
    tracker = pick(enterprise_id, question, history)
    if tracker == "jira":
        # Unchanged path: the Jira lookup owns its own session handling, connect/
        # reconnect copy, propose→confirm card and decision-log row. Its KG
        # degradation is wired inside jira_lookup.answer (it passes the flag).
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
            include_knowledge_graph=True,
            skill_action="ClickUp lookup",
        )
    # No tracker is connected. The interceptor only reaches this when a tracker
    # was named (its claim gate), and a named-but-unconnected tracker is already
    # handled above — so in practice this is the belt-and-braces none-named,
    # none-connected case. Read the graph rather than dead-ending on the old
    # "connect Jira or ClickUp" copy; both adapters are offered so the KG-only
    # branch is reached through the shared loop.
    return _kg_fallback(
        enterprise_id=enterprise_id, question=question,
        history=history, trackers=list(TRACKERS),
    )

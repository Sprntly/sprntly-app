"""The durable "a format switch is running" marker on a ticket row.

Deliberately a leaf module with no heavy imports: the db writers
(`db/prd_tickets.py`, `db/ticket_sets.py`), the route that schedules a switch
and the two read routes that report it all need this contract, and
`app/stories/relayout.py` — which owns the actual re-lay — pulls in the LLM
gateway, so importing it from the db layer would build a cycle for the sake of
two dict helpers.

The marker lives in the `relayout` jsonb column added by
20260817120000_ticket_relayout_marker.sql; that migration's header explains why
it is a column of its own rather than a `status` value.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

#: How long a marker is believed before it is treated as debris. The background
#: task lives in the API process, so a deploy or a crash mid-switch leaves a
#: marker nothing will ever clear; past this the readers report "not running"
#: and the client stops waiting. Generous against the work itself — a re-lay is
#: one batched fill call over one ticket set — because calling a live switch
#: dead is the worse error: the user would be told nothing is happening while
#: the tickets change under them a moment later.
RELAYOUT_STALE_AFTER_S = 10 * 60


def relaying_marker(artifact_template_id: Optional[str]) -> dict[str, Any]:
    """The value written when a switch is scheduled. `artifact_template_id` is
    the TARGET, and None is a real target here (Sprntly's built-in layout)."""
    return {
        "status": "running",
        "template_id": artifact_template_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def relayout_in_flight(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The live marker on `row`, or None when no switch is running.

    None covers all four "nothing to report" cases equally — no marker, a
    marker from before this column existed, a malformed one, and a stale one —
    because every one of them means the same thing to a caller: do not tell the
    user something is happening.
    """
    marker = (row or {}).get("relayout")
    if not isinstance(marker, dict) or marker.get("status") != "running":
        return None

    started = marker.get("started_at")
    if isinstance(started, str) and started:
        try:
            # Postgres hands back "+00:00"; a "Z" suffix is accepted too so a
            # value written by anything other than `relaying_marker` still
            # parses rather than reading as debris.
            at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - at).total_seconds() > RELAYOUT_STALE_AFTER_S:
            return None
    else:
        # No timestamp = no way to age it out. Refusing it is the safe read:
        # an un-ageable marker is the one shape that could wedge a client
        # forever.
        return None

    return marker

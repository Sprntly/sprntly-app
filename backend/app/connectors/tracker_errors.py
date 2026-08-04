"""Errors shared by the task-management connectors' write paths.

One class lives here rather than three per-connector ones because, unlike the
`*AuthExpiredError` family (whose whole point is a provider-specific reconnect
prompt), the caller's response to a refused delete is IDENTICAL across
ClickUp/Jira/Asana: fall back to closing the item instead of destroying it.
"""
from __future__ import annotations


class TrackerDeleteForbiddenError(RuntimeError):
    """The tracker understood the delete and REFUSED it on permissions.

    Distinct from the `*AuthExpiredError` family, which every connector raises
    on a 401/403 from its normal read/write calls. On a delete a 403 usually
    means the connected account simply may not destroy this kind of object —
    most commonly Jira's "Delete Issues" project permission, which is not
    granted to ordinary members in a default Jira scheme and is NOT implied by
    the `write:jira-work` OAuth scope. Re-prompting for a reconnect there would
    be wrong (the token is fine and reconnecting changes nothing), so the two
    cases have to be told apart.

    Callers treat this as "removal is unavailable, close the item instead" —
    never as a hard failure.
    """

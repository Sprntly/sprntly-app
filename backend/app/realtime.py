"""Best-effort publish primitive for Supabase Realtime Broadcast (AD-P21).

Under Option C (AD-P21/AD-P23), the backend — never a DB trigger — publishes
a backend-shaped event to a member-authorized `project:{id}` (or per-user
`project:{id}:user:{uid}`) channel immediately after a gated write. This is
the ONE place that talks to the Realtime Broadcast REST endpoint; every
call-site (`routes/projects.py`, `project_delegation.py`) reuses it rather
than hand-rolling a second HTTP call.

Transport = Supabase Realtime Broadcast REST, service-role
(`{SUPABASE_URL}/realtime/v1/api/broadcast`), over the same `httpx` dependency
already used by `welcome_email.py`/`team_email.py` (no new dependency).

Best-effort throughout (AD-P7): unconfigured env, a network failure, a
timeout, or a non-2xx response are all swallowed here — a broadcast is a
liveness nicety, never a correctness requirement, because every poll surface
this ticket feeds (`GET .../group/turns`, `GET .../individual/turns`) already
reconciles on its own `since`-cursor read (AD-P22). A dropped broadcast is
invisible to the caller and closes on the client's next poll / re-open.
"""
from __future__ import annotations

import logging

import httpx

from app import config as config_mod

logger = logging.getLogger(__name__)

_BROADCAST_TIMEOUT_SECONDS = 2.0


def publish_broadcast(topic: str, event: str, payload: dict) -> None:
    """Best-effort publish of a backend-shaped event to a Supabase Realtime
    Broadcast channel (AD-P21). NEVER raises (AD-P7): a failed publish is
    logged and swallowed — the client's next poll reconcile / re-open (AD-P22)
    closes the gap. No-op (returns) when Realtime env is unconfigured.

    `payload` MUST already be the client-facing read-DTO (e.g. the
    `list_group_turns`/`list_individual_turns` shape) — never a raw DB row
    (AD-P21 no-schema-coupling). Shaping is the caller's responsibility;
    this helper does not know or care what a "turn" or a "brief" looks like."""
    url = config_mod.settings.supabase_url
    key = config_mod.settings.supabase_service_role_key
    if not url or not key:
        return
    try:
        resp = httpx.post(
            f"{url}/realtime/v1/api/broadcast",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [
                    {"topic": topic, "event": event, "payload": payload, "private": True}
                ]
            },
            timeout=_BROADCAST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7: never block the write
        logger.warning(
            "realtime_publish_failed topic=%s event=%s error_class=%s",
            topic, event, type(exc).__name__,
        )

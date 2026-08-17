"""Real-Realtime round-trip proof for the mention/add liveness signal — the
one piece the fast lane (`test_mention_liveness.py`, which spies
`publish_broadcast`) cannot prove: that a `member.added` published by
`_publish_member_added` through the ACTUAL Supabase Realtime Broadcast REST
endpoint is genuinely RECEIVED on the target's per-user channel over a live
websocket subscription ([[feedback_stubbed-e2e-masks-loop-behaviour]]).

No `ANTHROPIC_API_KEY` needed — this path makes no LLM call at all (AC-9);
gating is purely on a real local Supabase Realtime.

Run it with:

    RUN_MENTION_LIVENESS_LIVE=1 \\
        SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \\
        pytest tests/test_mention_liveness_live.py -m integration
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_MENTION_LIVENESS_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase Realtime — set RUN_MENTION_LIVENESS_LIVE=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig"
        ),
    ),
]


def _ws_url(supabase_url: str) -> str:
    """The Realtime websocket endpoint derived from the REST SUPABASE_URL."""
    base = supabase_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/realtime/v1"


def test_member_added_received_on_target_channel_live():
    from app import project_delegation
    from app.config import settings
    from realtime import AsyncRealtimeClient

    url = settings.supabase_url
    key = settings.supabase_service_role_key
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live realtime round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r})"
    )
    assert key, "SUPABASE_SERVICE_ROLE_KEY is not set"

    project_id = 10_000_000 + int(uuid.uuid4().int % 1_000_000)
    target_uid = "live-target-" + uuid.uuid4().hex[:8]
    topic = f"project:{project_id}:user:{target_uid}"

    received: list[dict] = []

    async def _run() -> None:
        client = AsyncRealtimeClient(_ws_url(url), key)
        await client.connect()
        channel = client.channel(topic, params={"config": {"private": True}})
        channel.on_broadcast("member.added", lambda payload: received.append(payload))

        subscribed = asyncio.Event()

        def _on_subscribe(state, err):  # noqa: ANN001
            if str(state).upper().endswith("SUBSCRIBED"):
                subscribed.set()

        await channel.subscribe(_on_subscribe)
        try:
            await asyncio.wait_for(subscribed.wait(), timeout=10)
            # Publish through the REAL best-effort helper (REST Broadcast).
            project_delegation._publish_member_added(project_id, target_uid, "Live Project")
            # Give the broadcast time to fan back over the socket.
            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.2)
        finally:
            await client.close()

    asyncio.run(_run())

    assert received, "no member.added event received on the target's per-user channel"
    # supabase-js/realtime wraps the payload as {event, payload, type}; the
    # backend DTO is under `payload`.
    dto = received[0].get("payload", received[0])
    assert dto.get("kind") == "added"
    assert dto.get("project_id") == project_id
    assert dto.get("project_name") == "Live Project"
    # No content leak — the private nudge carries the whitelist only (AD-TNM2).
    assert set(dto.keys()) == {"project_id", "project_name", "actor_name", "kind"}

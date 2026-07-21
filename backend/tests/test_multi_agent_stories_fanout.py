"""The multi-agent orchestrator's user-story leg honors the ticket fan-out
settings.

The "generate from insight" flow used to call `generate_user_stories` with the
default strategy="single" — one ~3-minute 32k-token call (observed 171s on prod
vs ~110s fanned out) — because it never passed a strategy. It must follow the
same TICKET_GEN_* settings as the /v1/stories route.
"""
from __future__ import annotations

import asyncio

import app.config as config
import app.stories.generate as gen_mod
from app import multi_agent_orchestrator as orch


def _capture_generate(monkeypatch) -> dict:
    captured: dict = {}

    def _fake(cid, **kw):
        captured["company_id"] = cid
        captured.update(kw)
        return []

    monkeypatch.setattr(gen_mod, "generate_user_stories", _fake)
    return captured


def test_stories_leg_honors_fanout_settings(isolated_settings, monkeypatch):
    captured = _capture_generate(monkeypatch)
    monkeypatch.setattr(config.settings, "ticket_gen_fanout", True, raising=False)

    asyncio.run(orch._generate_stories_safe("ent-A", {"id": 5, "status": "ready"}))

    assert captured["company_id"] == "ent-A"
    assert captured["prd_id"] == 5
    assert captured["strategy"] == "fanout"
    assert captured["batch_size"] == config.settings.ticket_gen_batch_size
    assert captured["max_parallel"] == config.settings.ticket_gen_max_parallel


def test_stories_leg_respects_fanout_off(isolated_settings, monkeypatch):
    captured = _capture_generate(monkeypatch)
    monkeypatch.setattr(config.settings, "ticket_gen_fanout", False, raising=False)

    asyncio.run(orch._generate_stories_safe("ent-A", {"id": 5, "status": "ready"}))

    assert captured["strategy"] == "single"

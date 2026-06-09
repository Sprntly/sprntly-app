"""Tests for brief → Slack delivery (side effect; must never break generation)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

_BRIEF = {
    "summary_headline": "Offline sync is the week's dominant risk",
    "week_label": "Week of June 8, 2026",
    "insights": [
        {"tag": "something_broken", "title": "Offline sync failures 2.5x MoM"},
        {"tag": "something_new", "title": "SSO blocks $218k of pipeline"},
    ],
}


def _row(channel="C0123", status="active", token={"access_token": "xoxb-1"},
         user_id="user-1"):
    return {"user_id": user_id, "status": status,
            "config": {"channel_id": channel},
            "token_json_encrypted": "enc"}, token


def test_delivers_with_blocks(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    row, token = _row()
    sent = {}
    # Delivery is per-user now: list_slack_connections returns this user's row.
    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: [row])
    monkeypatch.setattr(delivery, "decrypt_token_json", lambda s: json.dumps(token))
    monkeypatch.setattr(delivery.slack_oauth, "post_message",
                        lambda tok, *, channel, text, blocks: sent.update(
                            tok=tok, channel=channel, text=text, blocks=blocks) or {"ok": True})

    out = delivery.deliver_brief_to_slack("ent-A", _BRIEF)
    assert out["delivered"] is True
    assert out["recipients"] == [
        {"user_id": "user-1", "delivered": True, "channel": "C0123"}
    ]
    assert sent["tok"] == "xoxb-1"
    assert "Offline sync is the week's dominant risk" in sent["text"]
    header = sent["blocks"][0]["text"]["text"]
    assert "Week of June 8, 2026" in header
    body = sent["blocks"][2]["text"]["text"]
    assert "FIX" in body and "Offline sync failures" in body
    assert "BUILD" in body
    assert sent["blocks"][-1]["elements"][0]["url"].endswith("/brief")


@pytest.mark.parametrize("rows,reason", [
    ([], "slack_not_connected"),
    ([{"user_id": "u", "status": "error", "config": {"channel_id": "C1"}, "token_json_encrypted": "e"}], "slack_not_connected"),
    ([{"user_id": "u", "status": "active", "config": {}, "token_json_encrypted": "e"}], "no_channel_configured"),
])
def test_clean_noops(isolated_settings, monkeypatch, rows, reason):
    from app.synthesis import delivery

    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: rows)
    out = delivery.deliver_brief_to_slack("ent-A", _BRIEF)
    assert out["delivered"] is False
    assert out["reason"] == reason


def test_post_failure_never_raises(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    row, token = _row()
    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: [row])
    monkeypatch.setattr(delivery, "decrypt_token_json", lambda s: json.dumps(token))
    monkeypatch.setattr(delivery.slack_oauth, "post_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("slack 500")))
    out = delivery.deliver_brief_to_slack("ent-A", _BRIEF)
    assert out["delivered"] is False
    assert "slack 500" in out["recipients"][0]["reason"]


def test_synthesis_attaches_delivery_status(isolated_settings, monkeypatch):
    from app.graph import GraphFacade
    from app.graph.gateway import LLMResult
    from app.synthesis import agent as synth
    from tests.test_synthesis_agent import _seed_theme_with_signals, _RANKED

    facade = GraphFacade()
    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 1)])
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: None)
    monkeypatch.setattr(synth, "llm_call", lambda **kw: LLMResult(
        output={**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": theme.id}]},
        model="m", prompt_version="t", input_tokens=1, output_tokens=1,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
        cost_usd=0, latency_ms=1, stop_reason="end_turn"))
    monkeypatch.setattr(synth, "deliver_brief_to_slack",
                        lambda eid, brief: {"delivered": False, "reason": "slack_not_connected"})
    brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    assert brief["_slack_delivery"]["reason"] == "slack_not_connected"

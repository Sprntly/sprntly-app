"""Tests for the "your PRD is ready" Slack ping.

When a PRD finishes generating (app.prd_runner.generate_prd_and_warm), the
requester gets a Slack message on THEIR configured target (DM or channel) with
a "View PRD here" button. Unlike the brief delivery, this goes to the ONE user
who generated the PRD (the copy is "your PRD"), not a company-wide fan-out.
Best-effort: a Slack hiccup never affects the finished PRD.
"""
from __future__ import annotations

import json

from app.synthesis.delivery import (
    PRD_READY_CTA_LABEL,
    deliver_prd_ready_to_slack,
    prd_deep_link,
    prd_ready_slack_blocks,
)


# ── Slack blocks ──────────────────────────────────────────────────────────────


def test_prd_ready_blocks_name_the_prd_with_a_view_button(isolated_settings):
    text, blocks = prd_ready_slack_blocks("Checkout redesign", 42)
    # The exact requested copy — PRD name inlined, one CTA line.
    assert text == (
        "Hey, your PRD for Checkout redesign has been generated successfully, "
        "view it by clicking the button below."
    )
    assert [b["type"] for b in blocks] == ["section", "actions"]
    assert blocks[0]["text"]["text"] == text
    button = blocks[1]["elements"][0]
    assert button["type"] == "button"
    assert button["text"]["text"] == PRD_READY_CTA_LABEL
    # The button carries the prd id so it lands on the app.
    assert button["url"] == prd_deep_link(42)
    assert button["url"].endswith("/brief?prd=42")


def test_prd_ready_blocks_fall_back_when_title_missing(isolated_settings):
    text, _ = prd_ready_slack_blocks(None, 7)
    assert "your latest insight" in text
    text2, _ = prd_ready_slack_blocks("   ", 7)
    assert "your latest insight" in text2


# ── delivery: single recipient (the requester), their configured target ───────


def test_prd_ready_delivers_to_the_requester_only(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    row = {"user_id": "user-1", "status": "active",
           "config": {"channel_id": "C0123"}, "token_json_encrypted": "enc"}
    seen: dict = {}

    def _fake_get(company_id, user_id):
        seen["lookup"] = (company_id, user_id)
        return row

    sent: dict = {}
    monkeypatch.setattr(delivery.db, "get_slack_connection", _fake_get)
    monkeypatch.setattr(delivery, "decrypt_token_json",
                        lambda s: json.dumps({"access_token": "xoxb-1"}))
    monkeypatch.setattr(delivery.slack_oauth, "post_message",
                        lambda tok, *, channel, text, blocks, **k: sent.update(
                            channel=channel, text=text, blocks=blocks)
                        or {"ok": True, "channel": channel})

    out = deliver_prd_ready_to_slack("ent-A", "user-1", 99, "Onboarding v2")

    # Looked up THIS user's connection (not a company-wide fan-out).
    assert seen["lookup"] == ("ent-A", "user-1")
    assert out["delivered"] is True
    assert len(out["recipients"]) == 1
    assert "Onboarding v2" in sent["text"]
    assert sent["blocks"][1]["elements"][0]["text"]["text"] == PRD_READY_CTA_LABEL


def test_prd_ready_clean_noop_when_not_connected(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    monkeypatch.setattr(delivery.db, "get_slack_connection",
                        lambda company_id, user_id: None)
    out = deliver_prd_ready_to_slack("ent-A", "user-1", 5, "Anything")
    assert out == {"delivered": False, "reason": "slack_not_connected",
                   "recipients": []}


def test_prd_ready_never_raises(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(delivery.db, "get_slack_connection", _boom)
    out = deliver_prd_ready_to_slack("ent-A", "user-1", 5, "Anything")
    assert out["delivered"] is False
    assert out["reason"].startswith("error:")

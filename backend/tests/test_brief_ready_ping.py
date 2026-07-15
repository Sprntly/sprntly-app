"""Tests for the short "Hey, your brief is generated." ping.

A USER-TRIGGERED regenerate (/v1/brief/regenerate, /regenerate-all, /generate)
must NOT push the full weekly brief message — that stays reserved for the
scheduled delivery time. It sends this one-line ping instead, with the same
deep-link CTA button the weekly message carries, over the same per-user Slack
and company email channels (same config gates).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from app.synthesis.delivery import (
    READY_PING_CTA_LABEL,
    READY_PING_TEXT,
    ready_ping_slack_blocks,
)


# ── Slack blocks ──────────────────────────────────────────────────────────────


def test_ping_blocks_are_short_with_deep_link_button(isolated_settings):
    text, blocks = ready_ping_slack_blocks()
    assert text == "Hey, your brief is generated."
    # One line of copy + one actions block — NOT the full brief message.
    assert [b["type"] for b in blocks] == ["section", "actions"]
    assert blocks[0]["text"]["text"] == READY_PING_TEXT
    button = blocks[1]["elements"][0]
    assert button["type"] == "button"
    assert button["text"]["text"] == READY_PING_CTA_LABEL
    assert button["url"].endswith("/brief")  # same deep link as the weekly message


def test_ping_slack_fans_out_per_user_without_llm_draft(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    row = {"user_id": "user-1", "status": "active",
           "config": {"channel_id": "C0123"}, "token_json_encrypted": "enc"}
    sent = {}
    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: [row])
    monkeypatch.setattr(delivery, "decrypt_token_json",
                        lambda s: json.dumps({"access_token": "xoxb-1"}))
    monkeypatch.setattr(delivery.slack_oauth, "post_message",
                        lambda tok, *, channel, text, blocks, **k: sent.update(
                            tok=tok, channel=channel, text=text, blocks=blocks)
                        or {"ok": True})
    # The static ping must never draft via the LLM skill.
    monkeypatch.setattr(delivery, "generate_nudge",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("ping must not call the LLM")))

    out = delivery.deliver_ready_ping_to_slack("ent-A")
    assert out["delivered"] is True
    assert sent["text"] == READY_PING_TEXT
    assert sent["blocks"][0]["text"]["text"] == READY_PING_TEXT


def test_ping_slack_clean_noop_when_not_connected(isolated_settings, monkeypatch):
    from app.synthesis import delivery

    monkeypatch.setattr(delivery.db, "list_slack_connections", lambda cid: [])
    out = delivery.deliver_ready_ping_to_slack("ent-A")
    assert out == {"delivered": False, "reason": "slack_not_connected",
                   "recipients": []}


# ── email ─────────────────────────────────────────────────────────────────────


def test_ping_email_is_short_with_cta(isolated_settings):
    from app.synthesis.email_delivery import render_brief_ready_ping_email

    subject, html_body, text_body = render_brief_ready_ping_email()
    assert subject == "Your brief is ready"
    assert READY_PING_TEXT in html_body
    assert READY_PING_TEXT in text_body
    assert "/brief" in html_body  # the CTA button target
    # Short ping — none of the weekly email's card structure.
    assert "PM COWORKER" not in html_body
    assert "Weekly Brief" not in subject


def test_ping_email_honors_email_enabled_gate(isolated_settings, monkeypatch):
    from app.synthesis import email_delivery

    monkeypatch.setattr(email_delivery.settings, "resend_api_key", "re_123",
                        raising=False)
    monkeypatch.setattr(email_delivery.companies_db, "get_notification_settings",
                        lambda cid: {"email_enabled": False})
    out = email_delivery.deliver_brief_ping_to_email("co-1")
    assert out["delivered"] is False
    assert out["reason"] == "email_disabled"


def test_ping_email_sends_to_recipients(isolated_settings, monkeypatch):
    from app.synthesis import email_delivery

    sent: list[dict] = []
    monkeypatch.setattr(email_delivery.settings, "resend_api_key", "re_123",
                        raising=False)
    monkeypatch.setattr(email_delivery.companies_db, "get_notification_settings",
                        lambda cid: {"email_enabled": True,
                                     "email_recipients": ["pm@acme.com"]})
    monkeypatch.setattr(
        email_delivery, "_send_via_resend",
        lambda key, *, to, subject, html_body, text_body: sent.append(
            {"to": to, "subject": subject, "text": text_body}))

    out = email_delivery.deliver_brief_ping_to_email("co-1")
    assert out["delivered"] is True
    assert sent[0]["to"] == "pm@acme.com"
    assert READY_PING_TEXT in sent[0]["text"]


# ── route wiring: regenerate sends the ping, not the full message ─────────────


def test_notify_brief_ready_pings_on_fresh_brief(monkeypatch):
    from app.routes import brief as brief_routes

    pinged: list[str] = []
    monkeypatch.setattr(brief_routes, "resolve_company", lambda d: ("co-1", d))
    with patch("app.synthesis.delivery.deliver_brief_ready_ping",
               side_effect=lambda cid: pinged.append(cid) or {}):
        brief_routes._notify_brief_ready("acme", {"id": 1})
    assert pinged == ["co-1"]


def test_notify_brief_ready_skips_cache_and_missing(monkeypatch):
    from app.routes import brief as brief_routes

    with patch("app.synthesis.delivery.deliver_brief_ready_ping") as ping:
        brief_routes._notify_brief_ready("acme", {"id": 1, "_from_cache": True})
        brief_routes._notify_brief_ready("acme", None)
    ping.assert_not_called()


def test_regenerate_bg_suppresses_full_delivery_and_pings(isolated_settings, monkeypatch):
    """The /regenerate background body generates with deliver=False (no full
    weekly message) and sends the short ping for the fresh brief."""
    from app.routes import brief as brief_routes

    calls: list[tuple] = []
    pings: list[tuple] = []
    with patch.object(brief_routes, "generate_brief_for",
                      side_effect=lambda slug, **kw: calls.append((slug, kw))
                      or {"id": 9}), \
         patch.object(brief_routes, "_notify_brief_ready",
                      side_effect=lambda d, b: pings.append((d, b))), \
         patch.object(brief_routes, "warm_synthesis_drilldowns"):
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))

    assert calls == [("acme", {"deliver": False})]
    assert pings == [("acme", {"id": 9})]


def test_regenerate_bg_no_ping_when_generation_fails(isolated_settings):
    from app.routes import brief as brief_routes

    with patch.object(brief_routes, "generate_brief_for",
                      side_effect=RuntimeError("boom")), \
         patch.object(brief_routes, "_notify_brief_ready") as ping:
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))  # no raise
    ping.assert_not_called()


# ── synthesis: deliver=False suppresses the inline full-brief push ────────────


def test_run_synthesis_deliver_false_skips_push(isolated_settings, monkeypatch):
    """generate_brief_for(deliver=False) reaches run_synthesis, which must not
    call deliver_brief — the flag exists so the scheduler/regenerate paths own
    delivery."""
    from app import synthesis_brief as sb

    seen_kwargs: dict = {}

    def _fake_run_synthesis(facade, company_id, *, dataset_slug, deliver=True, **kw):
        seen_kwargs["deliver"] = deliver
        return {"id": 1}

    monkeypatch.setattr(sb, "run_synthesis", _fake_run_synthesis)
    monkeypatch.setattr(sb, "resolve_company", lambda x: ("co-1", "acme"))
    monkeypatch.setattr(sb, "get_current_brief", lambda slug: None)
    monkeypatch.setattr(sb, "seed_incremental", lambda *a, **k: None)

    out = sb.generate_brief_for("acme", deliver=False)
    assert out == {"id": 1}
    assert seen_kwargs["deliver"] is False

"""Tests for prototype-ready → Slack/email delivery (side effect of a background
generation completing; must NEVER break the generation). Slack post + Resend
transport are ALWAYS mocked — nothing real is sent.

Mirrors the weekly-brief delivery tests: the ready notification reuses the
brief's per-user Slack fan-out (`_deliver_to_one`) and the brief's email
primitives (`_send_via_resend`, `_resolve_recipients`), each gated by the SAME
comms settings.
"""
from __future__ import annotations

import json


# ── pure rendering ────────────────────────────────────────────────────────────
def test_prototype_url_points_at_in_app_canvas(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.settings, "frontend_url", "https://app.sprntly.ai")
    assert pd._prototype_url(42) == "https://app.sprntly.ai/prototype?prd=42"


def test_slack_blocks_have_title_and_view_cta(isolated_settings):
    from app.synthesis.prototype_delivery import _prototype_slack_blocks

    text, blocks = _prototype_slack_blocks(
        "Offline sync retry", "https://app.sprntly.ai/prototype?prd=7"
    )
    assert "ready" in text.lower()
    # PRD title surfaces in the body section.
    assert "Offline sync retry" in blocks[1]["text"]["text"]
    # One CTA button linking to the canvas.
    btn = blocks[-1]["elements"][0]
    assert btn["text"]["text"] == "View prototype"
    assert btn["url"].endswith("/prototype?prd=7")


def test_email_render_escapes_title_and_links(isolated_settings):
    from app.synthesis.prototype_delivery import _render_prototype_email

    subject, html_body, text_body = _render_prototype_email(
        "<b>Risky</b> title", "https://app.sprntly.ai/prototype?prd=9"
    )
    assert "ready" in subject.lower()
    # Title is HTML-escaped in the HTML body, raw in the text body.
    assert "<b>Risky</b>" not in html_body
    assert "&lt;b&gt;Risky&lt;/b&gt;" in html_body
    assert "<b>Risky</b> title" in text_body
    assert "/prototype?prd=9" in html_body
    assert "/prototype?prd=9" in text_body


# ── Slack delivery (per-user fan-out) ─────────────────────────────────────────
def _slack_row(channel="C0123", status="active", user_id="user-1"):
    return {"user_id": user_id, "status": status,
            "config": {"channel_id": channel},
            "token_json_encrypted": "enc"}


def test_slack_delivers_per_user(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd
    from app.synthesis import delivery as brief_delivery

    row = _slack_row()
    sent = {}
    monkeypatch.setattr(pd.db, "list_slack_connections", lambda cid: [row])
    # _deliver_to_one lives in brief delivery; patch its token decrypt + post.
    monkeypatch.setattr(brief_delivery, "decrypt_token_json",
                        lambda s: json.dumps({"access_token": "xoxb-1"}))
    monkeypatch.setattr(brief_delivery.slack_oauth, "post_to_target",
                        lambda tok, *, config, authed_user_id, text, blocks:
                        sent.update(tok=tok, text=text, blocks=blocks) or
                        {"channel": config.get("channel_id")})

    out = pd._deliver_prototype_to_slack(
        "co-1", "My PRD", "https://app.sprntly.ai/prototype?prd=3")
    assert out["delivered"] is True
    assert out["recipients"] == [
        {"user_id": "user-1", "delivered": True, "channel": "C0123"}]
    assert sent["tok"] == "xoxb-1"
    assert "My PRD" in sent["blocks"][1]["text"]["text"]


def test_slack_noop_when_not_connected(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.db, "list_slack_connections", lambda cid: [])
    out = pd._deliver_prototype_to_slack("co-1", "P", "u")
    assert out["delivered"] is False
    assert out["reason"] == "slack_not_connected"


def test_slack_never_raises(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.db, "list_slack_connections",
                        lambda cid: (_ for _ in ()).throw(RuntimeError("db down")))
    out = pd._deliver_prototype_to_slack("co-1", "P", "u")
    assert out["delivered"] is False
    assert "db down" in out["reason"]


# ── Email delivery (email_enabled gate mirrors the brief) ─────────────────────
def _enable_email(monkeypatch, recipients=None):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.settings, "resend_api_key", "re_test_key")
    notif = {"email_enabled": True}
    if recipients is not None:
        notif["email_recipients"] = recipients
    monkeypatch.setattr(pd.companies_db, "get_notification_settings",
                        lambda cid: notif)
    return pd


def test_email_delivers_to_recipients(isolated_settings, monkeypatch):
    pd = _enable_email(monkeypatch, recipients=["a@co.com", "b@co.com"])
    sent = []
    monkeypatch.setattr(pd, "_send_via_resend",
                        lambda key, *, to, subject, html_body, text_body:
                        sent.append((to, subject)))
    out = pd._deliver_prototype_to_email(
        "co-1", "My PRD", "https://app.sprntly.ai/prototype?prd=3")
    assert out["delivered"] is True
    assert [s[0] for s in sent] == ["a@co.com", "b@co.com"]
    assert all("ready" in s[1].lower() for s in sent)


def test_email_noop_when_disabled(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(pd.companies_db, "get_notification_settings",
                        lambda cid: {"email_enabled": False})
    called = {"sent": False}
    monkeypatch.setattr(pd, "_send_via_resend",
                        lambda *a, **k: called.update(sent=True))
    out = pd._deliver_prototype_to_email("co-1", "P", "u")
    assert out["delivered"] is False
    assert out["reason"] == "email_disabled"
    assert called["sent"] is False


def test_email_noop_when_resend_unconfigured(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd.settings, "resend_api_key", "")
    out = pd._deliver_prototype_to_email("co-1", "P", "u")
    assert out["delivered"] is False
    assert out["reason"] == "resend_not_configured"


def test_email_per_recipient_isolation(isolated_settings, monkeypatch):
    pd = _enable_email(monkeypatch, recipients=["good@co.com", "bad@co.com"])

    def fake_send(key, *, to, **kw):
        if to == "bad@co.com":
            raise RuntimeError("resend 422")

    monkeypatch.setattr(pd, "_send_via_resend", fake_send)
    out = pd._deliver_prototype_to_email("co-1", "P", "u")
    assert out["delivered"] is True  # good one still went
    by_email = {r["email"]: r for r in out["recipients"]}
    assert by_email["good@co.com"]["delivered"] is True
    assert by_email["bad@co.com"]["delivered"] is False


# ── top-level dispatch ────────────────────────────────────────────────────────
def test_deliver_prototype_ready_aggregates_both(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    monkeypatch.setattr(pd, "_deliver_prototype_to_slack",
                        lambda *a, **k: {"delivered": True, "recipients": []})
    monkeypatch.setattr(pd, "_deliver_prototype_to_email",
                        lambda *a, **k: {"delivered": False, "reason": "email_disabled",
                                         "recipients": []})
    out = pd.deliver_prototype_ready("co-1", prd_id=5, prd_title="T")
    assert out["slack"]["delivered"] is True
    assert out["email"]["delivered"] is False


def test_deliver_prototype_ready_never_raises(isolated_settings, monkeypatch):
    from app.synthesis import prototype_delivery as pd

    # Even a total slack blow-up is swallowed inside the channel helper; the
    # dispatch returns a structured result rather than raising.
    monkeypatch.setattr(pd.db, "list_slack_connections",
                        lambda cid: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(pd.settings, "resend_api_key", "")
    out = pd.deliver_prototype_ready("co-1", prd_id=1, prd_title="T")
    assert out["slack"]["delivered"] is False
    assert out["email"]["delivered"] is False

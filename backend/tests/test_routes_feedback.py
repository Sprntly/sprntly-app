"""Tests for POST /v1/feedback (in-app feedback / feature-request, June 20 #13 + #A).

The endpoint:
  - stores the submission in the `feedback` table, and
  - emails it to the team via Resend (best-effort; never blocks the submission).

Validation:
  - 422 on empty/whitespace message.
  - 422 on an unknown type.
  - default type 'other' when omitted.

Email recipient resolution: FEEDBACK_ALERT_EMAIL wins, else SIGNIN_MONITOR_ALERT_EMAIL,
else no email (stored only). We patch `_send_via_resend` so no network is hit and we
can assert the call (and that storage succeeds even when the email send raises).
"""
from __future__ import annotations

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client


def _list_feedback(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("feedback")
        .select("id, company_id, user_id, type, message")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


def test_feedback_stores_and_emails(isolated_settings, monkeypatch):
    sent: list[dict] = []

    def _fake_send(api_key, *, to, subject, html_body, text_body):
        sent.append({"to": to, "subject": subject, "text": text_body})

    monkeypatch.setattr(
        "app.synthesis.email_delivery._send_via_resend", _fake_send
    )

    ctx = company_client(monkeypatch)
    # Configure a recipient + key so the email path is exercised. company_client
    # reloads app.config inside setup_supabase_auth, so the settings overrides
    # have to happen AFTER that reload (otherwise they land on a stale object).
    import app.config as config_mod
    monkeypatch.setattr(config_mod.settings, "resend_api_key", "re_test_key", raising=False)
    monkeypatch.setattr(config_mod.settings, "feedback_alert_email", "team@sprntly.ai", raising=False)

    r = ctx.client.post(
        "/v1/feedback",
        json={"message": "Please add a Notion connector", "type": "connector_request"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["type"] == "connector_request"
    assert body["email_sent"] is True
    assert body["id"]

    # Stored.
    rows = _list_feedback(ctx.company_id)
    assert len(rows) == 1
    assert rows[0]["message"] == "Please add a Notion connector"
    assert rows[0]["type"] == "connector_request"
    assert rows[0]["user_id"] == ctx.user_id

    # Emailed to the configured team address.
    assert len(sent) == 1
    assert sent[0]["to"] == "team@sprntly.ai"
    assert "connector" in sent[0]["text"].lower()


def test_feedback_defaults_type_other(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/feedback", json={"message": "Love the product"})
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "other"
    rows = _list_feedback(ctx.company_id)
    assert len(rows) == 1
    assert rows[0]["type"] == "other"


def test_feedback_stores_even_when_email_fails(isolated_settings, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr("app.synthesis.email_delivery._send_via_resend", _boom)

    ctx = company_client(monkeypatch)
    # Configure AFTER company_client (it reloads app.config); this forces the
    # send path so we can assert the failure is swallowed and storage survives.
    import app.config as config_mod
    monkeypatch.setattr(config_mod.settings, "resend_api_key", "re_test_key", raising=False)
    monkeypatch.setattr(config_mod.settings, "feedback_alert_email", "team@sprntly.ai", raising=False)

    r = ctx.client.post("/v1/feedback", json={"message": "Bug: chart is blank", "type": "bug"})
    assert r.status_code == 201, r.text
    assert r.json()["email_sent"] is False
    # Submission still stored despite the email failure.
    assert len(_list_feedback(ctx.company_id)) == 1


def test_feedback_rejects_empty_message(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/feedback", json={"message": "   ", "type": "bug"})
    assert r.status_code == 422
    assert _list_feedback(ctx.company_id) == []


def test_feedback_rejects_unknown_type(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/feedback", json={"message": "hello", "type": "nope"})
    assert r.status_code == 422

"""Tests for the onboarding welcome email (instant "your workspace is ready").

Covers:
  - render_welcome_email: subject/body fill {first_name}/{workspace_name},
    the guide link is a LINK (not an attachment), placeholder gaps degrade,
    user data is HTML-escaped, branded shell present.
  - send_welcome_email best-effort contract (no key → False; Resend ok/err/raise).
  - POST /v1/onboarding/complete: sends once, de-dupes on the second call,
    records skipped when the send can't go out, and skips cleanly with no email.

Uses the in-memory fake Supabase from conftest (isolated_settings) and the
company-auth TestClient helper (tests/_company_helpers.py).
"""
from __future__ import annotations

import importlib
import sys
import time
import uuid

import jwt
from fastapi.testclient import TestClient

from app.db.client import require_client
from tests._company_helpers import (
    SUPABASE_JWT_SECRET,
    seed_company,
    setup_supabase_auth,
)


# ── render_welcome_email ────────────────────────────────────────────────


def _welcome(isolated_settings):
    we = importlib.import_module("app.welcome_email")
    importlib.reload(we)
    return we


def test_render_fills_first_name_and_workspace(isolated_settings):
    we = _welcome(isolated_settings)
    subject, text, html = we.render_welcome_email(
        first_name="Fortune", workspace_name="Acme Product"
    )
    assert subject == "Welcome to Sprntly, Fortune — your workspace is ready"
    assert "Hi Fortune," in text
    assert "Your workspace, Acme Product, is ready." in text
    assert "Fortune" in html
    assert "Acme Product" in html


def test_render_guide_is_a_link_not_an_attachment(isolated_settings):
    we = _welcome(isolated_settings)
    _subject, text, html = we.render_welcome_email(
        first_name="Fortune", workspace_name="Acme"
    )
    # The founder note's "I've attached a one-page guide" becomes a LINK.
    assert "attached" not in text.lower()
    assert "one-page guide" in text
    # Default guide URL falls back to <frontend_url>/docs/sprntly-how-to-guide
    # (the public docs page) and renders as <a>.
    assert "/docs/sprntly-how-to-guide" in text
    assert 'href="' in html and "/docs/sprntly-how-to-guide" in html


def test_render_guide_url_override(isolated_settings, monkeypatch):
    monkeypatch.setenv("WELCOME_GUIDE_URL", "https://docs.sprntly.ai/start")
    importlib.reload(sys.modules["app.config"])
    we = _welcome(isolated_settings)
    _s, text, html = we.render_welcome_email(
        first_name="Fortune", workspace_name="Acme"
    )
    assert "https://docs.sprntly.ai/start" in text
    assert "https://docs.sprntly.ai/start" in html


def test_render_degrades_on_empty_inputs(isolated_settings):
    we = _welcome(isolated_settings)
    subject, text, _html = we.render_welcome_email(
        first_name="", workspace_name=""
    )
    assert subject == "Welcome to Sprntly, there — your workspace is ready"
    assert "Hi there," in text
    assert "your workspace" in text  # never "Your workspace, ,"


def test_render_escapes_user_data(isolated_settings):
    we = _welcome(isolated_settings)
    _s, _t, html = we.render_welcome_email(
        first_name="A<b>", workspace_name="Co <script>"
    )
    assert "A&lt;b&gt;" in html
    assert "A<b>" not in html
    assert "Co &lt;script&gt;" in html


def test_render_branded_shell(isolated_settings):
    we = _welcome(isolated_settings)
    _s, _t, html = we.render_welcome_email(
        first_name="Fortune", workspace_name="Acme"
    )
    assert "Sprntly<span" in html          # wordmark
    assert "#1a8a52" in html               # brand green
    assert "Open Sprntly" in html          # CTA
    assert "Co-founder, Sprntly" in html   # founder signature
    assert we.SUPPORT_PHONE in html        # support line


# ── send_welcome_email best-effort contract ─────────────────────────────


def test_send_no_key_returns_false(isolated_settings, monkeypatch):
    we = _welcome(isolated_settings)
    # Force the key empty on the settings object (a real backend/.env may
    # supply one — patch deterministically rather than via the environment).
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "resend_api_key", "", raising=False)
    assert we.send_welcome_email(
        to_email="a@b.com", first_name="F", workspace_name="W"
    ) is False


def test_send_success_posts_both_parts(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    importlib.reload(sys.modules["app.config"])
    we = _welcome(isolated_settings)

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return _Resp()

    monkeypatch.setattr(we.httpx, "post", _fake_post)
    ok = we.send_welcome_email(
        to_email="a@b.com", first_name="Fortune", workspace_name="Acme"
    )
    assert ok is True
    assert captured["url"] == we.RESEND_API_URL
    assert captured["json"]["to"] == ["a@b.com"]
    assert "Fortune" in captured["json"]["subject"]
    assert "Acme" in captured["json"]["text"]
    assert "Open Sprntly" in captured["json"]["html"]
    assert "Bearer re_test" in captured["headers"]["Authorization"]
    # Default From is the generic onboarding sender on the verified domain.
    assert "onboarding@mail.sprntly.ai" in captured["json"]["from"]


def test_send_non_2xx_returns_false(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    importlib.reload(sys.modules["app.config"])
    we = _welcome(isolated_settings)

    class _Resp:
        status_code = 422
        text = "bad"

    monkeypatch.setattr(we.httpx, "post", lambda url, **kw: _Resp())
    assert we.send_welcome_email(
        to_email="a@b.com", first_name="F", workspace_name="W"
    ) is False


def test_send_swallows_exceptions(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    importlib.reload(sys.modules["app.config"])
    we = _welcome(isolated_settings)

    def _boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(we.httpx, "post", _boom)
    assert we.send_welcome_email(
        to_email="a@b.com", first_name="F", workspace_name="W"
    ) is False


# ── POST /v1/onboarding/complete ────────────────────────────────────────


def _bearer_with_email(user_id: str, email: str) -> dict[str, str]:
    """A Supabase-style bearer JWT that also carries the `email` claim, so
    require_company populates CompanyContext.user_email."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "email": email, "exp": now + 3600},
        SUPABASE_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _complete_client(monkeypatch, *, email="fortune@acme.com", first_name="Fortune"):
    """A TestClient authed as a fresh user who owns one seeded company, with a
    profile row (email + first_name) so the endpoint can personalise."""
    setup_supabase_auth(monkeypatch)
    importlib.reload(sys.modules["app.main"])
    import app.main as main_mod

    user_id = "u-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug="acme")
    require_client().table("profiles").insert(
        {"id": user_id, "email": email, "first_name": first_name}
    ).execute()
    headers = _bearer_with_email(user_id, email)
    client = TestClient(main_mod.app, headers=headers)
    return client, company_id, user_id


def test_complete_sends_and_records_once(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    client, company_id, user_id = _complete_client(monkeypatch)

    sent: list[dict] = []
    import app.welcome_email as we

    monkeypatch.setattr(
        we, "send_welcome_email",
        lambda **kw: (sent.append(kw) or True),
    )

    resp = client.post("/v1/onboarding/complete", json={})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "sent": True}
    assert len(sent) == 1
    assert sent[0]["to_email"] == "fortune@acme.com"
    assert sent[0]["first_name"] == "Fortune"
    assert sent[0]["workspace_name"] == "Acme"  # companies.display_name

    rows = require_client().table("drip_email_sends").select(
        "step_key, status"
    ).eq("company_id", company_id).execute().data
    assert len(rows) == 1
    assert rows[0]["step_key"] == "welcome"
    assert rows[0]["status"] == "sent"


def test_complete_is_idempotent(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    client, company_id, _user_id = _complete_client(monkeypatch)

    calls: list[dict] = []
    import app.welcome_email as we
    monkeypatch.setattr(
        we, "send_welcome_email", lambda **kw: (calls.append(kw) or True)
    )

    first = client.post("/v1/onboarding/complete", json={})
    second = client.post("/v1/onboarding/complete", json={})
    assert first.json() == {"ok": True, "sent": True}
    assert second.json() == {"ok": True, "sent": False, "reason": "already_sent"}
    assert len(calls) == 1  # never double-sends

    rows = require_client().table("drip_email_sends").select("id").eq(
        "company_id", company_id
    ).execute().data
    assert len(rows) == 1


def test_complete_records_skipped_when_send_fails(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    client, company_id, _user_id = _complete_client(monkeypatch)

    import app.welcome_email as we
    monkeypatch.setattr(we, "send_welcome_email", lambda **kw: False)

    resp = client.post("/v1/onboarding/complete", json={})
    assert resp.json() == {"ok": True, "sent": False}

    rows = require_client().table("drip_email_sends").select(
        "step_key, status"
    ).eq("company_id", company_id).execute().data
    assert len(rows) == 1
    assert rows[0]["step_key"] == "welcome"
    assert rows[0]["status"] == "skipped"

    # A later completion does not retry the skipped send.
    again = client.post("/v1/onboarding/complete", json={})
    assert again.json()["reason"] == "already_sent"


def test_complete_disabled_is_noop(isolated_settings, monkeypatch):
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", "false")
    client, company_id, _user_id = _complete_client(monkeypatch)

    called = []
    import app.welcome_email as we
    monkeypatch.setattr(we, "send_welcome_email", lambda **kw: called.append(kw))

    resp = client.post("/v1/onboarding/complete", json={})
    assert resp.json() == {"ok": True, "sent": False, "reason": "disabled"}
    assert called == []
    rows = require_client().table("drip_email_sends").select("id").execute().data
    assert rows == []

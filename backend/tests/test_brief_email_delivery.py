"""Tests for brief → email delivery (Resend; side effect, must never break
generation). The Resend transport is ALWAYS mocked — no real email is sent.
"""
from __future__ import annotations

import pytest

_BRIEF = {
    "summary_headline": "Offline sync is the week's dominant risk",
    "week_label": "Week of June 8, 2026",
    "insights": [
        {"tag": "something_broken", "title": "Offline sync failures 2.5x MoM",
         "subtitle": "Mobile clients on flaky networks"},
        {"tag": "something_new", "title": "SSO blocks $218k of pipeline"},
        {"tag": "research", "title": "Usage cohort needs investigation"},
    ],
}


# ── pure rendering ────────────────────────────────────────────────────────────
def test_render_mirrors_slack_content(isolated_settings):
    from app.synthesis.email_delivery import render_brief_email

    subject, html_body, text_body = render_brief_email(_BRIEF)

    # Headline + week label present in both subject and bodies.
    assert "Offline sync is the week's dominant risk" in subject
    assert "Week of June 8, 2026" in subject
    # HTML escapes the apostrophe (correct), so match a clean substring.
    assert "Offline sync is the week" in html_body
    assert "dominant risk" in html_body
    assert "Offline sync is the week's dominant risk" in text_body

    # Tags mapped to FIX / BUILD / RESEARCH (mirrors Slack semantics).
    assert "FIX" in text_body and "FIX" in html_body
    assert "BUILD" in text_body
    assert "RESEARCH" in text_body

    # Insight titles + subtitle render.
    assert "Offline sync failures 2.5x MoM" in html_body
    assert "Mobile clients on flaky networks" in text_body

    # Link to the app brief.
    assert "/brief" in html_body
    assert "/brief" in text_body


def test_render_escapes_html(isolated_settings):
    from app.synthesis.email_delivery import render_brief_email

    brief = {"summary_headline": "<script>alert(1)</script>",
             "week_label": "W1", "insights": []}
    _subject, html_body, _text = render_brief_email(brief)
    assert "<script>alert(1)</script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_render_handles_no_insights(isolated_settings):
    from app.synthesis.email_delivery import render_brief_email

    subject, html_body, text_body = render_brief_email(
        {"summary_headline": "Quiet week", "week_label": "W2", "insights": []})
    assert "Quiet week" in subject
    assert "No insights this week." in text_body
    assert "No insights this week." in html_body


# ── delivery: enabled + per-recipient routing ────────────────────────────────
def _enable(monkeypatch, recipients=None, members=None):
    from app.synthesis import email_delivery as ed

    monkeypatch.setattr(ed.settings, "resend_api_key", "re_test_key")
    notif = {"email_enabled": True}
    if recipients is not None:
        notif["email_recipients"] = recipients
    monkeypatch.setattr(ed.companies_db, "get_notification_settings",
                        lambda cid: notif)
    monkeypatch.setattr(ed.team_db, "list_company_members",
                        lambda cid: members or [])
    return ed


def test_delivers_to_each_recipient(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, recipients=["a@co.com", "b@co.com"])
    sent = []
    monkeypatch.setattr(ed, "_send_via_resend",
                        lambda key, *, to, subject, html_body, text_body:
                        sent.append((to, subject)))

    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out["delivered"] is True
    assert [r["email"] for r in out["recipients"]] == ["a@co.com", "b@co.com"]
    assert all(r["delivered"] for r in out["recipients"])
    assert [s[0] for s in sent] == ["a@co.com", "b@co.com"]


def test_defaults_to_company_members(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, members=[
        {"email": "owner@co.com"}, {"email": "pm@co.com"}, {"email": None}])
    sent = []
    monkeypatch.setattr(ed, "_send_via_resend",
                        lambda key, *, to, **kw: sent.append(to))

    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out["delivered"] is True
    # None email dropped; only valid members sent.
    assert sent == ["owner@co.com", "pm@co.com"]


def test_dedupes_recipients(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, recipients=["a@co.com", "A@co.com", "a@co.com"])
    sent = []
    monkeypatch.setattr(ed, "_send_via_resend",
                        lambda key, *, to, **kw: sent.append(to))
    ed.deliver_brief_to_email("co-1", _BRIEF)
    assert sent == ["a@co.com"]


# ── failure isolation ────────────────────────────────────────────────────────
def test_per_recipient_failure_isolation(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, recipients=["good@co.com", "bad@co.com", "good2@co.com"])

    def fake_send(key, *, to, **kw):
        if to == "bad@co.com":
            raise RuntimeError("resend 422")

    monkeypatch.setattr(ed, "_send_via_resend", fake_send)
    out = ed.deliver_brief_to_email("co-1", _BRIEF)

    assert out["delivered"] is True  # the others still went
    by_email = {r["email"]: r for r in out["recipients"]}
    assert by_email["good@co.com"]["delivered"] is True
    assert by_email["good2@co.com"]["delivered"] is True
    assert by_email["bad@co.com"]["delivered"] is False
    assert "resend 422" in by_email["bad@co.com"]["reason"]


def test_all_fail_never_raises(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, recipients=["a@co.com"])
    monkeypatch.setattr(ed, "_send_via_resend",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out["delivered"] is False
    assert "boom" in out["reason"]


# ── clean no-ops ──────────────────────────────────────────────────────────────
def test_noop_when_resend_unconfigured(isolated_settings, monkeypatch):
    from app.synthesis import email_delivery as ed

    monkeypatch.setattr(ed.settings, "resend_api_key", "")
    called = {"sent": False}
    monkeypatch.setattr(ed, "_send_via_resend",
                        lambda *a, **k: called.update(sent=True))
    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out == {"delivered": False, "reason": "resend_not_configured",
                   "recipients": []}
    assert called["sent"] is False


def test_noop_when_email_disabled(isolated_settings, monkeypatch):
    from app.synthesis import email_delivery as ed

    monkeypatch.setattr(ed.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(ed.companies_db, "get_notification_settings",
                        lambda cid: {"email_enabled": False})
    sent = []
    monkeypatch.setattr(ed, "_send_via_resend", lambda *a, **k: sent.append(1))
    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out["delivered"] is False
    assert out["reason"] == "email_disabled"
    assert sent == []


def test_noop_when_no_recipients(isolated_settings, monkeypatch):
    ed = _enable(monkeypatch, members=[])  # enabled but nobody to send to
    sent = []
    monkeypatch.setattr(ed, "_send_via_resend", lambda *a, **k: sent.append(1))
    out = ed.deliver_brief_to_email("co-1", _BRIEF)
    assert out["delivered"] is False
    assert out["reason"] == "no_recipients"
    assert sent == []


# ── wiring into synthesis (attaches status, never raises) ─────────────────────
def test_synthesis_attaches_email_delivery_status(isolated_settings, monkeypatch):
    from app.graph import GraphFacade
    from app.graph.gateway import LLMResult
    from app.synthesis import agent as synth
    from tests.test_synthesis_agent import _seed_theme_with_signals, _RANKED

    facade = GraphFacade()
    theme = _seed_theme_with_signals(facade, "ent-A", "SSO", [
        ("revenue", "deal_blocker", {}, 1),
        ("customer_voice", "feature_request", {}, 1)])  # multi-source: clears gate
    monkeypatch.setattr(synth, "load_kpi_tree", lambda eid: None)
    monkeypatch.setattr(synth, "llm_call", lambda **kw: LLMResult(
        output={**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": theme.id}]},
        model="m", prompt_version="t", input_tokens=1, output_tokens=1,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
        cost_usd=0, latency_ms=1, stop_reason="end_turn"))
    monkeypatch.setattr(synth, "deliver_brief_to_slack",
                        lambda eid, brief: {"delivered": False, "reason": "slack_not_connected"})
    monkeypatch.setattr(synth, "deliver_brief_to_email",
                        lambda eid, brief: {"delivered": False, "reason": "email_disabled",
                                            "recipients": []})
    brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")
    assert brief["_email_delivery"]["reason"] == "email_disabled"

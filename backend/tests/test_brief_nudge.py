"""Tests for brief-nudge wiring (app/brief_nudge.py).

Covers: input grounding (real figures only), Slack rendering (one CTA,
figure-led headline, Day-3 pause note), skill binding, the flag gate, the
cadence/open-state/idempotency gating, recipient resolution, and the
scheduler cycle's day-offset logic. All external calls (gateway LLM, Slack
post, DB) are mocked — no network, no Supabase.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import brief_nudge
from app.graph.gateway import LLMResult


# ── fixtures / helpers ────────────────────────────────────────────────────
def _brief() -> dict:
    return {
        "id": 42,
        "dataset": "acme",
        "generated_at": "2026-06-23T09:00:00+00:00",
        "week_label": "Week of June 23, 2026",
        "summary_headline": "~$60M in upside is on the table",
        "greeting": "Morning — ~$60M across five plays is on the table this week.",
        "insights": [
            {"tag": "something_better", "title": "Expansion", "subtitle": "42 accounts outgrew their plan",
             "metrics": [{"label": "unclaimed", "value": "$8.4M"}], "impact_math": ["$8.4M unclaimed"]},
            {"tag": "something_broken", "title": "Checkout", "subtitle": "1 in 6 iOS payments failing",
             "metrics": [{"label": "at risk", "value": "$2.2M/yr"}], "impact_math": ["$2.2M/yr"]},
            {"tag": "something_new", "title": "Competitive", "subtitle": "a rival's search cost 3 deals",
             "metrics": [{"label": "renewals", "value": "$1.6M"}], "impact_math": ["$1.6M renewals"]},
        ],
    }


def _nudge_payload(pause: bool = False) -> dict:
    slack = {
        "headline": "$60M in upside is on the table",
        "intro": "The top plays this week:",
        "items": [{"label": "Expansion", "detail": "42 accounts ready", "impact": "$8.4M"}],
        "cta_label": "Open this week's brief",
        "cta_url": "http://localhost:3000/brief",
    }
    email = {
        "subject": "Your weekly brief: ~$60M on the table",
        "title": "~$60M is within reach",
        "intro": "Five plays ranked in your brief.",
        "items": [{"label": "Expansion", "detail": "42 accounts", "impact": "$8.4M"}],
        "cta_label": "Open the brief",
        "cta_url": "http://localhost:3000/brief",
    }
    if pause:
        slack["pause_note"] = "Last reminder — I'll pause after today."
    return {"slack": slack, "email": email}


def _conn(user_id="u1", channel="C123", status="active") -> dict:
    return {
        "user_id": user_id,
        "status": status,
        "config": {"channel_id": channel} if channel else {},
        "token_json_encrypted": "enc",
    }


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    """Enable the feature + stub the DB token decode by default. Per-test
    overrides for db/llm/slack are set inside each test."""
    monkeypatch.setattr(brief_nudge.settings, "brief_nudge_enabled", True, raising=False)
    monkeypatch.setattr(brief_nudge, "decrypt_token_json",
                        lambda _enc: json.dumps({"access_token": "xoxb-test"}))
    # default DB gating: nothing sent yet, nothing opened
    monkeypatch.setattr(brief_nudge.nudge_db, "has_nudge_been_sent",
                        lambda *a, **k: False)
    monkeypatch.setattr(brief_nudge.nudge_db, "is_brief_unopened",
                        lambda *a, **k: True)
    monkeypatch.setattr(brief_nudge.nudge_db, "record_nudge_sent",
                        lambda *a, **k: None)


def _stub_llm(monkeypatch, payload):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return LLMResult(output=payload, model="claude-sonnet-4-6",
                         prompt_version="brief-nudge-v1+brief-nudge@abc",
                         input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                         cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
                         stop_reason="tool_use")
    monkeypatch.setattr(brief_nudge, "llm_call", fake)
    return calls


def _stub_slack(monkeypatch):
    sent = []
    monkeypatch.setattr(brief_nudge.slack_oauth, "post_message",
                        lambda token, **kw: sent.append(kw) or {"ok": True})
    return sent


# ── input grounding ───────────────────────────────────────────────────────
def test_nudge_input_carries_real_figures_and_day_guide():
    txt = brief_nudge._nudge_input(_brief(), day=2, deep_link="http://x/brief")
    assert "DAY: 2" in txt and "cost of waiting" in txt.lower()
    assert "$8.4M" in txt and "~$60M" in txt          # greeting + item figures present
    assert "http://x/brief" in txt


# ── Slack rendering ───────────────────────────────────────────────────────
def test_slack_blocks_have_exactly_one_cta_button():
    headline, blocks = brief_nudge.nudge_slack_blocks(_nudge_payload(), "http://x/brief")
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    buttons = actions[0]["elements"]
    assert len(buttons) == 1
    assert buttons[0]["url"] == "http://localhost:3000/brief"
    assert headline == "$60M in upside is on the table"


def test_slack_blocks_day3_pause_note_rendered():
    _, blocks = brief_nudge.nudge_slack_blocks(_nudge_payload(pause=True), "http://x/brief")
    assert any(b["type"] == "context" for b in blocks)


# ── skill binding ─────────────────────────────────────────────────────────
def test_generate_nudge_binds_the_brief_nudge_skill(monkeypatch):
    calls = _stub_llm(monkeypatch, _nudge_payload())
    out = brief_nudge.generate_nudge("co1", _brief(), 0, "http://x/brief")
    assert out["slack"]["headline"]
    assert calls[0]["skill"] == "brief-nudge"
    assert calls[0]["agent"] == "brief_nudge"


# ── flag gate ─────────────────────────────────────────────────────────────
def test_disabled_is_noop_no_llm_no_send(monkeypatch):
    monkeypatch.setattr(brief_nudge.settings, "brief_nudge_enabled", False, raising=False)
    llm = _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [_conn()])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=0, brief_id=42)
    assert res["delivered"] is False and res["reason"] == "brief_nudge_disabled"
    assert not llm and not sent


# ── delivery + gating ─────────────────────────────────────────────────────
def test_day0_delivers_to_eligible_recipient(monkeypatch):
    _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    recorded = []
    monkeypatch.setattr(brief_nudge.nudge_db, "record_nudge_sent",
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [_conn()])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=0, brief_id=42)
    assert res["delivered"] is True
    assert len(sent) == 1 and sent[0]["channel"] == "C123"
    assert len(recorded) == 1                                   # send was ledgered


def test_no_slack_connection_is_clean_noop(monkeypatch):
    llm = _stub_llm(monkeypatch, _nudge_payload())
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=0, brief_id=42)
    assert res["delivered"] is False and res["reason"] == "slack_not_connected"
    assert not llm                                              # no LLM when nobody to send to


def test_day1_skips_when_brief_already_opened(monkeypatch):
    llm = _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    monkeypatch.setattr(brief_nudge.nudge_db, "is_brief_unopened", lambda *a, **k: False)
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [_conn()])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=1, brief_id=42)
    assert res["reason"] == "no_eligible_recipients"
    assert not llm and not sent                                 # no generation, no send


def test_day0_ignores_open_state_still_announces(monkeypatch):
    _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    monkeypatch.setattr(brief_nudge.nudge_db, "is_brief_unopened", lambda *a, **k: False)
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [_conn()])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=0, brief_id=42)
    assert res["delivered"] is True and len(sent) == 1          # announce ignores open-state


def test_idempotent_skips_already_sent_step(monkeypatch):
    llm = _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    monkeypatch.setattr(brief_nudge.nudge_db, "has_nudge_been_sent", lambda *a, **k: True)
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections", lambda _c: [_conn()])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=1, brief_id=42)
    assert res["reason"] == "no_eligible_recipients"
    assert not llm and not sent


def test_recipient_without_channel_is_skipped(monkeypatch):
    _stub_llm(monkeypatch, _nudge_payload())
    sent = _stub_slack(monkeypatch)
    monkeypatch.setattr(brief_nudge.db, "list_slack_connections",
                        lambda _c: [_conn(channel=None)])
    res = brief_nudge.deliver_brief_nudge_to_slack("co1", _brief(), day=0, brief_id=42)
    assert res["reason"] == "no_eligible_recipients" and not sent


# ── scheduler cycle ───────────────────────────────────────────────────────
def test_days_since_whole_days():
    base = datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc)
    assert brief_nudge._days_since("2026-06-23T09:00:00+00:00", base + timedelta(days=2)) == 2
    assert brief_nudge._days_since(None) is None


def test_cycle_delivers_due_day_and_skips_out_of_window(monkeypatch):
    now = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)   # brief is 2 days old → day 2
    monkeypatch.setattr("app.db.companies.list_companies",
                        lambda: [{"id": "co1", "slug": "acme"},
                                 {"id": "co2", "slug": "beta"}])
    briefs = {"acme": {**_brief(), "generated_at": "2026-06-23T09:00:00+00:00"},
              "beta": {**_brief(), "generated_at": "2026-06-25T08:00:00+00:00"}}  # 0 days → skip
    monkeypatch.setattr("app.db.briefs.get_current_brief", lambda slug: briefs.get(slug))
    seen = []
    monkeypatch.setattr(brief_nudge, "deliver_brief_nudge_to_slack",
                        lambda cid, brief, *, day, brief_id: seen.append((cid, day)) or {"delivered": True})
    out = brief_nudge.run_nudge_cycle(now=now)
    assert out["enabled"] is True
    assert ("co1", 2) in seen                     # acme is at day 2 → delivered
    assert all(cid != "co2" for cid, _ in seen)   # beta at day 0 → skipped (Day 0 is inline)
    assert out["delivered"] == 1


def test_cycle_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(brief_nudge.settings, "brief_nudge_enabled", False, raising=False)
    assert brief_nudge.run_nudge_cycle() == {"enabled": False}

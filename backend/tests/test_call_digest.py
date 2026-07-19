"""On-demand call-digest service — window parsing, intent, corpus, answer branches.

No network/LLM/DB: the Fireflies fetch, key load, and gateway llm_call are
patched in the call_digest namespace.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.call_digest as cd
from app.kg_ingest.pullers.fireflies import CallTranscript
from app.skill_router import is_call_digest, is_voc_report_request

# A fixed "now" so window math is deterministic. 2026-06-24 is a Wednesday.
NOW = datetime(2026, 6, 24, 15, 30, tzinfo=timezone.utc)


# ── intent detection ─────────────────────────────────────────────────────────

def test_is_call_digest_positive():
    for q in [
        "summarize all the customer calls from last week",
        "recap this week's meetings",
        "what did we hear on our sales calls?",
        "give me a digest of customer calls from the last 30 days",
        "voice of customer from last month's calls",
        "go over the CSM calls this month",
    ]:
        assert is_call_digest(q), q


def test_is_call_digest_negative():
    for q in [
        "generate a PRD for onboarding",
        "prioritize these features",
        "what's our churn rate?",
        "summarize this document",
        "what are users asking for?",
    ]:
        assert not is_call_digest(q), q


def test_is_voc_report_request():
    # Bare VoC asks (no call-noun) — is_call_digest misses these, is_voc_report_request catches them.
    for q in ["give me a voice of customer report", "VoC report please", "voice of customer"]:
        assert is_voc_report_request(q), q
        assert not is_call_digest(q), q  # no call-noun → not the call-digest matcher
    for q in ["summarize the customer calls", "generate a PRD", "what's our churn rate?"]:
        assert not is_voc_report_request(q), q


def test_is_voc_report_request_feedback_from_conversations():
    # "Feedback from customer conversations" phrasings are VoC by intent — they
    # carry no "voice of customer" literal and no call-noun, and previously fell
    # to the haiku router (which misrouted them to a DS-style answer).
    for q in [
        "Give me a summary of feedback of recent customer conversations",
        "Give me a summary of feedback from recent customer conversations",
        "summarize the feedback in our customer conversations",
        "what feedback came out of the client discussions last month?",
        "user conversations this quarter — any feedback themes?",
    ]:
        assert is_voc_report_request(q), q
    # Needs BOTH a feedback word and a customer-conversation noun.
    for q in [
        "summarize recent customer conversations",   # no "feedback" → call digest's turf
        "summarize the feedback from the beta survey",
        "give me feedback on my PRD draft",
        "how many customer conversations did we have?",
    ]:
        assert not is_voc_report_request(q), q


# ── window parsing ───────────────────────────────────────────────────────────

def test_window_default_is_last_7_days():
    w = cd.parse_window("summarize my customer calls", now=NOW)
    assert w.until == NOW
    assert (NOW - w.since).days == 7
    assert "7 days" in w.label


def test_window_last_n_days():
    w = cd.parse_window("recap calls from the last 14 days", now=NOW)
    assert w.until == NOW
    assert (NOW - w.since).days == 14
    assert "14 day" in w.label


def test_window_yesterday():
    w = cd.parse_window("summarize yesterday's calls", now=NOW)
    assert w.since == NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    assert (w.until - w.since) == timedelta(days=1)


def test_window_last_week_is_previous_calendar_week():
    w = cd.parse_window("summarize calls from last week", now=NOW)
    # span is exactly 7 days, aligned to midnight, ending at this week's Monday.
    assert (w.until - w.since) == timedelta(days=7)
    assert w.since.weekday() == 0 and w.until.weekday() == 0
    assert w.until <= NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    assert "last week" in w.label


def test_window_this_month():
    w = cd.parse_window("recap this month's meetings", now=NOW)
    assert w.since.day == 1 and w.since.month == 6
    assert w.until == NOW


def test_window_last_month():
    w = cd.parse_window("summarize last month's calls", now=NOW)
    assert w.since.day == 1 and w.since.month == 5  # May
    assert w.until.day == 1 and w.until.month == 6  # exclusive end = Jun 1


# ── corpus assembly ──────────────────────────────────────────────────────────

def _call(i):
    return CallTranscript(
        external_id=f"c{i}", title=f"Call {i}", date="2026-06-20",
        participants=["p@x.com"], overview=f"overview {i}",
        quotes=[{"speaker": "Cust", "text": f"quote {i}"}],
    )


def test_has_call_source_true_when_key_present(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    assert cd.has_call_source("co") is True


def test_has_call_source_false_when_no_key(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    assert cd.has_call_source("co") is False


def test_build_corpus_not_connected(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    assert out.status == "not_connected"


def test_build_corpus_no_calls(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [])
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    assert out.status == "no_calls"


def test_build_corpus_ok_renders_calls(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    assert out.status == "ok" and out.count == 2
    assert "Call 1" in out.text and 'Cust: "quote 2"' in out.text


def test_build_corpus_error(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    def boom(*a, **k):
        raise RuntimeError("fireflies down")
    monkeypatch.setattr(cd, "fetch_calls", boom)
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    assert out.status == "error" and "fireflies down" in out.error


def test_build_corpus_respects_char_budget(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", 50)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(i) for i in range(20)])
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    # First call always included; budget caps the rest well under 20.
    assert out.status == "ok" and out.count < 20 and out.count >= 1


# ── answer branches ──────────────────────────────────────────────────────────

def test_answer_not_connected_skips_report(monkeypatch):
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    called = []
    monkeypatch.setattr(vr, "build", lambda **k: called.append(k) or "<html></html>")
    p = cd.answer(enterprise_id="co", question="summarize calls last week")
    assert "Fireflies" in p["answer"]
    assert p["_skill_source"] == "call-digest"
    assert called == []  # no spend when there's nothing to summarize


def test_answer_ok_renders_html_report_over_corpus(monkeypatch):
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    captured = {}
    monkeypatch.setattr(
        vr, "build",
        lambda **k: captured.update(k) or "<!DOCTYPE html><html><body>report</body></html>",
    )
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    # The answer is the rendered HTML report (front-end shows it in an iframe).
    assert p["answer"].startswith("<!DOCTYPE html>")
    assert p["key_points"] == [] and p["citations"] == []
    assert p["_skill"] == "voice-of-customer-report"
    assert p["_skill_source"] == "call-digest"
    # The report ran over the assembled corpus, scoped to the VoC skill.
    assert captured["model"] == cd.ANSWER_MODEL
    assert "Call 1" in captured["corpus_text"] and 'Cust: "quote 1"' in captured["corpus_text"]


def test_answer_report_failure_degrades_gracefully(monkeypatch):
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    def boom(**k):
        raise RuntimeError("model timeout")
    monkeypatch.setattr(vr, "build", boom)
    p = cd.answer(enterprise_id="co", question="summarize customer calls")
    assert "error" in p["answer"].lower() and p["_skill_source"] == "call-digest"

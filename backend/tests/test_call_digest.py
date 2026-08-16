"""On-demand call-digest service — window parsing, intent, corpus, answer branches.

No network/LLM/DB: the Fireflies fetch, the Zoom context/listing/transcript
reads, the key load, and gateway llm_call are all patched in the call_digest
namespace.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import app.call_digest as cd
from app.connectors.zoom_oauth import ZoomContext
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


def test_is_voc_report_request_customer_feedback_phrasings():
    # "Top/summarize customer feedback" asks are VoC by intent — no call-noun,
    # no conversation-noun, no "voice of customer" literal (staging misroute:
    # "What is the top customer feedback" fell to the generic answer path).
    for q in [
        "What is the top customer feedback",
        "summarize customer feedback from last week",
        "customer feedback themes this month",
        "top user feedback right now?",
        "what are the main pieces of client feedback",
        "customer feedback report please",
    ]:
        assert is_voc_report_request(q), q
    # No intent word near "customer feedback", or feedback isn't customers' —
    # these must NOT divert.
    for q in [
        "give me feedback on my PRD draft",
        "summarize the feedback from the beta survey",
        "we built this from customer feedback",
        "customer feedback",   # bare mention, no ask
    ]:
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


# ── spoken windows, and the planner's window winning ─────────────────────────
#
# People SPEAK these questions. "give me a table week by week ... the last five
# weeks" was answered with four days of calls, and the report then described the
# missing weeks as history that "was not captured" — the digits-only regex could
# not read "five", so the digest fell to its 7-day default and said nothing
# about having done so (2026-08-16).


def test_window_accepts_a_spelled_out_count():
    w = cd.parse_window("give me the last five weeks of customer calls", now=NOW)
    assert (NOW - w.since).days == 35
    assert w.explicit


def test_window_accepts_dictation_run_together():
    """"look at the last10 weeks" arrived exactly like that."""
    w = cd.parse_window("look much further and look at the last10 weeks", now=NOW)
    assert (NOW - w.since).days == 70
    assert w.explicit


def test_a_bare_last_week_is_still_the_calendar_week():
    """The number is required — relaxing the separator must not let "last
    week" fall into the N-unit branch."""
    w = cd.parse_window("summarize calls from last week", now=NOW)
    assert "last week" in w.label


def test_the_planners_window_beats_re_parsing_the_question(monkeypatch):
    """The real fix. Even when the text defeats the regex, the planner read the
    whole sentence and its window is the one that runs."""
    seen: dict = {}

    def _corpus(enterprise_id, window):
        seen["since"] = window.since
        seen["until"] = window.until
        seen["explicit"] = window.explicit
        raise RuntimeError("stop here — the window is all this test needs")

    monkeypatch.setattr(cd, "build_corpus", _corpus)

    with pytest.raises(RuntimeError):
        cd.answer(
            enterprise_id="ent-1",
            question="table week by week for the last five weeks",
            constraints={"since": "2026-07-12", "until": "2026-08-16"},
        )

    assert seen["since"].date().isoformat() == "2026-07-12"
    assert seen["until"].date().isoformat() == "2026-08-16"
    # EXPLICIT, so the auto-widen never quietly replaces a stated period:
    # "no calls in those five weeks" is a real answer to that question.
    assert seen["explicit"] is True


def test_a_planner_window_that_will_not_parse_falls_back_to_the_question(
    monkeypatch,
):
    """A bad constraint degrades to the behaviour this replaced, never to a
    broken window."""
    seen: dict = {}

    def _corpus(enterprise_id, window):
        seen["since"] = window.since
        raise RuntimeError("stop")

    monkeypatch.setattr(cd, "build_corpus", _corpus)

    with pytest.raises(RuntimeError):
        cd.answer(
            enterprise_id="ent-1",
            question="recap calls from the last 14 days",
            constraints={"since": "not-a-date"},
        )

    assert (cd._utc_now() - seen["since"]).days >= 14


def test_no_constraints_parses_the_question_exactly_as_before(monkeypatch):
    seen: dict = {}

    def _corpus(enterprise_id, window):
        seen["label"] = window.label
        raise RuntimeError("stop")

    monkeypatch.setattr(cd, "build_corpus", _corpus)

    with pytest.raises(RuntimeError):
        cd.answer(enterprise_id="ent-1", question="recap calls from the last 14 days")

    assert "14 day" in seen["label"]


# ── the store must cover the window, not merely touch it ─────────────────────
#
# The stored-first path asked only whether ANY row existed for the window. A
# workspace whose earlier digests ran over 7 days had exactly those days
# stored, so the first correct 10-week question found 37 rows, skipped the live
# fetch, and described the missing 175 calls as weeks where "absence of records
# is not evidence of no activity" — a gap that existed only in our own cache.


def _window(days=70):
    now = cd._utc_now()
    return cd.Window(now - timedelta(days=days), now, f"the last {days} days",
                     explicit=True)


def test_a_short_store_does_not_pass_as_a_covered_window(monkeypatch):
    import app.call_index as ci

    monkeypatch.setattr(
        ci, "count_calls",
        lambda cid, since=None, until=None, provider=None: 212,
    )
    assert cd._store_covers("co-1", "fireflies", _window(), [{}] * 37) is False


def test_a_complete_store_is_used_without_a_live_fetch(monkeypatch):
    import app.call_index as ci

    monkeypatch.setattr(
        ci, "count_calls",
        lambda cid, since=None, until=None, provider=None: 37,
    )
    assert cd._store_covers("co-1", "fireflies", _window(), [{}] * 37) is True


def test_coverage_is_checked_per_provider(monkeypatch):
    """A mixed-source total would answer a single-provider question wrong."""
    import app.call_index as ci

    seen: dict = {}

    def _count(cid, since=None, until=None, provider=None):
        seen["provider"] = provider
        return 5

    monkeypatch.setattr(ci, "count_calls", _count)
    cd._store_covers("co-1", "zoom", _window(), [{}] * 5)
    assert seen["provider"] == "zoom"


def test_an_unreadable_index_trusts_the_store(monkeypatch):
    """Cannot tell → keep the old behaviour. Forcing a minutes-long live fetch
    on every question because a count query blipped is the worse failure."""
    import app.call_index as ci

    def _boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(ci, "count_calls", _boom)
    assert cd._store_covers("co-1", "fireflies", _window(), [{}] * 3) is True


def test_an_empty_store_always_fetches(monkeypatch):
    assert cd._store_covers("co-1", "fireflies", _window(), []) is False
    assert cd._store_covers("co-1", "fireflies", _window(), None) is False


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
    assert out.total == 20  # the drop is recorded, not silent


def _chatty_call(i, n_quotes=40):
    return CallTranscript(
        external_id=f"c{i}", title=f"Call {i}", date="2026-06-20",
        participants=["p@x.com"], overview=f"overview {i}",
        quotes=[{"speaker": "Cust", "text": f"call {i} quote {j} — {'x' * 60}"}
                for j in range(n_quotes)],
    )


def test_build_corpus_trims_quotes_before_dropping_calls(monkeypatch):
    # Regression: a month of chatty calls used to shrink to the newest ~5–7 —
    # whole calls were dropped to fit the budget. The fit must instead keep
    # EVERY call and sample fewer verbatim quotes per call.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    calls = [_chatty_call(i) for i in range(30)]
    full = len("\n\n".join(c.render() for c in calls))
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", full // 3)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)
    out = cd.build_corpus("co", cd.parse_window("last 30 days calls", now=NOW))
    assert out.status == "ok"
    assert out.count == 30                        # every call still in
    assert out.quote_cap is not None              # via a reduced quote cap
    assert len(out.text) <= full // 3
    for i in range(30):                           # all calls represented
        assert f"Call {i}" in out.text


def test_build_corpus_untrimmed_reports_no_quote_cap(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    out = cd.build_corpus("co", cd.parse_window("calls", now=NOW))
    assert out.quote_cap is None and out.total == 2 and out.count == 2


# ── answer branches ──────────────────────────────────────────────────────────
#
# `app.voc_report` is gone. It rendered the pinned HTML template from a filling
# schema; the full-window VoC pass is an ordinary gateway call now, so these
# tests stub `graph.gateway.llm_call` (imported lazily inside `answer`) instead
# of `voc_report.build`. Everything they assert about the CORPUS — the live
# fetch, the window, auto-widening, the coverage disclosure in the source line,
# uploaded voice documents — is unchanged; only the rendering seam moved, and
# the answer is markdown rather than a document.


class _VocResult:
    def __init__(self, answer):
        self.output = {"answer": answer, "key_points": [], "citations": [],
                       "confidence": 0.6, "unanswered": ""}


def _stub_voc_pass(monkeypatch, answer="## Voice of customer\n\nThemes…"):
    """Capture the full VoC pass's gateway kwargs; return the capture dict.

    `input` carries what `source_line` and `corpus_text` used to be handed
    separately, so assertions on either now read that one string.
    """
    import app.graph.gateway as gateway_mod

    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return _VocResult(answer)

    monkeypatch.setattr(gateway_mod, "llm_call", _fake)
    return captured



def test_answer_not_connected_skips_report(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    captured = _stub_voc_pass(monkeypatch)
    p = cd.answer(enterprise_id="co", question="summarize calls last week")
    assert "Fireflies" in p["answer"]
    assert p["_skill_source"] == "call-digest"
    assert captured == {}  # no spend when there's nothing to summarize


def test_answer_ok_runs_the_voc_pass_over_the_whole_corpus(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    captured = _stub_voc_pass(monkeypatch)
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    # The answer is an ordinary chat answer — markdown, not a document. This is
    # the point of the change: "give me top 3 product requests from last week"
    # used to come back as a 29 KB HTML report with web fonts.
    assert p["answer"].startswith("## Voice of customer")
    assert not p["answer"].lstrip().startswith("<")
    assert p["_skill"] == "voice-of-customer-report"
    assert p["_skill_source"] == "call-digest"
    # The pass ran over the assembled corpus, scoped to the VoC skill.
    assert captured["model"] == cd.ANSWER_MODEL
    assert captured["skill"] == "voice-of-customer-report"
    assert "Call 1" in captured["input"] and 'Cust: "quote 1"' in captured["input"]


def test_answer_disclosure_when_quotes_trimmed(monkeypatch):
    # When the fit trimmed quotes to keep every call in, the source line the
    # report sees says so — the run line can then state real coverage.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    calls = [_chatty_call(i) for i in range(30)]
    full = len("\n\n".join(c.render() for c in calls))
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", full // 3)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)
    captured = _stub_voc_pass(monkeypatch)
    p = cd.answer(enterprise_id="co", question="summarize calls from the last 30 days")
    assert p["answer"].startswith("## Voice of customer")
    assert "30 calls" in captured["input"]
    assert "quotes sampled" in captured["input"]


def test_answer_autowidens_default_window_until_calls_found(monkeypatch):
    # Generic ask (no window named): the 7-day default is empty, 30 days has
    # calls → the digest widens instead of dead-ending, and the report runs
    # over the widened window.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    windows = []
    def fetch(key, *, since, until):
        windows.append((until - since).days)
        return [_call(1)] if (until - since).days >= 29 else []
    monkeypatch.setattr(cd, "fetch_calls", fetch)
    captured = _stub_voc_pass(monkeypatch)
    p = cd.answer(enterprise_id="co", question="give me a summary of feedback of recent customer conversations")
    assert p["answer"].startswith("## Voice of customer")
    # Fetched 7d (empty) then 30d (found) — never needed 90d.
    assert len(windows) == 2 and windows[0] <= 7 and 29 <= windows[1] <= 30
    assert "the last 30 days" in p["_skill_action"]
    assert "the last 30 days" in captured["input"]


def test_answer_explicit_window_is_never_widened(monkeypatch):
    # The user NAMED a window — an empty result is the honest answer, and only
    # one fetch happens.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    calls = []
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls.append(1) or [])
    p = cd.answer(enterprise_id="co", question="summarize customer calls from last week")
    assert len(calls) == 1
    assert "No customer calls" in p["answer"] and "wider window" in p["answer"]


def test_answer_autowiden_exhausted_says_90_days(monkeypatch):
    # Nothing in 7/30/90 days → the message reports the widest window searched
    # and doesn't suggest widening further.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    fetches = []
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: fetches.append(1) or [])
    p = cd.answer(enterprise_id="co", question="summary of feedback from recent customer conversations")
    assert len(fetches) == 3  # 7 → 30 → 90
    assert "the last 90 days" in p["answer"]
    assert "wider window" not in p["answer"]


def test_parse_window_explicit_flag():
    assert cd.parse_window("calls from the last 30 days", now=NOW).explicit is True
    assert cd.parse_window("calls from last week", now=NOW).explicit is True
    assert cd.parse_window("summary of recent feedback", now=NOW).explicit is False


def test_answer_report_failure_degrades_gracefully(monkeypatch):
    import app.graph.gateway as gateway_mod
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    def boom(**k):
        raise RuntimeError("model timeout")
    monkeypatch.setattr(gateway_mod, "llm_call", boom)
    p = cd.answer(enterprise_id="co", question="summarize customer calls")
    assert "error" in p["answer"].lower() and p["_skill_source"] == "call-digest"


# ── uploaded voice-category documents (connector-category uploads) ───────────

def _doc(i, days_ago=1):
    return cd.UploadedVoiceDoc(
        name=f"doc{i}.pdf", added_at=NOW - timedelta(days=days_ago),
        text=f"support export {i}")


def test_build_corpus_docs_only_is_ok(monkeypatch):
    """No Fireflies key but voice-category uploads exist → real corpus, not
    not_connected. The docs render as <uploaded document> blocks."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1), _doc(2)])
    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))
    assert out.status == "ok"
    assert out.count == 0 and out.doc_count == 2
    assert '<uploaded document name="doc1.pdf"' in out.text
    assert "support export 2" in out.text


def test_build_corpus_merges_calls_and_docs(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1)])
    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))
    assert out.status == "ok"
    assert out.count == 1 and out.doc_count == 1
    # Calls first, then docs.
    assert out.text.index("Call 1") < out.text.index('<uploaded document')


def test_build_corpus_no_docs_in_window_is_no_calls_not_disconnected(monkeypatch):
    """Voice docs exist but none dated inside the window (and no key): that's
    an empty WINDOW (no_calls → auto-widen can rescue it), not not_connected."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(
        cd, "_voice_docs",
        lambda cid, w: [] if w is not None else [_doc(1, days_ago=400)])
    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))
    assert out.status == "no_calls"


def test_build_corpus_fetch_error_degrades_to_docs(monkeypatch):
    """Fireflies down but uploaded docs available → docs-only ok corpus
    instead of an error dead-end."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    def boom(*a, **k):
        raise RuntimeError("fireflies down")
    monkeypatch.setattr(cd, "fetch_calls", boom)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1)])
    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))
    assert out.status == "ok"
    assert out.doc_count == 1 and out.count == 0
    assert out.error  # the fetch failure is still recorded


def test_has_call_source_true_from_docs_alone(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1)])
    assert cd.has_call_source("co") is True


def test_answer_docs_only_runs_the_voc_pass(monkeypatch):
    """Docs-only tenant asking for VoC gets the SAME pipeline; the source line
    and skill action disclose the uploaded-document basis."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1), _doc(2)])
    captured = _stub_voc_pass(monkeypatch)
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    assert p["answer"].startswith("## Voice of customer")
    assert "UPLOADED VOICE DOCUMENTS" in captured["input"]
    assert "2 uploaded voice documents" in captured["input"]
    assert "support export 1" in captured["input"]
    assert "2 uploaded docs" in p["_skill_action"]


def test_voice_docs_reads_categorized_files_within_window(isolated_settings, monkeypatch):
    """_voice_docs end-to-end over a real dataset dir: voice-category files
    inside the window are returned with their converted markdown text; other
    categories and out-of-window files are excluded."""
    import os

    from app import datasets
    from app.ingest import md_filename

    base, raw = datasets.dataset_path("acme"), datasets.raw_path("acme")
    raw.mkdir(parents=True, exist_ok=True)
    for name, category, text in [
        ("calls.pdf", "voice", "call transcript body"),
        ("old_calls.pdf", "voice", "ancient transcript"),
        ("mrr.xlsx", "revenue", "revenue body"),
    ]:
        (raw / name).write_bytes(b"raw")
        (base / md_filename(name)).write_text(text)
        datasets.set_file_categories("acme", [name], category)
    # Pin upload times relative to the fixed NOW: calls.pdf inside the window,
    # old_calls.pdf aged out of any 7-day window.
    fresh = (NOW - timedelta(days=1)).timestamp()
    os.utime(raw / "calls.pdf", (fresh, fresh))
    old = (NOW - timedelta(days=300)).timestamp()
    os.utime(raw / "old_calls.pdf", (old, old))

    monkeypatch.setattr(
        "app.db.companies.slug_for_company_id", lambda cid: "acme")
    win = cd.Window(NOW - timedelta(days=7), NOW + timedelta(days=7), "test")
    docs = cd._voice_docs("co", win)
    assert [d.name for d in docs] == ["calls.pdf"]
    assert docs[0].text == "call transcript body"
    # No window → the aged file appears too; revenue category never does.
    all_docs = cd._voice_docs("co", None)
    assert {d.name for d in all_docs} == {"calls.pdf", "old_calls.pdf"}


def test_staging_scenario_top_customer_feedback_over_category_upload(
    isolated_settings, monkeypatch,
):
    """Replay of the 2026-07-26 staging misroute end-to-end on this branch:
    a feedback CSV uploaded via the Customer Voice & Support category strip,
    NO Fireflies connected, question "What is the top customer feedback".
    Must route to the digest (router match + has_call_source via docs) and run
    the pinned VoC report over the uploaded file's converted text."""
    import os

    from app import datasets
    from app.ingest import md_filename

    # The uploaded file, as the category strip stores it.
    base, raw = datasets.dataset_path("acme"), datasets.raw_path("acme")
    raw.mkdir(parents=True, exist_ok=True)
    name = "user_feedback_raw_2026_07_21_to_2026_07_26.csv"
    (raw / name).write_bytes(b"raw")
    (base / md_filename(name)).write_text("latency complaint; mobile access ask")
    datasets.set_file_categories("acme", [name], "voice")
    fresh = (NOW - timedelta(days=2)).timestamp()
    os.utime(raw / name, (fresh, fresh))

    monkeypatch.setattr("app.db.companies.slug_for_company_id", lambda cid: "acme")
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)  # no Fireflies
    monkeypatch.setattr(cd, "_utc_now", lambda: NOW)
    captured = _stub_voc_pass(monkeypatch)

    question = "What is the top customer feedback"
    assert is_voc_report_request(question)          # router now matches
    assert cd.has_call_source("co") is True         # voice-category doc counts

    p = cd.answer(enterprise_id="co", question=question)
    assert p["_skill"] == "voice-of-customer-report"
    assert p["answer"].startswith("## Voice of customer")
    # The uploaded file's CONVERTED TEXT is what the pass reasoned over — the
    # point of the original staging replay. Asserted on the corpus rather than
    # on the report branch's source line, because this phrasing is
    # query-shaped (`is_voc_query`) and both branches now run through the same
    # gateway seam; either one answering from this text is the correct outcome.
    assert "latency complaint; mobile access ask" in captured["input"]
    assert captured["skill"] == "voice-of-customer-report"


# ── Query mode: pointed questions answered FROM the corpus ───────────────────
# "did complaints about exports increase this week" wants a computed answer
# over the dated records, not the report artifact (Apurva, 2026-07-28).

def test_is_voc_query_shapes():
    from app.call_digest import is_voc_query

    positives = [
        "did complaints about exports increase this week",
        "how many customers raised billing issues this month",
        "which accounts complained about latency",
        "who reported the export bug",
        "what did Cascade Health say about the dashboard",
        "show me quotes about onboarding",
        "are export complaints getting worse compared to last week",
    ]
    # Report-shaped language ALWAYS wins — the artifact stays one ask away.
    negatives = [
        "give me the summary of customer feedback from today",
        "summarize the customer calls from last week",
        "voice of customer report for last month",
        "what are the themes from this week's calls",
        "recap yesterday's customer meetings",
    ]
    for q in positives:
        assert is_voc_query(q), f"query mode missed: {q!r}"
    for q in negatives:
        assert not is_voc_query(q), f"report ask misdetected as query: {q!r}"


def test_comparative_query_doubles_window_and_sets_boundary(monkeypatch):
    """Trend questions fetch the prior period too, and record the boundary so
    the answer can bucket records into asked-vs-prior by date."""
    from app import call_digest as cd

    captured = {}

    def _fake_build(company_id, window):
        captured["window"] = window
        return cd.DigestCorpus(status="ok", window=window, text="=== CALLS ===")

    def _fake_query(**kw):
        captured["boundary"] = kw["compare_boundary"]
        return {"answer": "counts", "_skill_source": "voc-query"}

    monkeypatch.setattr(cd, "build_corpus", _fake_build)
    monkeypatch.setattr(cd, "_answer_query", _fake_query)

    from app.call_digest import parse_window

    out = cd.answer(enterprise_id="ent-A",
                    question="did complaints about exports increase this week?")
    assert out["_skill_source"] == "voc-query"
    w = captured["window"]
    original = parse_window("did complaints about exports increase this week?")
    # The fetch reaches back at least one full week before the asked period
    # (a week-to-date window may span only a day — the prior period must
    # still cover a real week, not last weekend).
    assert (original.since - w.since).days >= 7
    # `until` is "now" in both parses — allow the sub-second skew between calls.
    assert abs((w.until - original.until).total_seconds()) < 5
    # The boundary marks where the asked period begins.
    assert captured["boundary"] == original.since.date().isoformat()


def test_non_comparative_query_keeps_window(monkeypatch):
    from app import call_digest as cd

    captured = {}

    def _fake_build(company_id, window):
        captured["window"] = window
        return cd.DigestCorpus(status="ok", window=window, text="=== CALLS ===")

    monkeypatch.setattr(cd, "build_corpus", _fake_build)
    monkeypatch.setattr(cd, "_answer_query",
                        lambda **kw: {"answer": "a", "_skill_source": "voc-query"})

    cd.answer(enterprise_id="ent-A",
              question="which accounts complained about latency")
    w = captured["window"]
    assert (w.until - w.since).days <= 8  # default 7-day window, not doubled


def test_query_mode_falls_back_to_report_on_failure(monkeypatch):
    """A query-mode LLM failure degrades to the report path, never a dead end."""
    from app import call_digest as cd

    def _fake_build(company_id, window):
        return cd.DigestCorpus(status="ok", window=window, text="=== CALLS ===")

    def _boom(**kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(cd, "build_corpus", _fake_build)
    monkeypatch.setattr(cd, "_answer_query", _boom)
    captured = _stub_voc_pass(monkeypatch)

    out = cd.answer(enterprise_id="ent-A",
                    question="how many customers raised billing issues")
    assert captured or out.get("answer")  # full-pass path reached


def test_apurva_acceptance_phrases_mode_selection():
    """The two acceptance phrases (Apurva, 2026-07-28): the summary ask
    produces the REPORT; the number-1-complaint ask gets the pointed QUERY
    answer — both on the VoC surface."""
    from app.call_digest import is_voc_query
    from app.skill_router import is_voc_report_request

    summary_ask = "give me the summary of customer feedback from today"
    complaint_ask = "what is the number 1 user complaint from todays customer conversation"

    # Both reach the VoC surface without the LLM router.
    assert is_voc_report_request(summary_ask)
    assert is_voc_report_request(complaint_ask)
    # Mode: summary → report artifact; pointed superlative → query answer.
    assert not is_voc_query(summary_ask)
    assert is_voc_query(complaint_ask)


# ── Zoom as a second live source ─────────────────────────────────────────────
#
# The digest fires on generic call/VoC-shaped questions with NO connector name
# in them ("what are customers saying"), which is exactly what the live
# connector-lookup adapter cannot do — it only fires when the message says
# "zoom". These cases pin the generalization: a Zoom-only company gets a real
# digest, a both-connected company gets one merged window, and a Fireflies-only
# company is left exactly as it was.


def _zoom_ctx(user_ids=None, user_names=None) -> ZoomContext:
    """The real ZoomContext, so a field rename in zoom_oauth breaks here rather
    than silently in production."""
    return ZoomContext(
        company_id="co", access_token="tok",
        user_ids=user_ids or [], user_names=user_names or {},
    )


def _recent_window() -> cd.Window:
    """The rolling 7-day window ending at the fixed NOW.

    Deliberately not "last week": that phrase means the previous CALENDAR week,
    which with NOW on Wednesday the 24th ends at Monday the 22nd — so a fixture
    call at 2026-06-22T14:00Z would fall outside it and the window filter would
    (correctly) drop every fixture."""
    return cd.parse_window("calls from the last 7 days", now=NOW)


#: A recording file that is NOT a transcript — an account with cloud recording
#: on but audio transcription off, which is the commonest real cause.
_NO_TRANSCRIPT_FILES = [
    {"file_type": "MP4", "file_extension": "MP4", "download_url": "https://zoom/a.mp4"},
]


def _meeting(uuid, *, topic="Acme QBR", start="2026-06-22T14:00:00Z",
             host_email="rep@ours.com", files=None):
    """Zoom's own recordings-listing meeting dict, as list_user_recordings
    returns it (raw, with recording_files, per zoom_oauth's contract)."""
    return {
        "uuid": uuid, "id": uuid, "topic": topic, "start_time": start,
        "duration": 30, "host_email": host_email,
        "recording_files": files if files is not None else [
            {"file_type": "TRANSCRIPT", "download_url": f"https://zoom/{uuid}.vtt"},
        ],
    }


_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Sam Lee: We keep hitting the export limit every single week.

2
00:00:04.000 --> 00:00:08.000
Dana Fox: How often does that actually block your team?
"""


def _stub_zoom(monkeypatch, *, meetings_by_host=None, vtt=_VTT, hosts=None):
    """Wire the whole Zoom read path to fixtures and record what was asked for.

    Returns a capture dict with `calls` — one (user_id, frm, to) tuple per
    list_user_recordings request — so a test can assert the ACTUAL date args,
    not merely that a fetch happened. A window silently clamped to a month is
    the bug class this exists to catch.
    """
    captured: dict = {"calls": [], "downloads": []}
    by_host = meetings_by_host if meetings_by_host is not None else {}

    def _list(access_token, user_id, *, frm, to, page_size, max_pages=1):
        captured["calls"].append((str(user_id), frm, to))
        return list(by_host.get(str(user_id), []))

    def _fetch(access_token, url):
        captured["downloads"].append(url)
        return vtt

    monkeypatch.setattr(cd, "list_user_recordings", _list)
    monkeypatch.setattr(cd, "fetch_transcript_text", _fetch)
    # `_transcript_for` falls back to a per-meeting call when the LISTING
    # carries no recording_files at all. Stubbed so no fixture can reach the
    # network by accident, and so the N+1 that fallback would be stays visible.
    import app.kg_ingest.pullers.zoom as zoom_puller
    monkeypatch.setattr(zoom_puller, "get_meeting_recordings", lambda t, u: {})
    if hosts is not None:
        monkeypatch.setattr(cd, "_zoom_hosts", lambda ctx: hosts)
    return captured


def _zoom_transcript(i, *, date_iso="2026-06-22T14:00:00Z", note="") -> CallTranscript:
    """A Zoom-provider CallTranscript, as fetch_zoom_calls yields them."""
    return CallTranscript(
        external_id=f"z{i}", title=f"Zoom call {i}", date=date_iso,
        participants=["rep@ours.com", "Sam Lee"], overview="",
        quotes=[{"speaker": "Sam Lee", "text": f"zoom quote {i}"}],
        provider="zoom", note=note,
    )


# ── has_call_source: the capability gate the router reads ────────────────────

def test_has_call_source_true_for_zoom_only_company(monkeypatch):
    """No Fireflies key, no uploaded docs, a live Zoom grant → the digest can
    build a corpus, so the router must divert to it."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    assert cd.has_call_source("co") is True


def test_has_call_source_fireflies_only_never_pays_for_zoom(monkeypatch):
    """Regression guard: a Fireflies company answers True exactly as before —
    and short-circuits, so it never spends a Zoom token refresh to learn
    something it already knew."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")

    def _boom(cid):
        raise AssertionError("zoom must not be consulted once Fireflies answered")

    monkeypatch.setattr(cd, "_zoom_context", _boom)
    assert cd.has_call_source("co") is True


def test_has_call_source_false_when_neither_source_nor_docs(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    assert cd.has_call_source("co") is False


def test_zoom_context_never_raises(monkeypatch):
    """A dead Zoom connection degrades to "no Zoom", never to a stack trace on
    the user's turn — this is consulted from a routing decision."""
    import app.call_digest as mod

    def _boom(cid):
        raise RuntimeError("zoom oauth exploded")

    monkeypatch.setattr(mod, "zoom_sync_context", _boom)
    assert cd._zoom_context("co") is None


# ── build_corpus / answer with Zoom ──────────────────────────────────────────

def test_answer_zoom_only_company_gets_a_real_digest(monkeypatch):
    """The old fallback ("connect Fireflies…") was the whole bug: a company with
    Zoom connected and no Fireflies got told it had no call source."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(
        cd, "fetch_zoom_calls", lambda ctx, w: [_zoom_transcript(1), _zoom_transcript(2)]
    )
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert p["answer"].startswith("## Voice of customer")
    assert p["_skill_source"] == "call-digest"
    assert "no call source is connected" not in p["answer"]
    # The VoC pass ran over the Zoom calls themselves.
    assert "Zoom call 1" in captured["input"]
    assert 'Sam Lee: "zoom quote 2"' in captured["input"]
    assert "2 calls" in captured["input"]


def test_build_corpus_merges_both_sources_newest_first(monkeypatch):
    """Both connected → ONE window, interleaved by recency. Fireflies returns
    newest-first and so does Zoom, but concatenating them would put every Zoom
    call after every Fireflies call regardless of date."""
    ff = [
        CallTranscript(external_id="f1", title="FF newest",
                       date="2026-06-23T09:00:00+00:00", overview="o"),
        CallTranscript(external_id="f2", title="FF oldest",
                       date="2026-06-19T09:00:00+00:00", overview="o"),
    ]
    zm = [
        _zoom_transcript(1, date_iso="2026-06-22T14:00:00Z"),
        _zoom_transcript(2, date_iso="2026-06-20T14:00:00Z"),
    ]
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: ff)
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: zm)

    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))

    assert out.status == "ok" and out.count == 4
    assert [c.external_id for c in out.calls] == ["f1", "z1", "z2", "f2"]
    assert out.sources == ["Fireflies", "Zoom"]
    # Provenance survives into the corpus so the report can attribute a theme.
    assert "source: zoom" in out.text
    assert out.text.index("FF newest") < out.text.index("Zoom call 1")


def test_answer_both_sources_discloses_the_split(monkeypatch):
    """With two sources in one corpus the coverage line names the split, so the
    report can say where a theme was heard."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [_zoom_transcript(9)])
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert "2 Fireflies" in captured["input"] and "1 Zoom" in captured["input"]


def test_one_source_failing_does_not_cost_the_other_its_calls(monkeypatch):
    """Per-source isolation, the rule call_index already holds for these two: a
    Zoom outage must not empty a Fireflies corpus — and the answer must say the
    picture is incomplete rather than imply full coverage."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])

    def _boom(ctx, w):
        raise RuntimeError("zoom 500")

    monkeypatch.setattr(cd, "fetch_zoom_calls", _boom)
    captured = _stub_voc_pass(monkeypatch)

    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))
    assert out.status == "ok" and out.count == 1
    assert out.failed_sources == ["Zoom"] and "zoom 500" in out.error

    cd.answer(enterprise_id="co", question="summarize customer calls last week")
    assert "Zoom could not be reached" in captured["input"]


def test_answer_zoom_only_error_names_zoom_not_fireflies(monkeypatch):
    """A Zoom-only company told to reconnect its Fireflies API key has a dead
    end it cannot act on."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())

    def _boom(ctx, w):
        raise RuntimeError("zoom 500")

    monkeypatch.setattr(cd, "fetch_zoom_calls", _boom)
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    assert "couldn't reach Zoom" in p["answer"]
    assert "Fireflies" not in p["answer"]


def test_answer_zoom_only_empty_window_points_at_zoom(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [])
    p = cd.answer(enterprise_id="co", question="summarize customer calls from last week")
    assert "No customer calls" in p["answer"]
    assert "syncing to Zoom" in p["answer"]


def test_not_connected_message_offers_both_sources(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    assert "Fireflies" in p["answer"] and "Zoom" in p["answer"]


# ── fetch_zoom_calls: windows, hosts, transcripts ────────────────────────────

def test_zoom_window_wider_than_a_month_is_walked_in_sub_windows(monkeypatch):
    """Zoom SILENTLY CLAMPS a from/to span wider than a month — it does not
    error — so a naive 90-day request returns a month and reads as a quiet
    quarter. Assert the real date args, not merely that a fetch happened."""
    captured = _stub_zoom(monkeypatch, hosts=[{"id": "u1", "email": "a@x.com"}])
    window = cd.Window(
        since=datetime(2026, 3, 26, tzinfo=timezone.utc), until=NOW,
        label="the last 90 days",
    )

    cd.fetch_zoom_calls(_zoom_ctx(), window)

    spans = [(frm, to) for _uid, frm, to in captured["calls"]]
    # Three requests, not one wide one.
    assert len(spans) == 3
    assert ("2026-03-26", "2026-06-24") not in spans
    # Newest first, each inside Zoom's one-month cap, and contiguous end to end.
    for frm, to in spans:
        assert (date.fromisoformat(to) - date.fromisoformat(frm)).days <= 30
    assert spans[0][1] == "2026-06-24"          # reaches the window's end
    assert spans[-1][0] == "2026-03-26"         # and its start
    for newer, older in zip(spans, spans[1:]):
        assert newer[0] == older[1]             # no gap between windows


def test_zoom_window_inside_a_month_is_one_request(monkeypatch):
    captured = _stub_zoom(monkeypatch, hosts=[{"id": "u1", "email": "a@x.com"}])
    cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())
    assert len(captured["calls"]) == 1


def test_zoom_host_selection_is_honoured(monkeypatch):
    """A company that picked hosts in Settings gets exactly those hosts read —
    via the puller's own `_hosts`, deliberately NOT patched here, so the picker
    rule is exercised rather than assumed."""
    captured = _stub_zoom(monkeypatch)   # real _hosts
    ctx = _zoom_ctx(user_ids=["u1", "u2"],
                    user_names={"u1": "a@x.com", "u2": "b@x.com"})

    cd.fetch_zoom_calls(ctx, _recent_window())

    assert sorted({uid for uid, _f, _t in captured["calls"]}) == ["u1", "u2"]


def test_zoom_no_selection_reads_every_licensed_host(monkeypatch):
    """An empty selection means every LICENSED host — unlicensed accounts cannot
    record to the cloud at all, so reading them would spend a request to learn
    nothing."""
    import app.kg_ingest.pullers.zoom as zoom_puller

    monkeypatch.setattr(
        zoom_puller, "list_users",
        lambda token: ([
            {"id": "u1", "email": "a@x.com", "licensed": True},
            {"id": "u2", "email": "b@x.com", "licensed": False},
        ], False),
    )
    captured = _stub_zoom(monkeypatch)
    cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())
    assert {uid for uid, _f, _t in captured["calls"]} == {"u1"}


def test_zoom_recording_without_a_transcript_still_appears_with_a_note(monkeypatch):
    """Never silently dropped. The commonest cause is audio transcription being
    switched off in the customer's own Zoom account — a setting they can change
    — and a half-empty corpus presented as a complete one is the failure this
    whole path exists to avoid."""
    captured = _stub_zoom(
        monkeypatch,
        meetings_by_host={"u1": [_meeting("m-silent", topic="Globex sync", files=_NO_TRANSCRIPT_FILES)]},
        hosts=[{"id": "u1", "email": "a@x.com"}],
    )

    calls = cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())

    assert len(calls) == 1
    call = calls[0]
    assert call.title == "Globex sync" and call.quotes == []
    assert "No transcript available" in call.note
    assert "audio transcription being turned off" in call.note
    # And it reaches the corpus that way, rather than vanishing from it.
    assert "note: No transcript available" in call.render()
    assert not captured["downloads"]  # nothing to download, nothing attempted


def test_zoom_transcript_becomes_speaker_attributed_quotes(monkeypatch):
    _stub_zoom(
        monkeypatch,
        meetings_by_host={"u1": [_meeting("m1")]},
        hosts=[{"id": "u1", "email": "rep@ours.com"}],
    )
    calls = cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())

    call = calls[0]
    assert call.provider == "zoom" and call.note == ""
    assert {q["speaker"] for q in call.quotes} == {"Sam Lee", "Dana Fox"}
    assert call.quotes[0]["text"].startswith("We keep hitting the export limit")
    # Speakers come from the transcript, not a per-meeting participants call.
    assert call.participants == ["rep@ours.com", "Sam Lee", "Dana Fox"]
    # Zoom writes no summary of its own; left empty rather than echoing the
    # topic, which a model reads as "nothing happened on this call".
    assert call.overview == ""


def test_zoom_recording_outside_the_window_is_filtered(monkeypatch):
    """Zoom's from/to are DATES read in the account's timezone, so a correct
    request still returns calls from the day either side. Both sources must
    answer the same question or the merged counts are nobody's."""
    _stub_zoom(
        monkeypatch,
        meetings_by_host={"u1": [
            _meeting("in", start="2026-06-22T14:00:00Z"),
            _meeting("out", start="2026-05-02T14:00:00Z"),
            _meeting("undated", start=""),
        ]},
        hosts=[{"id": "u1", "email": "a@x.com"}],
    )
    calls = cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())
    # The out-of-window call is dropped; the UNREADABLE one is kept — an
    # unparseable timestamp is a reason to include and caveat, never to drop a
    # real call.
    assert {c.external_id for c in calls} == {"in", "undated"}


def test_zoom_recurring_meeting_is_not_double_counted(monkeypatch):
    """A recurring meeting surfaces in two adjacent windows; counting it twice
    would inflate every theme size the report derives."""
    _stub_zoom(
        monkeypatch,
        meetings_by_host={"u1": [_meeting("dupe"), _meeting("dupe")]},
        hosts=[{"id": "u1", "email": "a@x.com"}],
    )
    calls = cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())
    assert [c.external_id for c in calls] == ["dupe"]


def test_zoom_one_bad_host_is_skipped_not_fatal(monkeypatch):
    """A recording deleted between the listing and the read is a normal race on
    a live account."""
    def _list(token, user_id, *, frm, to, page_size, max_pages=1):
        if user_id == "u1":
            raise RuntimeError("host 404")
        return [_meeting("m-ok")]

    monkeypatch.setattr(cd, "list_user_recordings", _list)
    monkeypatch.setattr(cd, "fetch_transcript_text", lambda t, u: _VTT)
    monkeypatch.setattr(
        cd, "_zoom_hosts",
        lambda ctx: [{"id": "u1", "email": "a@x.com"}, {"id": "u2", "email": "b@x.com"}],
    )
    calls = cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())
    assert [c.external_id for c in calls] == ["m-ok"]


def test_zoom_every_host_failing_raises_rather_than_reporting_a_quiet_week(monkeypatch):
    """A revoked grant returning zero calls looks exactly like "nobody had any
    meetings". It must reach build_corpus as an error instead."""
    import pytest

    def _list(token, user_id, *, frm, to, page_size, max_pages=1):
        raise RuntimeError("zoom 500")

    monkeypatch.setattr(cd, "list_user_recordings", _list)
    monkeypatch.setattr(cd, "_zoom_hosts", lambda ctx: [{"id": "u1", "email": "a@x.com"}])
    with pytest.raises(RuntimeError, match="zoom 500"):
        cd.fetch_zoom_calls(_zoom_ctx(), _recent_window())


# ── Fireflies-only regression guards ─────────────────────────────────────────

def test_fireflies_only_corpus_is_unchanged_by_the_zoom_branch(monkeypatch):
    """The bar the router fix held itself to: a Fireflies-only company's corpus
    is byte-identical to what it was before the digest learned about Zoom. No
    source tag, no note line, no split disclosure."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    calls = [_call(1), _call(2)]
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)

    out = cd.build_corpus("co", cd.parse_window("calls last week", now=NOW))

    assert out.text == "\n\n".join(c.render() for c in calls)
    assert "source:" not in out.text and "note:" not in out.text
    assert out.failed_sources == [] and out.sources == ["Fireflies"]


def test_fireflies_only_fit_ladder_is_unaffected(monkeypatch):
    """_fit_corpus keeps every call and trims quotes — unchanged with the Zoom
    branch present but unconnected."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    calls = [_chatty_call(i) for i in range(30)]
    full = len("\n\n".join(c.render() for c in calls))
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", full // 3)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)

    out = cd.build_corpus("co", cd.parse_window("last 30 days calls", now=NOW))

    assert out.count == 30 and out.quote_cap is not None
    assert len(out.text) <= full // 3
    assert "source:" not in out.text


def test_fireflies_only_answer_omits_the_source_split(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert "=== CUSTOMER CALLS — last week" in captured["input"]
    assert "Fireflies)" not in captured["input"]   # no split disclosure
    assert "could not be reached" not in captured["input"]


# ── The knowledge graph as the OTHER half of the corpus ──────────────────────
#
# The reported defect: connecting Zoom or Fireflies flipped `has_call_source()`
# True, and `qa_agent`'s either/or then answered every voice-of-customer
# question from live calls ALONE — Slack, support tickets and every other synced
# source vanished from answers that used to include them. A user asked "what are
# customers feedback" and got three Zoom calls while Slack sat connected and
# populated. These cases pin the merge, its budgets, and its per-source
# degradation.
#
# The stub seam is `ask_runner._retrieve_kg_bundle` — deliberately NOT
# `cd.build_kg_context` — so the real `render_context_section` runs and these
# tests would fail if the rendering the KG-only path shares with this one moved
# underneath us.


def _kg_signal(content, *, doc="slack_channels", source_type="customer_voice",
               kind="pain"):
    """One retrieval-bundle signal, in `retrieval._signal_payload`'s shape.

    `doc` is what decides both dedupe and the coverage line's source name:
    a connector sync writes "<provider>-sync-batch-N", a corpus document (which
    is how Slack reaches the graph — slack_sync writes slack_channels.md) writes
    its own filename.
    """
    return {
        "signal_id": f"sig-{abs(hash(content)) % 10_000}",
        "content": content, "kind": kind, "source_type": source_type,
        "provenance": {"source": "extractor", "doc": doc},
        "theme": None, "confidence": 0.8, "rank": 1.0,
    }


def _stub_kg(monkeypatch, signals, *, themes=None):
    """Wire the KG half to fixtures at the retrieval seam."""
    import app.ask_runner as ask_runner

    bundle = {
        "signals": list(signals), "themes": list(themes or []),
        "decisions": [], "hypotheses": [], "outcomes": [],
        "kg_refs": [], "token_estimate": 100, "empty": False,
    }
    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", lambda eid, q: bundle)
    return bundle


def _stub_no_kg(monkeypatch):
    """An empty/unreadable graph — what `_retrieve_kg_bundle` returns for a
    tenant with no signal, and what every pre-merge test implicitly assumed."""
    import app.ask_runner as ask_runner

    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", lambda eid, q: None)


def test_calls_and_kg_both_reach_the_corpus(monkeypatch):
    """THE REGRESSION TEST FOR THE REPORTED BUG. Zoom connected and returning
    calls, Slack synced into the graph: the answer must be built from BOTH.
    Before the fix the Slack signal was not in the prompt at all."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(
        cd, "fetch_zoom_calls", lambda ctx, w: [_zoom_transcript(1)]
    )
    _stub_kg(monkeypatch, [
        _kg_signal("Customers in #support keep asking for SSO", doc="slack_channels"),
        _kg_signal("Export timeouts raised on 4 tickets", doc="jira-sync-batch-0"),
    ])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    prompt = captured["input"]
    # The live Zoom call is still there — the calls half is not traded away.
    assert "Zoom call 1" in prompt and 'Sam Lee: "zoom quote 1"' in prompt
    # …and so is the Slack signal that used to disappear the moment Zoom
    # was connected. This single assertion is the bug.
    assert "Customers in #support keep asking for SSO" in prompt
    assert "Export timeouts raised on 4 tickets" in prompt
    assert p["_skill"] == "voice-of-customer-report"


def test_reported_question_is_query_shaped_and_still_gets_the_kg(monkeypatch):
    """The reported question routes to QUERY mode, not the report pass
    (`is_voc_query` matches its what/customers shape). A merge wired only into
    the report path would have left the actual bug untouched."""
    from app.call_digest import is_voc_query

    question = "what are customers feedback"
    assert is_voc_query(question) is True     # the mode the real case takes

    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [_zoom_transcript(1)])
    _stub_kg(monkeypatch, [_kg_signal("Slack: onboarding is confusing")])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question=question)

    assert p["_skill_source"] == "voc-query"
    assert captured["purpose"] == "voc_query"
    assert "Slack: onboarding is confusing" in captured["input"]
    assert "Zoom call 1" in captured["input"]
    # The pointed answer discloses its basis too, not only the report does.
    assert "stored signal" in captured["input"]


def test_calls_only_company_is_unchanged_when_the_graph_is_empty(monkeypatch):
    """No KG signal → the prompt, the coverage line and the run line are exactly
    what they were before the merge existed."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_no_kg(monkeypatch)
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    label = cd.parse_window("summarize customer calls last week").label
    assert f"=== CUSTOMER CALLS — {label} (2 calls) ===" in captured["input"]
    assert "stored signal" not in captured["input"]
    assert "CONNECTED-SOURCE SIGNAL" not in captured["input"]
    assert p["_skill_action"] == f"Voice of customer · 2 calls · {label}"


def test_kg_only_company_gets_a_real_answer_not_a_dead_end(monkeypatch):
    """No call source connected at all, but the graph is populated. Before the
    fix this fell out of the digest entirely; now it is the merged path
    degrading to KG-only, and it answers."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    _stub_kg(monkeypatch, [_kg_signal("Billing confusion reported in #cs")])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert p["answer"].startswith("## Voice of customer")
    assert "no call source is connected" not in p["answer"]
    assert "Billing confusion reported in #cs" in captured["input"]
    # The banner is honest about the missing half rather than printing "0 calls".
    assert "CONNECTED-SOURCE SIGNAL" in captured["input"]
    assert "no call source connected" in captured["input"]
    assert "0 calls" not in captured["input"]


def test_neither_source_keeps_the_what_to_connect_message(monkeypatch):
    """Both halves empty → the pre-existing guidance, unchanged, and no spend."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    _stub_no_kg(monkeypatch)
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert "no call source is connected" in p["answer"]
    assert "Fireflies" in p["answer"] and "Zoom" in p["answer"]
    assert captured == {}


def test_unreachable_call_source_still_answers_from_the_graph_and_says_so(monkeypatch):
    """An expired Zoom grant is a reason to CAVEAT an answer, not to withhold
    one built from the ticket queue. The disclosure is the load-bearing half:
    a KG answer silently standing in for a dead connector is the failure this
    whole path exists to avoid."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())

    def _boom(ctx, w):
        raise RuntimeError("zoom 401")

    monkeypatch.setattr(cd, "fetch_zoom_calls", _boom)
    _stub_kg(monkeypatch, [_kg_signal("Support backlog is growing")])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert p["answer"].startswith("## Voice of customer")   # answered, not refused
    assert "Support backlog is growing" in captured["input"]
    assert "Zoom could not be reached" in captured["input"]


def test_empty_window_still_answers_from_the_graph(monkeypatch):
    """Zoom connected and healthy but the window is quiet. The graph still has
    signal, so the answer runs and the banner says the window was empty."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [])
    _stub_kg(monkeypatch, [_kg_signal("Churn risk flagged on two accounts")])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls from last week")

    assert p["answer"].startswith("## Voice of customer")
    assert "No customer calls" not in p["answer"]
    assert "no calls or uploaded documents found in" in captured["input"]
    assert "Churn risk flagged on two accounts" in captured["input"]


def test_a_call_synced_into_the_graph_is_not_counted_twice(monkeypatch):
    """Zoom calls sync INTO the graph as well as being fetched live, so one
    conversation can arrive twice — once as a transcript, once as a distilled
    signal — and a model reads that as two accounts corroborating each other.
    With live calls in hand the distilled copies are dropped; the signal from
    every OTHER source stays."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [_zoom_transcript(1)])
    _stub_kg(monkeypatch, [
        _kg_signal("Export limit hit weekly", doc="zoom-sync-batch-0"),
        _kg_signal("Export limit hit weekly", doc="fireflies-sync-batch-2"),
        _kg_signal("Same ask raised in #support", doc="slack_channels"),
    ])
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize customer calls last week")

    prompt = captured["input"]
    # The live transcript is the richer copy and it is the one that survives.
    assert "Zoom call 1" in prompt
    assert "Export limit hit weekly" not in prompt
    # Non-call sources are untouched — dropping those would be the original bug.
    assert "Same ask raised in #support" in prompt
    # And the exclusion is disclosed rather than silent.
    assert "2 stored signals distilled from the same call sources were excluded" in prompt


def test_call_derived_signal_is_kept_when_no_live_call_came_back(monkeypatch):
    """The mirror of the dedupe rule. With no live calls, the graph's distilled
    copies are the ONLY record of those conversations — dropping them would
    recreate the reported bug pointing the other way."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: _zoom_ctx())
    monkeypatch.setattr(cd, "fetch_zoom_calls", lambda ctx, w: [])
    _stub_kg(monkeypatch, [
        _kg_signal("Export limit hit weekly", doc="zoom-sync-batch-0"),
    ])
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize customer calls from last week")

    assert "Export limit hit weekly" in captured["input"]
    assert "were excluded" not in captured["input"]


def test_neither_half_can_starve_the_other_to_zero(monkeypatch):
    """The budgets are separate constants precisely so this cannot happen: a
    company with 200 chatty calls must still show graph signal, and a company
    with a huge graph must still show its calls."""
    calls = [_chatty_call(i) for i in range(30)]
    full_calls = len("\n\n".join(c.render() for c in calls))
    big_kg = [_kg_signal(f"signal {i} — {'y' * 400}", doc="slack_channels")
              for i in range(200)]

    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)
    # Squeeze BOTH budgets hard at once.
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", full_calls // 4)
    monkeypatch.setattr(cd, "_KG_CHAR_BUDGET", 3_000)
    _stub_kg(monkeypatch, big_kg)
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize calls from the last 30 days")
    prompt = captured["input"]

    # Every call still represented (the fit ladder trims quotes, not calls)…
    for i in range(30):
        assert f"Call {i}" in prompt
    # …and the graph is still there, trimmed to its own ceiling rather than
    # evicted by the calls.
    assert "signal 0" in prompt
    assert "stored signal truncated for space" in prompt
    # Each half stayed inside ITS OWN budget — neither borrowed from the other.
    assert len(prompt) < full_calls // 4 + 3_000 + 5_000


def test_coverage_line_names_the_graph_sources_it_read(monkeypatch):
    """The single most important observable: a user has to be able to read one
    line and tell whether Slack was actually consulted."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    _stub_kg(monkeypatch, [
        _kg_signal("a", doc="slack_channels"),
        _kg_signal("b", doc="jira-sync-batch-0"),
        _kg_signal("c", doc="hubspot-sync-batch-1"),
    ])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    banner = captured["input"]
    assert "3 stored signals from your other connected sources" in banner
    assert "slack_channels" in banner and "jira" in banner and "hubspot" in banner
    # Stated as unwindowed, because retrieval ranks by relevance and does not
    # filter to the asked window — a count dated into it would be a lie.
    assert "NOT limited to this window" in banner
    assert "CUSTOMER CALLS + CONNECTED-SOURCE SIGNAL" in banner
    # The run line under the answer carries it too.
    assert "3 stored signals" in p["_skill_action"]


def test_a_broken_graph_read_never_costs_the_calls(monkeypatch):
    """Per-source isolation, extended to the new half: the graph failing must
    cost the answer its calls no more than a dead Zoom grant costs it the
    graph."""
    import app.ask_runner as ask_runner

    def _boom(eid, q):
        raise RuntimeError("pgvector down")

    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", _boom)
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    captured = _stub_voc_pass(monkeypatch)

    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")

    assert p["answer"].startswith("## Voice of customer")
    assert "Call 1" in captured["input"]
    assert "stored signal" not in captured["input"]


def test_kg_context_drops_the_section_when_dedupe_empties_it(monkeypatch):
    """A graph holding nothing BUT call-derived signal, with the live calls
    already in hand, must not render an empty header the model then treats as
    a source that was consulted and found silent."""
    _stub_kg(monkeypatch, [
        _kg_signal("x", doc="zoom-sync-batch-0"),
        _kg_signal("y", doc="fireflies-sync-batch-0"),
    ])
    kg = cd.build_kg_context("co", "what are customers saying", live_calls=True)
    assert kg.present is False
    assert kg.deduped == 2 and kg.signal_count == 0


def test_kg_source_label_reads_a_sync_batch_as_its_provider():
    assert cd._kg_source_label(_kg_signal("a", doc="jira-sync-batch-12")) == "jira"
    assert cd._kg_source_label(_kg_signal("a", doc="slack_channels")) == "slack_channels"
    # No doc at all (agent findings, web research) → the signal's source_type.
    bare = _kg_signal("a")
    bare["provenance"] = {}
    assert cd._kg_source_label(bare) == "customer_voice"


def test_call_transcript_render_defaults_stay_fireflies_shaped():
    """The shared shape gained `provider` and `note` as DEFAULTED fields; a
    record built the old way must render exactly as it always did."""
    c = CallTranscript(external_id="f1", title="Acme", date="2026-06-20",
                       participants=["a@x.com"], overview="o")
    assert c.provider == "fireflies" and c.note == ""
    assert c.render() == (
        "## Call: Acme\n"
        "date: 2026-06-20 · participants: a@x.com\n"
        "summary: o"
    )


# ── answer/key_points field contract ─────────────────────────────────────────
# Both VoC prompts drive `_ASK_RESPONSE_SCHEMA`, whose `key_points` is
# documented as a "Short bullet summary of the answer" — i.e. redundant. Only
# `answer` is rendered. Observed live on staging (asks 1079/1080): with no
# field contract stated, the model wrote a lead-in sentence into `answer`
# ("...here is the product-related feedback — grouped by theme:") and put all
# nine/ten findings into `key_points`, so the user saw a colon and nothing
# else — with status "ready" and error None. These are property tests on the
# actual wording, not presence checks, because a vaguer instruction would let
# the same split back in.

def test_query_system_states_the_answer_field_contract():
    s = cd._QUERY_SYSTEM
    assert "THE WHOLE ANSWER GOES IN `answer`" in s
    assert "only field rendered" in s
    assert "redundant summary" in s


def test_report_system_states_the_answer_field_contract():
    s = cd._REPORT_SYSTEM
    assert "THE WHOLE REPORT GOES IN `answer`" in s
    assert "only field rendered" in s
    assert "redundant summary" in s


def test_both_voc_prompts_forbid_key_points_as_the_content_home():
    """The specific failure mode, named in both prompts: `answer` as a lead-in
    whose content lives in `key_points`."""
    for name, s in (("_QUERY_SYSTEM", cd._QUERY_SYSTEM),
                    ("_REPORT_SYSTEM", cd._REPORT_SYSTEM)):
        assert "lead-in" in s, f"{name} does not name the lead-in failure"
        assert "never the continuation of a sentence" in s, name


def test_voc_prompts_do_not_invite_splitting_content_across_fields():
    for name, s in (("_QUERY_SYSTEM", cd._QUERY_SYSTEM),
                    ("_REPORT_SYSTEM", cd._REPORT_SYSTEM)):
        low = s.lower()
        for phrase in ("split the", "put the findings in key_points",
                       "list them in key_points"):
            assert phrase not in low, f"{name} invites a split: {phrase!r}"


def test_voc_prompt_lengths_within_bounds():
    assert 1200 <= len(cd._QUERY_SYSTEM) <= 3000
    assert 2500 <= len(cd._REPORT_SYSTEM) <= 5000

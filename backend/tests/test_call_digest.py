"""On-demand call-digest service — window parsing, intent, corpus, answer branches.

No network/LLM/DB: the Fireflies fetch, the Zoom context/listing/transcript
reads, the key load, and gateway llm_call are all patched in the call_digest
namespace.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

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


# ── a blank reply is never an acceptable answer ──────────────────────────────
#
# A schema'd call that runs out of output tokens returns a truncated object.
# Both digest passes handed that straight through, the Ask job was stamped
# `ready` with `_skill_action` and `citations` and no `answer`, and chat
# rendered NOTHING — the product looked broken and said nothing about why
# (reported 2026-08-16, after a widened corpus tipped a 3000-token ceiling).


class _Res:
    def __init__(self, output, stop_reason="end_turn"):
        self.output = output
        self.stop_reason = stop_reason


def test_a_truncated_model_reply_becomes_an_explanation_not_a_blank():
    out = cd._ensure_answer({}, _Res({}, stop_reason="max_tokens"), _window(35))
    assert out["answer"], "an empty payload survived as a blank reply"
    assert "narrower" in out["answer"]
    assert "the last 35 days" in out["answer"]


def test_an_empty_reply_for_any_other_reason_still_explains():
    out = cd._ensure_answer({}, _Res({}, stop_reason="end_turn"), _window(35))
    assert "couldn't compose an answer" in out["answer"]


def test_a_whitespace_only_answer_counts_as_empty():
    out = cd._ensure_answer({"answer": "   "}, _Res({}), _window(35))
    assert out["answer"].strip()


def test_a_real_answer_is_passed_through_untouched():
    payload = {"answer": "37 calls, here they are", "key_points": ["a"],
               "citations": [], "confidence": 0.9, "unanswered": ""}
    assert cd._ensure_answer(dict(payload), _Res(payload), _window(35)) == payload


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


def test_report_path_phases_name_gather_then_write_in_order(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch)
    phases: list[str] = []
    cd.answer(enterprise_id="co",
              question="summarize customer calls last week",
              on_phase=phases.append)
    # GATHERING (the live corpus/KG/Slack fetch) then WRITING (the synthesis).
    assert phases == [
        "Gathering the latest information…",
        "Writing your report…",
    ]


def test_query_path_emits_gathering_but_not_the_writing_leg(monkeypatch):
    # A query-shaped ask returns from _answer_query BEFORE the report synthesis,
    # so it narrates only the gather leg.
    monkeypatch.setattr(
        cd, "build_corpus",
        lambda cid, window: cd.DigestCorpus(status="ok", window=window,
                                            text="=== CALLS ==="))
    monkeypatch.setattr(
        cd, "_answer_query",
        lambda **kw: {"answer": "counts", "_skill_source": "voc-query"})
    phases: list[str] = []
    cd.answer(enterprise_id="co",
              question="did complaints about exports increase this week?",
              on_phase=phases.append)
    assert phases == ["Gathering the latest information…"]


def test_voc_query_llm_call_uses_long_output_without_a_delta_sink(monkeypatch):
    # The query branch used to run on the gateway's default 120s non-streamed
    # path and die silently on a wide corpus / table-shaped ask. It now asks
    # for the long-timeout streaming TRANSPORT (`long_output=True`) but never
    # wires a client-visible preview sink — `on_delta` stays unset, so no
    # partial text escapes (see app.llm._create_with_retries: a stream with
    # both callbacks unset drains silently to the final message).
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    captured = _stub_voc_pass(monkeypatch, answer="3 accounts asked for exports")
    p = cd.answer(
        enterprise_id="co",
        question="did complaints about exports increase this week?",
    )
    assert p["_skill_source"] == "voc-query"
    assert captured["purpose"] == "voc_query"
    assert captured["long_output"] is True
    assert captured.get("on_delta") is None


def test_query_path_emits_analyzing_after_gathering(monkeypatch):
    # The query branch's one real leg once GATHERING (the live fetch) has
    # finished — without it a slow pointed answer (now long_output=True and
    # genuinely allowed to run past 120s) looked like a dead spinner with no
    # narration at all past the fetch.
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch, answer="3 accounts asked for exports")
    phases: list[str] = []
    p = cd.answer(
        enterprise_id="co",
        question="did complaints about exports increase this week?",
        on_phase=phases.append,
    )
    assert p["_skill_source"] == "voc-query"
    assert phases == [
        "Gathering the latest information…",
        "Analyzing the findings…",
    ]


def test_query_path_runs_unchanged_without_a_phase_sink(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch, answer="3 accounts asked for exports")
    with_sink = cd.answer(
        enterprise_id="co",
        question="did complaints about exports increase this week?",
        on_phase=lambda _l: None,
    )
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch, answer="3 accounts asked for exports")
    without = cd.answer(
        enterprise_id="co",
        question="did complaints about exports increase this week?",
    )
    assert with_sink["answer"] == without["answer"]
    assert with_sink["_skill_source"] == without["_skill_source"] == "voc-query"


def test_report_path_runs_unchanged_without_a_phase_sink(monkeypatch):
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch)
    with_sink = cd.answer(enterprise_id="co", question="summarize calls last week",
                          on_phase=lambda _l: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _stub_voc_pass(monkeypatch)
    without = cd.answer(enterprise_id="co", question="summarize calls last week")
    assert with_sink["answer"] == without["answer"]
    assert with_sink["_skill_source"] == without["_skill_source"]


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


# ── Map-reduce count engine — eligibility ────────────────────────────────────
# `is_mapreducible_count` is a strict subset of `is_voc_query`: an
# aggregate/enumeration shape AND a per-item content predicate to classify
# against. Comparative/over-time, single-subject, and report-shaped asks stay
# on the existing query/report paths.

def test_is_mapreducible_count_davids_phrasing():
    from app.call_digest import is_mapreducible_count

    assert is_mapreducible_count(
        "how many customer calls did I have in the last 30 days that had "
        "asked for product features or raised product issues."
    )


def test_is_mapreducible_count_which_customers_and_count_shapes():
    from app.call_digest import is_mapreducible_count

    positives = [
        "which customers complained about pricing this month",
        "how many customers raised billing issues this month",
        "which accounts complained about latency",
        "how many customers asked about the mobile app",
    ]
    for q in positives:
        assert is_mapreducible_count(q), f"expected eligible: {q!r}"


def test_is_mapreducible_count_excludes_comparative_over_time():
    from app.call_digest import is_mapreducible_count

    negatives = [
        "did complaints about exports increase this week",
        "are export complaints getting worse compared to last week",
        "how many customers raised billing issues this week vs last week",
    ]
    for q in negatives:
        assert not is_mapreducible_count(q), f"expected NOT eligible: {q!r}"


def test_is_mapreducible_count_excludes_single_subject():
    from app.call_digest import is_mapreducible_count

    assert not is_mapreducible_count(
        "what did Cascade Health say about the dashboard"
    )


def test_is_mapreducible_count_excludes_report_shaped():
    from app.call_digest import is_mapreducible_count

    negatives = [
        "give me the summary of customer feedback from today",
        "summarize the customer calls from last week",
        "voice of customer report for last month",
        "what are the themes from this week's calls",
        "recap yesterday's customer meetings",
    ]
    for q in negatives:
        assert not is_mapreducible_count(q), f"expected NOT eligible: {q!r}"


def test_is_mapreducible_count_excludes_bare_headcount_with_no_predicate():
    from app.call_digest import is_mapreducible_count

    # A pure entity count with no per-item content filter has nothing for the
    # map step to classify against.
    assert not is_mapreducible_count("how many customers do we have")


# ── Map-reduce count engine — interception in `answer()` ─────────────────────
# Gated by `settings.voc_count_engine_enabled` (default OFF) AND
# `is_mapreducible_count`. Any engine exception falls through to the existing,
# unchanged `_answer_query` path — never a dead end.

def test_count_engine_off_never_runs_query_path_taken(monkeypatch):
    import app.corpus_mapreduce as cmr

    monkeypatch.setattr(cd.settings, "voc_count_engine_enabled", False)
    monkeypatch.setattr(
        cd, "build_corpus",
        lambda cid, window: cd.DigestCorpus(status="ok", window=window,
                                            calls=[_call(1)], text="=== CALLS ==="))

    def _must_not_run(*a, **k):
        raise AssertionError("the count engine must not run when the flag is off")

    monkeypatch.setattr(cmr, "run", _must_not_run)
    monkeypatch.setattr(
        cd, "_answer_query",
        lambda **kw: {"answer": "from query path", "_skill_source": "voc-query"})

    out = cd.answer(enterprise_id="co",
                    question="how many customers raised billing issues this month")
    assert out["_skill_source"] == "voc-query"
    assert out["answer"] == "from query path"


def test_count_engine_on_and_eligible_runs_engine_and_returns_assembled_answer(monkeypatch):
    import app.corpus_mapreduce as cmr

    calls_list = [_call(1), _call(2)]
    monkeypatch.setattr(cd.settings, "voc_count_engine_enabled", True)
    monkeypatch.setattr(
        cd, "build_corpus",
        lambda cid, window: cd.DigestCorpus(status="ok", window=window,
                                            calls=calls_list, text="=== CALLS ==="))

    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "asked for X"},
                           total_items=2, unclassified_ids=[])
    captured: dict = {}

    def _fake_run(spec, **kw):
        captured["spec"] = spec
        captured.update(kw)
        return eng

    monkeypatch.setattr(cmr, "run", _fake_run)

    def _query_must_not_run(**kw):
        raise AssertionError("the query path must not run when the engine succeeds")

    monkeypatch.setattr(cd, "_answer_query", _query_must_not_run)

    out = cd.answer(enterprise_id="co",
                    question="how many customers raised billing issues this month")
    assert out["_skill_source"] == "voc-count-engine"
    assert "1 of 2 calls" in out["answer"]
    assert captured["spec"] is cd.VOC_CALLS_SPEC
    assert captured["items"] is calls_list
    assert captured["enterprise_id"] == "co"


def test_count_engine_exception_falls_through_to_query_path(monkeypatch, caplog):
    import logging

    import app.corpus_mapreduce as cmr

    monkeypatch.setattr(cd.settings, "voc_count_engine_enabled", True)
    monkeypatch.setattr(
        cd, "build_corpus",
        lambda cid, window: cd.DigestCorpus(status="ok", window=window,
                                            calls=[_call(1)], text="=== CALLS ==="))

    def _boom(*a, **k):
        raise RuntimeError("engine blew up")

    monkeypatch.setattr(cmr, "run", _boom)
    monkeypatch.setattr(
        cd, "_answer_query",
        lambda **kw: {"answer": "fell through to query path",
                      "_skill_source": "voc-query"})

    with caplog.at_level(logging.ERROR, logger="app.call_digest"):
        out = cd.answer(enterprise_id="co",
                        question="how many customers raised billing issues this month")
    # No user-facing error — the fallback answer, not an exception, comes back.
    assert out["_skill_source"] == "voc-query"
    assert out["answer"] == "fell through to query path"
    assert "voc count engine failed" in caplog.text


def _count_corpus(window, calls=None) -> "cd.DigestCorpus":
    """A minimal `DigestCorpus` for `_assemble_count_answer`'s own tests —
    `corpus.calls`/`corpus.total`/`corpus.failed_sources` is what the
    per-hit label and coverage caveat are computed against."""
    return cd.DigestCorpus(status="ok", window=window,
                           calls=calls if calls is not None else [_call(1), _call(2)],
                           text="=== CALLS ===")


def test_assemble_count_answer_matches_query_path_response_contract():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=2, hit_ids=["c1", "c2"],
                           reasons={"c1": "asked for X", "c2": "raised bug Y"},
                           total_items=5, unclassified_ids=["c3"])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    for key in ("answer", "key_points", "citations", "confidence", "unanswered",
               "_skill", "_skill_action", "_skill_source", "_report"):
        assert key in out
    assert isinstance(out["answer"], str) and out["answer"]
    assert isinstance(out["key_points"], list)
    assert isinstance(out["citations"], list)
    assert all(set(c) == {"source", "evidence"} for c in out["citations"])
    assert isinstance(out["confidence"], float)
    assert isinstance(out["unanswered"], str) and "c3" in out["unanswered"]
    assert out["_skill"] == cd._VOC_SKILL
    assert out["_skill_source"] == "voc-count-engine"
    assert out["_report"] is False


# ── count answer — no inherited banner, opens on the count line ─────────────

def test_count_answer_never_carries_the_source_banner_or_quote_sampling_clause():
    """The `=== CUSTOMER CALLS — … ===` banner (and its "verbatim quotes
    sampled" clause) is `_coverage_line`'s — built for the query/report
    synthesis passes, which actually read corpus text. A count answer never
    renders any call text, so that banner must never appear here."""
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "asked for X"},
                           total_items=2, unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    assert "===" not in out["answer"]
    assert "CUSTOMER CALLS" not in out["answer"]
    assert "verbatim quotes sampled" not in out["answer"]
    assert "distilled summaries are complete" not in out["answer"]


def test_count_answer_opens_on_the_count_line_for_a_healthy_corpus():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "asked for X"},
                           total_items=2, unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    first_line = out["answer"].split("\n", 1)[0]
    assert first_line == "1 of 2 calls in last 7 days matched."


def test_count_answer_appends_a_truncation_caveat_only_when_the_corpus_was_cut():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=0, hit_ids=[], reasons={}, total_items=2,
                           unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    truncated = cd.DigestCorpus(status="ok", window=window, calls=[_call(1), _call(2)],
                                text="=== CALLS ===", total=9)
    out = cd._assemble_count_answer(eng, window=window, corpus=truncated)
    assert "most recent 2 of 9 calls" in out["answer"]
    assert "older calls omitted for space" in out["answer"]
    # And a healthy (untruncated) corpus states no such caveat at all.
    healthy = cd.DigestCorpus(status="ok", window=window, calls=[_call(1), _call(2)],
                              text="=== CALLS ===", total=2)
    out_healthy = cd._assemble_count_answer(eng, window=window, corpus=healthy)
    assert "omitted for space" not in out_healthy["answer"]
    assert "Note:" not in out_healthy["answer"]


def test_count_answer_appends_a_failed_source_caveat_when_present():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=0, hit_ids=[], reasons={}, total_items=1,
                           unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    corpus = cd.DigestCorpus(status="ok", window=window, calls=[_call(1)],
                             text="=== CALLS ===", total=1, failed_sources=["Zoom"])
    out = cd._assemble_count_answer(eng, window=window, corpus=corpus)
    assert "Zoom" in out["answer"]
    assert "could not be reached" in out["answer"]


def test_count_answer_per_hit_line_uses_render_label_not_a_customer_name_prefix():
    """No "Customer (Name)" prefix, no verbatim quote — the per-hit line is
    `eng.labels`' `render_label` output (date · account — title, see
    `VOC_CALLS_SPEC.render_label`) + the model's (now name/quote-free)
    reason, NOT a lesser date-title-only label and NOT the raw item id. This
    is a presentation-layer pin; the reason text itself is whatever
    `eng.reasons` carries (pinned separately by the rubric tests)."""
    import app.corpus_mapreduce as cmr

    label = cd.VOC_CALLS_SPEC.render_label(_call(1))
    eng = cmr.EngineResult(
        count=1, hit_ids=["c1"], reasons={"c1": "asked for SSO support"},
        total_items=1, unclassified_ids=[], labels={"c1": label},
    )
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window,
                                    corpus=_count_corpus(window, calls=[_call(1)]))
    assert f"- {label}: asked for SSO support" in out["answer"]
    assert "Customer (" not in out["answer"]
    assert "c1:" not in out["answer"]


def test_assemble_count_answer_is_never_a_report():
    """`_report` is explicit, not merely absent: `app.report_capture.
    is_report_payload` already reads a missing key as falsy, but this states
    on the payload itself that a count answer is an inline chat reply, never
    a saved report document."""
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "asked for X"},
                           total_items=1)
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    assert out["_report"] is False

    from app import report_capture
    assert report_capture.is_report_payload(out) is False


def test_assemble_count_answer_renders_the_friendly_label_not_the_raw_id():
    """The count answer's evidence lines and citations name each hit by
    `eng.labels` (`spec.render_label`'s output), never the raw provider id —
    the bug this fix exists for: an `external_id` ULID standing in for a
    human-friendly reference."""
    import app.corpus_mapreduce as cmr

    raw_id = "01KYHTZG5WZRKNRX0SJQTW9WVW"
    eng = cmr.EngineResult(
        count=1, hit_ids=[raw_id], reasons={raw_id: "asked for exports"},
        total_items=1, labels={raw_id: "2026-08-25 · Nimbusco — Renewal call"},
    )
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    assert raw_id not in out["answer"]
    assert "2026-08-25 · Nimbusco — Renewal call" in out["answer"]
    assert out["citations"] == [
        {"source": "2026-08-25 · Nimbusco — Renewal call",
         "evidence": "asked for exports"},
    ]


def test_assemble_count_answer_falls_back_to_the_raw_id_when_unlabelled():
    """A hit `run()` never labelled (a caller-constructed `EngineResult` with
    no `labels`, e.g. a legacy/degraded call) still renders — the raw id,
    not a crash or a blank line."""
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "asked for X"},
                           total_items=1)  # labels defaults to {}
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    assert "c1" in out["answer"]
    assert out["citations"] == [{"source": "c1", "evidence": "asked for X"}]


# ── render_label / phase_label — VOC_CALLS_SPEC ──────────────────────────────

def test_voc_calls_spec_render_label_is_date_account_title():
    """`VOC_CALLS_SPEC.render_label` matches
    `app.call_index.IndexedCall.render()`'s "date · account — title" shape —
    the SAME listing format a "which calls" answer already shows — reused
    rather than reinvented."""
    # Two Nimbusco participants outnumber the one Sprntly rep, so
    # `derive_account`'s most-common-domain tie-break picks Nimbusco
    # deterministically rather than depending on dict/set iteration order.
    call = CallTranscript(
        external_id="c1", title="Renewal call", date="2026-08-25T10:00:00",
        participants=["jane@nimbusco.com", "amit@nimbusco.com", "rep@sprntly.ai"],
    )
    assert cd.VOC_CALLS_SPEC.render_label(call) == "2026-08-25 · Nimbusco — Renewal call"


def test_voc_calls_spec_render_label_omits_account_when_undeterminable():
    """An internal-only call (or every participant on a generic domain) omits
    the account segment rather than guessing — same as
    `IndexedCall.render()` when `account` is None."""
    call = CallTranscript(
        external_id="c1", title="Internal standup", date="2026-08-25",
        participants=["a@gmail.com", "b@gmail.com"],
    )
    assert cd.VOC_CALLS_SPEC.render_label(call) == "2026-08-25 — Internal standup"


def test_voc_calls_spec_render_label_untitled_call():
    call = CallTranscript(external_id="c1", title="", date="2026-08-25",
                          participants=["jane@nimbusco.com"])
    assert cd.VOC_CALLS_SPEC.render_label(call) == "2026-08-25 · Nimbusco — (untitled)"


def test_voc_calls_spec_phase_label_is_not_a_reportphase_value():
    """The count engine's progress phrase names the domain and is never the
    shared report vocabulary — see `app.chat_intent._is_report_pipeline`'s
    carve-out, which depends on a count answer never carrying a `ReportPhase`
    signal."""
    from app.report_phases import ReportPhase

    assert cd.VOC_CALLS_SPEC.phase_label == "Analyzing your calls…"
    assert cd.VOC_CALLS_SPEC.phase_label not in {p.value for p in ReportPhase}


# ── map_model — VOC_CALLS_SPEC uses the Sonnet constant ──────────────────────

def test_voc_calls_spec_map_model_is_the_sonnet_constant():
    """Real-corpus accuracy verification found Haiku plateaus at precision
    0.71-0.78 / recall 0.42-0.58 even on the patched rubric — Sonnet cleared
    it (0.90-1.00 / 0.75-0.83). `VOC_CALLS_SPEC.map_model` must be the SAME
    Sonnet constant this module's own synthesis calls use, never a
    second, independently-drifting copy of the model string."""
    assert cd.VOC_CALLS_SPEC.map_model == cd.ANSWER_MODEL
    assert cd.VOC_CALLS_SPEC.map_model == "claude-sonnet-4-6"


# ── deterministic grounding — reused call_index prefilter ───────────────────
# `_voc_count_prefilter` reuses `app.call_index._own_domains` +
# `app.call_index.derive_account` (read-only, the same primitive
# `IndexedCall.account` is built from) to (1) drop fully-internal calls
# before the map pass ever runs and (2) annotate every surviving call's
# participant sides for the classify prompt. See EXISTING-GROUNDING-REUSE.md
# for why these two, specifically, are the reusable primitives.

def test_prefilter_drops_fully_internal_call_from_the_classification_pool(monkeypatch):
    """A call with no external customer/prospect participant can never be a
    hit — `derive_account` returns None for it, and `_voc_count_prefilter`
    excludes it before the map pass ever sees it (condition #2,
    "EXTERNAL PARTICIPANT", of `_VOC_BASE_DISCIPLINE`, enforced as a hard
    structural guard rather than a prompt instruction)."""
    monkeypatch.setattr(
        "app.db.drip.list_members_with_email",
        lambda cid: [{"email": "rep@sprntly.ai"}], raising=False,
    )
    internal = CallTranscript(
        external_id="c-internal", title="Standup", date="2026-08-20",
        participants=["rep@sprntly.ai", "rep2@sprntly.ai"],
    )
    external = CallTranscript(
        external_id="c-external", title="QBR", date="2026-08-21",
        participants=["rep@sprntly.ai", "jane@nimbusco.com"],
    )
    pool = cd._voc_count_prefilter([internal, external], "co")
    kept_ids = {cd.VOC_CALLS_SPEC.item_id(it) for it in pool}
    assert kept_ids == {"c-external"}


def test_prefilter_wraps_surviving_calls_with_own_domains_and_account(monkeypatch):
    monkeypatch.setattr(
        "app.db.drip.list_members_with_email",
        lambda cid: [{"email": "rep@sprntly.ai"}], raising=False,
    )
    calls = [
        CallTranscript(
            external_id="c1", title="QBR", date="2026-08-21",
            participants=["rep@sprntly.ai", "jane@nimbusco.com"],
        ),
    ]
    pool = cd._voc_count_prefilter(calls, "co")
    assert len(pool) == 1
    wrapped = pool[0]
    assert isinstance(wrapped, cd._VocAnnotatedCall)
    assert wrapped.account == "Nimbusco"
    assert "sprntly.ai" in wrapped.own_domains


def test_engine_run_never_shows_the_internal_call_id_to_the_model_and_never_counts_it(monkeypatch):
    """End-to-end through the real engine (`corpus_mapreduce.run`), not just
    the prefilter in isolation: the internal call's id must never appear in
    a batch's rendered prompt at all, and `total_items` must stay the FULL
    window count (2) even though only the external call was ever
    classifiable."""
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(
        "app.db.drip.list_members_with_email",
        lambda cid: [{"email": "rep@sprntly.ai"}], raising=False,
    )
    internal = CallTranscript(
        external_id="c-internal", title="Standup", date="2026-08-20",
        participants=["rep@sprntly.ai", "rep2@sprntly.ai"],
    )
    external = CallTranscript(
        external_id="c-external", title="QBR", date="2026-08-21",
        participants=["rep@sprntly.ai", "jane@nimbusco.com"],
    )
    seen_ids: set[str] = set()

    def _fake_llm(**kw):
        import re
        ids = re.findall(r'<item id="([^"]+)">', kw["input"])
        seen_ids.update(ids)
        return SimpleNamespace(
            output={"verdicts": {i: {"hit": True, "reason": "hit"} for i in ids}},
            stop_reason="end_turn",
        )

    monkeypatch.setattr(gateway_mod, "llm_call", _fake_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many calls raised X",
        window=SimpleNamespace(label="last 7 days"), items=[internal, external],
    )
    assert "c-internal" not in seen_ids
    assert "c-internal" not in eng.hit_ids
    assert eng.total_items == 2  # denominator stays the window's real total


def test_render_item_annotates_participant_sides_for_the_classifier(monkeypatch):
    """The classify prompt for a prefiltered call carries the deterministic
    participant-side map — the server fact a rep-vs-customer misattribution
    bug needs, not a flat unattributed participants line."""
    monkeypatch.setattr(
        "app.db.drip.list_members_with_email",
        lambda cid: [{"email": "jordan@sprntly.ai"}], raising=False,
    )
    call = CallTranscript(
        external_id="c1", title="Renewal", date="2026-08-20",
        participants=["jordan@sprntly.ai", "priya@nimbusco.com"],
    )
    pool = cd._voc_count_prefilter([call], "co")
    rendered = cd.VOC_CALLS_SPEC.render_item(pool[0])
    assert "company-side (never the customer): jordan@sprntly.ai" in rendered
    assert "external customer/prospect (account: Nimbusco): priya@nimbusco.com" in rendered


def test_render_item_on_a_bare_call_bypassing_the_prefilter_carries_no_annotation():
    """A direct `render_item` call on a bare `CallTranscript` (no prefilter
    run) renders exactly as before this change — no participant-side line,
    since there is no server-computed fact to show."""
    call = CallTranscript(
        external_id="c1", title="Renewal", date="2026-08-20",
        participants=["jordan@sprntly.ai", "priya@nimbusco.com"],
    )
    rendered = cd.VOC_CALLS_SPEC.render_item(call)
    assert rendered == call.render()
    assert "company-side" not in rendered


def test_jordan_kim_case_the_classify_prompt_lets_a_faithful_verdict_reject_the_rep_raised_ask(monkeypatch):
    """The exact bug this ticket exists for: a rep on the company's own
    domain must not be attributable as the customer. This proves WIRING —
    the deterministic fact reaches the prompt, and the engine faithfully
    carries a hit=false verdict through when the classifier honours it — not
    that the live model reasons correctly (that gate is the real-LLM
    re-validation, run separately; see the build report)."""
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(
        "app.db.drip.list_members_with_email",
        lambda cid: [{"email": "jordan@sprntly.ai"}], raising=False,
    )
    call = CallTranscript(
        external_id="c1", title="Renewal", date="2026-08-20",
        participants=["jordan@sprntly.ai", "priya@nimbusco.com"],
        overview="Jordan walked Priya through the new SSO capability.",
    )
    captured: dict = {}

    def _capture_llm(**kw):
        captured["input"] = kw["input"]
        # Stands in for a faithful Sonnet classify: the annotation says
        # Jordan is company-side, so the ask is rep-raised -> hit=false.
        return SimpleNamespace(
            output={"verdicts": {"c1": {"hit": False, "reason": ""}}},
            stop_reason="end_turn",
        )

    monkeypatch.setattr(gateway_mod, "llm_call", _capture_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many calls raised X",
        window=SimpleNamespace(label="last 7 days"), items=[call],
    )
    assert "company-side (never the customer): jordan@sprntly.ai" in captured["input"]
    assert "external customer/prospect (account: Nimbusco): priya@nimbusco.com" in captured["input"]
    assert eng.count == 0
    assert "c1" not in eng.hit_ids


# ── stated assumption — never a silent unilateral reading ───────────────────

def test_assemble_count_answer_states_the_default_assumption_when_no_criterion():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=3, hit_ids=[], reasons={}, total_items=10,
                           unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(eng, window=window, corpus=_count_corpus(window))
    assert cd._VOC_DEFAULT_ASSUMPTION_LINE in out["answer"]
    assert "actively asked for a feature or raised" in out["answer"]
    assert "compliance/hosting requirements not counted" in out["answer"]


def test_assemble_count_answer_states_a_supplied_criterion_instead_of_the_default():
    import app.corpus_mapreduce as cmr

    eng = cmr.EngineResult(count=3, hit_ids=[], reasons={}, total_items=10,
                           unclassified_ids=[])
    window = cd.Window(since=NOW - timedelta(days=7), until=NOW, label="last 7 days")
    out = cd._assemble_count_answer(
        eng, window=window, corpus=_count_corpus(window),
        criterion="asked about pricing changes",
    )
    assert "asked about pricing changes" in out["answer"]
    assert cd._VOC_DEFAULT_ASSUMPTION_LINE not in out["answer"]


def test_count_engine_call_site_passes_constraints_criterion_to_assemble(monkeypatch):
    """The `answer()` call site must resolve `constraints["criterion"]` and
    hand it to `_assemble_count_answer` — not just to the engine — so the
    stated-assumption sentence reflects what was actually classified against."""
    import app.corpus_mapreduce as cmr

    monkeypatch.setattr(cd.settings, "voc_count_engine_enabled", True)
    monkeypatch.setattr(
        cd, "build_corpus",
        lambda cid, window: cd.DigestCorpus(status="ok", window=window,
                                            calls=[_call(1)], text="=== CALLS ==="))
    eng = cmr.EngineResult(count=1, hit_ids=["c1"], reasons={"c1": "x"},
                           total_items=1, unclassified_ids=[])
    monkeypatch.setattr(cmr, "run", lambda spec, **kw: eng)

    out = cd.answer(
        enterprise_id="co",
        question="how many customers raised billing issues this month",
        constraints={"criterion": "raised a billing complaint"},
    )
    assert "raised a billing complaint" in out["answer"]
    assert cd._VOC_DEFAULT_ASSUMPTION_LINE not in out["answer"]


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


def _stub_kg(monkeypatch, signals, *, themes=None, signals_dropped=0):
    """Wire the KG half to fixtures at the retrieval seam.

    The stubs take `**kw` because the real call now passes `scale=VOC_SCALE`.
    They swallow it rather than asserting on it so every case below stays about
    the merge; the scale itself is pinned once, in
    `test_the_feedback_path_retrieves_at_voc_scale`.
    """
    import app.ask_runner as ask_runner

    bundle = {
        "signals": list(signals), "themes": list(themes or []),
        "decisions": [], "hypotheses": [], "outcomes": [],
        "kg_refs": [], "token_estimate": 100,
        "signals_dropped": signals_dropped, "empty": False,
    }
    monkeypatch.setattr(
        ask_runner, "_retrieve_kg_bundle", lambda eid, q, **kw: bundle
    )
    return bundle


def _stub_no_kg(monkeypatch):
    """An empty/unreadable graph — what `_retrieve_kg_bundle` returns for a
    tenant with no signal, and what every pre-merge test implicitly assumed."""
    import app.ask_runner as ask_runner

    monkeypatch.setattr(
        ask_runner, "_retrieve_kg_bundle", lambda eid, q, **kw: None
    )


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


def test_the_feedback_path_retrieves_at_voc_scale(monkeypatch):
    """THE REGRESSION TEST FOR THE SILENT CAP.

    The merged feedback path must widen retrieval, not inherit the Ask-sized
    defaults. Before the fix this call took whatever `_retrieve_kg_bundle`'s
    defaults gave it — at most 80 candidate signals cut to 2,200 tokens — so
    "show me all the customer feedback" was answered from roughly 9k characters
    of a tenant's graph with nothing anywhere saying so.

    Asserted at the seam rather than on the output because that is where the
    regression would reappear: every case above stubs this function, so a
    dropped `scale=` argument would break nothing else in this file.
    """
    from app.graph.retrieval import VOC_SCALE
    import app.ask_runner as ask_runner

    seen: dict = {}

    def _capture(eid, q, **kw):
        seen.update(kw)
        return {
            "signals": [_kg_signal("x")], "themes": [], "decisions": [],
            "hypotheses": [], "outcomes": [], "kg_refs": [],
            "token_estimate": 10, "signals_dropped": 0, "empty": False,
        }

    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", _capture)
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="what are customers saying?")

    assert seen.get("scale") == VOC_SCALE, (
        "the feedback path must retrieve at VOC scale; falling back to the Ask "
        "defaults is the silent-truncation bug this fixes"
    )


def test_coverage_line_admits_when_retrieval_could_not_fit_everything(monkeypatch):
    """The caveat that was previously impossible to state.

    `token_estimate` reports what retrieval SPENT, which reads the same whether
    the budget was generous or the bundle was clipped — so an answer built from
    a sample looked exactly like one built from everything. With
    `signals_dropped` threaded through, the banner has to say the part it left
    out, and say it as a shortfall rather than a count of what it read.
    """
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    _stub_kg(monkeypatch, [_kg_signal("a")], signals_dropped=17)
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="what are customers saying?")

    banner = captured["input"]
    assert "17 further stored signals matched but did not fit" in banner
    assert "NOT everything on record" in banner


def test_a_complete_feedback_answer_claims_no_shortfall(monkeypatch):
    """The mirror of the case above, and the one that keeps the caveat
    meaningful: when nothing was dropped the banner must not hedge. A warning
    printed unconditionally is a warning users learn to ignore."""
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    _stub_kg(monkeypatch, [_kg_signal("a")], signals_dropped=0)
    captured = _stub_voc_pass(monkeypatch)

    cd.answer(enterprise_id="co", question="what are customers saying?")

    assert "did not fit" not in captured["input"]


def test_a_broken_graph_read_never_costs_the_calls(monkeypatch):
    """Per-source isolation, extended to the new half: the graph failing must
    cost the answer its calls no more than a dead Zoom grant costs it the
    graph."""
    import app.ask_runner as ask_runner

    def _boom(eid, q, **kw):
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


# ── count-engine rubric — scope guard (vendor-buyer / internal / demo-only) ──
# Real-corpus accuracy verification (a real staging run compared against a
# strong-model oracle) found the count engine flagging calls where the
# reviewed company asked its OWN vendor for a feature, and calls with no real
# customer ask (internal-only, or a rep pitch/demo the customer only
# watched), as if they were customer feature requests/issues. These property
# tests pin the wording fix by content, the same way the query/report prompts
# above are pinned — a rewrite that silently drops a guard clause fails here
# even though it can't fail a live-model accuracy check in CI.

def test_base_discipline_states_the_vendor_buyer_scope_guard():
    s = cd._VOC_BASE_DISCIPLINE
    assert "OWN vendor" in s or "own vendor" in s.lower()
    assert "buying party" in s or "buying" in s.lower()


def test_base_discipline_states_the_internal_only_exclusion():
    s = cd._VOC_BASE_DISCIPLINE
    assert "internal-only" in s.lower()
    assert "external customer" in s.lower() or "external" in s.lower()


def test_base_discipline_states_the_demo_pitch_exclusion():
    s = cd._VOC_BASE_DISCIPLINE
    low = s.lower()
    assert "pitch" in low or "demo" in low
    assert "watched" in low or "acknowledged" in low


def test_default_criterion_still_states_the_original_content_rules():
    """The scope guard split is additive — the pre-existing feature/issue
    definitions and the routine-scheduling exclusion must survive, now on
    `_VOC_DEFAULT_CRITERION` rather than the old single-string rubric."""
    s = cd._VOC_DEFAULT_CRITERION
    assert "explicit ask for new functionality" in s
    assert "bug, a crash" in s
    assert "Routine scheduling" in s


def test_base_discipline_still_states_the_output_format_instructions():
    s = cd._VOC_BASE_DISCIPLINE
    assert "never invent an id" in s


def test_base_discipline_and_default_criterion_length_within_bounds():
    # Guards against both an accidental truncation and an unbounded rewrite —
    # same combined-length bound the old single-string rubric had.
    combined = len(cd._VOC_BASE_DISCIPLINE) + len(cd._VOC_DEFAULT_CRITERION)
    assert 900 <= combined <= 2600


# ── count-engine rubric — reason format (verbosity + name-attribution fix) ──
# A real-corpus run found the model volunteering multi-sentence reasons with
# an embedded verbatim quote AND a "Customer (<Name>)" attribution the prompt
# never asked for — and on several calls that name was the Sprntly rep, not
# the customer. These pin the tightened reason contract by content.

def test_base_discipline_caps_reason_length_and_bans_names_and_quotes():
    s = cd._VOC_BASE_DISCIPLINE.lower()
    assert "12 word" in s
    assert "verbatim quote" in s
    assert "speaker" in s and "name" in s


def test_base_discipline_states_hit_reason_consistency():
    """The structured `hit` boolean must agree with the narrated reason — a
    real-corpus run found a call counted `hit=true` whose own reason argued
    the ask was rep-attributed (`hit=false` in the model's own prose)."""
    s = cd._VOC_BASE_DISCIPLINE.lower()
    assert "hit and reason" in s and "agree" in s
    assert "hit must be false" in s


def test_base_discipline_states_a_self_check_step_for_the_scope_guard():
    s = cd._VOC_BASE_DISCIPLINE.lower()
    assert "self-check" in s
    assert "rep-side" in s or "rep/employee" in s


def test_voc_calls_spec_composes_base_discipline_and_default_criterion():
    """`VOC_CALLS_SPEC` carries the two pieces the engine composes together —
    pins the wiring between call_digest's constants and the spec, independent
    of the engine's own composition-order test in test_corpus_mapreduce.py."""
    assert cd.VOC_CALLS_SPEC.base_discipline == cd._VOC_BASE_DISCIPLINE
    assert cd.VOC_CALLS_SPEC.criterion == cd._VOC_DEFAULT_CRITERION


# ── engine-local caching finding — the composed rubric is below the floor ───
# `app.llm._build_base_kwargs` already attaches `cache_control` to `system`
# automatically WHENEVER it clears the acting model's own cacheable floor
# (`app.llm._is_cacheable` / `_MIN_CACHEABLE_TOKENS`, pinned by
# `tests/test_llm_cache_tiers.py`) — no per-call plumbing is needed for that
# mechanism to fire. What this test proves is the honest, measured state for
# VOC_CALLS_SPEC specifically: its composed base_discipline+criterion, even on
# the Sonnet map model, sits BELOW that floor — so attaching cache_control to
# it would be exactly the "dead breakpoint" `_is_cacheable`'s own docstring
# warns against (an ephemeral write that Anthropic's API will not actually
# fill, silently misreporting "we cache here" in the telemetry). There is also
# no caller-facing lever on `app.graph.gateway.llm_call` to force the `ttl:1h`
# tier at all (`_cache_ttl_for` derives it solely from a `skill` allowlist
# built for the top-insights case, not a general override) — using it here
# would mean either faking an unrelated "skill" id or bypassing the gateway
# (both explicitly out of bounds). See the build report for the full
# reasoning; this is a mutation-provable pin, not a wiring gap: if the rubric
# ever grows past the floor (e.g. a future domain reuses this engine with a
# longer method block), this test goes RED and the caching lever becomes live
# with zero further plumbing.

def test_composed_voc_rubric_is_below_the_cacheable_floor_on_sonnet():
    from app import llm

    composed = f"{cd._VOC_BASE_DISCIPLINE}\n\n{cd._VOC_DEFAULT_CRITERION}"
    assert not llm._is_cacheable(composed, cd.VOC_CALLS_SPEC.map_model)
    floor_chars = llm._MIN_CACHEABLE_TOKENS[cd.VOC_CALLS_SPEC.map_model] * llm._CHARS_PER_TOKEN
    assert len(composed) < floor_chars


# ── count-engine rubric — fixture-driven wiring pins ─────────────────────────
# These run the REAL `VOC_CALLS_SPEC` (real base_discipline+criterion +
# verdict_schema)
# through the REAL `app.corpus_mapreduce.run`, with `app.graph.gateway.llm_call`
# faked. The fake's verdict per fixture encodes the INTENDED behavior of the
# new rubric (what a model correctly applying the scope guard above should
# return) — this pins the engine's wiring end-to-end for each named failure
# class and gives the ship-gate's real-corpus/real-LLM check concrete,
# named cases to reproduce. It is NOT a live-model accuracy proof: a fake
# always returns exactly what it is told to, so it cannot catch the model
# itself misapplying the rubric text. That gate is the separate real-corpus
# re-validation against the oracle, not this test.

def _fixture_call(external_id: str, **kw) -> CallTranscript:
    return CallTranscript(
        external_id=external_id, title=kw.pop("title", "Call"),
        date="2026-06-20", **kw,
    )


def _vendor_buyer_call() -> CallTranscript:
    # The reviewed company (rep) asking its OWN vendor for a feature — not a
    # customer asking the reviewed company for anything.
    return _fixture_call(
        "vendor-buyer-1", title="Vendor sync",
        participants=["ourrep@reviewedco.com", "sales@vendorco.com"],
        overview="Our team asked our vendor to add bulk-export to their tool.",
        quotes=[{"speaker": "Our Rep",
                 "text": "Could you add a bulk-export API to your platform?"}],
    )


def _internal_only_call() -> CallTranscript:
    # No external customer/prospect participant at all.
    return _fixture_call(
        "internal-only-1", title="Internal sync",
        participants=["eng1@reviewedco.com", "eng2@reviewedco.com"],
        overview="Internal discussion about a UI bug in our own dashboard.",
        quotes=[{"speaker": "Eng1",
                 "text": "We should fix the broken filter before next sprint."}],
    )


def _demo_only_call() -> CallTranscript:
    # The rep pitched/demoed a feature; the customer only watched/acknowledged.
    return _fixture_call(
        "demo-only-1", title="Product demo",
        participants=["rep@reviewedco.com", "prospect@buyerco.com"],
        overview="Rep demoed the new reporting dashboard; prospect watched.",
        quotes=[{"speaker": "Rep",
                 "text": "Here's our new reporting dashboard — let me show you."},
                {"speaker": "Prospect", "text": "Nice, looks good."}],
    )


def _genuine_feature_request_call() -> CallTranscript:
    return _fixture_call(
        "feature-request-1", title="Customer check-in",
        participants=["rep@reviewedco.com", "buyer@customerco.com"],
        overview="Customer asked for CSV export of their reports.",
        quotes=[{"speaker": "Buyer",
                 "text": "Can you add a CSV export option for our reports?"}],
    )


def _genuine_issue_call() -> CallTranscript:
    # A DIFFERENT customer domain than `_genuine_feature_request_call()`,
    # deliberately: `_voc_count_prefilter` reuses `call_index._own_domains`'s
    # ubiquity heuristic (a domain on >= half a corpus's calls is treated as
    # OUR OWN, not a customer's — see call_index.py). In the mixed 5-call
    # batch below, reusing the same "customerco.com" for both genuine-hit
    # fixtures would put that domain on 2 of 5 calls — exactly the ubiquity
    # threshold — and falsely brand a real customer as "our own domain",
    # silently prefiltering both genuine calls out of the classification
    # pool. Two distinct customers is also the more realistic fixture shape
    # for "two different genuine asks in one window".
    return _fixture_call(
        "issue-1", title="Support call",
        participants=["rep@reviewedco.com", "buyer@otherco.com"],
        overview="Customer reported the dashboard crashes on load.",
        quotes=[{"speaker": "Buyer",
                 "text": "The dashboard crashes every time I try to load it."}],
    )


def _intended_verdicts_llm(**kw):
    """Fake `gateway.llm_call` for the VOC_CALLS_SPEC map call: returns the
    verdict the new rubric is SUPPOSED to produce for each named fixture id,
    keyed by whatever ids the real engine actually tagged into this batch's
    rendered input (never hand-listing ids independent of what was sent)."""
    import re
    intended_hit = {
        "vendor-buyer-1": False,
        "internal-only-1": False,
        "demo-only-1": False,
        "feature-request-1": True,
        "issue-1": True,
    }
    ids = re.findall(r'<item id="([^"]+)">', kw["input"])
    verdicts = {
        i: {"hit": intended_hit.get(i, False), "reason": f"reason-{i}"}
        for i in ids
    }
    from types import SimpleNamespace
    return SimpleNamespace(output={"verdicts": verdicts}, stop_reason="end_turn")


def test_vendor_buyer_inversion_fixture_is_not_a_hit(monkeypatch):
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW), items=[_vendor_buyer_call()],
    )
    assert eng.hit_ids == []
    assert eng.count == 0


def test_internal_only_fixture_is_not_a_hit(monkeypatch):
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW), items=[_internal_only_call()],
    )
    assert eng.hit_ids == []
    assert eng.count == 0


def test_demo_pitch_only_fixture_is_not_a_hit(monkeypatch):
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW), items=[_demo_only_call()],
    )
    assert eng.hit_ids == []
    assert eng.count == 0


def test_genuine_customer_feature_request_fixture_is_a_hit(monkeypatch):
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW),
        items=[_genuine_feature_request_call()],
    )
    assert eng.hit_ids == ["feature-request-1"]
    assert eng.count == 1


def test_genuine_customer_issue_fixture_is_a_hit(monkeypatch):
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW), items=[_genuine_issue_call()],
    )
    assert eng.hit_ids == ["issue-1"]
    assert eng.count == 1


def test_mixed_batch_only_the_two_genuine_fixtures_hit(monkeypatch):
    """All five fixtures in one batch — the count/roster the engine assembles
    matches exactly the two genuine customer-raised cases."""
    import app.corpus_mapreduce as cmr
    import app.graph.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "llm_call", _intended_verdicts_llm)
    eng = cmr.run(
        cd.VOC_CALLS_SPEC, enterprise_id="co", question="how many raised X",
        window=cd.parse_window("calls", now=NOW),
        items=[
            _vendor_buyer_call(), _internal_only_call(), _demo_only_call(),
            _genuine_feature_request_call(), _genuine_issue_call(),
        ],
    )
    assert eng.count == 2
    assert set(eng.hit_ids) == {"feature-request-1", "issue-1"}

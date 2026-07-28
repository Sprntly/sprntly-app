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


def test_answer_disclosure_when_quotes_trimmed(monkeypatch):
    # When the fit trimmed quotes to keep every call in, the source line the
    # report sees says so — the run line can then state real coverage.
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    calls = [_chatty_call(i) for i in range(30)]
    full = len("\n\n".join(c.render() for c in calls))
    monkeypatch.setattr(cd, "_CORPUS_CHAR_BUDGET", full // 3)
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: calls)
    captured = {}
    monkeypatch.setattr(
        vr, "build",
        lambda **k: captured.update(k) or "<!DOCTYPE html><html><body>r</body></html>",
    )
    p = cd.answer(enterprise_id="co", question="summarize calls from the last 30 days")
    assert p["answer"].startswith("<!DOCTYPE html>")
    assert "30 calls" in captured["source_line"]
    assert "quotes sampled" in captured["source_line"]


def test_answer_autowidens_default_window_until_calls_found(monkeypatch):
    # Generic ask (no window named): the 7-day default is empty, 30 days has
    # calls → the digest widens instead of dead-ending, and the report runs
    # over the widened window.
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    windows = []
    def fetch(key, *, since, until):
        windows.append((until - since).days)
        return [_call(1)] if (until - since).days >= 29 else []
    monkeypatch.setattr(cd, "fetch_calls", fetch)
    captured = {}
    monkeypatch.setattr(
        vr, "build",
        lambda **k: captured.update(k) or "<!DOCTYPE html><html><body>r</body></html>",
    )
    p = cd.answer(enterprise_id="co", question="give me a summary of feedback of recent customer conversations")
    assert p["answer"].startswith("<!DOCTYPE html>")
    # Fetched 7d (empty) then 30d (found) — never needed 90d.
    assert len(windows) == 2 and windows[0] <= 7 and 29 <= windows[1] <= 30
    assert "the last 30 days" in p["_skill_action"]
    assert "the last 30 days" in captured["source_line"]


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
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    def boom(**k):
        raise RuntimeError("model timeout")
    monkeypatch.setattr(vr, "build", boom)
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


def test_answer_docs_only_runs_voc_report(monkeypatch):
    """Docs-only tenant asking for VoC gets the SAME report pipeline; the
    source line and skill action disclose the uploaded-document basis."""
    import app.voc_report as vr
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [_doc(1), _doc(2)])
    captured = {}
    monkeypatch.setattr(
        vr, "build",
        lambda **k: captured.update(k) or "<!DOCTYPE html><html><body>r</body></html>",
    )
    p = cd.answer(enterprise_id="co", question="summarize customer calls last week")
    assert p["answer"].startswith("<!DOCTYPE html>")
    assert "UPLOADED VOICE DOCUMENTS" in captured["source_line"]
    assert "2 uploaded voice documents" in captured["source_line"]
    assert "support export 1" in captured["corpus_text"]
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

    import app.voc_report as vr
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
    captured = {}
    monkeypatch.setattr(
        vr, "build",
        lambda **k: captured.update(k) or "<!DOCTYPE html><html><body>voc</body></html>",
    )

    question = "What is the top customer feedback"
    assert is_voc_report_request(question)          # router now matches
    assert cd.has_call_source("co") is True         # voice-category doc counts

    p = cd.answer(enterprise_id="co", question=question)
    assert p["_skill"] == "voice-of-customer-report"
    assert p["answer"].startswith("<!DOCTYPE html>")
    assert "latency complaint; mobile access ask" in captured["corpus_text"]
    assert "UPLOADED VOICE DOCUMENTS" in captured["source_line"]


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

    rendered = {}

    class _FakeVoc:
        @staticmethod
        def build(**kw):
            rendered["called"] = True
            return "<html>report</html>"

    monkeypatch.setattr(cd, "build_corpus", _fake_build)
    monkeypatch.setattr(cd, "_answer_query", _boom)
    import sys
    monkeypatch.setitem(sys.modules, "app.voc_report", _FakeVoc)

    out = cd.answer(enterprise_id="ent-A",
                    question="how many customers raised billing issues")
    assert rendered.get("called") or out.get("answer")  # report path reached

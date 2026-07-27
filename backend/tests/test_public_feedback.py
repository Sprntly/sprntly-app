"""Public-feedback pipeline — record parsing, answer branches, routing wire.

No network/LLM/DB: company_profile, the capture pass, the gateway llm_call and
the run save are patched in the public_feedback namespace (or their source
module for the lazy imports).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import app.public_feedback as pf
from app.skill_router import detect_intent

RECORDS = [
    {"verbatim": "legit rides flagged", "category": "product", "owner": "product",
     "source": {"platform": "Trustpilot", "posted_date": "2026-01-24"}},
    {"verbatim": "too expensive", "category": "non_product", "subcategory": "pricing",
     "owner": "pricing", "source": {"platform": "Reddit"}},
]

PROFILE = {
    "display_name": "Strava", "industry": "Fitness",
    "product_description": "Activity tracking",
    "product": {"name": "Strava", "website": "https://strava.com"},
}

REPORT_DATA = {
    "eyebrow": "Public feedback · 2026", "prepared": "Prepared 26 July 2026",
    "covers": "c", "looked": "l", "answering": "a",
    "counts": {"collected": 2, "product": 1, "non_product": 1,
               "sources": 2, "leaving": 0},
    "tldr_source_line": "s", "tldr_intro": "i",
    "tldr": [{"headline": "h", "sub": "s", "quote": "", "quote_attr": ""}],
    "first_move": "f", "months": [], "chart_kpis": [], "chart_note": "",
    "chart_callout": "", "ratings_kpis": [], "ratings_callout": "",
    "problems": [], "new_items": [], "stuck_items": [], "fixed_items": [],
    "mix": {"product_pos": 0, "product_neu": 0, "product_neg": 1,
            "non_pos": 0, "non_neu": 0, "non_neg": 1, "note": ""},
    "progress_callout": "", "platforms": [], "platforms_note": "",
    "compare_title": "", "why_these": "", "us_name": "", "us_loves": [],
    "rivals": [], "dimensions": [], "switching": "", "recs_intro": "",
    "recommendations": [], "non_product_intro": "", "non_product_items": [],
    "next_steps": "n", "integrity": [],
    "metadata": {"totals": {"collected": 2}},
}


# ── record parsing ───────────────────────────────────────────────────────────

def test_parse_records_clean_array():
    assert pf._parse_records(json.dumps(RECORDS)) == RECORDS


def test_parse_records_fenced():
    assert pf._parse_records("```json\n" + json.dumps(RECORDS) + "\n```") == RECORDS


def test_parse_records_prose_wrapped():
    text = "Here are the records:\n" + json.dumps(RECORDS) + "\nThat's everything."
    assert pf._parse_records(text) == RECORDS


def test_parse_records_garbage_and_non_list():
    assert pf._parse_records("no json here") == []
    assert pf._parse_records('{"not": "a list"}') == []
    assert pf._parse_records("") == []
    # non-dict entries are dropped
    assert pf._parse_records('[1, "x", {"verbatim": "ok"}]') == [{"verbatim": "ok"}]


def test_parse_records_salvages_truncated_array():
    # Output budget cut the array mid-record (nested object open) — every
    # complete record before the cut must survive.
    truncated = json.dumps(RECORDS)[:-1].rstrip("}") + ', {"verbatim": "cut off", "source": {"platform": "Red'
    got = pf._parse_records(truncated)
    assert got == RECORDS[:1] or got == RECORDS  # complete prefix, never []
    assert len(got) >= 1


def test_parse_records_bracketed_prose_before_array():
    text = "We searched [several] sites and app stores.\n" + json.dumps(RECORDS)
    assert pf._parse_records(text) == RECORDS


# ── answer() branches ────────────────────────────────────────────────────────

def _patch_profile(monkeypatch, profile=PROFILE, error=False):
    import app.research.market as market

    if error:
        def boom(_): raise RuntimeError("db down")
        monkeypatch.setattr(market, "company_profile", boom)
    else:
        monkeypatch.setattr(market, "company_profile", lambda _eid: dict(profile))


def test_answer_falls_through_when_profile_unreadable(monkeypatch):
    _patch_profile(monkeypatch, error=True)
    assert pf.answer(enterprise_id="e1", question="public feedback?") is None


def test_answer_asks_for_onboarding_without_company_name(monkeypatch):
    _patch_profile(monkeypatch, profile={"display_name": ""})
    out = pf.answer(enterprise_id="e1", question="public feedback?")
    assert out is not None and "onboarding" in out["answer"]
    assert out["_skill"] == "public-feedback-report"


def test_answer_capture_failure_is_graceful(monkeypatch):
    _patch_profile(monkeypatch)
    def boom(*a, **k): raise RuntimeError("api down")
    monkeypatch.setattr(pf, "_capture", boom)
    out = pf.answer(enterprise_id="e1", question="public feedback?")
    assert "couldn't complete the public web search" in out["answer"]


def test_answer_no_records_found(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([], False))
    out = pf.answer(enterprise_id="e1", question="public feedback?")
    assert "couldn't find enough feedback" in out["answer"]
    assert "Strava" in out["answer"]


def test_answer_truncated_empty_capture_never_claims_no_feedback(monkeypatch):
    # The sweep hit the output budget and nothing was salvageable: the honest
    # answer is "retry", never "no feedback found".
    _patch_profile(monkeypatch)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([], True))
    out = pf.answer(enterprise_id="e1", question="public feedback?")
    assert "hit an internal limit" in out["answer"]
    assert "couldn't find enough feedback" not in out["answer"]


def test_answer_happy_path_renders_and_persists(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: (list(RECORDS), False))
    calls: dict = {}

    def fake_llm_call(**kw):
        calls["llm"] = kw
        return SimpleNamespace(output=dict(REPORT_DATA))

    monkeypatch.setattr(pf, "llm_call", fake_llm_call)

    import app.db as db

    def fake_save(company_id, **kw):
        calls["save"] = {"company_id": company_id, **kw}
        return 7

    monkeypatch.setattr(db, "save_public_feedback_run", fake_save)

    out = pf.answer(enterprise_id="e1", question="what are people saying about us online?")
    assert out["answer"].startswith("<!DOCTYPE html>")
    assert "report-metadata" in out["answer"]
    assert out["_skill"] == "public-feedback-report"
    assert out["_skill_action"] == "Public feedback · 2 posts"
    # analyse call carries the skill binding, schema, and the records
    assert calls["llm"]["skill"] == "public-feedback-report"
    assert calls["llm"]["json_schema"] is not None
    assert calls["llm"]["long_output"] is True
    assert "legit rides flagged" in calls["llm"]["input"]
    # the run is persisted with records + metadata + html
    assert calls["save"]["company_id"] == "e1"
    assert calls["save"]["records"] == RECORDS
    assert calls["save"]["metadata"] == {"totals": {"collected": 2}}
    assert calls["save"]["html"] == out["answer"]


def test_answer_save_failure_never_breaks_the_answer(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: (list(RECORDS), False))
    monkeypatch.setattr(
        pf, "llm_call", lambda **kw: SimpleNamespace(output=dict(REPORT_DATA))
    )
    import app.db as db

    def boom(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(db, "save_public_feedback_run", boom)
    out = pf.answer(enterprise_id="e1", question="q")
    assert out["answer"].startswith("<!DOCTYPE html>")


def test_answer_synthesis_failure_is_graceful(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: (list(RECORDS), False))
    def boom(**kw): raise RuntimeError("model error")
    monkeypatch.setattr(pf, "llm_call", boom)
    out = pf.answer(enterprise_id="e1", question="q")
    assert "found 2 public posts" in out["answer"]


# ── query mode (follow-ups over the stored run) ─────────────────────────────

RUN = {
    "id": 7, "question": "what are people saying about us online?",
    "window_label": "Public feedback · 2026",
    "records": RECORDS, "metadata": {"totals": {"collected": 2}},
    "created_at": "2026-07-26T22:00:00+00:00",
}


def test_followup_query_shapes():
    for q in [
        "what did the App Store say?",
        "what are users saying on reddit?",
        "show me the feedback from March",
        "how long has the flagging problem been raised?",
        "why are people leaving?",
        "what do people like?",
        "how many complained about crashes?",
        "is the login problem getting worse?",
        "what's new since last time?",
        # "report" as a REFERENCE (not a request) stays a follow-up
        "what did the report say about pricing?",
    ]:
        assert pf.is_followup_query(q), q


def test_report_shaped_asks_never_query_mode():
    # Every phrasing the router treats as a canonical report ask must re-run
    # the pipeline — a stale stored run must never quietly answer these.
    for q in [
        "what are people saying about us online?",
        "what do people say about us online?",
        "what's the public feedback on our product?",
        "how is our public sentiment trending?",
        "run a public feedback report",
        "generate a report on our reviews",
        "give me a fresh report on our online reputation",
        "review mining on the app store please",
        "what's our public standing?",
    ]:
        assert not pf.is_followup_query(q), q


def _patch_latest_run(monkeypatch, run):
    import app.db as db

    monkeypatch.setattr(db, "latest_public_feedback_run", lambda _eid: run)


def test_followup_answers_from_stored_run(monkeypatch):
    _patch_latest_run(monkeypatch, dict(RUN))
    calls: dict = {}

    def fake_llm_call(**kw):
        calls["llm"] = kw
        return SimpleNamespace(output={
            "answer": "Seventeen posts on Trustpilot — posts we found, not users.",
            "key_points": [], "citations": [], "confidence": 0.7, "unanswered": "",
        })

    monkeypatch.setattr(pf, "llm_call", fake_llm_call)
    out = pf.answer(enterprise_id="e1", question="what did the App Store say?")
    assert out["_skill_source"] == "public-feedback-query"
    assert "2026-07-26" in out["_skill_action"]
    # grounded on the stored records + metadata, cheap call, skill bound
    assert calls["llm"]["purpose"] == "public_feedback_query"
    assert "legit rides flagged" in calls["llm"]["input"]
    assert '"collected": 2' in calls["llm"]["input"]
    assert calls["llm"]["max_tokens"] == 4000


def test_followup_without_run_falls_to_full_pipeline(monkeypatch):
    _patch_profile(monkeypatch)
    _patch_latest_run(monkeypatch, None)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([], False))
    out = pf.answer(enterprise_id="e1", question="what did the App Store say?")
    # no stored run → the full pipeline ran (and found nothing, in this stub)
    assert "couldn't find enough feedback" in out["answer"]


def test_followup_query_failure_falls_back_to_full_run(monkeypatch):
    _patch_profile(monkeypatch)
    _patch_latest_run(monkeypatch, dict(RUN))

    def boom(**kw): raise RuntimeError("model error")
    monkeypatch.setattr(pf, "llm_call", boom)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([], False))
    out = pf.answer(enterprise_id="e1", question="what did the App Store say?")
    assert "couldn't find enough feedback" in out["answer"]


def test_report_shaped_ask_ignores_stored_run(monkeypatch):
    _patch_profile(monkeypatch)
    called = {"latest": False}
    import app.db as db

    def fake_latest(_eid):
        called["latest"] = True
        return dict(RUN)

    monkeypatch.setattr(db, "latest_public_feedback_run", fake_latest)
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([], False))
    pf.answer(enterprise_id="e1", question="what are people saying about us online?")
    assert called["latest"] is False  # report-shaped → straight to the pipeline


# ── routing wire ─────────────────────────────────────────────────────────────

def test_regex_routes_public_feedback_phrasings():
    for q in [
        "what's the public feedback on our product?",
        "how's our public standing right now?",
        "what are the company's current public standings?",
        "what are people saying about us online?",
        "run review mining on our app store reviews",
    ]:
        d = detect_intent(q)
        assert d and d.skill_id == "public-feedback-report", q


def test_voc_phrasings_do_not_hit_public_feedback():
    for q in [
        "give me a voice of customer report",
        "summarize the customer calls from last week",
    ]:
        d = detect_intent(q)
        assert not (d and d.skill_id == "public-feedback-report"), q


def test_qa_agent_routes_public_feedback_to_web_pipeline(monkeypatch):
    import app.qa_agent as qa

    monkeypatch.setattr(
        qa, "route",
        lambda *a, **k: qa.RouteDecision("public-feedback-report", 0.9, "regex",
                                         "Public feedback report"),
    )
    sentinel = {"answer": "<!DOCTYPE html>report", "key_points": [],
                "citations": [], "confidence": 0.6, "unanswered": "",
                "_skill": "public-feedback-report",
                "_skill_action": "Public feedback · 2 posts",
                "_skill_source": "public-feedback"}
    monkeypatch.setattr(pf, "answer", lambda **kw: dict(sentinel))
    out = qa.answer(enterprise_id="e1", question="what are people saying about us online?",
                    dataset="d1")
    assert out["_skill_source"] == "public-feedback"
    assert out["answer"] == "<!DOCTYPE html>report"


def test_qa_agent_falls_through_when_pipeline_returns_none(monkeypatch):
    import app.qa_agent as qa

    monkeypatch.setattr(
        qa, "route",
        lambda *a, **k: qa.RouteDecision("public-feedback-report", 0.9, "regex",
                                         "Public feedback report"),
    )
    monkeypatch.setattr(pf, "answer", lambda **kw: None)
    fallback = {"answer": "generic", "key_points": [], "citations": [],
                "confidence": 0.5, "unanswered": ""}
    monkeypatch.setattr(qa, "_answer_single_shot", lambda *a, **k: dict(fallback))
    out = qa.answer(enterprise_id="e1", question="what are people saying about us online?",
                    dataset="d1")
    assert out["answer"] == "generic"

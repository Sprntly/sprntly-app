"""Market-intelligence pipeline — answer branches, the capture contract, routing.

No network/LLM/DB: company_profile, the capture pass and the gateway llm_call
are patched in the market_intel namespace (or their source module for the lazy
imports), exactly as the public-feedback suite does.

The routing half is the interesting one here. This rule had to be slotted ABOVE
the competitive-intelligence rule — `_CIR_SUBJECT` admits "market", so CIR
would otherwise claim "market intelligence report", this report's own name —
without disturbing anything CIR legitimately owns. Those separations are pinned
below rather than left to the phrase file, because they are the reason the rule
is shaped the way it is.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.market_intel as mi
from app.skill_router import PIPELINE_SKILLS, detect_intent

MI = "market-intelligence-report"
CIR = "competitive-intelligence-review"

RECORDS = [
    {"category": "funding", "headline": "Northwind raises $40M Series B",
     "entity": "Northwind", "event_date": "2026-05-12", "source": "TechCrunch",
     "url": "https://example.com/a", "detail": "$40M led by Acme Ventures",
     "implication": "Expect faster enterprise feature parity."},
    {"category": "regulation", "headline": "EU data rule takes effect",
     "entity": "European Commission", "event_date": "2026-06-01",
     "source": "Reuters", "url": "https://example.com/b",
     "detail": "Portability duties for processors", "implication": ""},
]

PROFILE = {
    "display_name": "Strava", "industry": "Fitness tracking",
    "product_description": "Activity tracking for runners and cyclists",
    "product": {"name": "Strava", "website": "https://strava.com"},
}

REPORT_MD = (
    "## What moved this period\n\n"
    "Northwind raised $40M (TechCrunch, 2026-05-12).\n\n"
    "### Integrity\n- 2 events collected across 2 sources."
)

# A REALISTIC response — one that validates against `_REPORT_SCHEMA`. The
# sibling suites learned this the hard way: a hand-written stub thinner than the
# grammar hid an empty structured half through a fully green suite.
REPORT_DATA = {
    "answer": REPORT_MD,
    "window_label": "Market intelligence · 2026",
    "metadata": {
        "generated_by": "market-intelligence-report",
        "window": "Feb - Jul 2026",
        "totals": {"collected": 2, "sources": 2},
        "by_category": [{"category": "funding", "count": 1},
                        {"category": "regulation", "count": 1}],
        "movements": [
            {"headline": "Northwind raises $40M Series B", "category": "funding",
             "entity": "Northwind", "event_date": "2026-05-12",
             "source": "TechCrunch"},
        ],
        "entrants": ["Northwind"],
        "not_found": "No analyst coverage surfaced for this category.",
        "limits": "Public web coverage over-reports funding and under-reports failure.",
    },
}


def test_report_data_fixture_matches_the_schema():
    """The stub must be something the model could ACTUALLY produce — the same
    guard the competitive-intelligence and public-feedback suites carry, for
    the same reason."""
    import jsonschema

    jsonschema.validate(REPORT_DATA, mi._REPORT_SCHEMA)


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
    assert mi.answer(enterprise_id="e1", question="market intelligence report") is None


def test_answer_asks_for_the_industry_when_the_category_is_unknown(monkeypatch):
    """The CATEGORY is what this report is about. Without it the sweep has no
    subject and would quietly become a search about this one company — which
    is the competitive report's job, not this one's."""
    _patch_profile(monkeypatch, profile={"display_name": "Strava", "industry": "",
                                         "product_description": ""})
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert out is not None
    assert "which market you're in" in out["answer"]
    assert out["_skill"] == MI


def test_answer_capture_failure_is_graceful(monkeypatch):
    _patch_profile(monkeypatch)

    def boom(*a, **k): raise RuntimeError("api down")

    monkeypatch.setattr(mi, "_capture", boom)
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert "couldn't complete the market web search" in out["answer"]


def test_answer_no_records_found(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([], False))
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert "couldn't find enough market activity" in out["answer"]


def test_truncated_empty_capture_never_claims_nothing_happened(monkeypatch):
    """The sweep hit the output budget and nothing was salvageable: the honest
    answer is "retry", never "no market activity found"."""
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([], True))
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert "hit an internal limit" in out["answer"]
    assert "couldn't find enough market activity" not in out["answer"]


def test_capture_reads_truncation_from_the_stop_reason(monkeypatch):
    """`_capture`'s overflow signal is `meta_out["stop_reason"] == "max_tokens"`.

    Pinned because the obvious-looking `meta.get("truncated")` is a key nothing
    ever sets: reading it would make every overflowed capture report "nothing
    found", which is precisely the claim the truncated branch exists to avoid.
    """
    def fake_search(**kw):
        kw["meta_out"]["stop_reason"] = "max_tokens"
        return "[]"

    monkeypatch.setattr(mi, "call_with_web_search", fake_search)
    records, truncated = mi._capture("e1", "Market category: Fitness")
    assert records == []
    assert truncated is True


def test_capture_is_not_truncated_on_a_normal_stop(monkeypatch):
    def fake_search(**kw):
        kw["meta_out"]["stop_reason"] = "end_turn"
        return json.dumps(RECORDS)

    monkeypatch.setattr(mi, "call_with_web_search", fake_search)
    records, truncated = mi._capture("e1", "Market category: Fitness")
    assert records == RECORDS
    assert truncated is False


def test_answer_happy_path_returns_the_report(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))
    calls: dict = {}

    def fake_llm_call(**kw):
        calls["llm"] = kw
        return SimpleNamespace(output=dict(REPORT_DATA))

    monkeypatch.setattr(mi, "llm_call", fake_llm_call)

    out = mi.answer(enterprise_id="e1", question="run a market intelligence report")
    assert out["answer"] == REPORT_MD
    assert out["_skill"] == MI
    assert out["_report"] is True
    # The captured events are what the synthesis reasons over, and the skill is
    # bound so the call is attributable in `agent_decision_log`.
    assert "Northwind" in calls["llm"]["input"]
    assert calls["llm"]["skill"] == MI
    assert calls["llm"]["long_output"] is True


def test_phases_name_capture_then_synthesis_in_order(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))
    monkeypatch.setattr(
        mi, "llm_call",
        lambda **kw: SimpleNamespace(output=dict(REPORT_DATA)))
    phases: list[str] = []
    mi.answer(enterprise_id="e1", question="run a market intelligence report",
              on_phase=phases.append)
    # GATHERING (the paid web capture) then WRITING (the synthesis) — the two
    # real legs, via the shared report vocabulary.
    assert phases == [
        "Gathering the latest information…",
        "Writing your report…",
    ]


def test_no_synthesis_phase_when_capture_found_nothing(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([], False))
    phases: list[str] = []
    mi.answer(enterprise_id="e1", question="market intelligence report",
              on_phase=phases.append)
    # An empty capture returns before the synthesis leg — only GATHERING fired.
    assert phases == ["Gathering the latest information…"]


def test_pipeline_runs_unchanged_without_a_phase_sink(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))
    monkeypatch.setattr(
        mi, "llm_call",
        lambda **kw: SimpleNamespace(output=dict(REPORT_DATA)))
    with_sink = mi.answer(enterprise_id="e1", question="market intelligence report",
                          on_phase=lambda _l: None)
    without = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert with_sink == without


def test_synthesis_failure_is_graceful(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))

    def boom(**kw): raise RuntimeError("gateway down")

    monkeypatch.setattr(mi, "llm_call", boom)
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert "hit an error" in out["answer"]


def test_an_empty_synthesis_is_not_returned_as_a_report(monkeypatch):
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))
    monkeypatch.setattr(
        mi, "llm_call",
        lambda **kw: SimpleNamespace(output={"answer": "   ", "window_label": "",
                                             "metadata": {}}),
    )
    out = mi.answer(enterprise_id="e1", question="market intelligence report")
    assert "hit an error" in out["answer"]
    assert "_report" not in out


def test_a_stop_between_capture_and_synthesis_spends_nothing_more(monkeypatch):
    """The capture is already paid for; the boundary before the document-scale
    synthesis is where a Stop can still save the second spend."""
    _patch_profile(monkeypatch)
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: (list(RECORDS), False))

    def must_not_run(**kw):
        raise AssertionError("synthesis ran after cancellation")

    monkeypatch.setattr(mi, "llm_call", must_not_run)
    out = mi.answer(enterprise_id="e1", question="market intelligence report",
                    is_cancelled=lambda: True)
    assert "Stopped" in out["answer"]


# ── the monthly-reports contract ─────────────────────────────────────────────

def test_no_degraded_payload_ever_carries_the_report_marker(monkeypatch):
    """`app.monthly_reports` decides "is this a document" from `_report`. Every
    apology here stamps `_skill` (the UI attributes it to this path), so if one
    ever also stamped `_report` the scheduled run would file it as the month's
    report AND stamp the ledger that suppresses the real one."""
    _patch_profile(monkeypatch)

    apologies = [
        mi._plain_payload("no profile"),
        mi._plain_payload("search failed"),
    ]
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([], False))
    apologies.append(mi.answer(enterprise_id="e1", question="market report"))
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([], True))
    apologies.append(mi.answer(enterprise_id="e1", question="market report"))

    for payload in apologies:
        assert payload["_skill"] == MI
        assert "_report" not in payload


def test_the_scheduled_spec_reaches_this_engine():
    """The monthly spec's skill must equal this module's constant, or
    `monthly_reports._is_report` rejects every real run and the report silently
    never appears."""
    from app import monthly_reports

    assert monthly_reports.MI_SPEC.skill == mi.MI_SKILL
    assert monthly_reports.MI_SPEC in monthly_reports.MONTHLY_REPORT_SPECS


# ── routing ──────────────────────────────────────────────────────────────────

MI_POSITIVES = [
    "market intelligence",
    "run a market intelligence report",
    "market intel",
    "monthly market report",
    "quarterly market briefing",
    "give me a market update",
    "industry report",
    "category scan",
    "sector overview",
    "market roundup",
    "report on the market",
    "briefing on our category",
]


@pytest.mark.parametrize("q", MI_POSITIVES)
def test_market_report_shapes_route_to_market_intelligence(q):
    d = detect_intent(q)
    assert d is not None, f"fast-path missed: {q!r}"
    assert d.skill_id == MI, f"{q!r} routed to {d.skill_id}"


# Everything CIR legitimately owns, which this rule sits above and must not
# take. The comparison family is about OUR POSITION against them — that is
# CIR's question whatever noun it uses — and "market landscape" is PM usage for
# the competitive landscape.
CIR_KEEPS = [
    "market landscape",
    "competitive landscape report for our category",
    "how do we compare to the market right now",
    "benchmark us against the market",
    "competitive intelligence",
    "monthly competitor scan",
]


@pytest.mark.parametrize("q", CIR_KEEPS)
def test_competitive_asks_are_not_stolen_by_the_market_rule(q):
    d = detect_intent(q)
    assert d is not None, f"routing lost entirely: {q!r}"
    assert d.skill_id == CIR, f"{q!r} was taken by {d.skill_id}"


# Market SIZING is a framework over a category, not a news sweep of it. These
# were deferred to the intent router before this rule existed and still are.
SIZING_STAYS_DEFERRED = [
    "analyze the market structure for this category",
    "what's the TAM for this market?",
    "market sizing report",
    "do a deep dive on the market data I uploaded",
]


@pytest.mark.parametrize("q", SIZING_STAYS_DEFERRED)
def test_market_sizing_and_own_data_still_defer(q):
    d = detect_intent(q)
    assert d is None or d.skill_id not in (MI, CIR), (
        f"{q!r} should defer to the intent router, got "
        f"{d.skill_id if d else None}"
    )


def test_the_pipeline_id_is_registered():
    """A pipeline id missing from `PIPELINE_SKILLS` silently un-ships the
    capability: `_invocable` rejects it, so the classifier and any pinned skill
    can never name it."""
    assert MI in PIPELINE_SKILLS


def test_qa_agent_dispatches_the_id_to_this_module():
    """The id must key a dispatch branch in qa_agent, or routing succeeds and
    the turn falls through to the generic answer."""
    import inspect

    from app import qa_agent

    src = inspect.getsource(qa_agent)
    assert f'decision.skill_id == "{MI}"' in src
    assert "market_intel.answer(" in src

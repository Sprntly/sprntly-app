"""Tests for monthly report → KG ingest (app.kg_ingest.monthly_report).

The scheduled reports used to end in a Slack/email ready-ping, which left their
findings sealed inside one artifact row. They are now extracted into the graph
instead, so a question someone asks weeks later can be answered out of the sweep
we already paid for. What these tests pin down:

  1. happy path — channel="monthly_report" provenance carrying the report's
     skill/label/period/artifact id, and the pm_manual + web_research pins
  2. GATE INVARIANCE — a report-only tenant has NO evidence-eligible signals,
     even for findings the model typed as revenue/customer_voice on merit
  3. the content-hash ledger makes a second ingest of the same report free
  4. REPLACE semantics — August's A+B → September's B+C expires A, keeps B,
     writes C
  5. PER-REPORT scoping — the market report ingesting never expires the
     competitive report's signals (they run back-to-back in one tick)
  6. never wipe on empty — a zero-signal extraction keeps last month's findings
  7. no extractable text writes no ledger row
  8. an extraction failure leaves NO ledger row, so the next run retries
  9. company-root wiring, deduped for a finding repeated from last month
 10. THE POINT OF THE FEATURE — an ingested finding comes back out of
     `graph.retrieval.retrieve_context` for a question it answers, carrying the
     provenance that names the report it came from

DESIGN DECISION under test (2): report signals are pinned
``force_source_type="pm_manual"`` AND ``origin="web_research"``. A report is a
model's synthesis of facts scraped off the public web about the OUTSIDE world.
A tenant with no connectors and no uploads must not have the brief evidence gate
opened by a report Sprntly generated on its behalf, so neither the source_type
axis nor the origin axis may admit one.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.facade import GraphFacade
from app.graph.gateway import LLMResult
from app.kg_ingest import monthly_report as mrk

CIR = "competitive-intelligence-review"
MI = "market-intelligence-report"

# ── extraction fake ─────────────────────────────────────────────────────────
# The extractor runs for real (source_type coercion, provenance stamping,
# content-keyed ids, the additive signal_ids key); only the model + embedding
# calls are faked. The fake reads the text it is handed and emits one signal per
# "FACT <X>" marker, so a replaced report produces exactly the signals its text
# implies — the same shape test_roadmap_kg_ingest uses for BET markers.

_FACTS = {
    "A": ("Rival A raised a $40M Series B in July", "Funding"),
    "B": ("Rival B shipped usage-based pricing", "Pricing"),
    "C": ("A new entrant launched in the EU", "New entrants"),
}


def _llm_result(items: list[dict]) -> LLMResult:
    return LLMResult(
        output={"signals": items}, model="m", prompt_version=ex.PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _item(content: str, theme: str, source_type: str = "pm_manual") -> dict:
    return {"kind": "finding", "content": content, "source_type": source_type,
            "theme": theme, "relationship": "SUPPORTS", "confidence": 0.9}


class _Extraction:
    """Context manager: fakes llm_call/embed_texts and counts model calls."""

    def __init__(self, extra_items: list[dict] | None = None,
                 boom: bool = False) -> None:
        self.calls: list[str] = []
        self._extra = extra_items or []
        self._boom = boom
        self._patches: list = []

    def _llm(self, **kwargs):
        text = kwargs.get("input", "")
        self.calls.append(text)
        if self._boom:
            raise RuntimeError("401 incorrect api key provided")
        items = [_item(*_FACTS[k]) for k in sorted(_FACTS) if f"FACT {k}" in text]
        return _llm_result(items + self._extra)

    def __enter__(self):
        self._patches = [
            patch.object(ex, "llm_call", side_effect=lambda **k: self._llm(**k)),
            patch.object(ex, "embed_texts",
                         side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _report_text(*facts: str) -> str:
    return "# Competitive Intelligence\n\n" + "\n".join(
        f"FACT {f}: {_FACTS[f][0]}" for f in facts
    )


# ── fixtures / helpers ──────────────────────────────────────────────────────

def _seed_company(db, company_id: str, slug: str = "acme") -> None:
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _ingest(company_id: str, text: str, *, skill: str = CIR,
            label: str = "Competitive Intelligence report",
            period: str = "August 2026", report_id: int | None = 7,
            facade: GraphFacade | None = None) -> dict:
    return mrk.ingest_report(
        company_id, report_skill=skill, report_label=label, period=period,
        text=text, report_id=report_id, facade=facade or GraphFacade(),
    )


def _report_signals(facade: GraphFacade, company_id: str,
                    skill: str | None = None) -> dict:
    """{content: Signal} over this company's ACTIVE report-channel signals,
    optionally narrowed to one report."""
    out = {}
    for s in facade.active_signals(company_id):
        prov = s.provenance or {}
        if prov.get("channel") != mrk.REPORT_CHANNEL:
            continue
        if skill and prov.get("report_skill") != skill:
            continue
        out[s.content] = s
    return out


def _ledger_rows(facade: GraphFacade, company_id: str) -> list:
    return facade.list_sources(company_id, source_type=mrk.LEDGER_SOURCE_TYPE)


# ── 1. happy path ───────────────────────────────────────────────────────────

def test_ingest_stamps_the_report_channel_and_its_artifact_id(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    facade = GraphFacade()

    with _Extraction() as fake:
        result = _ingest("co-1", _report_text("A", "B"), facade=facade)

    assert result["status"] == "ingested"
    assert result["signals"] == 2
    assert fake.calls, "the extractor should have been called"

    sigs = _report_signals(facade, "co-1")
    assert set(sigs) == {_FACTS["A"][0], _FACTS["B"][0]}

    prov = sigs[_FACTS["A"][0]].provenance
    assert prov["channel"] == "monthly_report"
    assert prov["report_skill"] == CIR
    assert prov["report_label"] == "Competitive Intelligence report"
    assert prov["report_period"] == "August 2026"
    # The artifact id is what lets an answer grounded on this finding point
    # back at the report it came from.
    assert prov["report_id"] == 7


# ── 2. gate invariance ──────────────────────────────────────────────────────

def test_report_signals_are_never_evidence_on_either_axis(isolated_settings):
    """The model types a competitor's funding round as `revenue` on merit —
    exactly the case a mere default would let through. Both pins have to hold,
    or a tenant whose only data is a Sprntly-generated report about OTHER
    companies could open its own brief evidence gate.
    """
    db = isolated_settings["supabase"]
    _seed_company(db, "co-2")
    facade = GraphFacade()

    merited = [
        _item("Rival A's ARR reached $12M", "Funding", source_type="revenue"),
        _item("Reviewers call rival B's onboarding painful", "UX",
              source_type="customer_voice"),
    ]
    with _Extraction(extra_items=merited):
        _ingest("co-2", _report_text("A"), facade=facade)

    sigs = _report_signals(facade, "co-2")
    assert len(sigs) == 3
    for content, sig in sigs.items():
        assert sig.source_type == "pm_manual", f"{content} escaped the pin"
        assert sig.origin == "web_research", f"{content} escaped the origin pin"
        assert sig.evidence_eligible is False, f"{content} counts as evidence"


# ── 3. the ledger ───────────────────────────────────────────────────────────

def test_re_ingesting_the_same_report_is_free(isolated_settings):
    """A retry or a manual re-run must not re-buy the extraction — and must not
    double-wire the company root."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-3")
    facade = GraphFacade()
    text = _report_text("A", "B")

    with _Extraction() as first:
        assert _ingest("co-3", text, facade=facade)["status"] == "ingested"
        first_calls = len(first.calls)
    with _Extraction() as second:
        assert _ingest("co-3", text, facade=facade)["status"] == "unchanged"
        assert second.calls == []

    assert first_calls > 0
    assert len(_ledger_rows(facade, "co-3")) == 1


# ── 4. replace semantics ────────────────────────────────────────────────────

def test_next_month_replaces_last_months_findings(isolated_settings):
    """August asserts A+B; September asserts B+C. A is retired, B carries over
    untouched (content-keyed, so it re-derives the same id), C is new."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-4")
    facade = GraphFacade()

    with _Extraction():
        _ingest("co-4", _report_text("A", "B"), facade=facade)
    assert set(_report_signals(facade, "co-4")) == {
        _FACTS["A"][0], _FACTS["B"][0]}

    with _Extraction():
        result = _ingest("co-4", _report_text("B", "C"), facade=facade,
                         period="September 2026", report_id=8)

    assert result["expired"] == 1
    assert set(_report_signals(facade, "co-4")) == {
        _FACTS["B"][0], _FACTS["C"][0]}


# ── 5. per-report scoping ───────────────────────────────────────────────────

def test_one_report_never_expires_another_reports_signals(isolated_settings):
    """The three reports ingest back-to-back inside ONE scheduler tick. A
    channel-only expiry sweep would have each report retire its siblings'
    signals seconds after they were written — the whole month's other two
    reports silently gone from the graph.
    """
    db = isolated_settings["supabase"]
    _seed_company(db, "co-5")
    facade = GraphFacade()

    with _Extraction():
        _ingest("co-5", _report_text("A"), skill=CIR, facade=facade)
        result = _ingest("co-5", _report_text("B", "C"), skill=MI,
                         label="Market Intelligence report", facade=facade)

    assert result["expired"] == 0
    assert set(_report_signals(facade, "co-5", skill=CIR)) == {_FACTS["A"][0]}
    assert set(_report_signals(facade, "co-5", skill=MI)) == {
        _FACTS["B"][0], _FACTS["C"][0]}


# ── 6. never wipe on empty ──────────────────────────────────────────────────

def test_a_zero_signal_extraction_keeps_last_months_findings(isolated_settings):
    """Failing to READ this month's report is not evidence last month's
    findings were withdrawn. Wiping on empty would leave the tenant with an
    empty graph and no report to answer from until the following month."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-6")
    facade = GraphFacade()

    with _Extraction():
        _ingest("co-6", _report_text("A", "B"), facade=facade)

    # A report the model finds nothing extractable in — no FACT markers.
    with _Extraction():
        result = _ingest("co-6", "# Market Intelligence\n\nNothing to report.",
                         facade=facade, period="September 2026")

    assert result["expired"] == 0
    assert set(_report_signals(facade, "co-6")) == {
        _FACTS["A"][0], _FACTS["B"][0]}


# ── 7 / 8. nothing to ingest, and failure retries ───────────────────────────

def test_empty_text_writes_no_ledger_row(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-7")
    facade = GraphFacade()

    with _Extraction() as fake:
        assert _ingest("co-7", "   \n  ", facade=facade)["status"] == "no_text"
        assert fake.calls == []
    assert _ledger_rows(facade, "co-7") == []


def test_an_extraction_failure_leaves_no_ledger_row(isolated_settings):
    """Raises without a ledger row, so the next scheduled run retries rather
    than treating a failed month as ingested."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-8")
    facade = GraphFacade()

    with _Extraction(boom=True):
        with pytest.raises(Exception):
            _ingest("co-8", _report_text("A"), facade=facade)

    assert _ledger_rows(facade, "co-8") == []


# ── 9. company-root wiring ──────────────────────────────────────────────────

def test_findings_are_wired_to_the_company_root_without_duplicates(
        isolated_settings):
    """Every finding informs the tenant's company root, and a finding repeated
    from last month (which comes back duplicate-skipped, not re-written) does
    not earn a second edge."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-9")
    facade = GraphFacade()

    with _Extraction():
        _ingest("co-9", _report_text("A", "B"), facade=facade)
    root = facade.ensure_company_entity("co-9")
    first = [e for e in facade.edges_to("co-9", root, type="INFORMS")
             if e.source_kind == "signal"]
    assert len(first) == 2

    # September repeats B verbatim and adds C.
    with _Extraction():
        _ingest("co-9", _report_text("B", "C"), facade=facade,
                period="September 2026")
    second = [e for e in facade.edges_to("co-9", root, type="INFORMS")
              if e.source_kind == "signal"]
    # A's edge survives its signal's retirement (readers resolve the signal and
    # check retirement), B is not re-wired, C is new: 3, not 4.
    assert len(second) == 3
    assert len({e.source_id for e in second}) == 3


# ── 10. the point of the feature ────────────────────────────────────────────

def test_an_ingested_finding_is_retrievable_by_question(isolated_settings):
    """End to end: this is the behaviour the feature exists for.

    A ready-ping reached whoever read the channel that morning. The ingest has
    to reach someone asking a question weeks later — so assert the finding
    actually comes back out of the retrieval path Ask uses, not merely that a
    row landed in the graph.

    Guards a specific way this could regress silently: report signals are
    deliberately pinned NON-EVIDENCE (`pm_manual` + `web_research`), and if
    anything in retrieval ever started gating on `evidence_eligible` or an
    allow-list of connected source_types, the reports would vanish from Ask
    while every other test here still passed.
    """
    from app.graph.embeddings import EMBEDDING_DIM
    from app.graph.retrieval import retrieve_context

    db = isolated_settings["supabase"]
    _seed_company(db, "co-10")
    facade = GraphFacade()

    with _Extraction():
        _ingest("co-10", _report_text("A"), facade=facade)

    # No theme match (the fake backend has no pgvector) — the finding rides the
    # recent-signals path, which is how fresh report content reaches an answer.
    with patch("app.graph.embeddings.embed_texts",
               side_effect=lambda texts, **k: [[0.1] * EMBEDDING_DIM
                                               for _ in texts]), \
         patch.object(GraphFacade, "find_candidates",
                      lambda self, ent, typ, vec, k=10: []):
        bundle = retrieve_context(facade, "co-10",
                                  "did any competitor raise money recently?")

    assert bundle["empty"] is False
    contents = [s["content"] for s in bundle["signals"]]
    assert _FACTS["A"][0] in contents

    # And the answer can say WHERE it learned this — the report and its month.
    hit = next(s for s in bundle["signals"] if s["content"] == _FACTS["A"][0])
    assert hit["provenance"]["report_skill"] == CIR
    assert hit["provenance"]["report_period"] == "August 2026"
    assert hit["provenance"]["report_id"] == 7

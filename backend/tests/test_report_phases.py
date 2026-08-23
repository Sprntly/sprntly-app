"""Unit tests for the shared report-progress phase primitive.

Covers the single source every report path now calls: the standard step
vocabulary (`ReportPhase`), the emit helper (`emit_report_phase`), and the
company-research stage resolver (`emit_research_stage_phase`). The per-report
wiring is tested in each report module's own suite; these tests pin the
primitive's contract in one place so a change to the vocabulary is caught here.
"""
from __future__ import annotations

from app.report_phases import (
    RESEARCH_STAGE_PHASES,
    ReportPhase,
    emit_report_phase,
    emit_research_stage_phase,
)


def test_emit_report_phase_emits_the_steps_raw_label():
    seen: list[str] = []
    emit_report_phase(seen.append, ReportPhase.GATHERING)
    emit_report_phase(seen.append, ReportPhase.WRITING)
    assert seen == [
        "Gathering the latest information…",
        "Writing your report…",
    ]


def test_emit_report_phase_is_a_no_op_without_a_sink():
    # The whole safety story: a report path can call this unconditionally and a
    # scheduled/test caller (no sink) pays nothing and raises nothing.
    emit_report_phase(None, ReportPhase.GATHERING)
    emit_report_phase(None, ReportPhase.WRITING)


def test_emit_report_phase_never_breaks_the_answer_when_the_sink_raises():
    def boom(_label):
        raise RuntimeError("transport down")

    # Best-effort: a failing sink is display transport, never the answer.
    emit_report_phase(boom, ReportPhase.ANALYZING)


def test_research_stage_phase_maps_each_known_stage_to_its_own_line():
    seen: list[str] = []
    for stage in ("products", "positioning", "pricing", "market_news"):
        emit_research_stage_phase(seen.append, stage)
    assert seen == [
        "Researching products & features…",
        "Researching positioning…",
        "Researching pricing…",
        "Researching market & recent news…",
    ]


def test_research_stage_phase_unknown_stage_falls_back_to_gathering():
    seen: list[str] = []
    emit_research_stage_phase(seen.append, "some_new_stage")
    assert seen == [ReportPhase.GATHERING.value]


def test_research_stage_map_covers_every_defined_company_research_stage():
    # If company_research adds a stage, this fails until the shared vocabulary
    # gets a line for it — the mapping is the single source, so it must lead.
    from app import company_research

    stage_keys = {stage for stage, _brief in company_research._STAGES}
    assert stage_keys <= set(RESEARCH_STAGE_PHASES)


def test_every_report_phase_value_is_a_nonempty_user_safe_label():
    for phase in ReportPhase:
        assert phase.value and phase.value.endswith("…")
        # No ids, counts, mechanism, or internal tokens in the raw label.
        low = phase.value.lower()
        for banned in ("enterprise", "llm_call", "kg", "_", "select", "http"):
            assert banned not in low

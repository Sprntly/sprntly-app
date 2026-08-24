"""Shared report-progress phase vocabulary + emit helper.

ONE place that defines the standard progress steps for the long report waits
(voice-of-customer, market-intel, public-feedback, company-research, …) and the
one helper every report calls to announce them. Before this module each report
either emitted nothing (the reported gap: a four-minute voice-of-customer wait
showed the same generic "thinking" beat as a four-second answer) or would have
grown its own ad-hoc phase strings. Centralising the vocabulary here means a
report path adds progress by threading `on_phase` and calling
`emit_report_phase` at its real seams — no report invents or duplicates a phase
string, and the user-facing copy is curated in exactly one downstream place
(`web/app/lib/friendlyPhase.ts`, which maps each raw label below to safe copy).

Layering: `qa_agent.emit_phase` is the transport-safe primitive (best-effort,
no-op without a sink). This module is the vocabulary layer on top of it — the
named steps and the thin `emit_report_phase` wrapper that emits a step's raw
label. The safety (no-op without a sink, never break the answer) is inherited
from `emit_phase`, so it too lives in a single source.

Egress contract: the raw labels here are deliberately generic and user-safe —
no tenant data, ids, counts, or mechanism. `friendlyPhase.ts` maps each by its
leading intent to a hardcoded user-facing constant and never echoes the input,
so even the fixed company-research stage names surface as their own curated
line rather than raw text.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from app.qa_agent import emit_phase


class ReportPhase(str, Enum):
    """The standard, reusable report-progress steps.

    The value is the RAW backend label emitted onto the phase sink. It must
    have a matching entry in `web/app/lib/friendlyPhase.ts` or it falls back to
    the generic wait line (a `friendlyPhase` test guards that mapping).
    """

    # The three canonical legs every report can narrate.
    GATHERING = "Gathering the latest information…"
    ANALYZING = "Analyzing the findings…"
    WRITING = "Writing your report…"

    # Company-research runs a STAGED sweep, and each stage is a discrete,
    # multi-search leg the user waits minutes on — so each gets its own line,
    # turning the sweep into a visible checklist rather than one long silence.
    # The stage set is fixed and non-tenant, so surfacing it is safe.
    RESEARCH_PRODUCTS = "Researching products & features…"
    RESEARCH_POSITIONING = "Researching positioning…"
    RESEARCH_PRICING = "Researching pricing…"
    RESEARCH_MARKET = "Researching market & recent news…"


# Map company-research's internal stage keys (`_STAGES`) to the shared step, so
# the report path stays a thin lookup and the vocabulary still lives only here.
# Falls back to GATHERING for any stage key not enumerated above.
RESEARCH_STAGE_PHASES: dict[str, "ReportPhase"] = {
    "products": ReportPhase.RESEARCH_PRODUCTS,
    "positioning": ReportPhase.RESEARCH_POSITIONING,
    "pricing": ReportPhase.RESEARCH_PRICING,
    "market_news": ReportPhase.RESEARCH_MARKET,
}


def emit_report_phase(
    on_phase: Optional[Callable[[str], None]],
    step: "ReportPhase",
) -> None:
    """Announce a standard report leg on the chat's waiting surface.

    A no-op when no sink is wired (tests, scheduled callers) — delegates to
    `qa_agent.emit_phase`, so the "never break the answer" guarantee is defined
    once. Every report path calls THIS rather than emitting a phase string of
    its own, which is what keeps the vocabulary single-sourced.
    """
    emit_phase(on_phase, step.value)


def emit_research_stage_phase(
    on_phase: Optional[Callable[[str], None]],
    stage: str,
) -> None:
    """Announce one company-research sweep stage by its internal `_STAGES` key.

    Convenience over `emit_report_phase` for the staged sweep: it resolves the
    stage key to the shared step (GATHERING for anything unmapped) so the sweep
    loop stays a one-liner and the stage→copy mapping stays in this module.
    """
    emit_report_phase(on_phase, RESEARCH_STAGE_PHASES.get(stage, ReportPhase.GATHERING))

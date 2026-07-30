"""The report kinds offered by the Artifacts panel's "New report" picker.

Reports are normally CAPTURED from chat (app/report_capture.py): you ask for a
VoC report, the skill answers with a document, and it lands in the library. This
module backs the other direction — starting one straight from the Artifacts
surface, without composing a prompt.

A CURATED LIST, deliberately. ~78 skills are routable and many produce
document-shaped output, but only these produce the report-style HTML artifact
this surface is for; offering every skill would turn a focused picker into a
skill browser. Capture stays open-ended — a report generated from ANY skill in
chat is still captured — so this list bounds what we *offer*, not what we *keep*.

`prompt` is the question sent to the ask pipeline with the skill pinned, so the
generated report is identical to the one the same request would produce in chat
(same skill, same method, same capture path). Each is written to leave the skill's
own self-scoping intact rather than over-constraining it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportKind:
    """One entry in the picker. `skill` must be a real vendored skill id — the
    route drops any entry whose skill is missing rather than offering a button
    that would fail on click."""

    skill: str
    label: str
    blurb: str
    prompt: str


REPORT_KINDS: tuple[ReportKind, ...] = (
    ReportKind(
        skill="voice-of-customer-report",
        label="Voice of Customer",
        blurb="Themes, frustration and quotes from your own customer conversations.",
        prompt=(
            "Produce a Voice of Customer report from our customer feedback. "
            "Scope it to the most recent period the data supports and say which "
            "sources and window you used."
        ),
    ),
    ReportKind(
        skill="competitive-intelligence-review",
        label="Competitor Analysis",
        blurb="Where we stand against rivals, and the product decisions that follow.",
        prompt=(
            "Produce a competitive intelligence review for our product. Start from "
            "our own position, cover the competitors that actually shape our buyer's "
            "decision, and end in ranked product decisions."
        ),
    ),
    ReportKind(
        skill="public-feedback-report",
        label="Public Feedback",
        blurb="What people say about us in public — app stores, social, review sites.",
        prompt=(
            "Produce a public feedback report on what people are saying about us "
            "on public channels."
        ),
    ),
)


def available_report_kinds() -> list[dict]:
    """The picker's entries, filtered to skills that actually exist.

    A vendored skill can be renamed or removed; offering a kind whose skill is
    gone would surface a button that fails only once clicked. Checking the
    catalog here keeps the picker honest by construction.
    """
    from app.skills.loader import list_skills

    known = set(list_skills())
    return [
        {"skill": k.skill, "label": k.label, "blurb": k.blurb}
        for k in REPORT_KINDS
        if k.skill in known
    ]


def prompt_for_kind(skill: str) -> str | None:
    """The seeded question for `skill`, or None if it is not an offered kind.

    None is the route's 400: a caller may only generate the kinds we offer, not
    pin an arbitrary skill through this entry point.
    """
    for k in REPORT_KINDS:
        if k.skill == skill:
            return k.prompt
    return None

"""Which findings bear on the goal that was asked, and which merely surfaced.

Apurva: "The approach you are using to determine how to drive revenue is based
on the number of themes we are seeing... Look at the user complaints from sales
calls and figure out which ones if addressed will unlock revenue."

THE PROBLEM THIS SOLVES, in the engine's own words. `_rank` orders by
`(conflict, -impact_value, -confidence)`, and with no revenue mapped to accounts
`impact_value` IS `affected_population` — so the order is "how many accounts
mentioned this theme". What gets mentioned most on a sales call is the vendor's
own demo, and a real run for "grow revenue by 5%" put three product-capability
descriptions in its top five. The report already conceded the gap in as many
words: "It did not decide which findings appear below — nothing here was
filtered or ranked by it."

WHAT THIS IS AND IS NOT.

I2 says no LLM returns a score, a rank or a decision, and this module IS a
selection — Apurva ruled for it after the alternative (a claim-type filter) was
shown to demote real bugs alongside the demo talk. What is preserved is
everything I2 was protecting:

  - IT NEVER REORDERS. Findings that survive keep the frozen order they
    arrived in. The gate answers one yes/no per finding and nothing else.
  - IT NEVER RESCORES. Impact and confidence are computed before this runs and
    are not passed in, so they cannot be touched.
  - NOTHING IS DELETED. A finding judged irrelevant MOVES to an appendix,
    carrying the reason it was set aside — the shape Apurva's reference memo
    uses for the ten initiatives it did not recommend. The counts are stated up
    front, so a reader sees the funnel rather than a shorter list.
  - IT FAILS OPEN. If the call dies, every finding stays. A failed relevance
    pass that quietly hid three hundred findings would make a thin report look
    like a decisive one, which is the worst outcome available here.
  - A CONFLICT IS NEVER SET ASIDE. Two sources that may both speak disagreeing
    is the most decision-relevant thing a run can find — `_rank` already puts
    it first regardless of size, and no relevance judgement outranks that.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

from app.crucible.types import Finding

logger = logging.getLogger(__name__)

#: How many findings are judged in one model call. Themes are short — a label
#: and one quote — so a chunk this size stays well inside the input budget while
#: keeping the number of calls small on a 300-finding run.
CHUNK = 60

#: The most findings the gate will judge at all.
#:
#: A BOUND ON COST, AND IT IS DISCLOSED. Anything past it stays in the main
#: list rather than being set aside — an unjudged finding is not an irrelevant
#: one, and the renderer says how many were not evaluated. Silently truncating
#: here would be the "no silent caps" failure: a reader would see a filtered
#: list and have no way to know the filter ran out.
MAX_JUDGED = 240

RELEVANCE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "bears_on_goal", "reason"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "bears_on_goal": {"type": "boolean"},
                    "reason": {
                        "type": "string", "minLength": 4, "maxLength": 200,
                        "description": (
                            "Why it does or does not bear on the goal. For a "
                            "no, this is what the reader will see beside it in "
                            "the appendix, so make it specific to the theme."
                        ),
                    },
                },
            },
        },
    },
}

_SYSTEM = """You decide which findings bear on a specific business goal.

You are given a goal, the reader's own definition of the metric, and a numbered \
list of themes found in their company's calls, tickets, messages and documents.

For each theme answer ONE question: if this were addressed, could it plausibly \
move the metric as the reader has defined it?

Answer `true` when the theme describes something the company could act on that \
bears on the goal — a customer problem, an objection, a blocker, a request, a \
process failure, a competitive gap.

Answer `false` when the theme is not about something addressable that bears on \
this goal. The most common cases, from real data:
- a description of the company's OWN product or its capabilities, harvested \
from a sales demo. "The platform supports multi-role scenario customization" \
is the vendor talking about themselves, not a problem to solve.
- routine pipeline mechanics with no problem in them — a contact agreeing to \
meet, a demo being scheduled, a follow-up date.
- internal administration unrelated to the metric.

Judge the THEME, not how many accounts mentioned it — you are not ranking, \
sizing or ordering anything, and a theme mentioned once may bear on the goal \
while one mentioned thirty times may not.

Be willing to say false. A reader asked a specific question; a list that keeps \
everything answers a different one. But when genuinely unsure, answer true — a \
kept finding costs a line, a wrongly-set-aside one costs the answer."""


@dataclass(frozen=True)
class Verdict:
    """One yes/no, and why."""
    bears_on_goal: bool
    reason: str


def _offline() -> bool:
    """True when no model should be called.

    A FUNCTION, not an inline `"pytest" in sys.modules`, so a test that wants
    the real path can monkeypatch this one seam — the convention
    `crucible.recommend`, `project_memory` and `graph.decision_log` use.
    """
    return "pytest" in sys.modules


def _input(goal_text: str, definition_text: str, findings: Sequence[Finding]) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        "THEMES:",
    ]
    for f in findings:
        theme = (f.label or f.statement or "").strip()
        lines.append(f"- finding_id: {f.id} | theme: {theme}")
        if f.example:
            lines.append(f"    one claim: {f.example}")
    return "\n".join(lines)


def _judge_chunk(
    *, enterprise_id: str, goal_text: str, definition_text: str,
    findings: Sequence[Finding],
) -> dict[str, Verdict]:
    from app.graph.gateway import llm_call

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="judge_goal_relevance",
        prompt_version="crucible-relevance-v1",
        system=_SYSTEM,
        input=_input(goal_text, definition_text, findings),
        json_schema=RELEVANCE_SCHEMA,
        max_tokens=8000,
    )
    out = result.output
    if not isinstance(out, dict):
        return {}
    known = {f.id for f in findings}
    kept: dict[str, Verdict] = {}
    for v in out.get("verdicts") or []:
        if not isinstance(v, dict):
            continue
        fid = (v.get("finding_id") or "").strip()
        if fid not in known:
            continue
        reason = (v.get("reason") or "").strip()
        if not reason:
            continue
        kept[fid] = Verdict(bool(v.get("bears_on_goal")), reason)
    return kept


def judge_relevance(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str,
    findings: Sequence[Finding],
) -> dict[str, Verdict]:
    """Which findings bear on the goal. Absent id == judged relevant.

    TOTAL — never raises, and never returns a verdict it did not get. A finding
    the model did not answer for, or a chunk whose call failed, is simply not in
    the returned map, and the caller keeps it. Failing open is the whole safety
    property: the cost of keeping an irrelevant finding is a line in a list, and
    the cost of hiding a relevant one is the answer.
    """
    if _offline() or not findings:
        return {}

    verdicts: dict[str, Verdict] = {}
    judged = list(findings)[:MAX_JUDGED]
    for i in range(0, len(judged), CHUNK):
        chunk = judged[i:i + CHUNK]
        try:
            verdicts.update(_judge_chunk(
                enterprise_id=enterprise_id, goal_text=goal_text,
                definition_text=definition_text, findings=chunk,
            ))
        except Exception:  # noqa: BLE001 — one bad chunk must not lose the rest
            logger.exception(
                "crucible: relevance chunk %s failed; its findings are kept", i
            )
    return verdicts


def partition(
    findings: Sequence[Finding], verdicts: dict[str, Verdict],
) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Split into kept and set-aside, IN THE ORDER GIVEN.

    The order is the frozen rank and this function does not touch it — it walks
    the list once and appends. A gate that reordered would be making the
    decision I2 reserves for the scorers.
    """
    kept: list[Finding] = []
    aside: list[tuple[Finding, str]] = []
    for f in findings:
        v = verdicts.get(f.id)
        # NO VERDICT MEANS KEEP — an unjudged finding is not an irrelevant one.
        if v is None or v.bears_on_goal:
            kept.append(f)
            continue
        # AN AUTHORITATIVE CONFLICT IS NEVER SET ASIDE. Two sources that may
        # both speak disagreeing outranks a relevance judgement, which is the
        # same reason `_rank` puts it first regardless of size.
        if f.adjudication == "conflict":
            kept.append(f)
            continue
        aside.append((f, v.reason))
    return kept, aside

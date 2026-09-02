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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Sequence

from app.crucible.types import Finding

logger = logging.getLogger(__name__)

#: How many findings are judged in one model call.
#:
#: SIZED TO THE SDK TIMEOUT, NOT JUST THE BUDGET BELOW. Measured on a
#: 14,509-signal run: a 60-finding chunk took 88.9s against
#: `app.llm._REQUEST_TIMEOUT_S = 120.0` — close enough that a modestly slower
#: call times out and `_create_with_retries` retries it up to
#: `app.llm.MAX_ATTEMPTS = 4` times, turning one slow chunk into ~8 minutes
#: inside enrichment. Smaller chunks leave real margin against that ceiling.
CHUNK = 40

#: How many chunks may be in flight at once.
#:
#: STATED AND SMALL, DELIBERATELY. `app.llm`'s concurrency gate
#: (`LLM_MAX_CONCURRENCY`, default 6) is process-wide and shared with every
#: interactive chat call on the box — the client's #1 latency priority right
#: now. This gate judges relevance for a run a user is waiting on, so it is
#: allowed to run in parallel, but only a couple of slots at a time, never the
#: whole cap: on a 6-slot box, 2 here still leaves 4 free for chat.
MAX_PARALLEL = 2

#: How long the whole gate may take, in seconds.
#:
#: A DEADLINE, BECAUSE LATENCY IS A FAILURE MODE AND I ONLY GUARDED ERRORS.
#: The first version wrapped every call in a try and called that failing open.
#: It is not: a call that never returns raises nothing, so the run sat past the
#: last narration line with its findings computed, unsaved and invisible, and
#: from the reader's side a gate that eventually succeeds after ten minutes is
#: indistinguishable from one that died. Observed on staging — a 149-finding run
#: hung for thirteen minutes behind three sequential calls.
#:
#: Past this the gate stops asking and everything still unjudged is KEPT, which
#: is the same direction every other failure here resolves in.
DEADLINE_SECONDS = 75.0

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
                "required": ["idx", "bears_on_goal", "reason"],
                "properties": {
                    # THE ORDINAL POSITION IN THE NUMBERED LIST, not the
                    # finding's real id. A finding id is a long opaque string
                    # repeated once per verdict in the OUTPUT — the wall clock
                    # here is output-token-bound, so that repetition is pure
                    # waste. `_judge_chunk` maps the index back to the real id.
                    "idx": {"type": "integer"},
                    "bears_on_goal": {"type": "boolean"},
                    "reason": {
                        "type": "string", "maxLength": 200,
                        "description": (
                            "Required, and specific to the theme, when "
                            "bears_on_goal is false — this is what the reader "
                            "sees beside it in the appendix. Leave empty "
                            '("") when bears_on_goal is true: a kept '
                            "finding's reason is never shown anywhere, so "
                            "writing one only spends output tokens for "
                            "nothing displayed."
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
process failure, a competitive gap. Leave `reason` empty ("") on a `true` \
verdict — it is never shown to the reader, so writing one wastes your output.

Answer `false` when the theme is not about something addressable that bears on \
this goal. The most common cases, from real data:
- a description of the company's OWN product or its capabilities, harvested \
from a sales demo. "The platform supports multi-role scenario customization" \
is the vendor talking about themselves, not a problem to solve.
- routine pipeline mechanics with no problem in them — a contact agreeing to \
meet, a demo being scheduled, a follow-up date.
- internal administration unrelated to the metric.
A `false` verdict ALWAYS needs a `reason`, specific to that theme — the reader \
sees it printed beside the theme in an appendix, so it must say why THIS one \
does not bear on the goal, not a generic phrase.

Judge the THEME, not how many accounts mentioned it — you are not ranking, \
sizing or ordering anything, and a theme mentioned once may bear on the goal \
while one mentioned thirty times may not.

Reply with one verdict per theme shown, using the theme's own number as `idx`.

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
    """Numbered 1..N — the number IS `idx`, the only handle the model gets back
    to a theme. Sending the real finding id here (and asking for it back in
    every verdict) is the input half of the same waste `idx` removes from the
    output; the id never has to leave this function."""
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        "THEMES:",
    ]
    for i, f in enumerate(findings, start=1):
        theme = (f.label or f.statement or "").strip()
        lines.append(f"{i}. {theme}")
        if f.example:
            lines.append(f"    one claim: {f.example}")
    return "\n".join(lines)


def _judge_chunk(
    *, enterprise_id: str, goal_text: str, definition_text: str,
    findings: Sequence[Finding],
) -> dict[str, Verdict]:
    from app.graph.gateway import llm_call
    from app.llm import FAST_MODEL

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="judge_goal_relevance",
        prompt_version="crucible-relevance-v2",
        # HIGH-VOLUME, closed-set, short-output — exactly the shape
        # `FAST_MODEL`'s own charter names (`app/llm.py`). Ranking eight
        # letters is not the reasoning-depth job `DEFAULT_MODEL` is for.
        model=FAST_MODEL,
        system=_SYSTEM,
        input=_input(goal_text, definition_text, findings),
        json_schema=RELEVANCE_SCHEMA,
        max_tokens=8000,
    )
    out = result.output
    if not isinstance(out, dict):
        return {}
    # 1-indexed, matching `_input`'s numbering.
    by_idx = {i: f for i, f in enumerate(findings, start=1)}
    kept: dict[str, Verdict] = {}
    for v in out.get("verdicts") or []:
        if not isinstance(v, dict):
            continue
        f = by_idx.get(v.get("idx"))
        if f is None:
            continue
        bears = bool(v.get("bears_on_goal"))
        reason = (v.get("reason") or "").strip()
        # A `false` WITH NO REASON IS NOT USABLE. The reason is what the
        # reader sees in the appendix beside the theme; without one a false
        # verdict would set a finding aside and say nothing about why, which
        # is the silent filtering this whole design exists to avoid. A `true`
        # verdict needs no reason at all — it is never rendered — so only the
        # `false` side is held to this.
        if not bears and not reason:
            continue
        kept[f.id] = Verdict(bears, reason)
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

    judged = list(findings)[:MAX_JUDGED]
    chunks = [judged[i:i + CHUNK] for i in range(0, len(judged), CHUNK)]
    verdicts: dict[str, Verdict] = {}
    deadline = time.monotonic() + DEADLINE_SECONDS
    done = 0
    # PARALLEL, IN BOUNDED WAVES OF `MAX_PARALLEL` — never a single unbounded
    # fan-out. A wave of chunks is submitted together and this thread waits for
    # ALL of it before deciding whether to start the next, so the deadline is
    # still checked BEFORE new work starts, exactly as it was sequentially: a
    # call already in flight always finishes (there is nothing to save by
    # aborting a thread mid-request, and the SDK gives no such hook), but no
    # NEW wave begins once the budget is gone.
    with ThreadPoolExecutor(
        max_workers=max(1, min(MAX_PARALLEL, len(chunks))),
        thread_name_prefix="crucible-relevance",
    ) as ex:
        i = 0
        while i < len(chunks):
            if time.monotonic() >= deadline:
                logger.warning(
                    "crucible: relevance gate hit its %ss deadline after %s "
                    "of %s findings; the rest are kept",
                    DEADLINE_SECONDS, done, len(judged),
                )
                break
            wave = chunks[i:i + MAX_PARALLEL]
            futures = [
                ex.submit(
                    _judge_chunk, enterprise_id=enterprise_id,
                    goal_text=goal_text, definition_text=definition_text,
                    findings=c,
                )
                for c in wave
            ]
            for fut, c in zip(futures, wave):
                try:
                    verdicts.update(fut.result())
                except Exception:  # noqa: BLE001 — one bad chunk must not lose
                    # the rest.
                    logger.exception(
                        "crucible: relevance chunk %s failed; its findings "
                        "are kept", i,
                    )
                done += len(c)
            i += len(wave)
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

"""What to DO about a finding — the one thing the engine never said.

Apurva, on a real report: "this is only the issues, no suggestion on how to
solve or what's the exact recommendation from it".

WHAT THIS IS AND IS NOT, because the distinction is the whole design.

I2 says no LLM returns a score, a rank or a decision. That invariant is intact
and this module is why it can stay intact while the document still recommends
something: every NUMBER a reader sees — impact, confidence band, ordering — is
computed by `scoring.py` and frozen by `_rank` BEFORE this module is called, and
nothing here is ever fed back into any of them. `build_recommendations` takes
findings that are already ranked and returns prose to hang beside them.

The test that keeps this honest is `test_recommendations_never_move_the_ranking`:
it runs the pipeline twice, with and without, and asserts the order and every
score are identical. A recommendation that could change what ranks first would
be a decision, and then I2 really would be broken.

THREE THINGS THE PROSE IS NOT ALLOWED TO DO, enforced after generation rather
than asked for in the prompt, because a prompt is a request and a check is a
guarantee:

  1. Assert an outcome. "Fixing this will recover 12 accounts" is a causal claim
     the corpus cannot support, and I5 already forbids it in a finding — a
     recommendation is not a loophole. Linted with the same `lint_claim`.
  2. Quote a figure. The corpus has no revenue mapped to accounts, so any
     currency amount or percentage is invention. Same rule the plan step lives
     under, for the same reason.
  3. Outrun its evidence. The justification may only rest on what the finding's
     own claims say, which is why the input carries those claims and nothing
     else — no company profile, no other findings, no outside knowledge.

A recommendation that fails a check is DROPPED, not repaired. The finding still
renders exactly as it did before; the reader loses a suggestion and keeps a
document that never says anything it cannot stand behind.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

from app.crucible.lint import lint_claim
from app.crucible.types import Claim, Finding

logger = logging.getLogger(__name__)

#: How many findings get a recommendation.
#:
#: NOT a cost guess — a reading limit. A document that recommends 296 things
#: recommends nothing, and the reader has already been told the list is ordered.
#: The cap applies to the TOP of the frozen order, so the recommendations land
#: on the findings the ranking already put first.
MAX_RECOMMENDED = 8

#: Claims sent per finding. Enough to ground a suggestion, few enough that eight
#: findings fit one call.
MAX_CLAIMS_PER_FINDING = 6

#: Currency figures and percentages, which the corpus cannot support.
_FIGURE = re.compile(r"[$£€]\s?\d|\b\d+(?:\.\d+)?\s?%")

#: Verbs that promise an outcome rather than propose an action.
_PROMISE = re.compile(
    r"\b(will (?:recover|increase|reduce|unlock|drive|deliver|save|win)"
    r"|guarantee\w*|ensures?|results? in|leads? to)\b",
    re.IGNORECASE,
)

RECOMMENDATION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recommendations"],
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "action", "because"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "action": {
                        "type": "string", "minLength": 8, "maxLength": 240,
                        "description": "One thing to DO, in the imperative.",
                    },
                    "because": {
                        "type": "string", "minLength": 8, "maxLength": 400,
                        "description": (
                            "Why this action follows FROM THE CLAIMS SHOWN. "
                            "Cite what the sources said, not what you know."
                        ),
                    },
                },
            },
        },
    },
}

_SYSTEM = """You read findings from an evidence engine and propose what to do \
about each one.

The findings are already ranked and sized. You are not scoring, ordering or \
selecting anything — every finding you are shown gets exactly one \
recommendation, in the order given.

Rules, all of them hard:
- Propose an ACTION, in the imperative. "Route export tickets to the rendering \
on-call team", not "there is a routing problem".
- Justify it ONLY from the claims shown for that finding. You have no other \
information about this company and must not imply that you do.
- Never assert an outcome. You may not say a change will recover, increase, \
reduce or unlock anything. The evidence does not establish cause, and a \
recommendation that promises a result is worse than none.
- Never quote a figure. No currency amounts, no percentages. The corpus carries \
no revenue mapped to accounts.
- If the claims do not support any action, say so in `action` as "No action \
this evidence supports" and explain what is missing in `because`.

Write for a product manager who will have to defend the suggestion to their \
leadership from the same evidence."""


@dataclass(frozen=True)
class Recommendation:
    """One suggestion, already checked."""
    finding_id: str
    action: str
    because: str


def _claims_for(finding: Finding, claims_by_id: dict[str, Claim]) -> list[str]:
    out = []
    for cid in finding.claim_ids[:MAX_CLAIMS_PER_FINDING]:
        c = claims_by_id.get(cid)
        if c is None:
            continue
        said = (c.assertion or "").strip()
        if said:
            out.append(said)
    return out


def _input(
    goal_text: str, definition_text: str,
    findings: Sequence[Finding], claims_by_id: dict[str, Claim],
) -> str:
    lines = [
        f"GOAL: {goal_text}",
        f"THE READER'S OWN DEFINITION OF THE METRIC: {definition_text}",
        "",
        "FINDINGS. Each is followed by the claims it rests on. Recommend one "
        "action per finding, grounded only in its own claims.",
        "",
    ]
    for f in findings:
        lines.append(f"--- finding_id: {f.id}")
        lines.append(f"theme: {f.label or f.statement}")
        for said in _claims_for(f, claims_by_id):
            lines.append(f"  - {said}")
        lines.append("")
    return "\n".join(lines)


def _acceptable(rec: dict, finding: Finding, strength: str) -> Optional[Recommendation]:
    """Every check that keeps a suggestion inside what the evidence supports."""
    action = (rec.get("action") or "").strip()
    because = (rec.get("because") or "").strip()
    if not action or not because:
        return None
    both = f"{action} {because}"
    if _FIGURE.search(both):
        logger.info("crucible: dropped a recommendation quoting a figure")
        return None
    if _PROMISE.search(both):
        logger.info("crucible: dropped a recommendation promising an outcome")
        return None
    # I5, through the same gate a finding passes.
    if not lint_claim(because, strength).ok:
        logger.info("crucible: dropped a recommendation that failed the lint")
        return None
    return Recommendation(finding_id=finding.id, action=action, because=because)


def _offline() -> bool:
    """True when no model should be called.

    A FUNCTION, not an inline `"pytest" in sys.modules`, so a test that WANTS to
    exercise the real path can monkeypatch this one seam — the convention
    `project_memory` and `graph.decision_log` already use here.

    Without it, adding the first model call to the crucible run path made every
    route test in the suite attempt a network connection and wait for it to
    fail. The call is wrapped in a try, so those tests still passed — slowly,
    and with a stack trace in the log that means nothing.
    """
    return "pytest" in sys.modules


def build_recommendations(
    *,
    enterprise_id: str,
    goal_text: str,
    definition_text: str,
    findings: Sequence[Finding],
    claims: Sequence[Claim],
) -> dict[str, Recommendation]:
    """One suggestion per top finding, or {} if the call fails.

    TOTAL — never raises. A run that produced good findings must not die because
    the suggestion layer did; the document renders without recommendations and
    says nothing false.
    """
    top = list(findings)[:MAX_RECOMMENDED]
    if not top or _offline():
        return {}
    claims_by_id = {c.id: c for c in claims}
    # The strength a recommendation is linted at is the WEAKEST of the claims it
    # rests on, not the strongest: a suggestion inherits the least of what
    # supports it, and linting at the strongest would let a causal phrasing
    # through on the back of one good claim.
    strength_of: dict[str, str] = {}
    for f in top:
        strengths = [claims_by_id[c].strength for c in f.claim_ids
                     if c in claims_by_id]
        strength_of[f.id] = min(strengths, key=_STRENGTH_ORDER.index) if strengths else "reported"

    try:
        from app.graph.gateway import llm_call

        result = llm_call(
            enterprise_id=enterprise_id,
            agent="crucible",
            purpose="recommend_actions",
            prompt_version="crucible-recommend-v1",
            system=_SYSTEM,
            input=_input(goal_text, definition_text, top, claims_by_id),
            json_schema=RECOMMENDATION_SCHEMA,
            max_tokens=4000,
        )
        out = result.output
    except Exception:  # noqa: BLE001 — the suggestion layer never kills a run
        logger.exception("crucible: recommendation call failed")
        return {}

    if not isinstance(out, dict):
        return {}
    by_id = {f.id: f for f in top}
    kept: dict[str, Recommendation] = {}
    for rec in out.get("recommendations") or []:
        if not isinstance(rec, dict):
            continue
        f = by_id.get((rec.get("finding_id") or "").strip())
        if f is None:
            continue
        ok = _acceptable(rec, f, strength_of.get(f.id, "reported"))
        if ok is not None:
            kept[f.id] = ok
    return kept


#: Weakest first, so `min` picks the least-supported claim.
_STRENGTH_ORDER = ["reported", "inferred", "correlated", "measured", "causally_tested"]

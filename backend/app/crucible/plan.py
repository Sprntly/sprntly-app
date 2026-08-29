"""Stage 1 — say what will be done, BEFORE doing it.

WHY A PLAN STEP EXISTS AT ALL. A run reads a tenant's whole corpus and takes
minutes. Until now the first thing a user learned about its limits was the
coverage notes at the bottom of the finished output — after the wait, and
phrased as an apology. The same facts are worth far more BEFOREHAND, where they
are a decision: connect the missing source, narrow the window, or accept a
qualitative answer and press on.

THE PLAN IS AN INVENTORY, NOT A SAMPLE. It counts what exists per source and
reads no content, so it costs a few queries and returns in about a second. That
is deliberate: a plan that had to read the corpus to describe it would be the
expensive thing it exists to gate.

WHAT MAKES IT ACTIONABLE. Sprntly can ingest numbers — connectors exist, and a
user can upload a document. So a gap is never reported as a dead end: every
`cannot_answer` entry carries the thing that would close it. "No analytics
source is connected, so this run cannot state a point estimate" is a shrug;
"…connect Amplitude, or upload the cohort export" is a next step.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.crucible.claims import AUTHORITATIVE_FOR

logger = logging.getLogger(__name__)

#: What each source can and cannot witness, in the user's language rather than
#: the type system's. Mirrors AUTHORITATIVE_FOR — the plan must not promise more
#: than the engine will accept later.
_SOURCE_PROSE: dict[str, tuple[str, str]] = {
    "customer_voice": ("calls and customer tickets",
                       "what customers asked for and reported"),
    "communication":  ("Slack and email",
                       "what was discussed, hit and attempted"),
    "project_mgmt":   ("the tracker",
                       "what was built, broken, blocked or attempted"),
    "pm_manual":      ("your own business context",
                       "the company's stated constraints and goals"),
    "analytics":      ("product analytics",
                       "how much something moved, and in which direction"),
    "revenue":        ("revenue data",
                       "how much something moved, and in which direction"),
    "outcome_measured": ("measured outcomes",
                         "whether a change actually worked"),
    "verbal_claim":   ("unverified claims", "nothing — recorded, never counted"),
    "agent_inferred": ("our own inferences", "nothing — recorded, never counted"),
}

#: Sources that carry NUMBERS. Without at least one, no finding can be stated as
#: a point estimate in the goal's own unit — only as reach. This is the single
#: most consequential thing a user can learn before a run rather than after.
NUMERIC_SOURCES = ("analytics", "revenue", "outcome_measured")

#: How to close each gap. Sprntly can ingest all of these, so a gap is a next
#: step rather than a limitation.
_REMEDY = {
    "analytics": "connect Amplitude (or your product analytics), "
                 "or upload a cohort export",
    "revenue": "connect Stripe or your billing export",
    "outcome_measured": "connect your experiment tool, or upload the "
                        "experiment history",
    "customer_voice": "connect Gong, Fireflies or Zendesk",
    "project_mgmt": "connect Jira or Linear",
}


@dataclass(frozen=True)
class SourceInventory:
    source_type: str
    signal_count: int
    label: str
    witnesses: str


@dataclass(frozen=True)
class Gap:
    """Something this run will NOT be able to answer, and how to change that."""
    question: str
    because: str
    remedy: str


@dataclass(frozen=True)
class RunPlan:
    goal_text: str
    definition_text: str
    currency: str
    sources: tuple[SourceInventory, ...] = ()
    cannot_answer: tuple[Gap, ...] = ()
    will_produce: tuple[str, ...] = ()
    total_signals: int = 0
    #: Source types the user has excluded. Empty means everything available.
    excluded_sources: tuple[str, ...] = ()
    #: The user's own hypotheses, to be killed or carried EXPLICITLY. Without
    #: these a run can only report what it found; with them it can also say
    #: "the thing you believed is not supported, and here is what killed it",
    #: which is usually the more valuable half.
    hypotheses: tuple[str, ...] = ()
    #: WHERE THE DEFINITION CAME FROM, and whether a person has said yes to it.
    #:
    #: The goal definition used to be settled at its own gate, one screen
    #: earlier, before the plan existed. Product feedback collapsed the two: a
    #: separate clarification step made the reader answer a question with no
    #: context for it, and the answer it collected showed exactly why — a run
    #: went out with its definition recorded as the literal words "that is
    #: accurate", because that is what the reader typed at a question that was
    #: not asking for a definition.
    #:
    #: So the definition now arrives here as a PROPOSAL and is adopted by the
    #: same click that approves the plan. I9 is unchanged and these three
    #: fields are how it stays unchanged: the proposal must be shown, be
    #: attributed to whatever produced it, and be editable at the moment of
    #: approval. `definition_adopted` is False for exactly as long as no person
    #: has said yes.
    definition_source: str = ""
    definition_note: str = ""
    definition_adopted: bool = False
    #: HOW THE SURVIVORS GET ORDERED, said before the run rather than
    #: discovered in the output.
    #:
    #: RICE by default, on Apurva's call. The `prioritize` skill's own checklist
    #: says a framework should be "chosen with a reason, not defaulted to RICE"
    #: — the reason here is that it is the one the reader asked for, and naming
    #: it in the plan is what makes it a choice they can override rather than a
    #: convention they discover afterwards.
    framework: str = "RICE"
    #: ── THINGS THIS RUN CANNOT KNOW, AND NOW ASKS FOR. ──────────────────
    #:
    #: Apurva: "the plan gate can start asking questions it doesn't know
    #: answers to." Until now the gate asked exactly one thing — what the
    #: metric means — and everything else it lacked was reported as a limit.
    #: Four of the reference memo's sections are unreachable for want of three
    #: numbers, none of which are in any corpus and all of which a PM knows.
    #:
    #: EACH IS OPTIONAL AND EACH IS AN ASSUMPTION WHEN GIVEN. A value typed
    #: into a box is not evidence: it ships as an `AssumedParam` with the range
    #: it plausibly spans, so the document can say what the headline becomes at
    #: the pessimistic end rather than presenting an estimate as a measurement.
    #: That is I8, and it is the difference between asking for input and
    #: laundering a guess into a number.
    #:
    #: WHAT IS DELIBERATELY NOT ASKED HERE: effort. It is per-finding, the
    #: findings do not exist at plan time, and one value applied to every row
    #: is a common divisor that cannot change a ranking.
    #:
    #: What one account is worth per year, in the reader's own currency. Turns
    #: reach-in-accounts into money, which is the spine of the reference memo.
    account_value: Optional[float] = None
    #: Who signs off. The memo's decision box names one.
    decision_owner: str = ""
    #: When the decision is needed. Free text on purpose — "before the Q3 QBR"
    #: is a real answer and a date picker would refuse it.
    needed_by: str = ""

    def to_json(self) -> dict:
        return {
            "goal_text": self.goal_text,
            "definition_text": self.definition_text,
            "currency": self.currency,
            "total_signals": self.total_signals,
            "sources": [asdict(s) for s in self.sources],
            "cannot_answer": [asdict(g) for g in self.cannot_answer],
            "will_produce": list(self.will_produce),
            "excluded_sources": list(self.excluded_sources),
            "hypotheses": list(self.hypotheses),
            "definition_source": self.definition_source,
            "definition_note": self.definition_note,
            "definition_adopted": self.definition_adopted,
            "framework": self.framework,
            "account_value": self.account_value,
            "decision_owner": self.decision_owner,
            "needed_by": self.needed_by,
        }


def source_inventory(company_id: str) -> tuple[list[SourceInventory], int]:
    """Count signals per source. No content read, so this is cheap."""
    from app.db.client import require_client

    client = require_client()
    counts: dict[str, int] = {}
    total = 0
    for source_type in _SOURCE_PROSE:
        try:
            res = (
                client.table("kg_signal")
                .select("id", count="exact")
                .eq("enterprise_id", company_id)
                .eq("source_type", source_type)
                .limit(1)
                .execute()
            )
        except Exception:  # noqa: BLE001 — an uncountable source is reported as
            # absent rather than silently assumed present.
            logger.warning("crucible plan: could not count %s", source_type)
            continue
        n = res.count or 0
        if n:
            counts[source_type] = n
            total += n

    out = []
    for source_type, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        label, witnesses = _SOURCE_PROSE[source_type]
        out.append(SourceInventory(source_type, n, label, witnesses))
    return out, total


def derive_gaps_and_promises(
    kept: "tuple[SourceInventory, ...] | list[SourceInventory]",
    hypotheses: tuple[str, ...] = (),
) -> tuple[tuple["Gap", ...], tuple[str, ...]]:
    """What this run will NOT be able to answer, and what it WILL produce,
    derived from the sources it will actually read.

    EXTRACTED SO THE APPROVE PATH CAN RE-DERIVE THEM. `build_plan` computed
    these from the kept set correctly, but approval narrowed only `sources` and
    `total_signals` in place — so a reader who unticked analytics and revenue
    still got a plan promising "your analytics/revenue data is connected and
    will be read", in the same document that said those sources were excluded.
    Worse, they lost the gap that had just become TRUE ("nothing connected here
    carries numbers") along with its actionable remedy, and were handed "no
    action needed from you" instead.

    Pure: it reads only the kept inventory, so the plan gate and the approve
    path cannot drift.
    """

    present = {s.source_type for s in kept}
    gaps: list[Gap] = []

    if not present & set(NUMERIC_SOURCES):
        gaps.append(Gap(
            question="How many points will this move the metric?",
            because="nothing connected here carries numbers — every source is "
                    "prose, and a magnitude needs instrumentation",
            remedy=_REMEDY["analytics"],
        ))
    else:
        # Connected but not yet usable for sizing. Said plainly, because a user
        # who has connected analytics will reasonably expect a number and
        # should learn otherwise here rather than at the bottom of the output.
        gaps.append(Gap(
            question="How many points will this move the metric?",
            because="your numeric sources are connected and will be read, but "
                    "the engine cannot yet size a finding in the goal's own "
                    "unit — it reports reach instead",
            remedy="no action needed from you; this is the next capability "
                   "being built",
        ))
    if "outcome_measured" not in present:
        gaps.append(Gap(
            question="Did a change like this work last time?",
            because="no measured outcomes are connected, so nothing can close "
                    "the loop between a change and its effect",
            remedy=_REMEDY["outcome_measured"],
        ))
    if "customer_voice" not in present:
        gaps.append(Gap(
            question="What did customers actually ask for?",
            because="the tracker records a PM's paraphrase of a request; only "
                    "the customer is authoritative about their own motive",
            remedy=_REMEDY["customer_voice"],
        ))

    produce = [
        "Themes ranked by how much of your book they touch, each with the "
        "source documents it rests on",
        "A considered-and-dropped list, with the reason each candidate died",
        "Every degradation disclosed beside the findings it affects",
    ]
    if hypotheses:
        # NOT "a verdict on each". Nothing adjudicates hypotheses yet — they are
        # recorded on the run and shown beside the findings so a reader can
        # compare by eye. Promising a verdict the engine cannot deliver is the
        # same overpromise this whole step exists to remove, and it is the
        # second time I have written one into it.
        produce.append(
            f"The {len(hypotheses)} things you already believe, recorded and "
            f"shown beside the findings — this run does NOT yet test them "
            f"against the evidence, so comparing them is still your job"
        )
    # ALWAYS reach, for now. The engine has no numeric sizing path yet: impact
    # is computed from how many accounts a theme touches, whatever sources are
    # connected. Promising "sizes in the goal's own unit" because an analytics
    # source happens to EXIST would be the plan overpromising what the run will
    # deliver — which is the exact dishonesty this step was added to remove.
    # When numeric sizing ships, this branches on the capability, not on the
    # presence of a source.
    produce.append("Sizes stated in reach — how many accounts a theme touches, "
                   "not how many points it will move the metric")
    if present & set(NUMERIC_SOURCES):
        produce.append(
            f"Your {'/'.join(sorted(present & set(NUMERIC_SOURCES)))} data is "
            f"connected and will be read as evidence, but it cannot yet be "
            f"turned into a point estimate — that is the next thing being built"
        )

    return tuple(gaps), tuple(produce)


def build_plan(
    *,
    company_id: str,
    goal_text: str,
    definition_text: str,
    currency: str = "accounts",
    excluded_sources: tuple[str, ...] = (),
    hypotheses: tuple[str, ...] = (),
    definition_source: str = "",
    definition_note: str = "",
    definition_adopted: bool = False,
    framework: str = "RICE",
    account_value: Optional[float] = None,
    decision_owner: str = "",
    needed_by: str = "",
) -> RunPlan:
    """What this run will try to establish, where it will look, and what it
    will not be able to tell you."""
    sources, total = source_inventory(company_id)
    kept = tuple(s for s in sources if s.source_type not in excluded_sources)
    gaps, produce = derive_gaps_and_promises(kept, hypotheses)
    return RunPlan(
        goal_text=goal_text,
        definition_text=definition_text,
        currency=currency,
        sources=tuple(kept),
        cannot_answer=tuple(gaps),
        will_produce=tuple(produce),
        total_signals=sum(s.signal_count for s in kept),
        excluded_sources=tuple(excluded_sources),
        hypotheses=tuple(h.strip() for h in hypotheses if h.strip()),
        definition_source=definition_source,
        definition_note=definition_note,
        definition_adopted=definition_adopted,
        framework=framework or "RICE",
        account_value=account_value,
        decision_owner=decision_owner,
        needed_by=needed_by,
    )

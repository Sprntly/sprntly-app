"""Which investigations this run can actually attempt, given what is connected.

WHY THIS EXISTS. The plan is meant to read like an analyst's opening move:
numbered lines, each naming a thing to go and find out, each saying what the
answer would mean. The failure mode is obvious once stated — asked for eight
numbered lines, a model produces eight numbered lines whether or not the
evidence for them exists, and fills the gaps with numbers it invented. Measured
on real tenants, that gap is not hypothetical: analytics signals run 0-16 per
company and revenue 0-1, while the shape we are aiming at is numeric almost
end to end.

So the count is NOT a target. A line exists only when the sources it needs are
present, which makes fabrication structurally impossible rather than merely
discouraged: an investigation into spend cannot be proposed when nothing
measures spend. A thin tenant gets three lines and a sharp paragraph about what
to connect; an instrumented one gets eight. THE COUNT VARYING IS THE HONESTY.

This is the same rule `cannot_answer` already applies, moved out of a footnote
and into the shape of the plan itself.

INVENTORY ONLY, deliberately. Like the rest of `plan.py`, this reads counts and
never content, so the gate stays about a second. Reading the corpus to describe
the corpus is the expensive thing the plan exists to gate.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Sources that carry numbers. A line that needs to size or trend anything
#: needs at least one of these, and saying so in one place stops each line kind
#: reinventing the rule.
NUMERIC = ("analytics", "revenue")

#: Below this, a source is present but cannot support a claim about SHAPE.
#: Three tickets cannot establish whether reasons are concentrated or
#: scattered, and a line that pretends otherwise is worse than no line: it
#: invites a conclusion the evidence cannot carry. Per source type, because
#: twelve monthly revenue points is a series while twelve tickets is anecdote.
MIN_FOR_SHAPE = {
    "analytics": 20,
    "revenue": 6,
    "customer_voice": 15,
    "project_mgmt": 5,
    "communication": 8,
    "pm_manual": 1,
}


@dataclass(frozen=True)
class LineKind:
    """One investigation the plan may propose.

    `requires_all` must every one be present; `requires_any` needs one. A kind
    with neither is unconditional, which no kind currently is — every
    investigation rests on something.
    """
    key: str
    #: What the line goes and finds out, in the plan's own voice.
    question: str
    #: What makes the answer worth having — the "and here is what it means"
    #: half. Without this a plan is a list of chores.
    reading: str
    requires_all: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()
    #: Stated when the line CANNOT run. Names the missing thing, never "no data".
    absent_because: str = ""
    #: What would make it possible. A gap without a remedy is a shrug.
    remedy: str = ""

    def blocked_by(self, counts: dict[str, int]) -> tuple[str, ...]:
        """Source types that stop this line running. Empty means it can run.

        A source present but BELOW `MIN_FOR_SHAPE` counts as blocking: the
        honest failure is "you have three of these, which cannot show a shape",
        not a confident line resting on three rows.
        """
        def usable(s: str) -> bool:
            return counts.get(s, 0) >= MIN_FOR_SHAPE.get(s, 1)

        missing = [s for s in self.requires_all if not usable(s)]
        if self.requires_any and not any(usable(s) for s in self.requires_any):
            missing.extend(self.requires_any)
        return tuple(dict.fromkeys(missing))


#: The investigations, in the order a reader should meet them: decomposition
#: first (what is actually happening), then shape, then causes, then the
#: forward-looking one.
LINE_KINDS: tuple[LineKind, ...] = (
    LineKind(
        key="decompose_metric",
        question="Split the decline into its causes, and size each one",
        reading="Different causes are different fixes. An onboarding failure, "
                "competitive loss and market mix are three different pieces of "
                "work, and one of them may not be yours to solve at all.",
        requires_any=NUMERIC,
        absent_because="nothing connected here measures the metric over time, "
                       "so there is no curve to decompose",
        remedy="connect analytics or revenue, or upload the monthly export",
    ),
    LineKind(
        key="adoption_shape",
        question="Pull adoption at the level the decision is made, not the "
                 "level it is reported",
        reading="Bimodal means a named-account play. Uniformly low means a "
                "product problem, which is worth considerably more.",
        requires_all=("analytics",),
        absent_because="no product analytics are connected, so adoption cannot "
                       "be seen below the account level",
        remedy="connect Amplitude or Mixpanel, or upload the per-account export",
    ),
    LineKind(
        key="shipped_levers",
        question="Audit what you have already shipped for partial rollout",
        reading="Anything half-rolled-out is revenue you have already built "
                "and not collected — the cheapest thing on this list.",
        requires_all=("project_mgmt",),
        absent_because="no tracker is connected, so there is no record of what "
                       "shipped or how far it rolled out",
        remedy="connect Jira or Linear",
    ),
    LineKind(
        key="cohort_contrast",
        question="Go deep on the accounts that carry most of the outcome",
        reading="Looking for what separates the ones that worked from the ones "
                "that did not. That difference is the intervention.",
        requires_all=("analytics",),
        requires_any=NUMERIC,
        absent_because="sizing the top accounts needs both behaviour and value, "
                       "and one of those is missing",
        remedy="connect analytics alongside revenue or CRM",
    ),
    LineKind(
        key="reason_concentration",
        question="Count the reasons, and see whether they concentrate",
        reading="Concentrated reasons point to one closable gap. Scattered "
                "reasons mean inertia, which is a different and harder problem.",
        requires_all=("customer_voice",),
        absent_because="too little customer voice is connected to tell a "
                       "concentrated reason from a scattered one",
        remedy="connect Zendesk, Gong or Fireflies",
    ),
    LineKind(
        key="dated_intervention",
        question="Test your own changes against the curve",
        reading="If something you did moved it, that is the most actionable "
                "finding available — because you can undo it.",
        requires_all=("pm_manual",),
        requires_any=NUMERIC,
        absent_because="testing a change against a curve needs both a dated "
                       "change and the curve",
        remedy="record the change dates, and connect analytics or revenue",
    ),
    LineKind(
        key="external_timing",
        question="Date external events against the curve",
        reading="Worth separating from your own changes: if they landed in "
                "different quarters, the curve can tell them apart.",
        requires_all=("communication",),
        requires_any=NUMERIC,
        absent_because="nothing here records external events, so they cannot "
                       "be dated against anything",
        remedy="connect Slack or email, or add a competitive-intelligence source",
    ),
    LineKind(
        key="leading_indicator",
        question="Cross-reference behaviour against outcome, looking for a "
                 "leading indicator",
        reading="If one exists it is both an explanation and an early warning "
                "you could run continuously.",
        requires_all=("analytics", "customer_voice"),
        requires_any=NUMERIC,
        absent_because="a leading indicator needs at least two sources that "
                       "describe the same accounts",
        remedy="connect a second source keyed to the same accounts",
    ),
)


def plan_lines(counts: dict[str, int]) -> tuple[list[LineKind], list[LineKind]]:
    """`(runnable, blocked)` for this tenant's connected sources.

    Both halves are returned because both are the plan. The blocked list is
    what "the gap that hurts" is written from, and it is the more useful half
    for a tenant that has not connected much: it is the only part of the
    product that tells them what to do next.
    """
    runnable, blocked = [], []
    for kind in LINE_KINDS:
        (blocked if kind.blocked_by(counts) else runnable).append(kind)
    return runnable, blocked

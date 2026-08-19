"""Stage 4a — projecting KG signals into Crucible claims.

The spec budgets ~40% of total effort for claim extraction and calls it the
single point of failure: every number downstream is computed on top of these,
so a wrong strength or a wrong population produces precise nonsense with clean
provenance, and nothing later in the pipeline can detect it.

**This module is cheaper than that, and the Phase 0 spike is why.** A
`kg_signal` row is ALREADY an extracted assertion — the graph extractor did the
hard part at ingest. What it does not carry is the four things a claim needs:

    kind         -> claim_type      what is being asserted
    source_type  -> strength        how strongly, capped by who is asserting it
    source_type  -> authoritative   whether this source may VOTE on that type
    properties   -> population      who it is about

All four are deterministic table lookups. No LLM call, no prompt to drift, no
eval harness needed for the mapping itself — which is why the risk the spec
warns about lands mostly on the graph extractor rather than here. What DOES
need an eval is whether the tables are right, and `tests/fixtures/
crucible_claim_labels.json` is that: hand-labelled signals with the claim the
projection should produce.

WHAT THIS MODULE REFUSES TO DO. It never raises a claim's strength above what
its source can support, and it never lets a source vote outside its authority
(I4). Both are enforced here rather than at scoring time, because a claim that
reaches the substrate over-strengthened is indistinguishable from a real one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from app.crucible.types import (
    STRENGTH_SCORE,
    Claim,
    ClaimType,
    EvidenceStrength,
    PopulationFilter,
)

logger = logging.getLogger(__name__)

# ── kind → what is being asserted ────────────────────────────────────────────
# `kind` is the graph extractor's own taxonomy (app/graph/types.py). Anything
# unmapped falls to `mechanism`, the weakest-consequence choice: a mechanism
# claim can never vote on magnitude, so a mis-mapped signal cannot inflate a
# size. Getting this wrong in the other direction — defaulting to `magnitude` —
# would let an unrecognised kind size a finding.
KIND_TO_CLAIM_TYPE: Mapping[str, ClaimType] = {
    "feature_request": "preference",
    "sentiment": "preference",
    "customer_voice": "preference",
    "finding": "mechanism",
    "metric_anomaly": "magnitude",
    "deal_blocker": "constraint",
    "bug": "existence",
    "incident": "existence",
    "milestone": "attempt",
    "competitor_move": "direction",
}
DEFAULT_CLAIM_TYPE: ClaimType = "mechanism"

# ── source_type → what it may vote on (I4) ───────────────────────────────────
# SPEC §4.3 and §4.5. A source contributes ZERO confidence outside these, and
# the claim is retained rather than dropped (Stage 4) because non-authoritative
# claims still supply the mechanism detail that makes a finding actionable.
#
# THE SELF-SELECTION RULE IS THE LOAD-BEARING ONE. `customer_voice` and
# `communication` describe people who chose to speak — a ticket, a review, a
# sales call. Letting either size a population is how a system ends up telling
# a company that its loudest problem is its biggest one, and it is one line to
# prevent.
#
# `project_mgmt` is the execution source (§4.5): authoritative about what THIS
# ORGANISATION did, never about users. A Jira ticket saying "users churn
# because export is slow" is one engineer's framing typed into a text field.
AUTHORITATIVE_FOR: Mapping[str, frozenset[str]] = {
    "customer_voice":   frozenset({"preference", "mechanism"}),
    "communication":    frozenset({"attempt", "existence", "constraint"}),
    "project_mgmt":     frozenset({"attempt", "existence", "constraint"}),
    "analytics":        frozenset({"magnitude", "direction"}),
    "revenue":          frozenset({"magnitude", "direction"}),
    "outcome_measured": frozenset({"magnitude", "direction", "mechanism"}),
    "pm_manual":        frozenset({"constraint"}),
    # Neither is evidence about anything: a verbal claim is unverified
    # self-report, and an agent inference is our own guess read back to us.
    "verbal_claim":     frozenset(),
    "agent_inferred":   frozenset(),
}

# ── source_type → the strongest thing it can support ─────────────────────────
# A CEILING, not an assignment. `measured` here means the source records what
# happened rather than what someone believed happened; nothing short of an
# experiment platform reaches `causally_tested`, so no projection ever emits it.
DEFAULT_STRENGTH: Mapping[str, EvidenceStrength] = {
    "outcome_measured": "measured",
    "analytics":        "measured",
    "revenue":          "measured",
    # Ticket status, transitions and merge state are structured fields, not
    # prose — schema-mode extraction in the spec's terms (§4.5).
    "project_mgmt":     "measured",
    "communication":    "reported",
    "customer_voice":   "reported",
    "verbal_claim":     "reported",
    "pm_manual":        "reported",
    "agent_inferred":   "inferred",
}
FALLBACK_STRENGTH: EvidenceStrength = "reported"

# ── properties → who the claim is about ──────────────────────────────────────
# Named accounts, split by side of the funnel. The split does real work: against
# a retention goal a finding about prospects scores zero however loud it is, and
# the same account name appears under `customer` on one row and `prospect` on
# another, so the side has to be decided per ACCOUNT across the corpus rather
# than per row. See `infer_account_sides`.
CUSTOMER_KEYS = ("customer", "poc_customer", "account", "organization", "company")
PROSPECT_KEYS = ("prospect", "candidate")

#: Strings that appear in those fields and are not an account name.
_NOT_A_NAME = frozenset({
    "", "n/a", "na", "none", "null", "unknown", "tbd", "customer", "customers",
    "the customer", "prospect", "prospects", "all", "various", "multiple",
    "several", "team", "user", "users",
})


def normalise_account(value: Any) -> Optional[str]:
    """An account name, or None if the value is a placeholder rather than one."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.strip().split())
    if name.lower() in _NOT_A_NAME or not 3 <= len(name) <= 80:
        return None
    return name


def infer_account_sides(signals: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Decide customer-vs-prospect per ACCOUNT, over the whole corpus.

    The population intersection is only real if it can tell them apart, and no
    single row can: the same account shows up under `customer` on one signal and
    `prospect` on another as a deal progresses. So any appearance under a
    customer-only key wins, a name seen only under prospect keys is prospect-
    side, and ambiguity resolves to customer — which is the conservative choice
    for a retention goal, and is disclosed as an assumed parameter (I8) by the
    caller rather than hidden here.
    """
    customers: set[str] = set()
    prospects: set[str] = set()
    for signal in signals:
        props = signal.get("properties")
        if not isinstance(props, dict):
            continue
        for key in ("customer", "poc_customer"):
            name = normalise_account(props.get(key))
            if name:
                customers.add(name)
        for key in PROSPECT_KEYS:
            name = normalise_account(props.get(key))
            if name:
                prospects.add(name)
    return {
        name: ("customer" if name in customers else "prospect")
        for name in customers | prospects
    }


def _population(props: Mapping[str, Any], sides: Mapping[str, str]) -> PopulationFilter:
    named: list[str] = []
    for key in (*CUSTOMER_KEYS, *PROSPECT_KEYS):
        name = normalise_account(props.get(key))
        if name and name not in named:
            named.append(name)
    if not named:
        # I3: no named account is NOT MEASURED. It is not "zero accounts" — the
        # signal simply did not record who it was about, and a finding built
        # from these must render as unsizeable rather than as worthless.
        return PopulationFilter(segments={}, estimated_size=None)
    return PopulationFilter(
        segments={
            "accounts": tuple(named),
            "customer_side": tuple(
                n for n in named if sides.get(n, "customer") == "customer"
            ),
        },
        estimated_size=len(named),
    )


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def project_signal(
    signal: Mapping[str, Any],
    sides: Mapping[str, str],
) -> Optional[Claim]:
    """One `kg_signal` row → one `Claim`, or None if it cannot be a claim.

    Returns None only for a signal with no usable timestamp: `observed_at` drives
    per-claim-type decay, and defaulting it to now() would make stale evidence
    look fresh — a silent, permanent overstatement. Dropping the row is the
    honest option and the caller counts what it dropped.
    """
    observed_at = _parse_ts(signal.get("valid_at"))
    if observed_at is None:
        return None

    source_type = str(signal.get("source_type") or "")
    kind = str(signal.get("kind") or "")
    claim_type: ClaimType = KIND_TO_CLAIM_TYPE.get(kind, DEFAULT_CLAIM_TYPE)

    authoritative = claim_type in AUTHORITATIVE_FOR.get(source_type, frozenset())
    strength: EvidenceStrength = DEFAULT_STRENGTH.get(source_type, FALLBACK_STRENGTH)

    # A source may not exceed `reported` on a claim type it cannot vote on.
    # Without this, `project_mgmt` (ceiling `measured`, because status fields
    # are facts) would emit a MEASURED preference claim from a ticket whose
    # body speculates about why users churn — precisely the failure §4.5 exists
    # to prevent, wearing the strength of a structured field.
    if not authoritative and STRENGTH_SCORE[strength] > STRENGTH_SCORE["reported"]:
        strength = "reported"

    props = signal.get("properties") if isinstance(signal.get("properties"), dict) else {}

    return Claim(
        id=str(signal.get("id") or ""),
        assertion=str(signal.get("content") or ""),
        type=claim_type,
        subject=str(props.get("subject") or kind or ""),
        source_id=source_type,
        artifact_id=str(signal.get("source_id") or ""),
        artifact_type=kind,
        strength=strength,
        observed_at=observed_at,
        authoritative=authoritative,
        population=_population(props, sides),
        # Sizing comes from the substrate, not from a single signal. Left
        # unmeasured here rather than guessed (I3).
        population_value=None,
        magnitude=None,
        direction="neutral",
        raw=dict(signal.get("properties") or {}),
    )


def project_signals(
    signals: Iterable[Mapping[str, Any]],
) -> tuple[tuple[Claim, ...], dict[str, int]]:
    """Project a corpus. Returns the claims and a count of what was dropped.

    The counts are not diagnostics — they are the input to a `CoverageNote`. A
    run that silently discarded a third of its evidence looks exactly like one
    that read everything, and that is the degradation the spec calls worse than
    an outright failure.
    """
    rows = list(signals)
    sides = infer_account_sides(rows)

    claims: list[Claim] = []
    stats = {"seen": len(rows), "projected": 0, "no_timestamp": 0, "retired": 0}
    for row in rows:
        props = row.get("properties")
        if isinstance(props, dict) and props.get("retired"):
            stats["retired"] += 1
            continue
        claim = project_signal(row, sides)
        if claim is None:
            stats["no_timestamp"] += 1
            continue
        claims.append(claim)
    stats["projected"] = len(claims)

    if stats["no_timestamp"]:
        logger.info(
            "crucible: dropped %d signal(s) with no usable timestamp out of %d",
            stats["no_timestamp"], stats["seen"],
        )
    return tuple(claims), stats

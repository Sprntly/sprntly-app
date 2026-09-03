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

from app.graph.types import signal_is_retired
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
    # Written by app/research/business_context_projection.py — the company's
    # OWN stated constraints and its definition of a good outcome. These were
    # missing, so a constraint the company itself stated projected as a generic
    # `mechanism`, which `pm_manual` has no authority over, and was refuted as
    # "outside its source's authority". `AUTHORITATIVE_FOR["pm_manual"]` was
    # unreachable in consequence: it grants authority over `constraint`, and
    # nothing could ever produce one.
    "constraint": "constraint",
    "good_outcome": "preference",
}
DEFAULT_CLAIM_TYPE: ClaimType = "mechanism"

# ── a grounded commercial figure reclassifies its OWN claim, not its kind ────
# `commercial_term` is left OUT of `KIND_TO_CLAIM_TYPE` on purpose: most
# commercial-term signals are a paraphrase ("pricing came up") with no real
# number behind them, and defaulting the whole kind to `magnitude` would let
# every one of those vote on size. A signal that DOES carry a grounded figure
# — the checklist pass's `properties.amount`, a real number a customer stated
# on the call (see app/graph/extractor.py's checklist 'commercial' shape) —
# is a different, stronger kind of evidence, and is reclassified per-CLAIM
# below rather than per-kind.
_GROUNDED_MAGNITUDE_KINDS: frozenset[str] = frozenset({"commercial_term"})


def _grounded_commercial_amount(kind: str, props: Mapping[str, Any]) -> Optional[float]:
    """The real, transcript-stated dollar figure on this claim, or `None`.

    Reads `properties["amount"]` — written ONLY by the checklist pass's
    'commercial'/'partnership_commercial' shape, and only when a speaker
    actually stated a figure (`_sanitize_checklist_properties` in the
    extractor never writes `amount` on an invention or a 0 stand-in for
    "not stated" — I2/I3). Gated on `kind` too: a `commercial_term` signal
    from the OPEN extraction pass (`_EXTRACT_SCHEMA`) can carry an
    unrelated numeric `properties` value under the same key by coincidence,
    so this only trusts the checklist-shaped kind the amount contract
    actually applies to.
    """
    if kind not in _GROUNDED_MAGNITUDE_KINDS:
        return None
    amount = props.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if amount != amount or amount in (float("inf"), float("-inf")):  # NaN/inf
        return None
    return float(amount)

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
#: What each source is a legitimate WITNESS to. SPEC §4.5.
#:
#: I tried to widen this after measuring that 661 `project_mgmt -> preference`
#: claims were being rejected on a real tenant, and I was wrong to. A tracker
#: ticket reading "customers want bulk export" is a PM's PARAPHRASE; the
#: authoritative witness for what a customer wants is the customer, and
#: `customer_voice` is where that lives. Letting the tracker vote on preference
#: would let one engineer's framing of a user's motive enter the substrate
#: carrying the weight of a structured field.
#:
#: The rejections were real but the diagnosis was wrong. They were concentrated
#: in clusters that held ONLY tracker claims — an artefact of grouping, which
#: was splitting the corpus into single-source buckets. Grouping by the graph's
#: own themes mixes sources within a theme, so a theme carrying both a customer
#: call and the tickets about it now has an authoritative member. See
#: app/crucible/kg_themes.py.
#:
#: Authority is not weight: `DEFAULT_STRENGTH` separately caps a self-reporting
#: source at `reported`.
AUTHORITATIVE_FOR: Mapping[str, frozenset[str]] = {
    # `constraint` BECAUSE A CUSTOMER IS THE WITNESS TO ITS OWN BLOCKER.
    #
    # The extractor types a blocked deal as `deal_blocker` -> `constraint`
    # (KIND_TO_CLAIM_TYPE above). Without this entry, a tenant whose only
    # connected source is call recordings had every blocked deal REFUTED as
    # "no source that may speak to this claim type reported it" — three real
    # blockers across three named accounts, weeks apart, produced zero findings
    # and three lines in the ruled-out ledger. The identical sentence typed
    # into Slack surfaced, because `communication` already had `constraint`.
    #
    # That asymmetry was not the self-selection rule doing its job. The rule is
    # about MAGNITUDE — `test_a_self_selected_source_can_never_size_a_population`
    # asserts exactly that, for `customer_voice` and `communication` together —
    # and it is untouched here. Authority is not sizing: `score_impact` reads
    # `impact_inputs` and nothing else, which
    # `assert_impact_ignores_corroboration` sweeps every field to enforce. What
    # this entry changes is whether the claim survives Stage 4 and how much
    # confidence it carries, not how big anything is.
    #
    # And on the merits: nobody is better placed than the customer to report
    # their own procurement queue, security review or budget freeze. That is a
    # claim about themselves, not about the market.
    "customer_voice":   frozenset({"preference", "mechanism", "constraint"}),
    "communication":    frozenset({"attempt", "existence", "constraint"}),
    "project_mgmt":     frozenset({"attempt", "existence", "constraint"}),
    "analytics":        frozenset({"magnitude", "direction"}),
    "revenue":          frozenset({"magnitude", "direction"}),
    "outcome_measured": frozenset({"magnitude", "direction", "mechanism"}),
    # A PM stating the company's own business context is authoritative about
    # what the company is constrained by AND about what it wants — but not
    # about mechanism or magnitude out in the world. `good_outcome` projects as
    # `preference`, so without it here the company's own stated definition of
    # success was refuted as "outside its source's authority".
    "pm_manual":        frozenset({"constraint", "preference"}),
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


def _artifact_id(signal: Mapping) -> str:
    """Which source DOCUMENT this signal came out of.

    `source_id` is the obvious column and it is NULL on every row — nothing in
    `app/` ever sets `Signal.source_id`. Document identity actually lives in
    `provenance["doc"]` (`"slack/#mvp-product (part 2/3)"`, a Fireflies sync
    batch, a Drive file), which is populated on every real signal: measured 71
    distinct docs across a 2,777-signal tenant, so it genuinely discriminates.

    This matters because the refutation step asks "did all this evidence come
    from ONE conversation" — read off a column that is always empty, that test
    answers "yes" every time and the ledger asserts a provenance the system
    does not have.
    """
    provenance = signal.get("provenance")
    if isinstance(provenance, str):
        import json

        try:
            provenance = json.loads(provenance)
        except Exception:  # noqa: BLE001 — unreadable provenance is no doc
            provenance = None
    if isinstance(provenance, Mapping):
        doc = provenance.get("doc")
        if doc:
            return str(doc)
    return str(signal.get("source_id") or "")


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
    props = signal.get("properties") if isinstance(signal.get("properties"), dict) else {}
    grounded_amount = _grounded_commercial_amount(kind, props)
    claim_type: ClaimType = KIND_TO_CLAIM_TYPE.get(kind, DEFAULT_CLAIM_TYPE)
    if grounded_amount is not None:
        # A real, transcript-stated dollar figure IS a magnitude claim —
        # see `_grounded_commercial_amount`'s docstring. This is what lets a
        # `revenue`-source signal (already authoritative for `magnitude` —
        # see `AUTHORITATIVE_FOR` below) actually vote on size, instead of
        # every commercial-term claim defaulting to `mechanism` and being
        # capped at `reported` strength regardless of what it carries.
        claim_type = "magnitude"

    authoritative = claim_type in AUTHORITATIVE_FOR.get(source_type, frozenset())
    strength: EvidenceStrength = DEFAULT_STRENGTH.get(source_type, FALLBACK_STRENGTH)

    # A source may not exceed `reported` on a claim type it cannot vote on.
    # Without this, `project_mgmt` (ceiling `measured`, because status fields
    # are facts) would emit a MEASURED preference claim from a ticket whose
    # body speculates about why users churn — precisely the failure §4.5 exists
    # to prevent, wearing the strength of a structured field.
    if not authoritative and STRENGTH_SCORE[strength] > STRENGTH_SCORE["reported"]:
        strength = "reported"

    return Claim(
        id=str(signal.get("id") or ""),
        assertion=str(signal.get("content") or ""),
        type=claim_type,
        subject=str(props.get("subject") or kind or ""),
        source_id=source_type,
        artifact_id=_artifact_id(signal),
        artifact_type=kind,
        strength=strength,
        observed_at=observed_at,
        authoritative=authoritative,
        population=_population(props, sides),
        # Sizing across a POPULATION still comes from the substrate, not
        # from a single signal — left unmeasured here rather than guessed
        # (I3). `magnitude` is different: it is this ONE claim's own
        # grounded figure (a number a speaker actually stated), not a
        # population size, so it is populated when one is present.
        population_value=None,
        magnitude=grounded_amount,
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
        # THE REPO'S OWN DEFINITION, not a key invented here. Retirement is
        # `superseded_by`/`expired_at`, which is what every other reader checks
        # via `signal_is_retired`; `properties["retired"]` is written by
        # nothing, so this guard let expired roadmap bets and superseded
        # metrics vote while reporting a retired count of zero forever.
        if signal_is_retired(props if isinstance(props, dict) else None):
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

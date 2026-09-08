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
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
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
#
# BOTH KINDS ARE ADMITTED, AND THEY FEED DIFFERENT LINES. They are not
# better and worse; they hold different things, and the report needs both.
#
#   * `commercial_term` carries COMMITTED money — an issued quote, a
#     contract value, a named deal. Summable, and the only population a
#     money target may be answered from. Measured at 55% precision on a real
#     sample, and the junk is refused by the phrase families in
#     `app.crucible.backfill` rather than by excluding the kind, because the
#     genuine rows here are exactly the ones a reader most needs.
#   * `pricing` carries LIST PRICES — a rate card. Measured at 93%
#     precision, but its rows are the same handful of values repeated: one
#     "$30,000 for 50 users" tier appeared across sixteen different
#     accounts. Reported as a range, never summed, because the total of a
#     rate card is meaningless.
#
# NO BASIS GATE ANY MORE, and the reason it existed is the reason it can go.
# It was admitting a `pricing` amount only with a sum-eligible basis
# (`total-contract`/`one-off`), because "a per-seat rate without a stated
# seat count is not a sum". That is still true — and nothing sums a list
# price now. `pipeline._figure_is_committed` keeps priced figures out of the
# committed total by construction, so the arithmetic the gate was protecting
# against can no longer happen, while the gate itself was excluding almost
# every priced row on a historical corpus (the backfill never writes
# `basis`, so those rows have none) and emptying the pricing line it now
# feeds.
_GROUNDED_MAGNITUDE_KINDS: frozenset[str] = frozenset({"commercial_term", "pricing"})


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

    Re-checks the SAME numeric/zero/NaN/inf exclusions the extractor's own
    writer applies, rather than trusting an upstream write path to have
    enforced them — this module reads `kg_signal` generically, not only rows
    this repo's own extractor produced, and I2/I3 ("unmeasured is never
    zero") has to hold here even if some other writer ever puts a literal
    `0` in `properties["amount"]`.

    Both eligible kinds are admitted here; whether a figure may be ADDED UP
    is a separate question answered downstream by
    `pipeline._figure_is_committed`, not by refusing to read it.
    """
    if kind not in _GROUNDED_MAGNITUDE_KINDS:
        return None
    amount = props.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if amount != amount or amount in (float("inf"), float("-inf")):  # NaN/inf
        return None
    if amount == 0:
        # A stated figure of zero is not a real quoted amount — treat it as
        # absent, the same as any other missing figure.
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

#: The property key the extractor writes an EXPLICIT side onto, alongside a
#: named `account` — see `app.graph.extractor.ACCOUNT_SIDE_PROPERTY_KEY` for
#: the write side and the closed vocabulary. Held here as its own copy
#: rather than imported: `graph.extractor` is an ingest-time module with its
#: own heavy dependencies (the LLM gateway, embeddings), and this read-time
#: module has no reason to carry that import weight for one string constant.
#: MUST STAY IN SYNC WITH THE WRITE SIDE — there is no shared source of truth
#: for this literal.
ACCOUNT_SIDE_KEY = "account_side"

#: Values `ACCOUNT_SIDE_KEY` is trusted to carry. Anything else — an older
#: value, a typo, a future addition the reader has not caught up to — is
#: ignored by `infer_account_sides` exactly like the key being absent,
#: never coerced into one of these.
_CUSTOMER_SIDE_VALUES = frozenset({"customer"})
_PROSPECT_SIDE_VALUES = frozenset({"prospect"})
#: `partner` and `unknown` are both real, disclosed answers to "which side
#: is this on" — neither is a customer or a prospect, so neither may vote
#: `customer_side` in either direction. Tracked so an account that is
#: EXPLICITLY known to be ambiguous is never silently indistinguishable from
#: one nothing was ever said about.
_NEITHER_SIDE_VALUES = frozenset({"partner", "unknown"})

#: Strings that appear in those fields and are not an account name.
_NOT_A_NAME = frozenset({
    "", "n/a", "na", "none", "null", "unknown", "tbd", "customer", "customers",
    "the customer", "prospect", "prospects", "all", "various", "multiple",
    "several", "team", "user", "users",
})


#: Trailing words that are a legal form rather than part of the name. Dropped
#: as a whole TOKEN, never as a trailing substring — stripping "co" off the end
#: of the letters would turn `Cisco` into `cis` and start merging real
#: customers into each other.
_LEGAL_SUFFIXES = frozenset({
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc",
    "gmbh", "sa", "ag", "bv", "nv", "pty", "group", "holdings",
})


#: A category, not a company. These are filtered out of the DERIVED self-name
#: sources — the product's name, a website's domain stem, a hand-entered alias
#: — because a product called `Analytics` is no evidence at all that a signal
#: naming `Analytics` is about the vendor, and promoting one to an exclusion
#: key would silently delete a real account from the analysis. The legal
#: suffixes are in here for the same reason: an alias list split on its commas
#: turns `Acme, Inc.` into a fragment `Inc.`, whose key would otherwise match
#: every account whose own name collapses to `inc`.
#:
#: THE TENANT'S OWN COMPANY NAME IS DELIBERATELY NOT FILTERED THIS WAY. If a
#: workspace is literally called `Hub`, then rows naming `Hub` really are it,
#: and dropping that key would cost the exclusion the whole reason it exists.
#: A generic PRODUCT name is a guess about identity; a generic COMPANY name is
#: the identity.
_GENERIC_SELF_NAMES = frozenset({
    "admin", "ai", "analytics", "api", "app", "base", "beta", "cloud",
    "company", "console", "core", "dashboard", "data", "demo", "desktop",
    "engine", "enterprise", "home", "hub", "insights", "internal", "main",
    "ml", "mobile", "network", "platform", "portal", "product", "production",
    "report", "reporting", "reports", "sandbox", "service", "services",
    "site", "software", "solutions", "staging", "studio", "suite", "system",
    "test", "tool", "tools", "web", "workspace", "www",
}) | _LEGAL_SUFFIXES


#: Dropped before the generic test, never listed as generic themselves — an
#: article carries no identity either way, but `The Contoso Group` is a
#: company and `The Platform` is not.
_ARTICLES = frozenset({"a", "an", "the"})


#: Registry labels that sit UNDER a country TLD rather than being a name —
#: `.co.uk`, `.com.au`, `.ac.nz`. Dropped so the stem of `acme.co.uk` is
#: `acme` and not `co`.
_PUBLIC_SECOND_LEVEL = frozenset({
    "ac", "co", "com", "edu", "go", "gov", "mil", "ne", "net", "or", "org",
})


def account_key(value: str) -> str:
    """Collapse the spellings of one account name to a single key.

    Case, spacing and punctuation are spelling, not identity: one tenant
    carried its own name four different ways across its rows — squashed,
    spaced, title-cased and lowercase — and any exclusion matching the RAW
    STRING would have dropped one spelling and kept the other three, which is
    the same bug wearing a fix.
    """
    return "".join(_account_tokens(value))


def _account_tokens(value: Any) -> list[str]:
    """`account_key`'s own tokens, before they are joined.

    SPLIT OUT SO THE GENERIC-NAME GUARD SEES EXACTLY WHAT THE KEY IS MADE OF.
    The guard asks a question about the WORDS in a candidate self-name; asking
    it of a second, separately-written tokenizer is how a guard comes to
    disagree with the key it is supposed to be guarding.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", str(value).lower()) if t]
    # NEVER THE ONLY TOKEN. "Inc" on its own is not a legal suffix attached to
    # a name, and returning "" here would make every such row match every
    # other one.
    if len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def _clean_account_name(value: Any) -> Optional[str]:
    """A tidied account name, or None when the value is not one.

    LIFTED OUT SO BOTH DIRECTIONS SHARE IT. `normalise_account` decides whether
    a value read OFF a signal is a name; `self_name` has to make the same
    decision about a value read out of the tenant's OWN record. Two copies of
    "is this a name" would drift, and the exclusion would end up keyed on
    strings the corpus side can never produce — an exclusion that matches
    nothing while looking like it works, which is the exact failure this whole
    area exists to stop.
    """
    if not isinstance(value, str):
        return None
    name = " ".join(value.strip().split())
    if name.lower() in _NOT_A_NAME or not 3 <= len(name) <= 80:
        return None
    return name


@dataclass(frozen=True)
class SelfName:
    """One name that means "the tenant", and where it was read from.

    THE SOURCE TRAVELS WITH THE KEY BECAUSE THE DISCLOSURE NEEDS IT. A reader
    told only that "2,357 signals were excluded as your own company" cannot
    check the decision; told that the name was `Contoso`, read from the
    product name, they can say "that is right" or "that is our biggest
    customer" in one glance.
    """
    #: `account_key` output — what the exclusion actually matches on.
    key: str
    #: The spelling a reader would recognise, for rendering.
    name: str
    #: The field it came out of, in the reader's words ("product name").
    source: str


def domain_stem(url: Any) -> Optional[str]:
    """`https://www.example-corp.com/pricing` → `example corp`; else None.

    People write the company's name into their domain, and a domain is one of
    the few self-descriptions a workspace almost always has. The stem is
    returned SPACED rather than keyed so it passes the same name guards as
    every other source before `account_key` ever sees it.
    """
    if not isinstance(url, str):
        return None
    host = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", url.strip().lower())
    host = host.split("/")[0].split("?")[0].split("#")[0]
    host = host.split("@")[-1].split(":")[0]
    labels = [part for part in host.split(".") if part]
    # A bare word is a hostname, not a domain — there is no name in it.
    if len(labels) < 2:
        return None
    labels = labels[:-1]                    # the TLD is never the name
    if len(labels) > 1 and labels[-1] in _PUBLIC_SECOND_LEVEL:
        labels = labels[:-1]                # ...nor is the `co` of `.co.uk`
    # THE LAST REMAINING LABEL, not the first: `app.example-corp.com` is the
    # same company as `example-corp.com`, and the subdomain is never the name.
    return labels[-1].replace("-", " ").replace("_", " ").strip() or None


def self_name(value: Any, source: str, *,
              allow_generic: bool = False) -> Optional[SelfName]:
    """Resolve one candidate self-name, or None if it is not usable as one.

    `allow_generic` is TRUE ONLY FOR THE TENANT'S OWN COMPANY NAME — see
    `_GENERIC_SELF_NAMES`. Every derived source goes through the filter.
    """
    name = _clean_account_name(value)
    if name is None:
        return None
    key = account_key(name)
    if not key:
        return None
    # ALL-GENERIC, NOT JUST EQUAL-TO-GENERIC. `The Platform` and `Analytics
    # Platform` are as much a category as `Platform` is, and a denylist
    # compared against the whole key lets a leading article walk straight past
    # it. Articles are dropped rather than listed as generic so `The Contoso
    # Group` still resolves.
    if not allow_generic:
        words = [t for t in _account_tokens(name) if t not in _ARTICLES]
        if not words or all(w in _GENERIC_SELF_NAMES for w in words):
            return None
    return SelfName(key=key, name=name, source=source)


def self_account_key_set(names: Iterable[SelfName]) -> frozenset[str]:
    """The match set for `normalise_account`, from resolved `SelfName`s.

    EXACT KEYS, AND THE UNION OF THEM. Widening the SOURCES a name can come
    from is safe; widening the MATCH is not. A token-subset rule would let a
    tenant called `Salesforce Ventures` swallow a customer called
    `Salesforce`, and a false exclusion deletes a real account with nothing on
    the page to show it happened.
    """
    return frozenset(n.key for n in names if n.key)


def self_account_keys(display_name: Optional[str]) -> frozenset[str]:
    """The keys that mean "this is the tenant, not one of its customers".

    A tenant's own company name is written into `properties.account` by the
    extractor on every row that mentions it, and on a real corpus that made
    the vendor the single largest account in its own analysis — 30.6% of all
    attributed signals. Counting it inflates every reach it touches, and it
    silently rescues one-account findings from the `single_account`
    refutation by padding them to two.

    THE ONE-SOURCE ENTRY POINT, kept because the workspace name is the one
    source every caller has. Callers that can read more of the tenant's record
    resolve a list of `SelfName`s and take `self_account_key_set` of it; this
    is that path with a single candidate.

    EMPTY IS THE SAFE DEFAULT AND MEANS "EXCLUDE NOTHING". A tenant whose
    display name will not load must lose the exclusion, never its accounts.
    """
    return self_account_key_set(
        [n for n in (self_name(display_name, "workspace name",
                               allow_generic=True),) if n])


def names_self(props: Any, self_names: frozenset[str]) -> bool:
    """True when this signal's account fields name the TENANT itself.

    THE COUNTING SIDE OF `normalise_account`'s EXCLUSION, and deliberately the
    same guards in the same order — a disclosed count that does not equal what
    the engine actually dropped is a second wrong number rather than a fix.
    """
    if not self_names or not isinstance(props, Mapping):
        return False
    for field in (*CUSTOMER_KEYS, *PROSPECT_KEYS):
        name = _clean_account_name(props.get(field))
        if name is not None and account_key(name) in self_names:
            return True
    return False


def canonical_account_names(
    signals: Iterable[Mapping[str, Any]],
    self_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """One display spelling per account, decided over the WHOLE corpus.

    `account_key` already knows that `Northwind Labs` and `NorthwindLabs` are
    one customer. Nothing acted on it: `normalise_account` returned the raw
    display string, so the two spellings travelled through the engine as two
    accounts — inflating the account count of any theme that touched both,
    splitting a real customer's evidence across two names in the reach, and
    handing a value join two keys where the contracts carry one.

    MEASURED ON A REAL TENANT: 14 keys collapse 30 raw spellings; outside the
    vendor's own four spellings that is 559 signals, about 7% of everything
    attributed — `Northwind Labs`/`NorthwindLabs` (127) and
    `FabrikamPay`/`Fabrikam Pay` (102) are the two largest.

    THE BLIND SPOTS ARE REAL AND ARE NOT HIDDEN. This collapses spellings of
    the same tokens, and nothing else. On that same tenant the graph holds
    `Contoso Federal` (46), `Contoso Federal Credit Union` (8) and
    `Contoso Fed` (2) — three token sets, so three keys, and they do NOT merge
    here. A looser
    token-SUBSET rule would reach all three, and is deliberately not used: the
    same rule would have to refuse `Contoso Air` (13 signals, a different
    organisation entirely) on nothing but a token count, and a false merge
    combines two customers' revenue into one number with no way for a reader
    to see it happened. Under-merging costs reach on one theme; over-merging
    corrupts the money. The conservative rule is the one that ships.

    THE CHOSEN SPELLING IS THE MOST FREQUENT ONE, ties broken lexicographically
    so two runs over the same corpus can never disagree. Most frequent rather
    than longest or first-seen because it is the spelling the reader's own
    people actually use, and this string is rendered.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    for signal in signals:
        props = signal.get("properties")
        if not isinstance(props, Mapping):
            continue
        for key in (*CUSTOMER_KEYS, *PROSPECT_KEYS):
            name = normalise_account(props.get(key), self_names)
            if name:
                counts[account_key(name)][name] += 1
    return {
        key: min(spellings.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        for key, spellings in counts.items()
        if key
    }


def normalise_account(
    value: Any, self_names: frozenset[str] = frozenset(),
    canonical: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """An account name, or None if the value is a placeholder rather than one.

    `self_names` are `account_key` outputs, not raw names — see
    `self_account_keys`. THE SINGLE CHOKE-POINT: every reader of an account
    name in this engine resolves it here (`_population` and
    `infer_account_sides` are the only two callers, and reach, the graph
    relations, the deduped grounded figures, the commercial native units and
    the repeated amounts all read what they produce), so excluding a name
    here excludes it everywhere at once — and, now, collapsing two spellings
    here collapses them everywhere at once.

    `canonical` is `canonical_account_names`' output, keyed on `account_key`.
    EMPTY OR ABSENT MEANS "COLLAPSE NOTHING", which is exactly the behaviour
    every caller had before this parameter existed: the raw display string is
    returned unchanged. A corpus-wide map cannot be built from one row, so the
    default has to be the identity rather than a guess.
    """
    name = _clean_account_name(value)
    if name is None:
        return None
    key = account_key(name)
    if self_names and key in self_names:
        return None
    return canonical.get(key, name) if canonical else name


def infer_account_sides(
    signals: Iterable[Mapping[str, Any]],
    self_names: frozenset[str] = frozenset(),
    canonical: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Decide customer-vs-prospect per ACCOUNT, over the whole corpus.

    TWO SOURCES, READ TOGETHER, NEVER DRIFTING FROM EACH OTHER'S DEFAULT.

    The legacy source is per-row NAME fields — `customer`/`poc_customer`
    naming a customer directly, `PROSPECT_KEYS` naming a prospect — and stays
    exactly as it always has. `poc_customer` is kept in that check though
    nothing has ever written it (measured on a real corpus: zero times
    across every tenant) — a dead key costs nothing to keep reading, and
    removing it buys nothing either, so it is left rather than pulled to
    avoid touching a check this module does not need to change to add the
    source below.

    The SECOND, LIVE source is the extractor's own explicit `ACCOUNT_SIDE_KEY`
    property, recorded against a signal's named `account`
    (`app.graph.extractor.ACCOUNT_SIDE_PROPERTY_KEY` is the write side).
    Every signal extracted before that property existed carries neither key,
    so for a corpus with none of them this function's output is BYTE-
    IDENTICAL to before this property existed: an account with no evidence
    from EITHER source is simply absent from the returned dict, and the
    caller's own "ambiguity resolves to customer" default (I8, disclosed at
    the call site) is exactly what a reader still sees for it. This function
    never manufactures that default itself — see `_grounded_account_side`'s
    sibling in `graph.extractor` for why an unrecognised or missing value is
    dropped rather than coerced to any side, here or there.

    Once a corpus DOES carry `account_side`, an EXPLICIT "partner" or
    "unknown" answer is recorded as `partner`/`unknown` — neither `customer`
    nor `prospect` — because an explicit "we don't know which side" is a
    different, stronger claim than "nothing was said" and must not silently
    read as `customer` either. Any appearance under a customer-key or an
    explicit `account_side="customer"` wins over every other signal for the
    same account (a customer relationship does not un-happen), a name seen
    only under prospect evidence is prospect-side, and the remaining
    ambiguity (customer XOR prospect never resolved either way, only
    partner/unknown) still resolves to customer at the CALLER — this
    function's own contract is unchanged.
    """
    customers: set[str] = set()
    prospects: set[str] = set()
    partners: set[str] = set()
    unknowns: set[str] = set()
    for signal in signals:
        props = signal.get("properties")
        if not isinstance(props, dict):
            continue
        for key in ("customer", "poc_customer"):
            name = normalise_account(props.get(key), self_names, canonical)
            if name:
                customers.add(name)
        for key in PROSPECT_KEYS:
            name = normalise_account(props.get(key), self_names, canonical)
            if name:
                prospects.add(name)
        side = props.get(ACCOUNT_SIDE_KEY)
        if isinstance(side, str):
            side = side.strip().lower()
            if side in _CUSTOMER_SIDE_VALUES | _PROSPECT_SIDE_VALUES | _NEITHER_SIDE_VALUES:
                name = normalise_account(props.get("account"), self_names, canonical)
                if name:
                    if side in _CUSTOMER_SIDE_VALUES:
                        customers.add(name)
                    elif side in _PROSPECT_SIDE_VALUES:
                        prospects.add(name)
                    elif side == "partner":
                        partners.add(name)
                    else:
                        unknowns.add(name)
    # Priority, strongest evidence first: customer beats everything (it
    # cannot un-happen), prospect beats an explicit non-answer, and an
    # explicit "partner"/"unknown" only shows up for a name nothing
    # stronger ever named.
    partners -= customers | prospects
    unknowns -= customers | prospects | partners
    sides: dict[str, str] = {name: "prospect" for name in prospects}
    sides.update({name: "customer" for name in customers})
    sides.update({name: "partner" for name in partners})
    sides.update({name: "unknown" for name in unknowns})
    return sides


def _population(
    props: Mapping[str, Any], sides: Mapping[str, str],
    self_names: frozenset[str] = frozenset(),
    canonical: Optional[Mapping[str, str]] = None,
) -> PopulationFilter:
    named: list[str] = []
    for key in (*CUSTOMER_KEYS, *PROSPECT_KEYS):
        name = normalise_account(props.get(key), self_names, canonical)
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


#: `provenance["channel"]` on a signal that was read out of a file attached to
#: a chat message rather than pulled from a connected source. Written by
#: `crucible.prose.ATTACHMENT_CHANNEL`; read here, and only here, to apply the
#: strength ceiling below.
ATTACHMENT_CHANNEL = "chat_attachment"


def _provenance(signal: Mapping) -> Mapping:
    """A signal's `provenance` as a mapping, whatever shape it arrived in.

    Factored out because two things now read it — document identity and the
    attachment ceiling — and a second inline `json.loads` fallback beside the
    first is a second place for the string case to be forgotten.
    """
    provenance = signal.get("provenance")
    if isinstance(provenance, str):
        import json

        try:
            provenance = json.loads(provenance)
        except Exception:  # noqa: BLE001 — unreadable provenance is no
            # provenance, which is the honest reading of it
            provenance = None
    return provenance if isinstance(provenance, Mapping) else {}


def came_from_an_attachment(signal: Mapping) -> bool:
    """Did this row come out of a file someone attached to a message?

    THE TRANSPORT, WHICH IS NOT THE WITNESS. `AUTHORITATIVE_FOR` and
    `DEFAULT_STRENGTH` are keyed on WHO is speaking — an analytics platform, a
    customer on a call, a PM writing down the company's own constraints — and
    "upload" is none of those, it is how the bytes arrived. So the source type
    is left exactly as the extractor classified it, and this is used for the
    one thing the transport genuinely does bear on: the ceiling.
    """
    return _provenance(signal).get("channel") == ATTACHMENT_CHANNEL


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
    doc = _provenance(signal).get("doc")
    if doc:
        return str(doc)
    return str(signal.get("source_id") or "")


def _stored_figure_class(props: Mapping[str, Any]) -> Optional[str]:
    """The class a previous run drew for this row, if any and if valid."""
    from app.crucible.figure_class import FIGURE_CLASSES, PROPERTY_KEY

    value = props.get(PROPERTY_KEY)
    return value if value in FIGURE_CLASSES else None


def _stored_blocker_reason(props: Mapping[str, Any]) -> Optional[str]:
    """The blocker reason a previous run drew for this row, if any and if
    valid. See `_stored_figure_class` — same read-back-never-redraw shape."""
    from app.crucible.blocker_reason import BLOCKER_REASON_KEYS
    from app.crucible.blocker_reason import PROPERTY_KEY as REASON_KEY

    value = props.get(REASON_KEY)
    return value if value in BLOCKER_REASON_KEYS else None


def project_signal(
    signal: Mapping[str, Any],
    sides: Mapping[str, str],
    self_names: frozenset[str] = frozenset(),
    canonical: Optional[Mapping[str, str]] = None,
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

    # AND A FILE SOMEBODY ATTACHED MAY NOT EXCEED `reported` EITHER, whatever
    # witness the extractor read in it.
    #
    # THE CEILING IS ABOUT PROVENANCE, NOT ABOUT PROSE. A connector is a
    # standing agreement: the tenant wired up their analytics platform once and
    # every row it produces carries that platform's standing. A document handed
    # over inside one message carries no such standing — it may be a customer
    # transcript, and it may equally be a deck the reader wrote last night
    # arguing for the thing they are about to ask the engine to rank. Nothing
    # in the bytes distinguishes those, which is exactly why the extractor's
    # answer is kept (it is the best read available of WHO is speaking) and the
    # confidence it can carry is capped (nothing available says how much that
    # speaker should be trusted).
    #
    # A CEILING RATHER THAN A REFUSAL, in both directions and deliberately.
    # Dropping to `inferred` — or refusing authority outright — would kill every
    # upload-only cluster at `no_authority` and hand back a run that read the
    # document and found nothing in it. Leaving `measured` reachable would let
    # an attached spreadsheet-turned-narrative size a finding as measured fact,
    # outranking the connected corpus it is supposed to be weighed against.
    if (came_from_an_attachment(signal)
            and STRENGTH_SCORE[strength] > STRENGTH_SCORE["reported"]):
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
        population=_population(props, sides, self_names, canonical),
        # Sizing across a POPULATION still comes from the substrate, not
        # from a single signal — left unmeasured here rather than guessed
        # (I3). `magnitude` is different: it is this ONE claim's own
        # grounded figure (a number a speaker actually stated), not a
        # population size, so it is populated when one is present.
        population_value=None,
        magnitude=grounded_amount,
        # READ BACK, NEVER RE-DRAWN. A class stored on a previous run is a
        # fact about that row; recomputing it would make the committed total
        # a function of how many times the analysis was run. Validated
        # against the closed vocabulary here rather than trusted, so a
        # hand-edited or legacy value cannot reach the consequence table.
        figure_class=_stored_figure_class(props),
        blocker_reason=_stored_blocker_reason(props),
        direction="neutral",
        raw=dict(signal.get("properties") or {}),
    )


def project_signals(
    signals: Iterable[Mapping[str, Any]],
    #: The tenant's own name, as `account_key` keys — see `self_account_keys`.
    #: Default empty excludes nothing, which is exactly what every caller
    #: without a resolved company display name should get.
    self_names: frozenset[str] = frozenset(),
) -> tuple[tuple[Claim, ...], dict[str, int]]:
    """Project a corpus. Returns the claims and a count of what was dropped.

    `self_excluded` counts ROWS, not names: a row naming the tenant under any
    account field counts once, and a row whose ONLY account was the tenant
    therefore lands in both `projected` and `self_excluded` — it is still a
    claim, it just no longer names an account. That is the honest shape, since
    the exclusion drops an ATTRIBUTION rather than a signal.

    THE CORPUS IS READ TWICE AND THAT IS THE POINT. `canonical_account_names`
    and `infer_account_sides` are both corpus-wide facts that no single row
    can answer — which spelling of a customer is the one to render, and which
    side of the funnel that customer is on — so both are settled over the
    whole list before any row is projected.

    The counts are not diagnostics — they are the input to a `CoverageNote`. A
    run that silently discarded a third of its evidence looks exactly like one
    that read everything, and that is the degradation the spec calls worse than
    an outright failure.
    """
    rows = list(signals)
    # CORPUS-WIDE, AND BEFORE THE SIDES ARE INFERRED. `infer_account_sides`
    # keys on the display name, so building it from raw spellings would decide
    # customer-vs-prospect separately for `Northwind Labs` and `NorthwindLabs`
    # and then hand the two answers to one collapsed account.
    canonical = canonical_account_names(rows, self_names)
    sides = infer_account_sides(rows, self_names, canonical)

    claims: list[Claim] = []
    stats = {"seen": len(rows), "projected": 0, "no_timestamp": 0,
             "retired": 0, "self_excluded": 0}
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
        # COUNTED, NOT JUST DONE. The exclusion fired on 2,357 rows of a real
        # tenant — 30.6% of everything attributed — and said nothing, so a run
        # with it working and a run with it inert were byte-identical. This
        # count is what lets the coverage note tell those two apart. Measured
        # AFTER the retired drop so the two numbers do not double-count one
        # row, and on the same guards `normalise_account` uses so it equals
        # what was actually dropped rather than approximating it.
        if names_self(props, self_names):
            stats["self_excluded"] += 1
        claim = project_signal(row, sides, self_names, canonical)
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

"""Goal Analysis (Crucible) — Phase 0 spike. READ-ONLY, writes nothing.

THE EXPERIMENT. Build the theme clusters exactly the way `synthesis.convergence`
builds them (same themes, same signal->theme edges, same dedup, same recency
decay), then score each cluster TWICE:

  A. SHIPPED       impact = breadth/5 (distinct source types agreeing)
                   -> convergence.py:220, the formula behind today's brief.
  B. CRUCIBLE      impact = named accounts in the goal population (I1: never
                   reads corroboration), confidence scored separately from
                   strength / authority / recency / sample, corroboration
                   capped at +0.15 and confined to confidence.

Identical inputs, one variable. The question the spike answers is the one
README F17 says to answer before building anything:

  Does B surface a real finding that A buries?

Everything here is deterministic. No LLM call is made -- the ranking difference
is the finding, and an LLM writing prose over it would only obscure whether the
difference is real.

    python scripts/crucible_spike.py --company <uuid>
    python scripts/crucible_spike.py --company <uuid> --goal-population prospect
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.client import require_client

# ── Claim projection ─────────────────────────────────────────────────────────
# A KG signal is already an extracted assertion, so the spike projects rather
# than re-extracts: `kind` carries what is being asserted, `source_type` carries
# who is asserting it. That is the whole of Crucible's claim atom minus the
# population, which comes off `properties` below.

KIND_TO_CLAIM_TYPE: dict[str, str] = {
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

# SPEC 4.5 + 4.3. What each source may VOTE on. A claim outside its source's
# authority is retained (Stage 4: never dropped) and contributes zero
# confidence. The self-selection rule is the load-bearing one: customer_voice
# and communication describe people who chose to speak, so neither may ever
# size a population.
AUTHORITATIVE_FOR: dict[str, set[str]] = {
    "customer_voice":   {"preference", "mechanism"},
    "communication":    {"attempt", "existence", "constraint"},
    "project_mgmt":     {"attempt", "existence", "constraint"},
    "analytics":        {"magnitude", "direction"},
    "revenue":          {"magnitude", "direction"},
    "outcome_measured": {"magnitude", "direction", "mechanism"},
    "verbal_claim":     set(),
    "pm_manual":        {"constraint"},
    "agent_inferred":   set(),
}

STRENGTH_SCORE = {
    "causally_tested": 1.00, "measured": 0.90, "correlated": 0.60,
    "inferred": 0.40, "reported": 0.25,
}

DEFAULT_STRENGTH: dict[str, str] = {
    "outcome_measured": "measured",
    "analytics": "measured",
    "revenue": "measured",
    "project_mgmt": "measured",     # schema fields: status/transition are facts
    "communication": "reported",
    "customer_voice": "reported",
    "verbal_claim": "reported",
    "pm_manual": "reported",
    "agent_inferred": "inferred",
}

# Per-claim-type half-lives (SPEC §3). Execution facts are re-readable, so they
# do not decay.
DECAY_HALFLIFE_DAYS: dict[str, float] = {
    "magnitude": 180, "mechanism": 540, "preference": 270,
    "constraint": 120, "direction": 90,
    "existence": math.inf, "attempt": math.inf,
}

# Property keys that name WHO a claim is about. Split by side of the funnel
# because the goal population intersection is real work: against a retention
# goal a finding about prospects scores zero, however loud it is.
CUSTOMER_KEYS = ("customer", "poc_customer", "account", "organization", "company")
PROSPECT_KEYS = ("prospect", "candidate")

# Non-names that show up in these fields and are not an account.
_JUNK = {"", "n/a", "na", "none", "unknown", "tbd", "customer", "the customer",
         "customers", "prospect", "all", "various", "multiple"}


def norm_account(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = " ".join(v.strip().split())
    if s.lower() in _JUNK or len(s) < 3 or len(s) > 80:
        return None
    return s


@dataclass
class Claim:
    signal_id: str
    claim_type: str
    source_type: str
    kind: str
    content: str
    strength: str
    authoritative: bool
    customers: set[str] = field(default_factory=set)
    prospects: set[str] = field(default_factory=set)
    valid_at: datetime | None = None
    confidence: float = 1.0
    weight: float = 1.0


def project_claim(sig: dict) -> Claim:
    kind = sig.get("kind") or "finding"
    st = sig.get("source_type") or "pm_manual"
    ctype = KIND_TO_CLAIM_TYPE.get(kind, "mechanism")
    authoritative = ctype in AUTHORITATIVE_FOR.get(st, set())
    strength = DEFAULT_STRENGTH.get(st, "reported")
    # A source may not exceed `reported` on a claim type it cannot vote on.
    if not authoritative and STRENGTH_SCORE[strength] > STRENGTH_SCORE["reported"]:
        strength = "reported"

    props = sig.get("properties") or {}
    customers, prospects = set(), set()
    if isinstance(props, dict):
        for k in CUSTOMER_KEYS:
            n = norm_account(props.get(k))
            if n:
                customers.add(n)
        for k in PROSPECT_KEYS:
            n = norm_account(props.get(k))
            if n:
                prospects.add(n)

    return Claim(
        signal_id=sig["id"], claim_type=ctype, source_type=st, kind=kind,
        content=sig.get("content") or "", strength=strength,
        authoritative=authoritative, customers=customers, prospects=prospects,
        valid_at=parse_ts(sig.get("valid_at")),
        confidence=float(sig.get("confidence") or 1.0),
        weight=float(sig.get("weight") or 1.0),
    )


def parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Data loading (paged, no embeddings — 1536-float columns time the query out)
def page(table: str, key: str, cid: str, cols: str, cap: int = 40) -> list[dict]:
    c = require_client()
    rows: list[dict] = []
    i = 0
    while i < cap:
        chunk = (c.table(table).select(cols).eq(key, cid)
                 .range(i * 1000, i * 1000 + 999).execute()).data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        i += 1
    return rows


SOURCE_STALE_WINDOW_DAYS = {
    "analytics": 30, "project_mgmt": 14, "communication": 7, "customer_voice": 30,
    "revenue": 30, "verbal_claim": 7, "pm_manual": 60, "agent_inferred": 14,
    "outcome_measured": None,
}
CONNECTED_SOURCE_TYPES = {"analytics", "project_mgmt", "communication",
                          "customer_voice", "revenue", "outcome_measured"}


def shipped_recency(source_type: str, valid_at: datetime | None, now: datetime) -> float:
    """convergence._recency_factor, verbatim in behaviour."""
    window = SOURCE_STALE_WINDOW_DAYS.get(source_type)
    if not window or valid_at is None:
        return 1.0
    age = max(0.0, (now - valid_at).total_seconds() / 86400)
    return math.pow(0.5, age / window)


def crucible_recency(claim: Claim, now: datetime) -> float:
    """Per CLAIM TYPE, not per source type (SPEC §3). A mechanism stays true
    far longer than a competitor fact, and the shipped decay cannot see that
    because it keys off where the claim came from."""
    hl = DECAY_HALFLIFE_DAYS.get(claim.claim_type, 180)
    if hl == math.inf or claim.valid_at is None:
        return 1.0
    age = max(0.0, (now - claim.valid_at).total_seconds() / 86400)
    return math.pow(0.5, age / hl)


@dataclass
class Cluster:
    theme_id: str
    label: str
    claims: list[Claim] = field(default_factory=list)
    source_types: set[str] = field(default_factory=set)
    competitor_pressure: int = 0
    effective_weight: float = 0.0
    merged_labels: set[str] = field(default_factory=set)
    merged_count: int = 0
    seen_signal_ids: set[str] = field(default_factory=set)
    account_sides: dict[str, str] = field(default_factory=dict)

    @property
    def breadth(self) -> int:
        return len(self.source_types)

    @property
    def all_accounts(self) -> set[str]:
        out: set[str] = set()
        for c in self.claims:
            out |= c.customers | c.prospects
        return out

    @property
    def customers(self) -> set[str]:
        """Accounts resolved CUSTOMER-side corpus-wide — the NRR population."""
        return {a for a in self.all_accounts
                if self.account_sides.get(a, "customer") == "customer"}

    @property
    def prospects(self) -> set[str]:
        return {a for a in self.all_accounts
                if self.account_sides.get(a) == "prospect"}


_STOP = {"and", "the", "of", "for", "to", "a", "an", "&", "/", "-", "–"}


def merge_key(label: str) -> str:
    """Crude stand-in for SPEC's 'deduplicate by mechanism, not by wording'.

    The KG carries several labels for one thing ('sales pipeline', 'Sales /
    Deal Progression', 'sales deal progression'), and left unmerged they split
    one theme's accounts across four rows and each looks smaller than it is.
    Sorted content words is enough to catch that family; a real implementation
    would dedupe on the mechanism string.
    """
    words = [w for w in "".join(
        ch.lower() if (ch.isalnum() or ch.isspace()) else " " for ch in label
    ).split() if w not in _STOP and len(w) > 2]
    # Light stemming so 'exercises' == 'exercise', 'pipeline' == 'pipelines'.
    stems = sorted({w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words})
    return " ".join(stems)


def infer_account_sides(sigs: list[dict]) -> dict[str, str]:
    """Which named accounts are CUSTOMERS and which are PROSPECTS.

    The population intersection is only real if it can tell them apart, and the
    property key on any single signal cannot: the same account appears under
    `customer` on one row and `prospect` on another. So decide per ACCOUNT over
    the whole corpus — any appearance under a customer-only key (`poc_customer`,
    `customer`) makes it customer-side; a name seen only under `prospect` /
    `candidate` is prospect-side. Ambiguous names resolve to customer, and are
    counted so the assumption is visible (I8).
    """
    cust: set[str] = set()
    prosp: set[str] = set()
    for s in sigs:
        p = s.get("properties") or {}
        if not isinstance(p, dict):
            continue
        for k in ("customer", "poc_customer"):
            n = norm_account(p.get(k))
            if n:
                cust.add(n)
        for k in PROSPECT_KEYS:
            n = norm_account(p.get(k))
            if n:
                prosp.add(n)
    sides: dict[str, str] = {}
    for n in cust | prosp:
        sides[n] = "customer" if n in cust else "prospect"
    return sides


def build_clusters(cid: str) -> tuple[list[Cluster], dict]:
    now = datetime.now(timezone.utc)
    sigs = page("kg_signal", "enterprise_id", cid,
                "id,kind,source_type,content,properties,confidence,weight,valid_at,provenance")
    ents = page("kg_entity", "enterprise_id", cid, "id,type,canonical_label")
    rels = page("kg_relationship", "enterprise_id", cid,
                "type,source_kind,source_id,target_kind,target_id")

    themes = {e["id"]: e for e in ents if e.get("type") == "theme"}
    by_id = {s["id"]: s for s in sigs}

    edges_by_theme: dict[str, list[dict]] = {}
    for r in rels:
        if r.get("source_kind") != "signal" or r.get("target_id") not in themes:
            continue
        edges_by_theme.setdefault(r["target_id"], []).append(r)

    sides = infer_account_sides(sigs)

    # Merge near-duplicate themes BEFORE scoring (see merge_key).
    merged: dict[str, Cluster] = {}
    for tid, t in themes.items():
        label = t.get("canonical_label") or "(unlabelled)"
        key = merge_key(label) or tid
        cl = merged.get(key)
        if cl is None:
            cl = Cluster(theme_id=tid, label=label)
            cl.merged_labels = {label}
            cl.merged_count = 1
            merged[key] = cl
        else:
            cl.merged_labels.add(label)
            cl.merged_count += 1
            if len(label) < len(cl.label):   # shortest label reads best
                cl.label = label
        seen = cl.seen_signal_ids
        for e in edges_by_theme.get(tid, []):
            sid = e.get("source_id")
            if sid in seen:
                continue
            sig = by_id.get(sid)
            if sig is None:
                continue
            props = sig.get("properties") or {}
            if isinstance(props, dict) and props.get("retired"):
                continue
            seen.add(sid)
            claim = project_claim(sig)
            cl.claims.append(claim)
            prov = sig.get("provenance") or {}
            if (prov.get("origin") if isinstance(prov, dict) else None) != "web_research":
                cl.source_types.add(claim.source_type)
            cl.effective_weight += (
                claim.confidence * claim.weight
                * shipped_recency(claim.source_type, claim.valid_at, now)
            )
            if e.get("type") == "PRESSURES" or claim.kind == "competitor_move":
                cl.competitor_pressure += 1

    clusters = [c for c in merged.values() if c.claims]
    for c in clusters:
        c.account_sides = sides

    meta = {"signals": len(sigs), "themes": len(themes), "edges": len(rels),
            "themes_after_merge": len(merged),
            "clusters_with_claims": len(clusters),
            "named_accounts": len(sides),
            "customer_side": sum(1 for v in sides.values() if v == "customer"),
            "prospect_side": sum(1 for v in sides.values() if v == "prospect")}
    return clusters, meta


# ── A. The shipped score ─────────────────────────────────────────────────────
def shipped_score(cl: Cluster) -> float:
    """convergence.py:214-223 — impact IS breadth."""
    n = max(len(cl.claims), 1)
    impact = min(1.0, cl.breadth / 5.0)
    severity = min(1.0, cl.effective_weight / n)
    trend = 1.0 + 0.1 * cl.competitor_pressure
    return impact * severity * trend


# ── B. The Crucible score ────────────────────────────────────────────────────
def crucible_impact(cl: Cluster, goal_population: str) -> int | None:
    """I1: this function may not read `source_types`, and does not.
    I3: no named account is `null` (not measured), never 0."""
    pop = cl.customers if goal_population == "customer" else cl.prospects
    return len(pop) if pop else None


def crucible_confidence(cl: Cluster, now: datetime) -> dict:
    auth = [c for c in cl.claims if c.authoritative]
    strongest = max((STRENGTH_SCORE[c.strength] for c in cl.claims), default=0.0)
    authority = 1.0 if auth else 0.4
    n = len(cl.claims)
    sample = 1.0 if n >= 5 else (0.6 if n >= 2 else 0.3)
    recency = (sum(crucible_recency(c, now) for c in cl.claims) / n) if n else 0.0
    # Coverage: how much of the claim set is inside the source's authority.
    coverage = (len(auth) / n) if n else 0.0
    # CAPPED, and the ONLY place corroboration is allowed to appear.
    independent_classes = len({c.source_type for c in auth})
    corrob = min(0.15, 0.05 * max(0, independent_classes - 1))

    problem = min(1.0, max(0.0,
        strongest * 0.35 + authority * 0.20 + sample * 0.15
        + coverage * 0.20 + recency * 0.10 + corrob))

    # Solution leg: this corpus has no `outcome_measured` signals at all, so
    # nothing records whether any fix ever worked. Every theme therefore scores
    # the same unknown-no-prior base, which makes the combined score a constant
    # and useless for ordering. CORPUS-ONLY MODE (enterprise-readiness §1) is
    # the honest handling: band on the PROBLEM leg, cap the band at medium
    # because no solution evidence exists, and say so rather than rendering a
    # confident-looking number over an absent leg.
    solution = 0.25
    band = "medium" if problem >= 0.50 else "low"
    return {"band": band, "score": problem, "problem": problem,
            "solution": solution, "weakest_leg": "solution",
            "cap_reason": "no outcome evidence in corpus — capped at medium"}


def adjudicate(cl: Cluster) -> str:
    auth = [c for c in cl.claims if c.authoritative]
    if not auth:
        return "no_authoritative_source"
    if len(auth) == 1:
        return "single_authoritative"      # full weight — the quiet-finding guard
    return "corroborated"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--goal-population", default="customer",
                    choices=("customer", "prospect"))
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json-out")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)

    clusters, meta = build_clusters(a.company)
    print(json.dumps(meta, indent=2))

    rows = []
    for cl in clusters:
        conf = crucible_confidence(cl, now)
        rows.append({
            "theme_id": cl.theme_id, "label": cl.label,
            "claims": len(cl.claims), "breadth": cl.breadth,
            "source_types": sorted(cl.source_types),
            "shipped": shipped_score(cl),
            "impact_accounts": crucible_impact(cl, a.goal_population),
            "customers": sorted(cl.customers), "prospects": sorted(cl.prospects),
            "confidence": conf, "verdict": adjudicate(cl),
            "kinds": dict(collections.Counter(c.kind for c in cl.claims)),
            "evidence": [c.content[:220] for c in cl.claims[:6]],
            "merged_from": sorted(cl.merged_labels)[:6],
            "merged_count": cl.merged_count,
        })

    # Ranking A — as shipped.
    by_shipped = sorted(rows, key=lambda r: -r["shipped"])
    for i, r in enumerate(by_shipped):
        r["rank_shipped"] = i + 1

    # Ranking B — Crucible. Unsized items are NOT zero (I3): they rank in their
    # own section rather than being silently sorted to the bottom as if measured.
    sized = [r for r in rows if r["impact_accounts"] is not None]
    unsized = [r for r in rows if r["impact_accounts"] is None]
    by_cru = sorted(sized, key=lambda r: (-r["impact_accounts"], -r["confidence"]["score"]))
    for i, r in enumerate(by_cru):
        r["rank_crucible"] = i + 1

    print(f"\nsizeable themes (>=1 named {a.goal_population}): {len(sized)}"
          f"   unsized (null, not zero): {len(unsized)}")

    print(f"\n{'='*78}\nA. AS SHIPPED — top {a.top} by convergence base score\n{'='*78}")
    for r in by_shipped[:a.top]:
        print(f"{r['rank_shipped']:>3}. [{r['shipped']:.3f}] breadth={r['breadth']} "
              f"claims={r['claims']} accts={r['impact_accounts']}  {r['label'][:88]}")

    print(f"\n{'='*78}\nB. CRUCIBLE — top {a.top} by accounts affected, "
          f"confidence scored separately\n{'='*78}")
    for r in by_cru[:a.top]:
        c = r["confidence"]
        print(f"{r['rank_crucible']:>3}. [{r['impact_accounts']} accts] "
              f"conf={c['band']:<6} ({c['score']:.2f}, weak={c['weakest_leg']}) "
              f"breadth={r['breadth']} shipped_rank={r['rank_shipped']}  {r['label'][:70]}")

    # THE PASS CONDITION, and it is deliberately NOT "the rankings differ".
    # Two rankings always differ. The claim under test is narrower: that the
    # corpus contains a finding which reaches MANY accounts on FEW mentions —
    # quiet by construction, invisible to anything that ranks on how much was
    # said — and that reading its evidence shows it is real.
    #
    # `reach_per_claim` is the shape. A theme with 60 claims across 12 accounts
    # is the loud aggregate everyone already discusses; one with 4 claims across
    # 7 accounts is a pattern nobody has assembled, because no single account's
    # thread contains it.
    print(f"\n{'='*78}\nTHE QUIET FINDINGS — wide reach, thin evidence\n"
          f"(>=4 accounts, <=10 claims, buried outside A's top 50)\n{'='*78}")
    quiet = sorted(
        (r for r in sized
         if r["impact_accounts"] >= 4 and r["claims"] <= 10 and r["rank_shipped"] > 50),
        key=lambda r: -(r["impact_accounts"] / max(r["claims"], 1)),
    )[:8]
    if not quiet:
        print("NONE — no wide-reach/thin-evidence theme exists in this corpus. "
              "The spike FAILS its pass condition here.")
    for r in quiet:
        r["reach_per_claim"] = round(r["impact_accounts"] / max(r["claims"], 1), 2)
        c = r["confidence"]
        print(f"\n--- {r['label']}")
        print(f"    reach/claim {r['reach_per_claim']}   "
              f"crucible #{r['rank_crucible']}  vs  shipped #{r['rank_shipped']}"
              f"   ({r['impact_accounts']} accounts, {r['claims']} claims, "
              f"breadth={r['breadth']}, conf={c['band']}, {r['verdict']})")
        print(f"    sources : {', '.join(r['source_types'])}")
        print(f"    kinds   : {r['kinds']}")
        print(f"    accounts: {', '.join(r['customers'][:12])}")
        for e in r["evidence"][:6]:
            print(f"      · {e}")

    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump({"meta": meta, "rows": rows}, f, indent=2, default=str)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

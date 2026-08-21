"""Every number the plan may state, computed here and carrying its provenance.

THE RULE THIS ENFORCES. The prose layer above never writes a figure from
memory. It is handed a set of `Fact`s — each one computed in Python from
signals, each one carrying the ids of the signals it came from — and may use
those and nothing else. `unstated_numbers()` then checks the generated text
against that set, so a fabricated figure fails the generation instead of
reaching a reader.

WHY NOT JUST INSTRUCT THE MODEL. "Only use the numbers provided" is followed
right up until the evidence is thin, which is exactly when it matters. The
target output is dense with specifics — "46% of potential spend", "the decline
steepened after March" — and a model asked for that shape against a corpus that
cannot support it will produce the shape. The guard has to be mechanical.

EXTRACTION IS DEFENSIVE ON PURPOSE. It reads `properties`, and real signals
routinely lack the keys you would expect: clustering once keyed on
`properties.subject`, which was present on 0 of 400 real signals and produced
nine taxonomy "findings" before anyone noticed. So every extractor here returns
what it can prove and stays silent otherwise — a missing key yields no fact,
never a zero, and never a guess. I3: unmeasured is not the same as zero.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: How many signals a fact may cite. Enough to be checkable, bounded so a fact
#: derived from 3,000 rows does not carry 3,000 ids into a prompt.
MAX_CITED = 8


@dataclass(frozen=True)
class Fact:
    """One computed number, and where it came from.

    `signal_ids` is the point of the whole class. A number without provenance
    is indistinguishable from a number a model made up, and this feature's
    credibility rests on the difference.
    """
    key: str
    statement: str
    value: Any
    unit: str = ""
    signal_ids: tuple[str, ...] = ()

    def numerals(self) -> set[str]:
        """The digit groups this fact licenses in generated prose."""
        return _numerals(self.statement) | _numerals(str(self.value))


def _numerals(text: str) -> set[str]:
    """Digit groups, normalised so 1,900 and 1900 are the same claim.

    Percentages, currency and thousands separators all vary in rendering; what
    must not vary is the number itself.
    """
    out = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        clean = m.replace(",", "").rstrip(".")
        if not clean:
            continue
        out.add(clean)
        if clean.endswith(".0"):
            out.add(clean[:-2])
    return out


def _prop(sig: dict, key: str) -> Any:
    return (sig.get("properties") or {}).get(key)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _ids(sigs: Iterable[dict]) -> tuple[str, ...]:
    return tuple(s["id"] for s in list(sigs)[:MAX_CITED] if s.get("id"))


# --------------------------------------------------------------------------
# Extractors. One per line kind that states numbers. Each returns [] rather
# than a guess when the properties it needs are absent.
# --------------------------------------------------------------------------

def _metric_series(sigs: list[dict]) -> list[Fact]:
    """The curve: where it is now, and how it moved."""
    points = [
        (_prop(s, "period"), _num(_prop(s, "value")), s)
        for s in sigs if _prop(s, "period") and _num(_prop(s, "value")) is not None
    ]
    if len(points) < 3:
        return []
    points.sort(key=lambda p: p[0])
    first, last = points[0], points[-1]
    delta = last[1] - first[1]
    pct = (delta / first[1] * 100) if first[1] else 0.0
    metric = _prop(last[2], "metric") or "the metric"
    return [
        Fact(key="metric.latest",
             statement=f"{metric} in {last[0]} was {last[1]:,.0f}",
             value=last[1], unit=metric, signal_ids=_ids([last[2]])),
        Fact(key="metric.change",
             statement=(f"{metric} moved {pct:+.1f}% across the "
                        f"{len(points)} periods on record, from {first[1]:,.0f} "
                        f"in {first[0]} to {last[1]:,.0f} in {last[0]}"),
             value=round(pct, 1), unit="percent",
             signal_ids=_ids([p[2] for p in points])),
    ]


def _attribution(sigs: list[dict]) -> list[Fact]:
    """A decline already split into causes — only where a split was RECORDED.

    The obvious implementation, "collect every `*_pct` property", is wrong in a
    way worth spelling out: it does not fabricate a number, it fabricates a
    RELATIONSHIP. Run against this tenant it produced "debit interchange cap
    accounts for 0% of the movement" and "yield accounts for 4% of the
    movement" — real percentages, lifted from unrelated market-research
    signals, asserted to explain a decline they say nothing about. That is a
    worse failure than an invented figure, because every individual number is
    traceable and the sentence is still false.

    So a decomposition is recognised only when it looks like one: two or more
    percentages ON THE SAME SIGNAL that between them account for the whole.
    A single stray percentage is not a split, and percentages from different
    signals are not parts of one thing.
    """
    candidates = []
    for s in sigs:
        pcts = {
            k[:-4]: float(v)
            for k, v in (s.get("properties") or {}).items()
            if k.endswith("_pct") and _num(v) is not None
        }
        if len(pcts) >= 2 and 95.0 <= sum(pcts.values()) <= 105.0:
            candidates.append((pcts, s))
    if not candidates:
        return []

    merged: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for pcts, sig in candidates:
        for name, v in pcts.items():
            merged[name].append((v, sig))

    out = []
    for name, vals in sorted(merged.items(), key=lambda kv: -sum(v for v, _ in kv[1])):
        avg = sum(v for v, _ in vals) / len(vals)
        out.append(Fact(
            key=f"attribution.{name}",
            statement=(f"{name.replace('_', ' ')} accounts for {avg:.0f}% of the "
                       f"movement, across {len(vals)} periods on record"),
            value=round(avg), unit="percent",
            signal_ids=_ids([sg for _, sg in vals])))
    return out


def _adoption_shape(sigs: list[dict]) -> list[Fact]:
    """Is adoption bimodal or uniformly low? The two mean different work."""
    vals = [(_num(_prop(s, "seat_adoption_pct")), s) for s in sigs]
    vals = [(v, s) for v, s in vals if v is not None]
    if len(vals) < 20:
        return []
    nums = sorted(v for v, _ in vals)
    lo = [v for v in nums if v < 40]
    hi = [v for v in nums if v >= 40]
    # Bimodal in the only sense that changes the decision: two populated
    # clusters with an empty middle, rather than one spread.
    middle = [v for v in nums if 25 <= v < 55]
    bimodal = bool(lo) and bool(hi) and len(middle) < 0.15 * len(nums)
    out = [Fact(
        key="adoption.shape",
        statement=(f"adoption across {len(nums)} accounts is "
                   f"{'bimodal' if bimodal else 'a single spread'}: "
                   f"{len(lo)} below 40% and {len(hi)} at or above it"),
        value="bimodal" if bimodal else "unimodal",
        signal_ids=_ids([s for _, s in vals]))]
    if bimodal:
        out.append(Fact(
            key="adoption.low_cohort",
            statement=f"{len(lo)} of {len(nums)} accounts sit below 40% seat adoption",
            value=len(lo), unit="accounts",
            signal_ids=_ids([s for v, s in vals if v < 40])))
    return out


def _rollout(sigs: list[dict]) -> list[Fact]:
    """Shipped but not fully rolled out — built revenue, uncollected."""
    out = []
    for s in sigs:
        pct, lever = _num(_prop(s, "rollout_pct")), _prop(s, "lever")
        if pct is None or not lever:
            continue
        if pct < 60:
            out.append(Fact(
                key=f"rollout.{lever.replace(' ', '_')}",
                statement=f"{lever} is shipped but enabled for only {pct:.0f}% of eligible accounts",
                value=round(pct), unit="percent", signal_ids=_ids([s])))
    return out


def _reason_concentration(sigs: list[dict]) -> list[Fact]:
    """Concentrated reasons are a closable gap; scattered ones are inertia."""
    reasons = Counter()
    by_reason = defaultdict(list)
    for s in sigs:
        r = _prop(s, "reason")
        if r:
            reasons[r] += 1
            by_reason[r].append(s)
    total = sum(reasons.values())
    if total < 15:
        return []
    top = reasons.most_common(2)
    share = sum(n for _, n in top) / total * 100
    return [Fact(
        key="reasons.concentration",
        statement=(f"the top {len(top)} of {len(reasons)} stated reasons account "
                   f"for {share:.0f}% of {total} mentions"),
        value=round(share), unit="percent",
        signal_ids=_ids([s for r, _ in top for s in by_reason[r]])),
        Fact(key="reasons.top",
             statement=f"the most common stated reason is: {top[0][0]}",
             value=top[0][0], signal_ids=_ids(by_reason[top[0][0]]))]


#: key -> extractor. Line kinds absent here state no numbers of their own.
EXTRACTORS = {
    "decompose_metric": (_metric_series, _attribution),
    "adoption_shape": (_adoption_shape,),
    "shipped_levers": (_rollout,),
    "reason_concentration": (_reason_concentration,),
}


def extract_facts(signals: list[dict], line_keys: Iterable[str]) -> list[Fact]:
    """Facts the plan is licensed to state, for these line kinds.

    Takes signals rather than a company id so it stays pure and testable — the
    caller does the paging. An extractor that raises is skipped with a warning
    rather than failing the plan: a missing fact narrows what may be said,
    which is the safe direction.
    """
    out: list[Fact] = []
    for key in line_keys:
        for fn in EXTRACTORS.get(key, ()):
            try:
                out.extend(fn(signals))
            except Exception:  # noqa: BLE001
                logger.warning("crucible facts: %s failed for %s", fn.__name__, key)
    return out


def allowed_numerals(facts: Iterable[Fact]) -> set[str]:
    """Every digit group the prose is permitted to contain."""
    allowed: set[str] = set()
    for f in facts:
        allowed |= f.numerals()
    return allowed


#: Numbers that are never fabrication: list positions, small counts, years.
#: Without this a plan cannot write "1." or "line 7" or "2026".
_ALWAYS_OK = {str(n) for n in range(0, 13)} | {str(y) for y in range(2015, 2036)}


def unstated_numbers(text: str, facts: Iterable[Fact]) -> set[str]:
    """Digit groups in `text` that no fact supports. Empty means clean.

    This is the guard the whole design turns on: whatever the prompt says, a
    number that cannot be traced to a signal does not ship.
    """
    return _numerals(text) - allowed_numerals(facts) - _ALWAYS_OK

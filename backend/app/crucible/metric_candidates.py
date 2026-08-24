"""Steps 2–3 and the Step 5 grounding — what the company already measures.

`CRUCIBLE-GOAL-RESOLUTION.md` §5 makes four requirements of an ask, and the
shipped ask met none of them. It opened with what it could not find ("I can't
find X defined anywhere in your systems"), asked an open question ("describe
what you'd want to see move"), named no consequence, and handed the user an
empty box. §5 requirement 2 is explicit: *"Never ask an open question. Every
candidate carries its current value, its population, its freshness, and where
it lives. The user should be able to answer by pointing rather than by
composing."*

This module is what makes pointing possible.

WHERE THE NUMBERS COME FROM. No connector declares `capabilities.metricRegistry`
yet, so Step 2's registry rung has nothing to query. But the knowledge graph
already stores metric observations: signals whose `properties` carry a `metric`
key alongside a numeric `value` and a `period`. On a real tenant that is 192
rows spanning eleven months of interchange revenue, deposit volume and the
rest. Grouping them by metric key yields exactly the shape §5 asks for — a
name, a current value, how long it has been measured, when it last moved, and
which source it lives in.

WHAT THIS IS NOT. It is not a definition. A metric observation says "interchange
revenue for September was $2,264,810"; it does not say what interchange revenue
counts, over what population, over what window. So a candidate produced here is
`origin='observed'` and can only ever be a CANDIDATE — §9 of the spec ("what is
explicitly not permitted") bars inferring a definition, and I9 bars locking one
without a human. The user still confirms, and what they confirm is their own
sentence, not ours.

NO LLM. Grouping is by exact `properties.metric` key and ranking is by token
overlap with the goal text, reusing the tokeniser Stage 0 already uses for KPI
tree matching. A model that "found" a metric would be inventing one.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

#: How many candidates an ask may carry. §5 wants the user to answer by
#: pointing; a list of forty is a search results page, not a decision.
MAX_CANDIDATES = 6

#: Below this many observations a metric is an anecdote about a number, not a
#: series anyone steers by. It is still SEARCHED and still counted in the "what
#: I looked at" line — it just does not get offered as a candidate.
MIN_OBSERVATIONS = 2

#: Rows to page when scanning for metric-bearing signals. `properties` is small
#: next to `content`, and nothing here reads the embedding.
_PAGE = 500
_MAX_PAGES = 40


@dataclass(frozen=True)
class MetricCandidate:
    """One thing the company measures, with the grounding §5 requires.

    Every field here answers one of the four requirements: `label` and
    `source_label` say where it lives, `current_value`/`current_period` give it
    a live number, `observations`/`first_period`/`last_period` give freshness
    and history, and `consequence` states what changes if the user picks it.
    """
    key: str
    label: str
    source_type: str
    source_label: str
    observations: int
    current_value: Optional[float]
    current_period: str
    first_period: str
    last_period: str
    consequence: str

    def to_json(self) -> dict:
        return asdict(self)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001 — a malformed row is not a candidate
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _humanise(key: str) -> str:
    """`interchange_revenue_usd` -> `Interchange revenue (usd)`.

    COSMETIC ONLY, and it never reaches a definition. The raw key travels in
    `key` and is what gets recorded; this is the string a person reads while
    pointing at it.
    """
    parts = [p for p in str(key).replace("-", "_").split("_") if p]
    if not parts:
        return str(key)
    head, tail = parts[0], parts[1:]
    unit = ""
    if tail and tail[-1].lower() in {"usd", "eur", "gbp", "pct", "percent", "bps"}:
        unit = f" ({tail[-1].lower()})"
        tail = tail[:-1]
    return (" ".join([head] + tail).capitalize() + unit).strip()


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "no value recorded"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:g}"


def scan_metric_observations(company_id: str) -> tuple[list[dict], int]:
    """Every signal carrying a `properties.metric`. Read-only, paged, cheap.

    Returns the rows and the total number of signals SEEN, because §5's first
    requirement is to show the search before the gap — "I looked at 726 signals
    across 7 sources" is the sentence that makes the question land as diligence
    rather than helplessness.
    """
    from app.db.client import require_client

    client = require_client()
    rows: list[dict] = []
    seen = 0
    for page in range(_MAX_PAGES):
        try:
            chunk = (
                client.table("kg_signal")
                .select("id,source_type,properties,valid_at")
                .eq("enterprise_id", company_id)
                .order("id")
                .range(page * _PAGE, page * _PAGE + _PAGE - 1)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — an unreadable page costs candidates,
            # never the run. The ask degrades to its open-door branch, which is
            # the behaviour that shipped before this module existed.
            logger.warning("crucible: metric scan failed at page %d for %s",
                           page, company_id)
            break
        seen += len(chunk)
        for row in chunk:
            props = _as_dict(row.get("properties"))
            if props.get("metric"):
                rows.append({**row, "_props": props})
        if len(chunk) < _PAGE:
            break
    return rows, seen


def _consequence(source_type: str, observations: int,
                 first_period: str, last_period: str) -> str:
    """What changes about the analysis if the user picks this one.

    §5 requirement 3. DERIVED, never invented: it states the span actually
    measured and what that source is allowed to witness, both of which are
    facts about this tenant's own data. It deliberately does not promise a
    point estimate — the engine still sizes in reach, and a consequence line
    that implied otherwise would be the overpromise `plan.py` has already been
    burned by twice.
    """
    from app.crucible.plan import _SOURCE_PROSE

    witness = _SOURCE_PROSE.get(source_type, (source_type, "unknown"))[1]
    span = (f"{first_period} to {last_period}"
            if first_period and last_period and first_period != last_period
            else (last_period or "a single period"))
    return (
        f"Picking this scopes the run to what {witness}. "
        f"{observations} observations, {span}. "
        f"Findings are still sized in reach — how many accounts a theme "
        f"touches — not in this metric's own unit."
    )


def _tokens(text: str) -> tuple[str, ...]:
    from app.crucible.goal import _tokens as goal_tokens

    return goal_tokens(text)


def candidates_for_goal(
    company_id: str, goal_text: str,
) -> tuple[list[MetricCandidate], dict]:
    """Everything the company measures, ranked by fit to the goal.

    RANKED, NOT FILTERED. A goal that matches nothing still gets the full list
    back, because §5 requirement 2 is about giving the user something to point
    at and requirement 4 keeps the door open regardless. Filtering to a
    confident match would be Step 2's "exactly one match" adoption path
    arriving through the back door, without the confirmation I9 requires.
    """
    rows, seen = scan_metric_observations(company_id)

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["_props"]["metric"]), []).append(row)

    goal_tokens = set(_tokens(goal_text))
    built: list[tuple[int, int, MetricCandidate]] = []
    for key, group in grouped.items():
        if len(group) < MIN_OBSERVATIONS:
            continue

        def _period(r: dict) -> str:
            return str(r["_props"].get("period") or str(r.get("valid_at") or "")[:10])

        ordered = sorted(group, key=_period)
        latest = ordered[-1]
        raw_value = latest["_props"].get("value")
        value = float(raw_value) if isinstance(raw_value, (int, float)) else None
        source_type = str(latest.get("source_type") or "")

        from app.crucible.plan import _SOURCE_PROSE

        source_label = _SOURCE_PROSE.get(source_type, (source_type, ""))[0]
        overlap = len(goal_tokens & set(_tokens(key.replace("_", " "))))
        built.append((overlap, len(group), MetricCandidate(
            key=key,
            label=_humanise(key),
            source_type=source_type,
            source_label=source_label,
            observations=len(group),
            current_value=value,
            current_period=_period(latest),
            first_period=_period(ordered[0]),
            last_period=_period(latest),
            consequence=_consequence(source_type, len(group),
                                     _period(ordered[0]), _period(latest)),
        )))

    # Best token overlap first, then the longest series — a metric measured for
    # eleven months is a better thing to steer by than one measured twice.
    built.sort(key=lambda t: (-t[0], -t[1], t[2].key))
    stats = {
        "signals_seen": seen,
        "metric_bearing": len(rows),
        "distinct_metrics": len(grouped),
        "offered": min(len(built), MAX_CANDIDATES),
    }
    return [c for _, _, c in built[:MAX_CANDIDATES]], stats


def searched_summary(company_id: str) -> list[dict]:
    """What was looked at, per source — §5 requirement 1.

    "Never open with what you do not know. Open with what you looked at." This
    is that line's data, and it reuses `plan.source_inventory` rather than
    counting again, so the ask and the plan can never disagree about how much
    the company has.
    """
    from app.crucible.plan import source_inventory

    sources, _total = source_inventory(company_id)
    return [{"label": s.label, "signal_count": s.signal_count,
             "source_type": s.source_type} for s in sources]

"""Step 2's registry rung — what the company actually measures.

`CRUCIBLE-GOAL-RESOLUTION.md` §5 makes four requirements of an ask, and the
shipped ask met none of them. It opened with what it could not find, asked an
open question, named no consequence, and handed over an empty box. §5
requirement 2: *"Never ask an open question. Every candidate carries its current
value, its population, its freshness, and where it lives. The user should be
able to answer by pointing rather than by composing."*

WHICH STORE, AND WHY IT MATTERS MORE THAN IT SOUNDS.

The first version of this module read `kg_signal.properties.metric`. That was
wrong, and wrong in the way §10 exists to prevent. Two things write that key and
neither is a metric registry:

  * `ds/analyses.py::_write_finding` writes ONE ROW PER DETECTED ANOMALY
    (`source_type='agent_inferred'`, gated on z ≥ 2.0). A metric that moves
    smoothly all year writes nothing and could never be offered; a metric that
    spiked twice looks like a two-point series whose "current value" is a
    months-old outlier. Ranking by row count ranked by INSTABILITY.
  * `graph/extractor.py` passes model output into `properties` unfiltered, so a
    `metric` key can be any number a model lifted out of any document —
    including a competitor's, out of a competitive analysis. Offering that under
    "what you already measure" is a false claim about the company's own data,
    which is README F11's contamination with the label doing the damage.

`app/db/metric_points.py` IS the registry, and is the only store read here:
unique on `(enterprise_id, metric, period_start, source)`, periods normalised to
ISO by `_period_key`, and already rendered in-product by `routes/metrics.py`.
Reading anything else would put this ask and the product's own Metrics page in
disagreement about what the company measures.

**IT IS EMPTY TODAY — measured 2026-08-24, 0 rows fleet-wide** — even though
`upsert_metric_point` is wired at `ds/analyses.py:275`, because that aggregation
has not run for any tenant. So this returns nothing on every current company and
the ask falls through to its open-door branch, which is §5 requirement 4 and was
always the honest floor. That is deliberate: an empty candidate list is a data
gap, and a list built from the anomaly log would be a lie. This lights up on its
own the moment the registry is populated, with no change here.

NO LLM. Grouping is by the registry's own `metric` column and ranking is by
token overlap with the goal text, reusing the tokeniser Stage 0 already uses for
KPI-tree matching. A model that "found" a metric would be inventing one.

WHAT A CANDIDATE IS NOT. It is not a definition. A point says "interchange
revenue for the week of 1 Sep was 2,264,810"; it does not say what is counted,
over what population, over what window. §10 bars inferring a definition and I9
bars locking one without a human, so a pick only ever SEEDS a sentence the user
then owns.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from typing import Optional

logger = logging.getLogger(__name__)

#: How many candidates an ask may carry. §5 wants the user to answer by
#: pointing; a list of forty is a search results page, not a decision.
MAX_CANDIDATES = 6

#: Below this many periods a metric is not a series.
#:
#: MEASURED IN PERIODS, and worth being precise about why that is currently the
#: same as counting rows. The registry's unique key is
#: `(enterprise_id, metric, period_start, source)`, and this module groups on
#: `(metric, source)` — so within one group `period_start` is unique and the two
#: counts cannot diverge. Mutating the check to `len(group)` passes every test,
#: because it is not a reachable difference.
#:
#: It is kept in period form because that is what the number MEANS ("how many
#: periods have we measured"), and because the divergence becomes reachable the
#: moment the grouping or the unique key changes — which is exactly what the
#: first version of this module got wrong: grouped on the metric name alone, two
#: trackers writing the same week cleared a row-count bar with zero historical
#: depth. `test_the_unique_key_is_what_makes_rows_and_periods_agree` pins the
#: premise rather than the unreachable branch.
#:
#: A metric below the bar is still counted in "what I looked at": §5
#: requirement 1 is about showing the effort, not only the hits.
MIN_PERIODS = 2


@dataclass(frozen=True)
class MetricCandidate:
    """One thing the company measures, with the grounding §5 requires.

    Each field answers one of the four requirements: `label`/`source_label` say
    where it lives, `current_value`/`current_period` give it a live number,
    `points`/`first_period`/`last_period` give freshness and history, and
    `consequence` states what changes if it is chosen.
    """
    key: str
    label: str
    source: str
    source_label: str
    points: int
    current_value: Optional[float]
    current_period: str
    first_period: str
    last_period: str
    consequence: str

    def to_json(self) -> dict:
        return asdict(self)


def _humanise(key: str) -> str:
    """`interchange_revenue_usd` -> `Interchange revenue (usd)`.

    A READING AID, and it must never become the definition. The raw key travels
    in `key` and is what gets recorded against the run; this is only the string
    a person reads while pointing at it. The first version of this module made
    exactly that mistake — `seedFromCandidate` wrote the humanised label into
    the box that becomes the locked definition, so `interchange_revenue_usd`
    reached the definition row as "Interchange revenue (usd)". The seed now
    carries the raw key too.
    """
    parts = [p for p in str(key).replace("-", "_").split("_") if p]
    if not parts:
        return str(key)
    unit = ""
    if len(parts) > 1 and parts[-1].lower() in {
        "usd", "eur", "gbp", "inr", "pct", "percent", "bps", "count", "ratio",
    }:
        unit = f" ({parts[-1].lower()})"
        parts = parts[:-1]
    return (" ".join(parts).capitalize() + unit).strip()


def _consequence(points: int, first_period: str, last_period: str,
                 source_label: str) -> str:
    """What changes about the analysis if the user picks this one.

    §5 requirement 3, DERIVED and nothing else: the span actually measured and
    the provider it came from, both facts about this tenant's own registry.

    It deliberately does not promise a point estimate. The engine still sizes in
    reach, and a consequence line implying otherwise would be the overpromise
    `plan.py` has already been burned by twice.

    It also does not reach into `plan._SOURCE_PROSE`, which the first version
    did — that maps KG `source_type` values, and for the DS-written rows it
    produced the literal sentence "Picking this scopes the run to what nothing —
    recorded, never counted." The registry's `source` is a provider name, so it
    is rendered as one.
    """
    span = (f"{first_period} to {last_period}"
            if first_period and last_period and first_period != last_period
            else (last_period or "a single period"))
    where = f" from {source_label}" if source_label else ""
    return (
        f"Measured{where}: {points} point{'' if points == 1 else 's'}, {span}. "
        f"Picking it fixes what the run is steering by. Findings are still "
        f"sized in reach — how many accounts a theme touches — not in this "
        f"metric's own unit."
    )


def _tokens(text: str) -> tuple[str, ...]:
    from app.crucible.goal import _tokens as goal_tokens

    return goal_tokens(text)


def candidates_for_goal(
    company_id: str, goal_text: str,
) -> tuple[list[MetricCandidate], dict]:
    """Everything in the registry, ranked by fit to the goal.

    ONE QUERY. `list_metric_points` selects a whole enterprise's points, which
    is one number per metric per week — tiny by construction, and indexed on the
    unique key. The version this replaced swept up to 20,000 `kg_signal` rows
    pulling JSON on the critical path to the question, and truncated silently at
    the cap.

    RANKED, NOT FILTERED. A goal that matches nothing still gets the list back:
    filtering to a confident match would be Step 2's "exactly one match"
    adoption arriving without the confirmation I9 requires.
    """
    from app.db.metric_points import list_metric_points

    try:
        rows = list_metric_points(company_id)
    except Exception:  # noqa: BLE001 — an unreadable registry costs candidates,
        # never the run: the ask falls through to its open-door branch, which is
        # the behaviour that shipped before this module existed.
        logger.warning("crucible: metric registry unreadable for %s", company_id)
        return [], {"registry_readable": False, "points": 0,
                    "distinct_metrics": 0, "offered": 0}

    # KEYED ON (metric, source), NOT metric ALONE, and this is not fussiness.
    # `ds/analyses.py` gives ClickUp and Jira the SAME metric tuple on purpose
    # ("both trackers feed the identical DS series/views"), so a tenant with
    # both connected writes two `tasks_open` rows for the same week — a normal
    # shape, not a corrupt one. Grouped on the name alone they collapse into one
    # series whose "current value" is whichever row the store happened to return
    # last, silently reporting one tracker's number as the company's while a
    # disagreeing source for the same week goes unmentioned.
    #
    # Two providers measuring the same thing are two candidates. Which one
    # governs is the user's call, which is the entire point of the ask.
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = row.get("metric")
        if key:
            grouped.setdefault((str(key), str(row.get("source") or "")), []).append(row)

    goal_tokens = set(_tokens(goal_text))
    built: list[tuple[int, int, MetricCandidate]] = []
    for (key, source), group in grouped.items():
        # DISTINCT PERIODS, not rows — see MIN_PERIODS.
        periods = sorted({str(r.get("period_start") or "") for r in group})
        if len(periods) < MIN_PERIODS:
            continue
        # `list_metric_points` sorts ascending by a NORMALISED period. Within one
        # (metric, source) the period is unique by the table's own key, so there
        # is no tie left for row order to decide.
        latest, earliest = group[-1], group[0]
        raw = latest.get("value")
        # `bool` is an `int` in Python, so it is excluded explicitly. NOT a
        # reachable branch through this store — `metric_points.value` is
        # `REAL NOT NULL`, so the database coerces a bool before anything reads
        # it — but the field is Optional for the renderer's "absent, never
        # zero" contract and this keeps that true if the store ever loosens.
        value = (float(raw) if isinstance(raw, (int, float))
                 and not isinstance(raw, bool) else None)
        source_label = source.replace("_", " ") if source else ""
        first_p = str(earliest.get("period_start") or "")
        last_p = str(latest.get("period_start") or "")
        overlap = len(goal_tokens & set(_tokens(key.replace("_", " "))))
        built.append((overlap, len(periods), MetricCandidate(
            key=key,
            # Labelled below, once the surviving set is known.
            label=_humanise(key),
            source=source,
            source_label=source_label,
            points=len(periods),
            current_value=value,
            current_period=last_p,
            first_period=first_p,
            last_period=last_p,
            consequence=_consequence(len(periods), first_p, last_p, source_label),
        )))

    # Best name match first, then the longest series BY DISTINCT PERIODS — a
    # metric measured for a year is a better thing to steer by than one read
    # twice in the same week by two trackers. Key is TOTAL (`key` and `source`
    # are unique together), so the order cannot flip between requests.
    built.sort(key=lambda t: (-t[0], -t[1], t[2].key, t[2].source))

    # THE SOURCE GOES IN THE LABEL ONLY WHERE THERE IS A SIBLING TO
    # DISAMBIGUATE FROM, and that has to be decided over the SURVIVING
    # candidates rather than over the registry. Counted at registry level, a
    # metric whose second provider was filtered out by MIN_PERIODS still got a
    # "· clickup" suffix with nothing to distinguish it from — an annotation
    # pretending to be a disambiguation.
    survivors: dict[str, int] = {}
    for _o, _p, cand in built:
        survivors[cand.key] = survivors.get(cand.key, 0) + 1
    labelled = [
        (replace(cand, label=f"{cand.label} · {cand.source_label}")
         if survivors.get(cand.key, 0) > 1 and cand.source_label else cand)
        for _o, _p, cand in built
    ]
    stats = {
        "registry_readable": True,
        "points": len(rows),
        "distinct_metrics": len({k for k, _ in grouped}),
        "distinct_series": len(grouped),
        "offered": min(len(built), MAX_CANDIDATES),
    }
    return labelled[:MAX_CANDIDATES], stats


def searched_summary(company_id: str, *, registry_stats: dict) -> list[dict]:
    """What the DEFINITION SEARCH looked at — §5 requirement 1.

    "Never open with what you do not know. Open with what you looked at."

    THE CORPUS INVENTORY IS THE WRONG NUMBER, and the first version of this
    printed it. `plan.source_inventory` counts every signal per source — the
    corpus the RUN will later read, not the places a DEFINITION was sought. It
    produced lines like "8,412 Slack and email", which the definition search
    never consulted, while omitting the KPI tree, which is the one thing it
    actually read. Inflating diligence is worse than not claiming it.

    So this reports the rungs of §4's ladder and what each returned.
    """
    out: list[dict] = []

    tree_metrics = 0
    try:
        from app.kpi_tree import load_kpi_tree

        tree = load_kpi_tree(company_id) or {}
        tree_metrics = len(tree) if isinstance(tree, (dict, list)) else 0
    except Exception:  # noqa: BLE001 — an unreadable rung reports as empty,
        # which is what it was for this search.
        logger.warning("crucible: kpi_tree unreadable for %s", company_id)
    out.append({
        "rung": "your KPI tree",
        "found": tree_metrics,
        "detail": (f"{tree_metrics} metric{'' if tree_metrics == 1 else 's'} defined"
                   if tree_metrics else "no metrics defined"),
    })

    metrics = registry_stats.get("distinct_metrics") or 0
    points = registry_stats.get("points") or 0
    if not registry_stats.get("registry_readable", True):
        detail = "could not be read"
    elif metrics:
        detail = f"{metrics} metric{'' if metrics == 1 else 's'}, {points} points"
    else:
        detail = "nothing recorded yet"
    out.append({"rung": "your measured metrics", "found": metrics,
                "detail": detail})
    return out

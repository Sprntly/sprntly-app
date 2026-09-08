"""A goal plus a schema view, turned into computed comparisons — real grouped
numbers, ready for a later stage to write up. STOPS SHORT OF FINDINGS: no
ranking, no prose, no report change. That is a later module's job.

THE MODEL SELECTS. THE CODE COMPUTES. `select_comparisons` hands the model
`crucible.evidence.schema_view(...)` — shape only, never a row — and gets back
which `(table, dimension, outcome, measure)` tuples are worth grouping and
why, plus an explicit `declined` list naming what it chose not to compute.
`compute_comparisons` then does every sum, rate, median and band deterministically,
over the real rows, in code the model never sees the output of. A function that
could hand a model both a schema view AND rows is a function a careless caller
could use to let the model grade its own homework; nothing here is shaped to
allow that.

WHY SELECTION, NOT ENUMERATION. Ranking every `(dimension × outcome)` pair by
effect size was tried and measured: 983 candidate splits on a comparable
corpus put a planted finding at the ~50th percentile of the noise, 13 of the
top 20 true only by construction, and the loudest single output a known bad
answer. A goal-conditioned selection call returned zero tautologies across 49
comparisons against the same data. So this asks the model which comparisons
are worth making, not which ones score highest.

WHY A MANDATORY COVERAGE SWEEP. The column carrying the single most important
churn finding in a real corpus was selected 0 times across 7 unswept runs —
never rejected, never considered, because it was never asked about by name.
Naming every candidate dimension column on the goal's LEAD table and requiring
the selector to say "used" or "declined" for each one is the cheapest recall
fix available: one call over roughly a dozen column names.

WHY FORCED ORIENTATION AND EXPLICIT BANDING. `dimension -> measure(outcome)`,
never the reverse — an inverted orientation reached the same finding and lost
the table that made it readable. And any numeric dimension must carry explicit
band `edges`: automatic tertiles flattened a real dose-response into a step,
automatic quartiles inverted it, and the edges that reproduce the expected
table have to be stated, not derived by a formula that already got it wrong
twice. A tuple missing either is REJECTED, never repaired — a rejected tuple
is visible in `Selection.rejected`; a silently repaired one would not be.

WHY THE DRAW IS PERSISTED, AND TAKEN ONCE. Only 3 of 11 comparison topics
survived three identical selector calls over the same corpus (exact-signature
Jaccard 0.33). That is `figure_class.classify_figures`'s and
`relevance.judge_relevance`'s own finding, restated for this stage: a model
call is a draw, not a lookup, and re-sampling to pick a "better" draw is
banned in this engine because it promotes an answer by how many times it was
asked, not by whether it is right. `select_comparisons` reads a stored
selection back rather than drawing again — see `dump_selection`/`load_selection`
— and computing twice from the same stored selection must produce the same
numbers, because the arithmetic is pure.

WHY PASSTHROUGHS ARE CARRIED HERE, VERBATIM. `crucible.evidence` marks a small
methodology sheet or a coded column's free-text sibling as retrievable but
takes no action on either — neither is expressible as a dimension or an
outcome, so a selector asked only to group things will never propose them.
Measured: the two things these marks catch were each selected 0 times in 7
runs; retrieving them moved the reachable finding count from 5-6 to 9 of 13.
`passthrough_payload` carries them through here, alongside the grouped
comparisons, deterministically — no model involved in choosing them because
`crucible.evidence` already decided they are always worth carrying.

NO MODEL CALL IS TESTED THROUGH AN `_offline()` GATE. Every existing Crucible
LLM module ships one whose body is `return "pytest" in sys.modules`, and the
consequence is that not one of the suite's tests exercises a model — the
parse, validation, orientation-check, banding-check and rejection paths have
never actually run against a payload shaped the way a real response is shaped.
Here the model call is INJECTABLE instead (`select_comparisons(..., call=...)`):
production leaves `call` unset and gets the real gateway; a test passes a
stub returning a recorded (or deliberately malformed) response, and every
downstream path — parse, validate, reject, persist — runs for real.

RUN-SCOPED ONLY. `dump_selection`/`load_selection` read and write the shape a
caller stores under `crucible_runs.prioritisation` — the same run-scoped JSON
`relevance.dump_verdicts`/`load_verdicts` already rides, so this needs no
migration. Nothing here is ever written to `kg_signal`, `kg_entity` or
`kg_relationship` — see `crucible.prose`'s opening docstring for why that
boundary matters for evidence read once.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from app.crucible.evidence import SchemaSnapshot, TableSchema
from app.crucible.recon import (
    ACCOUNT_HINTS,
    Table,
    aggregate_by_group,
    count_present_by_group,
    median_by_group,
    profile,
    rate_by_group,
    with_banded_column,
)

logger = logging.getLogger(__name__)

MEASURES: tuple[str, ...] = ("rate", "median", "mean")

# ── GUARDS. Every one is a judgement, so every one is named and every one
# records why it fired — a silently suppressed comparison is the failure mode
# these exist to prevent. ────────────────────────────────────────────────────

#: A group below this many contributing rows has its VALUE withheld — the
#: group still appears, with its count, so a reader sees "too few to show"
#: rather than a group that silently vanished.
MIN_GROUP_N = 5

#: A comparison whose dimension bands into more groups than this is
#: suppressed WHOLE — this is the shape a near-unique identifier used as a
#: dimension takes (as many groups as rows), and no ceiling on the individual
#: groups fixes a chart with forty bars.
MAX_GROUP_COUNT = 12

#: A comparison whose dimension or outcome column is emptier than this share
#: is suppressed WHOLE — a computation over a column that is mostly absent is
#: not a comparison, it is noise wearing a table's shape.
MAX_NULL_SHARE = 0.5

# ── THE SELECTION — what the model returns, validated, never repaired ───────


@dataclass(frozen=True)
class Comparison:
    """One VALIDATED, not-yet-computed comparison. `edges` is non-empty only
    when `dimension` is a numeric column — banding is meaningless on anything
    else and `_validate_one` never attaches it to one."""
    table: str
    dimension: str
    outcome: str
    measure: str          # one of MEASURES
    level: Optional[str]  # the value `measure == "rate"` counts toward
    edges: tuple[float, ...]
    why: str

    def orientation(self) -> str:
        """`dimension -> measure(outcome)` — the one direction this may ever
        read, stated as a string so a reader (or a test) can check it without
        re-deriving it from the fields."""
        inner = f"{self.measure}({self.outcome})"
        if self.measure == "rate" and self.level:
            inner = f"rate({self.outcome} == {self.level!r})"
        return f"{self.dimension} -> {inner}"


@dataclass(frozen=True)
class Declined:
    """One column or comparison the selector considered and chose not to
    compute, and why — named, not omitted. See the module docstring for why
    an unnamed decline is exactly the failure this whole design exists to
    catch."""
    table: str
    dimension: str
    why: str


@dataclass(frozen=True)
class RejectedComparison:
    """A tuple the model returned that failed validation — kept, not
    discarded, and never silently repaired. `kind` distinguishes a malformed
    proposed comparison from a malformed decline; both are visible here
    rather than one of them quietly vanishing."""
    raw: dict
    reason: str
    kind: str = "comparison"


@dataclass(frozen=True)
class Selection:
    """The validated draw for one run: what to compute, what was declined,
    what was rejected, and — for the lead table only — which candidate
    dimensions the selector never addressed at all."""
    lead_table: str
    comparisons: tuple[Comparison, ...]
    declined: tuple[Declined, ...]
    rejected: tuple[RejectedComparison, ...]
    missed_dimensions: tuple[str, ...]


def candidate_dimensions(table: TableSchema) -> tuple[str, ...]:
    """Every column on TABLE worth asking the selector about as a dimension —
    the coverage sweep's own input. Free text is excluded (nothing groups
    sensibly by a paragraph) and a wholly empty column is excluded (there is
    nothing to group). Everything else — coded, numeric, dated, ordinary
    text — is a candidate, because the sweep's whole point is to ask about
    columns a selector would not have thought to ask about on its own.
    """
    return tuple(
        c.name for c in table.columns if not c.is_freetext and c.kind != "empty"
    )


# ── THE MODEL CALL — one call, injectable, routed through the existing
# gateway. No new client, no `_offline()`. ───────────────────────────────────

PROMPT_VERSION = "crucible-table-select-v1"

SELECT_SYSTEM = """You are given a goal and the SHAPE of every table attached \
to this analysis — column names, kinds, fill rates and a few sample values. \
You are never given the rows. Your job is to choose which comparisons are \
worth computing to inform the goal, not to compute them yourself: you never \
report a number, a rate, or an average. Code does that afterwards, exactly \
as you specify it.

Return a list of comparisons, each one:
  - table: which attached table
  - dimension: the column to GROUP BY
  - outcome: the column being measured
  - measure: one of "rate", "median", "mean"
      * "rate" needs a `level` — the value of `outcome` being rated (e.g.
        dimension=plan_tier, outcome=status, measure=rate, level="Churned"
        asks "what share of each plan tier churned")
      * "median"/"mean" summarise a NUMERIC outcome per group and take no
        level
  - edges: REQUIRED, and only meaningful, when `dimension` is a NUMERIC
    column. An ascending list of at least two numbers marking band
    boundaries, e.g. [0, 10, 50, 200]. A numeric dimension without edges
    cannot be computed and will be rejected. Never invent edges for a
    non-numeric dimension.
  - why: one sentence, tied to the goal, for why this comparison matters

The orientation is always `dimension -> measure(outcome)`. Never propose the
reverse — grouping the outcome by the dimension's own values loses the table
a reader needs.

Also return a `declined` list: comparisons or columns you considered and
chose NOT to compute, each with a one-sentence reason. A declined column is
visible to the reader; an unmentioned one is invisible, so when this prompt
names a required coverage list, every column on it must appear either in a
comparison's `dimension` or in `declined` naming it — never both, never
neither.

Propose only comparisons genuinely useful for the stated goal. Fewer good
comparisons beat many marginal ones."""

SELECT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["comparisons", "declined"],
    "properties": {
        "comparisons": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "dimension", "outcome", "measure", "why"],
                "properties": {
                    "table": {"type": "string"},
                    "dimension": {"type": "string"},
                    "outcome": {"type": "string"},
                    "measure": {"type": "string", "enum": list(MEASURES)},
                    "level": {"type": ["string", "null"]},
                    "edges": {"type": "array", "items": {"type": "number"}},
                    "why": {"type": "string"},
                },
            },
        },
        "declined": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "dimension", "why"],
                "properties": {
                    "table": {"type": "string"},
                    "dimension": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
    },
}


def _input(
    *, goal_text: str, schema: SchemaSnapshot, lead_table: str,
    candidates: Sequence[str],
) -> str:
    lines = [f"GOAL:\n{goal_text.strip()}", "", "ATTACHED TABLE SHAPES (no rows):",
              json.dumps(schema.to_json(), indent=2, default=str)]
    if candidates:
        lines += [
            "",
            f"COVERAGE SWEEP for the lead table {lead_table!r} — you must "
            f"address every one of these {len(candidates)} candidate "
            f"dimension columns, each either used in a comparison's "
            f"`dimension` or named in `declined`: " + ", ".join(candidates),
        ]
    return "\n".join(lines)


def _default_call(
    *, enterprise_id: str, goal_text: str, schema: SchemaSnapshot,
    lead_table: str, candidates: Sequence[str],
) -> dict:
    from app.graph.gateway import llm_call
    from app.llm import DEFAULT_MODEL  # "claude-sonnet-4-6" — AD2.

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="select_table_comparisons",
        prompt_version=PROMPT_VERSION,
        model=DEFAULT_MODEL,
        system=SELECT_SYSTEM,
        input=_input(goal_text=goal_text, schema=schema, lead_table=lead_table,
                      candidates=candidates),
        json_schema=SELECT_SCHEMA,
        max_tokens=8000,
    )
    return result.output if isinstance(result.output, dict) else {}


# ── VALIDATION — reject a malformed tuple, never repair it ──────────────────


def _as_edges(raw: Any) -> Optional[tuple[float, ...]]:
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    try:
        edges = tuple(float(e) for e in raw
                       if isinstance(e, (int, float)) and not isinstance(e, bool))
    except (TypeError, ValueError):
        return None
    if len(edges) != len(raw):
        return None
    if list(edges) != sorted(edges) or len(set(edges)) != len(edges):
        return None
    return edges


def _validate_comparison(
    raw: Mapping[str, Any], *, tables_by_name: Mapping[str, TableSchema],
) -> "Comparison | RejectedComparison":
    table = str(raw.get("table") or "").strip()
    dimension = str(raw.get("dimension") or "").strip()
    outcome = str(raw.get("outcome") or "").strip()
    measure = str(raw.get("measure") or "").strip()
    why = str(raw.get("why") or "").strip()
    level_raw = raw.get("level")
    level = level_raw.strip() if isinstance(level_raw, str) and level_raw.strip() else None

    ts = tables_by_name.get(table)
    if ts is None:
        return RejectedComparison(raw=dict(raw), reason=f"unknown table {table!r}")
    cols = {c.name: c for c in ts.columns}
    if not dimension or dimension not in cols:
        return RejectedComparison(
            raw=dict(raw),
            reason="missing orientation: no dimension column on this table")
    if not outcome or outcome not in cols:
        return RejectedComparison(
            raw=dict(raw),
            reason="missing orientation: no outcome column on this table")
    if dimension == outcome:
        return RejectedComparison(
            raw=dict(raw),
            reason="missing orientation: dimension and outcome are the same column")
    if measure not in MEASURES:
        return RejectedComparison(raw=dict(raw), reason=f"unrecognised measure {measure!r}")
    if measure == "rate" and not level:
        return RejectedComparison(
            raw=dict(raw), reason="rate measure given without a level to rate")
    if not why:
        return RejectedComparison(raw=dict(raw), reason="no reason given")

    edges = _as_edges(raw.get("edges"))
    if cols[dimension].kind == "number":
        if edges is None:
            return RejectedComparison(
                raw=dict(raw),
                reason="numeric dimension missing explicit ascending band edges")
    else:
        # Banding only means anything for a numeric dimension — edges on
        # anything else are ignored rather than rejected, since they change
        # nothing about how the comparison computes.
        edges = None

    return Comparison(table=table, dimension=dimension, outcome=outcome,
                       measure=measure, level=level, edges=edges or (), why=why)


def _validate_declined(
    raw: Mapping[str, Any], *, tables_by_name: Mapping[str, TableSchema],
) -> "Declined | RejectedComparison":
    table = str(raw.get("table") or "").strip()
    dimension = str(raw.get("dimension") or "").strip()
    why = str(raw.get("why") or "").strip()
    if table not in tables_by_name or not dimension or not why:
        return RejectedComparison(
            raw=dict(raw), reason="malformed decline: missing table, dimension or why",
            kind="declined")
    return Declined(table=table, dimension=dimension, why=why)


def _parse(
    raw_output: Any, *, tables_by_name: Mapping[str, TableSchema],
    lead_table: str, candidates: Sequence[str],
) -> Selection:
    if not isinstance(raw_output, Mapping):
        return Selection(lead_table=lead_table, comparisons=(), declined=(),
                          rejected=(), missed_dimensions=tuple(candidates))

    comparisons: list[Comparison] = []
    declined: list[Declined] = []
    rejected: list[RejectedComparison] = []

    for item in raw_output.get("comparisons") or []:
        if not isinstance(item, Mapping):
            rejected.append(RejectedComparison(
                raw={"value": item} if not isinstance(item, dict) else dict(item),
                reason="comparison entry was not an object"))
            continue
        result = _validate_comparison(item, tables_by_name=tables_by_name)
        (comparisons if isinstance(result, Comparison) else rejected).append(result)

    for item in raw_output.get("declined") or []:
        if not isinstance(item, Mapping):
            rejected.append(RejectedComparison(
                raw={"value": item} if not isinstance(item, dict) else dict(item),
                reason="decline entry was not an object", kind="declined"))
            continue
        result = _validate_declined(item, tables_by_name=tables_by_name)
        (declined if isinstance(result, Declined) else rejected).append(result)

    # A column is COVERED if the selector engaged with it by name at all for
    # the lead table — as a dimension, as an outcome (it was worth measuring,
    # even if never grouped by), or explicitly declined. Only a column the
    # selector never mentioned in any role counts as missed.
    covered = {c.dimension for c in comparisons if c.table == lead_table}
    covered |= {c.outcome for c in comparisons if c.table == lead_table}
    covered |= {d.dimension for d in declined if d.table == lead_table}
    missed = tuple(name for name in candidates if name not in covered)

    return Selection(lead_table=lead_table, comparisons=tuple(comparisons),
                      declined=tuple(declined), rejected=tuple(rejected),
                      missed_dimensions=missed)


# ── PERSISTENCE — draw once, store the draw, read it back. Never re-sample
# to check it. Mirrors `figure_class.classify_figures` /
# `relevance.judge_relevance`, and rides `crucible_runs.prioritisation` the
# same way `relevance.VERDICTS_KEY` does, so no migration is needed. ────────

#: Where a drawn selection lives on the run's own JSON.
SELECTION_KEY = "table_selection"
#: Bumped only if the stored shape changes incompatibly. An unrecognised
#: version is treated as absent, which re-draws — the direction every other
#: stored fact here fails in.
SELECTION_VERSION = 1


def dump_selection(selection: Selection) -> dict:
    """SELECTION, in the shape stored on the run. Round-trips exactly through
    `load_selection` — see that function."""
    return {
        "version": SELECTION_VERSION,
        "lead_table": selection.lead_table,
        "comparisons": [
            {"table": c.table, "dimension": c.dimension, "outcome": c.outcome,
             "measure": c.measure, "level": c.level, "edges": list(c.edges),
             "why": c.why}
            for c in selection.comparisons
        ],
        "declined": [
            {"table": d.table, "dimension": d.dimension, "why": d.why}
            for d in selection.declined
        ],
        "rejected": [
            {"raw": r.raw, "reason": r.reason, "kind": r.kind}
            for r in selection.rejected
        ],
        "missed_dimensions": list(selection.missed_dimensions),
    }


def load_selection(run_meta: Mapping[str, Any]) -> Optional[Selection]:
    """The selection already drawn for this run, or `None` if none ever was.

    `None` and an EMPTY selection are different answers — the same
    distinction `relevance.load_verdicts` draws and for the same reason. A
    run whose selector returned nothing usable is a real, storable outcome;
    conflating it with "never drawn" would re-draw on every read and defeat
    the whole point of persisting the first draw.
    """
    if not isinstance(run_meta, Mapping):
        return None
    blob = run_meta.get(SELECTION_KEY)
    if not isinstance(blob, Mapping) or blob.get("version") != SELECTION_VERSION:
        return None
    comparisons = tuple(
        Comparison(
            table=str(c.get("table") or ""), dimension=str(c.get("dimension") or ""),
            outcome=str(c.get("outcome") or ""), measure=str(c.get("measure") or ""),
            level=(str(c["level"]) if c.get("level") else None),
            edges=tuple(float(e) for e in (c.get("edges") or ())),
            why=str(c.get("why") or ""),
        )
        for c in (blob.get("comparisons") or []) if isinstance(c, Mapping)
    )
    declined = tuple(
        Declined(table=str(d.get("table") or ""), dimension=str(d.get("dimension") or ""),
                 why=str(d.get("why") or ""))
        for d in (blob.get("declined") or []) if isinstance(d, Mapping)
    )
    rejected = tuple(
        RejectedComparison(raw=dict(r.get("raw") or {}), reason=str(r.get("reason") or ""),
                            kind=str(r.get("kind") or "comparison"))
        for r in (blob.get("rejected") or []) if isinstance(r, Mapping)
    )
    missed = tuple(str(m) for m in (blob.get("missed_dimensions") or [])
                    if isinstance(m, str))
    return Selection(lead_table=str(blob.get("lead_table") or ""),
                      comparisons=comparisons, declined=declined,
                      rejected=rejected, missed_dimensions=missed)


def select_comparisons(
    *,
    enterprise_id: str,
    goal_text: str,
    schema: SchemaSnapshot,
    lead_table: str,
    run_meta: Optional[Mapping[str, Any]] = None,
    call: Optional[Callable[..., dict]] = None,
) -> Selection:
    """The validated selection for this run: read back if one was already
    drawn, otherwise one call, validated, and returned — NOT stored. Storing
    it is the caller's job (`dump_selection`), the same split
    `relevance.judge_relevance` uses and for the same reason stated there: the
    caller owns the write, so a concurrent progress update cannot be
    clobbered here.

    READ BACK FIRST, ALWAYS — before `call` is even built, because reading a
    stored fact is not a model call and there is never a reason to withhold
    it from a caller that has one.

    `call` is the injectable seam: production leaves it `None` and gets the
    real gateway (`_default_call`); a test supplies a stub returning a
    recorded response, so parsing, validation, orientation and banding checks,
    and rejection all run against something shaped like the real thing.
    """
    stored = load_selection(run_meta or {})
    if stored is not None:
        logger.info(
            "crucible_table_selection_reused comparisons=%s declined=%s rejected=%s",
            len(stored.comparisons), len(stored.declined), len(stored.rejected),
        )
        return stored

    tables_by_name = {t.name: t for t in schema.tables}
    lead = tables_by_name.get(lead_table)
    candidates = candidate_dimensions(lead) if lead is not None else ()

    caller = call or _default_call
    try:
        raw_output = caller(enterprise_id=enterprise_id, goal_text=goal_text,
                             schema=schema, lead_table=lead_table,
                             candidates=candidates)
    except Exception:  # noqa: BLE001 — a failed draw is an empty one, not a
        # crashed run. The caller sees zero comparisons and every lead-table
        # candidate as missed, which is honest: nothing was decided.
        logger.exception("crucible: table selection call failed")
        raw_output = {}

    selection = _parse(raw_output, tables_by_name=tables_by_name,
                        lead_table=lead_table, candidates=candidates)
    logger.info(
        "crucible_table_selection comparisons=%s declined=%s rejected=%s missed=%s",
        len(selection.comparisons), len(selection.declined),
        len(selection.rejected), len(selection.missed_dimensions),
    )
    return selection


# ── COMPUTATION — deterministic, guarded, every suppression named ───────────


@dataclass(frozen=True)
class GroupValue:
    """One group's number, or the reason it is withheld. `value` is `None`
    exactly when `suppressed` is true — a suppressed group is never given a
    number that then has to be ignored by whoever reads it.

    `accounts` is the raw account names, in sheet order, of the rows that
    counted toward `n` — see `ComputedComparison.account_column` for when
    this is populated at all. Empty whenever no account column could be
    told for this comparison's table, never a guess."""
    group: str
    value: Optional[float]
    n: int
    suppressed: bool
    suppression_reason: str = ""
    accounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComputedComparison:
    """One comparison's real numbers, or the reason the whole thing is
    withheld. `as_of`/`today` travel with EVERY computed comparison — see the
    module docstring on why that fact is passed through rather than reasoned
    about later.

    `account_column` names the column on the source table that identifies the
    customer, or is `None` when this rule could not tell — either nothing on
    the table looked like one, or more than one candidate did and guessing
    between them was refused. `account_note` carries why, and is empty
    exactly when `account_column` is not `None`. See `_account_column`."""
    table: str
    dimension: str
    outcome: str
    measure: str
    level: Optional[str]
    edges: tuple[float, ...]
    why: str
    groups: tuple[GroupValue, ...]
    suppressed: bool
    suppression_reason: str
    as_of: Optional[datetime]
    today: datetime
    account_column: Optional[str] = None
    account_note: str = ""

    def orientation(self) -> str:
        inner = f"{self.measure}({self.outcome})"
        if self.measure == "rate" and self.level:
            inner = f"rate({self.outcome} == {self.level!r})"
        return f"{self.dimension} -> {inner}"


def _withheld(
    c: Comparison, *, reason: str, as_of: Optional[datetime], today: datetime,
) -> ComputedComparison:
    return ComputedComparison(
        table=c.table, dimension=c.dimension, outcome=c.outcome, measure=c.measure,
        level=c.level, edges=c.edges, why=c.why, groups=(), suppressed=True,
        suppression_reason=reason, as_of=as_of, today=today,
    )


# ── ACCOUNT IDENTITY — carried out of the rows, per group, never invented ───
#
# `ComputedComparison` used to carry no account identity at all: `git grep
# account` over this file returned nothing. Reach reads
# `population.segments["accounts"]`, filled from a row's `account` key one
# name at a time — so a computed comparison could not be sized even if
# everything downstream of it existed. This carries the raw names out of
# `Table.rows`, PER GROUP — "which accounts are in the solo-facilitator
# group" is the question that has to be answerable — without normalising,
# canonicalising or deduping across rows: `claims.account_key` and
# `canonical_account_names` already own that judgement, and duplicating it
# here is exactly how the two would drift apart.

#: The `_ID_TOKEN_RE` suffix check below excludes an id/key column even when
#: its name also contains one of these hints (`account_id`, `Customer ID`) —
#: an id is not a name a reader recognises, and this rule only ever carries
#: names.
_ID_TOKEN_RE = re.compile(r"(?:^|[_\s-])id$", re.IGNORECASE)


def _is_id_like(column: str) -> bool:
    """`True` for a column name ending in an "id" token — `account_id`,
    `Account ID`, `account-id` — on any separator. A column spelled with no
    separator at all (`AccountID`) is not matched; on real exports that
    spelling is rare enough that missing it costs less than excluding a
    genuine "Valid ID"-style name elsewhere would."""
    return bool(_ID_TOKEN_RE.search(column.strip()))


def _looks_like_account_column(column: str, kind: str) -> bool:
    """A TEXT column (never a number — an id or a code is not a name a
    reader recognises) whose name contains one of `recon.ACCOUNT_HINTS` and
    is not itself an id/key column. The same vocabulary `recon` already uses
    for the identical judgement one module over (`_looks_like_account`,
    there used to find a per-account VALUE column) — read from
    `ACCOUNT_HINTS`, not retyped, so the two decisions can never disagree
    about what counts as "account-ish" in a column name."""
    if kind != "text":
        return False
    c = column.lower()
    return any(h in c for h in ACCOUNT_HINTS) and not _is_id_like(column)


def _account_column(table: Table) -> tuple[Optional[str], str]:
    """The single column on TABLE that names the customer, or `None` plus why
    it could not be told.

    EXACTLY ONE CANDIDATE IS REQUIRED. Zero means nothing on this table looks
    like it names a customer — a real, ordinary case (an internal ops sheet
    with no customer-facing field). More than one means this rule cannot tell
    WHICH does, and guessing between two plausible columns is worse than
    naming neither: a wrong account attribution would silently mis-size a
    finding, where an absent one merely leaves it unsized. Both cases return
    `None`, never a guess — see the module's out-of-scope note on
    normalisation for why this stops at "which column", not "which spelling".
    """
    profs = {c: profile(table, c) for c in table.columns}
    candidates = [c for c in table.columns
                  if _looks_like_account_column(c, profs[c].kind)]
    if not candidates:
        return None, "no column name suggests it identifies the customer"
    if len(candidates) > 1:
        return None, (
            "ambiguous — more than one column name suggests it identifies "
            "the customer: " + ", ".join(candidates)
        )
    return candidates[0], ""


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _numberish(v: Any) -> Optional[float]:
    if isinstance(v, bool) or _blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _accounts_by_group(
    source: Table, group_field: str, account_column: Optional[str], *,
    outcome_field: str, measure: str,
) -> dict[str, tuple[str, ...]]:
    """Raw account names per group KEY, in sheet order — pulled from exactly
    the rows that already count toward that group's `n`, and no others:
    every row with a present GROUP_FIELD for `"rate"` (the same denominator
    `aggregate_by_group` counts with no measure field), and only rows where
    OUTCOME_FIELD is a present number for `"median"`/`"mean"` (the same rows
    `count_present_by_group` counts). A row that counts toward `n` but whose
    own account cell is blank simply names no one — it is not dropped from
    `n`, it contributes nothing here.

    DEDUPED ONLY ON AN EXACT REPEATED SPELLING WITHIN THE SAME GROUP, never
    across groups, never fuzzy. One account can file several rows in one
    group (several support tickets from the same customer in one segment);
    naming it once per group rather than once per row is what leaves "one
    row per account" to the later producer, rather than re-implementing that
    producer's own job here.
    """
    if account_column is None:
        return {}
    out: dict[str, list[str]] = {}
    seen: dict[str, set] = {}
    for r in source.rows:
        g = r.get(group_field)
        if _blank(g):
            continue
        if measure != "rate" and _numberish(r.get(outcome_field)) is None:
            continue
        key = str(g).strip()
        raw = r.get(account_column)
        if _blank(raw):
            continue
        name = str(raw).strip()
        bucket = seen.setdefault(key, set())
        if name in bucket:
            continue
        bucket.add(name)
        out.setdefault(key, []).append(name)
    return {k: tuple(v) for k, v in out.items()}


def _compute_one(
    c: Comparison, table: Table, *, today: datetime,
) -> ComputedComparison:
    # NULL-SHARE CEILING — checked before anything is grouped, because a
    # comparison over a mostly-empty column is not a comparison worth
    # grouping at all.
    dim_missing = profile(table, c.dimension).missing_share
    out_missing = profile(table, c.outcome).missing_share
    if dim_missing > MAX_NULL_SHARE or out_missing > MAX_NULL_SHARE:
        field, share = ((c.dimension, dim_missing) if dim_missing > MAX_NULL_SHARE
                         else (c.outcome, out_missing))
        return _withheld(
            c, as_of=table.as_of, today=today,
            reason=(f"{field!r} is {share:.0%} empty, over the "
                    f"{MAX_NULL_SHARE:.0%} null-share ceiling"))

    source = table
    group_field = c.dimension
    if c.edges:
        band_field = f"__band__{c.dimension}"
        source = with_banded_column(table, c.dimension, c.edges, as_field=band_field)
        group_field = band_field

    if c.measure == "rate":
        values = rate_by_group(source, group_field, c.outcome, c.level)
        counts = aggregate_by_group(source, group_field)
    elif c.measure == "median":
        values = median_by_group(source, group_field, c.outcome)
        counts = count_present_by_group(source, group_field, c.outcome)
    else:  # mean
        totals = aggregate_by_group(source, group_field, c.outcome)
        counts = count_present_by_group(source, group_field, c.outcome)
        values = {k: totals[k] / counts[k] for k in totals if counts.get(k)}

    # GROUP COUNT CEILING — on the raw group set the arithmetic produced,
    # before any group is suppressed for a low count. A dimension that bands
    # into forty groups is the same failure whether every group is big enough
    # to show or not.
    if len(values) > MAX_GROUP_COUNT:
        return _withheld(
            c, as_of=table.as_of, today=today,
            reason=(f"{len(values)} groups exceeds the ceiling of "
                    f"{MAX_GROUP_COUNT}"))

    account_column, account_note = _account_column(table)
    accounts_by_group = _accounts_by_group(
        source, group_field, account_column,
        outcome_field=c.outcome, measure=c.measure)

    groups = []
    for key in sorted(values):
        n = int(counts.get(key, 0))
        group_accounts = accounts_by_group.get(key, ())
        # MINIMUM-N PER GROUP — the group still appears, with its count, so a
        # reader sees "n=2, too few to show" rather than a group that just
        # is not there.
        if n < MIN_GROUP_N:
            groups.append(GroupValue(
                group=key, value=None, n=n, suppressed=True,
                suppression_reason=f"n={n} is below the minimum of {MIN_GROUP_N}",
                accounts=group_accounts))
        else:
            groups.append(GroupValue(group=key, value=values[key], n=n,
                                      suppressed=False, accounts=group_accounts))

    return ComputedComparison(
        table=c.table, dimension=c.dimension, outcome=c.outcome, measure=c.measure,
        level=c.level, edges=c.edges, why=c.why, groups=tuple(groups),
        suppressed=False, suppression_reason="", as_of=table.as_of, today=today,
        account_column=account_column, account_note=account_note,
    )


def compute_comparisons(
    tables_by_name: Mapping[str, Table], selection: Selection, *,
    today: Optional[datetime] = None,
) -> tuple[ComputedComparison, ...]:
    """Every comparison in SELECTION, computed for real over `tables_by_name`.

    PURE AND DETERMINISTIC: the same selection over the same tables produces
    the same output every time, which is the entire point of persisting the
    selection rather than the numbers — a second run over a stored selection
    recomputes rather than re-draws, and gets back the same numbers because
    nothing here is a draw.
    """
    today = today or datetime.now(timezone.utc)
    out = []
    for c in selection.comparisons:
        table = tables_by_name.get(c.table)
        if table is None:
            out.append(_withheld(
                c, as_of=None, today=today,
                reason=f"table {c.table!r} is not attached to this run"))
            continue
        out.append(_compute_one(c, table, today=today))
    return tuple(out)


# ── PASSTHROUGHS — carried verbatim, deterministically, no model involved ───


@dataclass(frozen=True)
class PassthroughSheet:
    """A whole small table (or field/value sheet), carried through untouched
    — see `crucible.evidence.is_passthrough_sheet`."""
    table: str
    label: str
    as_of: Optional[datetime]
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PassthroughColumn:
    """One free-text column carried through untouched, beside a coded
    sibling on the same table — see `crucible.evidence.passthrough_columns`."""
    table: str
    label: str
    column: str
    as_of: Optional[datetime]
    values: tuple[Any, ...]


def passthrough_payload(
    tables: Sequence[Table],
) -> tuple[tuple[PassthroughSheet, ...], tuple[PassthroughColumn, ...]]:
    """Every passthrough sheet and passthrough column across TABLES, verbatim.

    ACTS ON the marks `crucible.evidence.is_passthrough_sheet` /
    `passthrough_columns` already compute; it does not re-derive them. Marked,
    not selected — nothing here asks a model whether to carry these through,
    because `crucible.evidence` already decided they always are.
    """
    from app.crucible.evidence import is_passthrough_sheet, passthrough_columns

    sheets = []
    columns = []
    for t in tables:
        if is_passthrough_sheet(t):
            sheets.append(PassthroughSheet(
                table=t.name, label=t.label, as_of=t.as_of, rows=t.rows))
        for col in passthrough_columns(t):
            columns.append(PassthroughColumn(
                table=t.name, label=t.label, column=col, as_of=t.as_of,
                values=tuple(t.values(col))))
    return tuple(sheets), tuple(columns)

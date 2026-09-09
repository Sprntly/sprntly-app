"""Stage — `table_select.ComputedComparison` into run-scoped, `kg_signal`-
shaped claim rows a reader actually sees as a finding.

`crucible.evidence` (rows-free schema view) and `crucible.table_select`
(goal-conditioned selection, deterministic computation, per-group account
names) are both already merged and imported by nothing. This module is what
makes them produce a finding: it turns `table_select.compute_comparisons`'
output into the same `dict` shape `crucible.prose.signal_to_row` builds for
attached prose — read by `claims.project_signals` alongside the corpus rows,
and NEVER written to `kg_signal`, `kg_entity` or `kg_relationship`. See
`crucible.prose`'s opening docstring for why that boundary matters for
evidence read once.

── THE BUCKET DECISION, AND WHY IT IS SAFE ──────────────────────────────────

A computed differential ranks ABOVE `constraint` in `moscow.type_bucket` —
not because it is bigger, but because it is evidence of a different, stronger
class: something the customer's own operational records show, as opposed to
something a customer said on a call. Measured on a real churn goal, the four
largest kept findings after the reason classifier were all `constraint`
claims (47/36/34/14 accounts) and a genuine, correctly-computed 15-account
churn answer from the customer's own workbook ranked fourth — below
`MAX_WRITTEN_UP_FINDINGS` — behind a wrong answer for the goal asked
(`stakeholder alignment`, which is pre-sale material on a retention
question). See `moscow.type_bucket`'s own docstring for the ranking mechanics
this reads.

Two properties make that placement safe rather than aggressive, and this
module is where both are enforced:

1. **THE TYPE IS ASSIGNED DETERMINISTICALLY, NEVER BY A MODEL.**
   `COMPUTED_COMPARISON_KIND` is a fixed Python string this module stamps on
   every row it builds. `select_comparisons`'s model call chooses WHICH
   comparisons to compute — never the `kind` a row carries, never the claim
   type that kind maps to. `claims.KIND_TO_CLAIM_TYPE` reads the kind this
   module wrote, deterministically, exactly as it does for every other kind.
   `tests/test_crucible_tabular_findings.py` asserts no prompt or schema
   description anywhere in the repo names the claim type this maps to — that
   test IS the safety argument for the bucket, not decoration on top of it.
2. **ONLY THIS MODULE EMITS THE KIND.** Nothing else in the corpus — no
   connector, no extractor, no prose pass — ever writes
   `COMPUTED_COMPARISON_KIND`, so nothing existing changes bucket or rank by
   this change landing.

── ONE FINDING PER COMPARISON, NOT PER GROUP ───────────────────────────────

A comparison is a TABLE and each group is a ROW of it. The first version of
this module minted a distinct theme entity per (comparison, group), so every
row became its own top-level finding: measured on a real nine-run benchmark,
**89 distinct `(dimension, statistic)` comparisons became 372 findings, a
4.2x multiplier**, and the single most valuable thing a comparison can say —
"aggregate retention hides the segment split" — was shredded across seven
non-adjacent ranks (#20, #31, #38, #49, #51, #52, #55) and sorted by group
size, the one ordering guaranteed to separate the rows of one table.

So the entity id is now keyed on the COMPARISON alone. Every account the
comparison covers, in any of its groups, shares one theme entity and clusters
into one finding whose label states the dimension, the statistic and every
group with its value and its count — a reader sees the split AS a split.

Three consequences, each of which bites if missed:

- **THE UNION OF ACCOUNTS, NOT ONE GROUP'S `n`.** `impact_value` reads
  `population.segments["accounts"]`, so it now sizes the whole comparison.
  Nothing sums group counts: an account is emitted ONCE per comparison even
  where it files rows in several groups (a support-ticket table groups the
  same customer under two priorities), because two rows for one account under
  one entity id would collide on `id` and double-count reach.
- **THE ECHO GATE STILL DEPENDS ON THE ARTIFACT IDS, NOT THE ENTITY ID** —
  §2 below is unchanged and is the reason collapsing findings is safe. The
  artifact id is keyed per ACCOUNT, so collapsing groups can only ever raise
  the distinct-artifact count within a cluster, never lower it.
- **A ONE-GROUP COMPARISON IS STILL A FINDING**, labelled as the single group
  it is rather than as a range of one.

── THE FOUR THINGS THIS PRODUCER MUST GET RIGHT, EACH MEASURED ─────────────

**1. ONE ROW PER ACCOUNT.** `claims._population` reads at most one name per
property key, so a 35-account finding needs 35 rows, each naming exactly one
account under `properties["account"]`. `table_select.GroupValue.accounts`
already carries the raw names per group; `account_column is None` means the
comparison cannot be sized at all, and this module says so (a log line, zero
rows) rather than guessing an account column.

**2. A DISTINCT ARTIFACT ID PER ROW.** `pipeline._refute`'s echo rule kills a
cluster whose claims all name ONE artifact within `ECHO_WINDOW` — and every
row from one workbook shares a file and, via `evidence.derive_as_of`, a
single per-TABLE `as_of` date. So `span == 0` for every group this module
ever produces, and the ONLY lever left to keep `one_conversation` from
reading `True` is `len({claim.artifact_id for claim in group}) > 1`. Every
row here therefore carries `provenance["doc"]` keyed by TABLE and ACCOUNT,
not by table alone — one workbook, many artifacts, exactly as many as there
are named rows in a group. See `prose.py`'s own lesson on this same rule:
"A DOCUMENT IS NOT AN ARTIFACT. A CONVERSATION IS." — restated here for a
spreadsheet: a row is not the file it came from.

**3. A `subject_cluster_id` THAT IS NOT THE UNGROUPABLE PREFIX.**
`pipeline._cluster` reads a claim's `subject_cluster_id` first, and it is
never falsy on a measured real corpus. Every claim `execute_run` produces —
graph-sourced or not — is run through `kg_themes.assign_themes` and then
`cluster.assign_clusters` for whatever the graph did not theme; the second of
those marks ANY claim with no embedding `cluster.UNGROUPABLE_PREFIX`, and
these rows have none. So a computed-comparison row must never reach
`assign_clusters` unthemed: `TabularEvidence.theme_map` is built here,
exactly as `prose.theme_map_for` is for attached prose, and the caller
(`execute_run`) merges it into the graph's own theme map BEFORE theming runs
— which is what keeps `assign_themes` finding an entry for every row this
module wrote, and every row in one group sharing the SAME entry, so they
cluster into one finding rather than one pseudo-group each.

**4. A REAL `valid_at`.** `claims.project_signal` drops a row outright when
its timestamp cannot be parsed (`claims.py`, `project_signal`'s first check)
— `observed_at` drives decay and a silent `now()` fallback would make stale
evidence look fresh. `evidence.derive_as_of` already exists and was unwired;
this module calls it once per table and stamps the same `as_of` onto every
row from that table.

── TOTALITY ──────────────────────────────────────────────────────────────

`produce` never raises. A malformed workbook, a failed selection, or a
raising model call each costs this module's own rows and never the run —
exactly the contract `crucible.prose._prose_evidence` already keeps for
attached prose. `select_comparisons` itself is already total (it catches its
own model-call failure and returns an empty selection); the `try/except`
here is defence for everything downstream of that: a malformed table, a
computation that cannot be sized, a row that cannot be built.

Gated at the call site (`execute_run`) on the run having at least one
attached, groupable table — a run with none takes today's path with no
extra model call and no extra cost, which is `produce`'s own first check.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from app.crucible import evidence, table_select
from app.crucible.recon import Table

logger = logging.getLogger(__name__)

# ── THE ONLY TWO STRINGS A MODEL NEVER SEES AND NEVER CHOOSES ───────────────
#
# `select_comparisons`'s model call picks WHICH `(table, dimension, outcome,
# measure)` tuples to compute. It never sees, and never returns, either of
# these — they are stamped on every row by this module's own code, which is
# the entire safety argument for `moscow.type_bucket` ranking the claim type
# these map to above `constraint`. See the module docstring §1.

#: `kg_signal.kind` this producer stamps on every row — mapped in
#: `claims.KIND_TO_CLAIM_TYPE` to the `computed_differential` claim type.
COMPUTED_COMPARISON_KIND = "computed_comparison"

#: `kg_signal.source_type` this producer stamps on every row — a witness
#: class of its own (the customer's own operational records, read once, over
#: a workbook attached to this run) rather than any connector's standing
#: feed. Mapped in `claims.AUTHORITATIVE_FOR`.
COMPUTED_SOURCE_TYPE = "computed"

#: Mirrors `claims.ATTACHMENT_CHANNEL` (the reader) / `prose.ATTACHMENT_CHANNEL`
#: (prose's own writer copy). A third copy here for the same reason prose
#: keeps its own rather than importing `claims`': avoiding a load-time import
#: back into a module this one is already imported alongside.
ATTACHMENT_CHANNEL = "chat_attachment"

#: Prefix for the deterministic `subject_cluster_id` this module stamps via
#: its theme-map entries — see the module docstring §3. Not
#: `cluster.UNGROUPABLE_PREFIX`, which is the one string this must never
#: collide with; a `sha256` digest under a distinct, human-legible prefix
#: makes that collision astronomically unlikely without needing to import
#: `cluster` just to compare against its constant.
ENTITY_ID_PREFIX = "computed_comparison"


@dataclass(frozen=True)
class TabularEvidence:
    """Everything one run's attached tables contributed toward a computed
    finding, and what it cost. Empty is the correct, total answer for a run
    with nothing groupable attached — see the module docstring on totality.
    """
    #: `kg_signal`-shaped dicts, never persisted — read by
    #: `claims.project_signals` alongside the corpus rows.
    rows: tuple[dict, ...] = ()
    #: `claim id -> (entity_id, label, relation)` — merged into the graph's
    #: own theme map by the caller, BEFORE `kg_themes.assign_themes` runs.
    #: See the module docstring §3.
    theme_map: Mapping[str, tuple[str, str, Optional[str]]] = field(
        default_factory=dict)
    #: The validated draw this run computed over, or `None` if nothing was
    #: attached, groupable, or drawable.
    selection: Optional[table_select.Selection] = None
    #: Every comparison this run computed, suppressed or not — kept so a
    #: caller can disclose what was tried even where it produced no rows.
    computed: tuple[table_select.ComputedComparison, ...] = ()

    @property
    def summary(self) -> dict:
        return {
            "comparisons": len(self.computed),
            "rows": len(self.rows),
            # One entity per comparison that produced rows — the count of
            # findings this stage will contribute, which is what a caller
            # disclosing the stage actually wants to say.
            "findings": len({tm[0] for tm in self.theme_map.values()}),
        }


def _pick_lead_table(tables: Sequence[Table]) -> Optional[str]:
    """The table `select_comparisons`'s coverage sweep runs over.

    THE LARGEST NON-EMPTY ATTACHED TABLE, TIES BROKEN BY NAME. Nothing in
    this run tells this module which of several attached sheets the reader
    means to ask about, so the same "biggest rectangle is the one worth
    grouping" default `table_select`'s own worked examples assume is applied
    here, deterministically — never a guess that could vary between two runs
    over the same upload. `None` when nothing attached has any rows at all,
    which the caller reads as "nothing to compute", the same as no upload.

    NOT FILTERED BY `evidence.is_passthrough_sheet`. That mark says a table
    is ALSO small enough to carry through verbatim
    (`table_select.passthrough_payload` already does, independently of
    anything here) — it does not say the table is ungroupable. A compact but
    genuine account-level sheet (well under
    `evidence.PASSTHROUGH_SHEET_MAX_ROWS`) is exactly the shape a smaller
    tenant attaches, and excluding it from lead-table candidacy would leave
    this module producing nothing for them.
    """
    candidates = [t for t in tables if t.rows]
    if not candidates:
        return None
    candidates.sort(key=lambda t: (-len(t.rows), t.name))
    return candidates[0].name


def _group_signature(cc: "table_select.Comparison | table_select.ComputedComparison") -> str:
    """A stable string identifying one COMPARISON (not yet one group within
    it) — the same for every group `_compute_one` produces from it, so two
    different comparisons over the same columns never share an entity id."""
    edges = ",".join(str(e) for e in cc.edges)
    level = cc.level or ""
    return f"{cc.table}|{cc.dimension}|{cc.outcome}|{cc.measure}|{level}|{edges}"


def _entity_id(comparison_signature: str) -> str:
    """One deterministic id per COMPARISON — identical across every row of
    every group of that comparison (so the whole comparison clusters into ONE
    finding, module docstring §"one finding per comparison"), different across
    comparisons (so two comparisons never merge).

    IT USED TO TAKE A GROUP KEY, and that is precisely the defect this
    replaces: a distinct entity id per group became a distinct cluster, which
    became a distinct top-level finding, which is how one segment split
    reached the reader as seven unrelated statements. The digest is still over
    the comparison signature alone, so it stays stable across runs over the
    same selection.
    """
    digest = hashlib.sha256(
        comparison_signature.encode("utf-8")).hexdigest()[:20]
    return f"{ENTITY_ID_PREFIX}:{digest}"


#: How long a comparison label may get before it starts saying "+K more".
#:
#: `report.MAX_STATEMENT_CHARS = 400` is what clips a finding's HEADING, and
#: `report.MAX_PARAM_NAME_CHARS = 120` clips the same label in the ranking
#: table — so the head of the label (dimension, statistic, span, group count)
#: has to carry the finding on its own, and the group-by-group breakdown that
#: follows has to fit inside the heading's budget with room for the ellipsis
#: the clipper adds. `table_select.MAX_GROUP_COUNT = 12` bounds how many
#: groups can ever arrive here; twelve short group names fit, twelve long
#: tenant strings do not, and the ones past the budget are SUMMARISED rather
#: than silently dropped.
MAX_LABEL_CHARS = 360

#: The same budget for the per-account content sentence. `cluster.example_for`
#: clips a rendered example at 200 characters, so anything past this is for
#: the stored row rather than for the page — kept bounded anyway so one
#: pathological workbook cannot write a paragraph into every claim.
MAX_CONTENT_CHARS = 420


def _stat_phrase(cc: "table_select.ComputedComparison") -> str:
    if cc.measure == "rate" and cc.level:
        return f"{cc.level!r} rate of {cc.outcome}"
    return f"{cc.measure} of {cc.outcome}"


def _format_value(cc: "table_select.ComputedComparison", value: float) -> str:
    if cc.measure == "rate":
        return f"{value:.0%}"
    return f"{value:,.3g}"


def _shown_groups(
    cc: "table_select.ComputedComparison",
) -> tuple["table_select.GroupValue", ...]:
    """Every group of CC that has a real number and at least one named
    account, STRONGEST FIRST.

    A suppressed group (`n` under `table_select.MIN_GROUP_N`) has no value by
    construction and an unnamed one has nobody to attribute a row to, so
    neither can appear in the comparison's table. Ordering by value descending
    — ties broken by group name so the order is stable across runs — is what
    makes the label read as a SPLIT rather than as a list: the reader sees the
    worst and the best group beside each other rather than having to sort
    twelve numbers themselves. Sorting by group SIZE is what the per-group
    version effectively did, and it is the one ordering that hides a split.
    """
    shown = [g for g in cc.groups
             if not g.suppressed and g.value is not None and g.accounts]
    shown.sort(key=lambda g: (-(g.value or 0.0), g.group))
    return tuple(shown)


def _group_phrase(
    cc: "table_select.ComputedComparison", g: "table_select.GroupValue",
) -> str:
    return f"{g.group} {_format_value(cc, g.value or 0.0)} (n={g.n})"


def _group_list(
    cc: "table_select.ComputedComparison",
    shown: Sequence["table_select.GroupValue"], *, budget: int, used: int,
) -> str:
    """The groups, in order, until BUDGET is spent — then how many are left.

    SUMMARISED, NEVER TRUNCATED SILENTLY. A label that simply stopped would
    read as though the comparison had four groups when it had eleven, which
    is a worse failure than a long label: the whole point of collapsing to one
    finding per comparison is that the reader can see how many groups there
    are and where each sits.
    """
    parts: list[str] = []
    for i, g in enumerate(shown):
        piece = _group_phrase(cc, g)
        if parts and used + len(piece) + 2 > budget:
            parts.append(f"+{len(shown) - i} more")
            break
        parts.append(piece)
        used += len(piece) + 2
    return "; ".join(parts)


def _label(
    cc: "table_select.ComputedComparison",
    shown: Sequence["table_select.GroupValue"],
) -> str:
    """The theme label for a WHOLE COMPARISON — becomes `Claim.subject` on
    every row of every group via `kg_themes.assign_themes`, and therefore
    `Finding.label` (`pipeline._label` takes the most common subject in the
    cluster, and here every claim carries the same one).

    THE HEAD CARRIES THE FINDING ALONE. `report` clips this to 120 characters
    in the ranking table and to 400 as the write-up heading, so the dimension,
    the statistic, the number of groups and the span come first and the
    group-by-group breakdown follows. A reader who sees only the first clause
    still knows what was compared and how far apart the groups are.
    """
    head = f"{cc.dimension}: {_stat_phrase(cc)}"
    if len(shown) == 1:
        return f"{head} — {_group_phrase(cc, shown[0])}"
    lo, hi = shown[-1], shown[0]
    head = (
        f"{head} across {len(shown)} groups, "
        f"{_format_value(cc, lo.value or 0.0)}–{_format_value(cc, hi.value or 0.0)}"
    )
    return f"{head} — " + _group_list(
        cc, shown, budget=MAX_LABEL_CHARS, used=len(head) + 3)


def _content(
    account: str, cc: "table_select.ComputedComparison",
    shown: Sequence["table_select.GroupValue"],
    member: Sequence["table_select.GroupValue"], total_accounts: int,
) -> str:
    """One account's row of the comparison, said as a sentence.

    NAMES THE ACCOUNT'S OWN GROUP AND THEN THE WHOLE TABLE, in that order —
    the row is what makes this claim about this account (`claims._population`
    reads exactly one name per row), and the table is what makes the finding
    worth reading. `member` is every group this account appears in; on most
    tables that is exactly one, but a per-EVENT table (support tickets, say)
    can file the same customer under two priorities, and saying so is cheaper
    than pretending a partition exists that does not.
    """
    as_of_txt = cc.as_of.date().isoformat() if cc.as_of else "an unspecified date"
    why = f" {cc.why}" if cc.why else ""
    if not member:
        where = ""
    elif len(member) == 1:
        where = (f", where {cc.dimension} = {member[0].group!r}")
    else:
        where = (
            f", filing rows under {len(member)} of the {cc.dimension} values "
            f"compared ({', '.join(repr(g.group) for g in member)})"
        )
    head = (
        f"{account} is one of {total_accounts} accounts covered by a "
        f"comparison over the attached {cc.table!r} table{where}. "
        f"Grouped by {cc.dimension}, {_stat_phrase(cc)} is "
    )
    body = _group_list(cc, shown, budget=MAX_CONTENT_CHARS, used=len(head))
    return f"{head}{body}, as of {as_of_txt}.{why}"


def rows_for_computed(
    computed: Sequence["table_select.ComputedComparison"], *, now: datetime,
) -> tuple[tuple[dict, ...], dict[str, tuple[str, str, Optional[str]]]]:
    """Every un-suppressed, sizeable COMPARISON across COMPUTED -> one row per
    account it covers, plus the theme-map entries that keep all of a
    comparison's rows clustered into ONE finding and off the ungroupable path.

    ONE ROW PER ACCOUNT PER COMPARISON, NOT PER (ACCOUNT, GROUP). The row id
    is `(entity_id, account)` and the entity id is now the comparison's, so an
    account that files rows under two group values would otherwise produce two
    rows with the SAME id — a duplicate `claims._population` would count
    twice. The account is emitted once, and its `content` names every group it
    appears in.

    Skips a whole comparison when `account_column is None` — it cannot be
    sized by account at all, and guessing which column names the customer is
    exactly the failure `table_select._account_column` already refused to
    commit. Skips a comparison with no showable group (every group under
    `MIN_GROUP_N`, or naming no accounts): there is no table left to state.
    """
    rows: list[dict] = []
    theme_map: dict[str, tuple[str, str, Optional[str]]] = {}
    for cc in computed:
        if cc.suppressed:
            continue
        if cc.account_column is None:
            logger.info(
                "crucible: computed comparison %s cannot be sized by "
                "account (%s); no rows produced",
                cc.orientation(), cc.account_note or "no reason given")
            continue
        shown = _shown_groups(cc)
        if not shown:
            logger.info(
                "crucible: computed comparison %s has no group with both a "
                "value and a named account; no rows produced",
                cc.orientation())
            continue
        entity_id = _entity_id(_group_signature(cc))
        label = _label(cc, shown)

        # WHICH GROUPS EACH ACCOUNT IS IN. Insertion order is (group order,
        # then sheet order within the group), so the emitted rows are
        # deterministic for a given selection over a given table.
        member: dict[str, list["table_select.GroupValue"]] = {}
        for g in shown:
            for account in g.accounts:
                member.setdefault(account, []).append(g)
        overlapping = sum(1 for gs in member.values() if len(gs) > 1)
        if overlapping:
            # NOT AN ERROR, AND WORTH SAYING OUT LOUD. A per-ACCOUNT table
            # partitions its accounts across the groups of any dimension; a
            # per-EVENT table (tickets, sessions) does not, and the difference
            # decides whether the union below is also the sum. Logged rather
            # than assumed either way.
            logger.info(
                "crucible: %s of %s accounts appear in more than one group of "
                "computed comparison %s — the comparison is sized by the "
                "union, not the sum",
                overlapping, len(member), cc.orientation())

        for account, groups in member.items():
            account_digest = hashlib.sha256(
                account.encode("utf-8")).hexdigest()[:16]
            row_id = f"{entity_id}:{account_digest}"
            # A DISTINCT ARTIFACT PER ROW — module docstring §2, and the
            # property collapsing the findings must not disturb. Keyed on the
            # TABLE and the ACCOUNT, not on the comparison or the group, so
            # the SAME account named by two different comparisons over the
            # same table is (correctly) the same underlying record — while
            # every account within one comparison still reads as a separate
            # source document to `pipeline._refute`. Collapsing groups can
            # only ever RAISE the distinct-artifact count inside a cluster.
            artifact_id = f"{ENTITY_ID_PREFIX}:{cc.table}:{account}"
            rows.append({
                "id": row_id,
                "kind": COMPUTED_COMPARISON_KIND,
                "source_type": COMPUTED_SOURCE_TYPE,
                "content": _content(account, cc, shown, groups, len(member)),
                "properties": {"account": account},
                "provenance": {
                    "doc": artifact_id, "channel": ATTACHMENT_CHANNEL,
                },
                "valid_at": cc.as_of.isoformat() if cc.as_of else now.isoformat(),
                "created_at": now.isoformat(),
                "source_id": artifact_id,
            })
            theme_map[row_id] = (entity_id, label, None)
    return tuple(rows), theme_map


def produce(
    *,
    tables: Sequence[Table],
    enterprise_id: str,
    goal_text: str,
    now: Optional[datetime] = None,
    run_meta: Optional[Mapping[str, Any]] = None,
    lead_table: Optional[str] = None,
    call: Optional[Callable[..., dict]] = None,
) -> TabularEvidence:
    """Every row a reader actually sees from the tables attached to this run.

    TOTAL — never raises. See the module docstring on totality: a malformed
    workbook, a failed selection or a raising model call each degrade to an
    empty `TabularEvidence`, never propagate.

    `tables` ARRIVE UNDATED OR DATED — either is accepted. `evidence.with_as_of`
    is idempotent-in-effect here: it is called unconditionally so a caller
    that has not yet dated its tables (most callers) gets a real `as_of` on
    every row (module docstring §4), and a caller that already ran
    `with_as_of` simply gets the same dates recomputed.

    `call` IS THE SAME INJECTABLE SEAM `table_select.select_comparisons`
    exposes — production leaves it unset and gets the real gateway; a test
    supplies a stub, including one that raises, to prove the totality
    contract above without a network call.
    """
    if not tables:
        return TabularEvidence()
    now = now or datetime.now(timezone.utc)
    try:
        dated = evidence.with_as_of(tables, now=now)
        lead = lead_table or _pick_lead_table(dated)
        if lead is None:
            return TabularEvidence()
        tables_by_name = {t.name: t for t in dated}
        schema = evidence.schema_view(dated, today=now)
        selection = table_select.select_comparisons(
            enterprise_id=enterprise_id, goal_text=goal_text, schema=schema,
            lead_table=lead, run_meta=run_meta, call=call,
        )
        computed = table_select.compute_comparisons(
            tables_by_name, selection, today=now)
        rows, theme_map = rows_for_computed(computed, now=now)
        return TabularEvidence(
            rows=rows, theme_map=theme_map, selection=selection,
            computed=computed,
        )
    except Exception:  # noqa: BLE001 — see the module docstring on totality.
        # A run that died here because one attached workbook was malformed
        # would be strictly worse than one that reads its connected corpus
        # and the rest of its attachments and says nothing about this one.
        logger.exception(
            "crucible: could not produce tabular findings for %s",
            enterprise_id)
        return TabularEvidence()

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
            "groups": len({tm[0] for tm in self.theme_map.values()}),
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


def _entity_id(comparison_signature: str, group_key: str) -> str:
    """One deterministic id per (comparison, group) — identical across every
    row of one group (so they cluster together, module docstring §3),
    different across groups and comparisons (so they never merge)."""
    digest = hashlib.sha256(
        f"{comparison_signature}\x1f{group_key}".encode("utf-8")).hexdigest()[:20]
    return f"{ENTITY_ID_PREFIX}:{digest}"


def _stat_phrase(cc: "table_select.ComputedComparison") -> str:
    if cc.measure == "rate" and cc.level:
        return f"{cc.level!r} rate of {cc.outcome}"
    return f"{cc.measure} of {cc.outcome}"


def _format_value(cc: "table_select.ComputedComparison", value: float) -> str:
    if cc.measure == "rate":
        return f"{value:.0%}"
    return f"{value:,.3g}"


def _label(cc: "table_select.ComputedComparison", group_key: str, value: float, n: int) -> str:
    """The theme label — becomes `Claim.subject` for every row in the
    group via `kg_themes.assign_themes`."""
    return (f"{cc.dimension} = {group_key}: {_stat_phrase(cc)} = "
            f"{_format_value(cc, value)} (n={n})")


def _content(
    account: str, cc: "table_select.ComputedComparison", group_key: str,
    value: float, n: int,
) -> str:
    as_of_txt = cc.as_of.date().isoformat() if cc.as_of else "an unspecified date"
    why = f" {cc.why}" if cc.why else ""
    return (
        f"{account} is one of {n} accounts in the attached {cc.table!r} "
        f"table where {cc.dimension} = {group_key!r}; for that group, "
        f"{_stat_phrase(cc)} is {_format_value(cc, value)} as of {as_of_txt}."
        f"{why}"
    )


def rows_for_computed(
    computed: Sequence["table_select.ComputedComparison"], *, now: datetime,
) -> tuple[tuple[dict, ...], dict[str, tuple[str, str, Optional[str]]]]:
    """Every un-suppressed, sizeable group across COMPUTED -> one row per
    account, plus the theme-map entries that keep each group's rows
    clustered together and off the ungroupable path.

    Skips a whole comparison when `account_column is None` — it cannot be
    sized by account at all, and guessing which column names the customer is
    exactly the failure `table_select._account_column` already refused to
    commit. Skips a single group when it is suppressed (below `MIN_GROUP_N`,
    or the whole comparison over the group-count ceiling) or names no
    accounts — nothing to build a row from.
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
        signature = _group_signature(cc)
        for g in cc.groups:
            if g.suppressed or g.value is None or not g.accounts:
                continue
            entity_id = _entity_id(signature, g.group)
            label = _label(cc, g.group, g.value, g.n)
            content = None  # built per-account below
            for account in g.accounts:
                account_digest = hashlib.sha256(
                    account.encode("utf-8")).hexdigest()[:16]
                row_id = f"{entity_id}:{account_digest}"
                # A DISTINCT ARTIFACT PER ROW — module docstring §2. Keyed on
                # the TABLE and the ACCOUNT, not on the comparison or group,
                # so the SAME account named by two different comparisons
                # over the same table is (correctly) treated as the same
                # underlying record — while every account within one group
                # still reads as a separate source document to `_refute`.
                artifact_id = f"{ENTITY_ID_PREFIX}:{cc.table}:{account}"
                content = _content(account, cc, g.group, g.value, g.n)
                rows.append({
                    "id": row_id,
                    "kind": COMPUTED_COMPARISON_KIND,
                    "source_type": COMPUTED_SOURCE_TYPE,
                    "content": content,
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

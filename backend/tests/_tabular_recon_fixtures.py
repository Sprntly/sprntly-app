"""Tabular fixtures for the reconnaissance pass, built from a real data pack.

WHERE THESE NUMBERS COME FROM. Every figure below is lifted from a real
multi-source B2B test pack (contracts, support tickets, third-party review
feed, activation funnel) rather than invented, because the checks in
`app.crucible.recon` are about SHAPE and an invented fixture is shaped like
whatever the person writing the check expected to find. The reconciliation
gap, the ticket-volume-against-revenue divergence, the share of review items
carrying no star rating and the duplicated funnel stage are all real
properties of that pack, at the real magnitudes.

WHAT IS NOT REAL: the row labels. Account names and review prose are replaced
with neutral stand-ins, because they are opaque keys as far as every check is
concerned — nothing groups on the CONTENT of an account name — and a third
party's customer list has no business in this repository. The column names are
kept, because two of the checks reason about column naming, and the ordering
is kept, because the funnel check reasons about adjacency.

TABLES ARE NAMED THE WAY THE REAL LOADER NAMES THEM — `08_sales_data:
contracts`, a workbook stem and a sheet joined by a colon. That is not
decoration: two of the assertions here are about storage keys never reaching a
reader, and fixtures named `contracts` cannot fail them, so they would pass
against code that renders keys verbatim. Five tables across four sources also
makes the plan's opening sentence a countable thing to assert on.

EACH FIXTURE HAS A NEGATIVE TWIN. `*_clean` is the same table with the
structural property removed and nothing else changed. A check that fires on
both is not detecting the property, it is detecting the table.
"""
from __future__ import annotations

from app.crucible.recon import Table, make_table

# ── Contracts: two value columns, and the third that explains the gap. ──────
#
# `base_acv_usd + expansion_acv_usd == total_acv_usd` on every row; the two
# value columns differ on 2 of 8 accounts and the gap is 273,378 (11.9% of the
# book). Two of eight is the load-bearing part: columns that differ on EVERY
# row are two different quantities, not two readings of one.
CONTRACT_COLUMNS = ("account", "base_acv_usd", "expansion_acv_usd", "total_acv_usd")
CONTRACT_ROWS: tuple[tuple[str, int, int, int], ...] = (
    ("Account A", 22500, 0, 22500),
    ("Account B", 372810, 174034, 546844),
    ("Account C", 210000, 0, 210000),
    ("Account D", 414846, 0, 414846),
    ("Account E", 210000, 99344, 309344),
    ("Account F", 301893, 0, 301893),
    ("Account G", 265159, 0, 265159),
    ("Account H", 235911, 0, 235911),
)

#: How many support tickets each of those accounts filed. The top three by
#: ticket volume hold 87.9% of the tickets and 33.8% of the revenue — 2.6
#: times their share of the money, which is the divergence a run that ranks by
#: how often something is mentioned walks straight into.
TICKETS_PER_ACCOUNT: dict[str, int] = {
    "Account A": 47,
    "Account B": 46,
    "Account C": 38,
    "Account D": 1,
    "Account E": 7,
    "Account F": 6,
    "Account G": 1,
    "Account H": 3,
}

#: An activation funnel whose second and third columns are the same
#: measurement recorded twice. `accounts` -> `reached_scenario_setup` is a real
#: drop; `reached_scenario_setup` -> `ran_first_exercise` is not a step at all.
FUNNEL_COLUMNS = (
    "cohort", "accounts", "reached_scenario_setup", "ran_first_exercise",
    "ran_first_exercise_2plus_facilitators",
)
FUNNEL_ROWS: tuple[tuple[str, int, int, int, int], ...] = (
    ("All accounts", 60, 53, 53, 40),
    ("Started from template", 33, 33, 33, 27),
    ("Authored from scratch", 27, 20, 20, 13),
)

#: A third-party review feed where the star rating is a field only some
#: sources carry, while every item has prose. Four of fourteen rows have no
#: rating and all four have text — the shape of a coded field the coder gave
#: up on, as opposed to a row that has simply not happened yet.
FEEDBACK_COLUMNS = ("source", "rating_out_of_5", "text")
_TEXT = (
    "Ran four exercises last quarter against two per year before, and the "
    "integration is what made it stick.",
    "Scenarios are well written but turning the output into something a "
    "board will read is still a manual job.",
    "The bots would not stop and participants tuned out, so we went back to "
    "a facilitator script instead.",
    "Compliance preparation went from painful to routine over about two "
    "quarters of running this regularly.",
    "Building a scenario from scratch took most of a week; templates would "
    "fix the worst of that problem.",
)
FEEDBACK_ROWS: tuple[tuple[str, "int | None", str], ...] = (
    ("Review site", 5, _TEXT[0]),
    ("Review site", 4, _TEXT[1]),
    ("Review site", 2, _TEXT[2]),
    ("Review site", 5, _TEXT[3]),
    ("Review site", 3, _TEXT[4]),
    ("Review site", 4, _TEXT[0]),
    ("Review site", 5, _TEXT[1]),
    ("Review site", 2, _TEXT[2]),
    ("Review site", 3, _TEXT[3]),
    ("Review site", 4, _TEXT[4]),
    ("Forum", None, _TEXT[0]),
    ("Forum", None, _TEXT[1]),
    ("Forum", None, _TEXT[2]),
    ("Forum", None, _TEXT[3]),
)


def contracts(*, clean: bool = False) -> Table:
    """`clean=True` zeroes the expansion column, so the two value columns
    agree everywhere and there is nothing to reconcile."""
    rows = [
        {
            "account": a,
            "base_acv_usd": total if clean else base,
            "expansion_acv_usd": 0 if clean else expansion,
            "total_acv_usd": total,
        }
        for a, base, expansion, total in CONTRACT_ROWS
    ]
    return make_table("08_sales_data:contracts", rows, columns=CONTRACT_COLUMNS,
                      source_type="revenue")


def tickets(*, aligned: bool = False) -> Table:
    """One row per ticket, expanded from the per-account counts.

    `aligned=True` gives every account the same ticket count, so activity and
    revenue can no longer diverge — the negative twin for the concentration
    check.
    """
    rows = []
    for account, n in TICKETS_PER_ACCOUNT.items():
        for i in range(10 if aligned else n):
            rows.append({"ticket_id": f"{account}-{i}", "account": account,
                         "category": "Reporting"})
    return make_table("02_support_tickets:tickets", rows,
                      columns=("ticket_id", "account", "category"),
                      source_type="customer_voice")


def funnel(*, clean: bool = False) -> Table:
    """`clean=True` moves one cohort's `ran_first_exercise` down by one, so the
    two columns are no longer the same measurement."""
    rows = []
    for cohort, accounts, reached, ran, two_plus in FUNNEL_ROWS:
        rows.append({
            "cohort": cohort, "accounts": accounts,
            "reached_scenario_setup": reached,
            "ran_first_exercise": ran - 1 if clean and cohort == "All accounts" else ran,
            "ran_first_exercise_2plus_facilitators": two_plus,
        })
    return make_table("03_product_analytics:activation_funnel", rows, columns=FUNNEL_COLUMNS,
                      source_type="analytics")


def feedback(*, clean: bool = False) -> Table:
    """`clean=True` blanks the free text on exactly the rows with no rating.

    THE NEGATIVE TWIN THAT MATTERS MOST. A coded field and its prose going
    missing TOGETHER is a row that has not happened yet — an open deal has no
    loss reason because it has not lost one — and a check that fires on it
    would send every run chasing a hole that is not there.
    """
    rows = []
    for source, rating, text in FEEDBACK_ROWS:
        rows.append({
            "source": source,
            "rating_out_of_5": rating,
            "text": "" if (clean and rating is None) else text,
        })
    return make_table("11_third_party_feedback:feedback_items", rows, columns=FEEDBACK_COLUMNS,
                      source_type="customer_voice")



#: A cohort-by-period retention grid, verbatim from the real pack.
#:
#: THE TRAP, AND IT IS THE BIGGEST ONE HERE. Eight of these sixteen cohorts
#: have zeros in their later months because those months have not happened
#: yet — every one of them stops exactly at the end of the data. Summing
#: `month_12` over every cohort gives 28/60 = 46.7%; over the eight cohorts
#: with a full twelve-month window it is 28/30 = 93.3%. A 46.6 point error, in
#: the direction that invents a retention crisis and hides the real number.
#:
#: It is also where `stage_collapse` used to fire twice and be wrong twice:
#: `month_1` equals `cohort_size` because retention starts at 100%, and
#: `month_1` equals `month_2` because nobody left in the first month. Neither
#: is a collapsed funnel stage; a retention grid is not a funnel.
RETENTION_PERIODS = 12
RETENTION_ROWS: tuple[tuple[str, int, tuple[int, ...]], ...] = (
    ("2024-07", 4, (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4)),
    ("2024-09", 4, (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4)),
    ("2024-11", 4, (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4)),
    ("2025-01", 3, (3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3)),
    ("2025-03", 2, (2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2)),
    ("2025-05", 2, (2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2)),
    ("2025-07", 7, (7, 7, 7, 7, 7, 7, 7, 7, 7, 6, 5, 5)),
    ("2025-09", 4, (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4)),
    ("2025-10", 2, (2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 0)),
    ("2025-11", 3, (3, 3, 3, 3, 3, 3, 3, 3, 3, 2, 0, 0)),
    ("2025-12", 6, (6, 6, 6, 6, 5, 5, 4, 4, 4, 0, 0, 0)),
    ("2026-01", 4, (4, 4, 4, 4, 4, 4, 4, 4, 0, 0, 0, 0)),
    ("2026-02", 7, (7, 7, 7, 7, 7, 7, 7, 0, 0, 0, 0, 0)),
    ("2026-03", 3, (3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0, 0)),
    ("2026-05", 3, (3, 3, 3, 3, 0, 0, 0, 0, 0, 0, 0, 0)),
    ("2026-06", 2, (2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
)
RETENTION_COLUMNS = ("cohort_month", "cohort_size") + tuple(
    f"month_{i}" for i in range(1, RETENTION_PERIODS + 1))


def retention(*, mature_only: bool = False, unaligned: bool = False) -> Table:
    """The grid, and two twins that each remove one property.

    `mature_only` keeps the eight cohorts with a full window, so the grid is
    no longer ragged: it is then a fully populated ordinal series — a stage
    sequence as far as any shape test can tell — and `stage_collapse` should
    speak up again while the censoring check goes quiet. That pair is what
    proves the discriminator is RAGGEDNESS rather than the column names.

    `unaligned` keeps the raggedness but walks one cohort's window off the
    shared frontier, so the trailing zeros no longer all land on the same
    date. That is a cohort that actually emptied out, and calling it censoring
    would be the check inventing the opposite error to the one it fixes.
    """
    rows = []
    for cohort, size, periods in RETENTION_ROWS:
        observed = sum(1 for p in periods if p > 0)
        if mature_only and observed < RETENTION_PERIODS:
            continue
        vals = list(periods)
        if unaligned and cohort == "2025-12":
            vals = [v if i < 3 else 0 for i, v in enumerate(vals)]
        row = {"cohort_month": cohort, "cohort_size": size}
        row.update({f"month_{i}": v for i, v in enumerate(vals, start=1)})
        rows.append(row)
    return make_table("03_product_analytics:cohort_retention", rows, columns=RETENTION_COLUMNS,
                      source_type="analytics")


#: Signals carrying the triage category the ingest pass already writes, in the
#: proportions measured on a real 1,416-signal tenant: 90.0% classified, the
#: largest kind product requirements at ~41% of the classified, and only ~4.5%
#: a customer speaking firsthand.
MEASURED_TRIAGE_MIX: dict[str, int] = {
    "product_prd": 518,
    "decision_record": 264,
    "business_context": 206,
    "meeting_notes": 194,
    "sales_deal": 57,
    "marketing_content": 29,
    "engineering_activity": 7,
}
MEASURED_UNCLASSIFIED = 141


def signals(*, fail_open: int = 0) -> list[dict]:
    """Signal rows shaped as `kg_signal` reads them, at measured proportions.

    `fail_open` adds rows carrying the `uncategorized` sentinel — not a member
    of the declared taxonomy, stamped when the triage call itself errors. A
    reader has to tolerate it and must not count it as a category.
    """
    out: list[dict] = []
    for code, n in MEASURED_TRIAGE_MIX.items():
        out.extend({"provenance": {"triage_category": code}} for _ in range(n))
    out.extend({"provenance": {}} for _ in range(MEASURED_UNCLASSIFIED))
    out.extend({"provenance": {"triage_category": "uncategorized"}}
               for _ in range(fail_open))
    return out


def full_pack() -> list[Table]:
    """Everything, as a run would see it."""
    return [contracts(), tickets(), funnel(), feedback(), retention()]


# ── Knowledge-graph signals: the shape a prose tenant actually has ─────────
#
# Proportions taken from two real local tenants, because the whole point of
# these checks is that a prose corpus has facts worth reporting and nothing was
# reading them. The larger measured 100% ingest-clock dating, 0.3% account
# attribution, 0% monetary coverage, 11 documents behind 1,275 signals and 58%
# of one claim kind. The smaller measured 0% ingest-clock, which is why it is
# the negative twin rather than a second positive.

def kg_signals(
    *,
    n: int = 200,
    ingest_clock: bool = True,
    attributed: bool = False,
    monetary: bool = False,
    documents: int = 4,
    one_kind: bool = True,
) -> list[dict]:
    """Signal rows shaped as `kg_signal` reads them.

    Every flag turns exactly one property on or off, so a check that fires on
    both settings of its own flag is detecting the fixture rather than the
    property.
    """
    out: list[dict] = []
    for i in range(n):
        # `valid_at` is either the moment of import (the backfill shape) or a
        # real spread of event dates months earlier.
        created = "2026-08-19T12:00:00+00:00"
        valid = created if ingest_clock else f"2026-0{1 + i % 6}-1{i % 9}T09:00:00+00:00"
        props: dict = {"summary": f"signal {i}"}
        if attributed:
            props["account"] = f"Account {i % 12}"
        if monetary:
            props["amount"] = 1000 * (i % 7 + 1)
        out.append({
            "id": f"sig-{i:04d}",
            "kind": "finding" if one_kind or i % 3 == 0 else
                    ["bug", "feature_request", "deal_blocker",
                     "sentiment", "incident"][i % 5],
            "source_type": "pm_manual",
            "properties": props,
            "provenance": {"doc": f"document-{i % max(1, documents)}"},
            "valid_at": valid,
            "created_at": created,
        })
    return out


def kg_tables(signals: list[dict]) -> list[Table]:
    """The tables a prose corpus produces — sparse unions of stray keys."""
    from app.crucible.recon import tables_from_signals

    return tables_from_signals(signals)


def sparse_table(*, rows: int = 60, columns: int = 40) -> Table:
    """A union of keys across heterogeneous signals: wide, and almost empty.

    The shape that cost a measured prose tenant ~25ms of profiling per pass and
    yielded nothing. Two cells per row, so density lands near 0.05 — where the
    real ones measured.
    """
    records = []
    for i in range(rows):
        records.append({
            f"key_{(i * 2) % columns}": i,
            f"key_{(i * 2 + 1) % columns}": f"value {i}",
        })
    return make_table(
        "pm_manual:finding", records,
        columns=tuple(f"key_{i}" for i in range(columns)),
        source_type="pm_manual",
    )

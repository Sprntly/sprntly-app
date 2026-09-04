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
    return make_table("contracts", rows, columns=CONTRACT_COLUMNS,
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
    return make_table("tickets", rows,
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
    return make_table("activation_funnel", rows, columns=FUNNEL_COLUMNS,
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
    return make_table("feedback_items", rows, columns=FEEDBACK_COLUMNS,
                      source_type="customer_voice")


def full_pack() -> list[Table]:
    """Everything, as a run would see it."""
    return [contracts(), tickets(), funnel(), feedback()]

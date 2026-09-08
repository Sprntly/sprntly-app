"""`app.crucible.tabular_findings` — the producer that turns
`table_select.ComputedComparison` into claim rows a reader actually sees.

TWO KINDS OF EVIDENCE HERE, DELIBERATELY.

Most of this file is unit tests over `produce`/`rows_for_computed`, in the
same injectable-`call` style `test_crucible_table_select.py` already uses —
no `_offline()` gate, a real parse/validate/compute path every time.

The tests under "REAL PIPELINE" go one step further: they hand the rows this
module builds to the REAL `claims.project_signal` and the REAL
`pipeline.build_findings` — not a fixture standing in for what those stages
would do. Two earlier attempts at moving this exact ranking each looked
correct against their own tests and did nothing at scale; both were only
caught by running real code. This is the strongest proof available without a
live tenant (see the PR's own report for what a live tenant would still need
to confirm) — it is not a substitute for one, and is not represented as one.

No network, no DB, no LLM.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.crucible import tabular_findings as tf
from app.crucible.claims import project_signal
from app.crucible.cluster import UNGROUPABLE_PREFIX
from app.crucible.pipeline import build_findings
from app.crucible.recon import make_table
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


# ── fixtures ──────────────────────────────────────────────────────────────


def _churn_table():
    """12 accounts, real names, an `account` column `table_select` and
    `claims._population` both recognise. `facilitator_type`: 6 `solo`
    (4 churned), 6 `team` (1 churned) — both groups clear `MIN_GROUP_N`
    (5) on their own, so both produce rows."""
    rows = []
    for i in range(1, 7):
        rows.append({
            "account": f"Solo Co {i}", "facilitator_type": "solo",
            "status": "Churned" if i <= 4 else "Active",
        })
    for i in range(1, 7):
        rows.append({
            "account": f"Team Co {i}", "facilitator_type": "team",
            "status": "Churned" if i == 1 else "Active",
        })
    return make_table(
        "workbook:accounts.xlsx:Sheet1", rows,
        columns=["account", "facilitator_type", "status"],
    )


def _select_facilitator_churn(**kw):
    """A well-formed selector response naming exactly one comparison — the
    shape `table_select._validate_comparison` accepts."""
    return {
        "comparisons": [{
            "table": kw["lead_table"], "dimension": "facilitator_type",
            "outcome": "status", "measure": "rate", "level": "Churned",
            "why": "solo facilitators may churn at a different rate",
        }],
        "declined": [],
    }


def _empty_selection(**kw):
    return {"comparisons": [], "declined": [
        {"table": kw["lead_table"], "dimension": "facilitator_type",
         "why": "not asked about"},
    ]}


# ── AC1 / totality: nothing attached does nothing ───────────────────────────


def test_no_tables_produces_nothing_and_calls_no_model():
    calls = []

    def call(**kw):
        calls.append(kw)
        return {}

    ev = tf.produce(tables=[], enterprise_id="e1", goal_text="reduce churn",
                    now=NOW, call=call)
    assert ev == tf.TabularEvidence()
    assert calls == []


def test_a_table_with_no_groupable_rows_produces_nothing():
    empty = make_table("workbook:empty.xlsx:Sheet1", [], columns=["account"])
    ev = tf.produce(tables=[empty], enterprise_id="e1", goal_text="x", now=NOW)
    assert ev.rows == ()


# ── AC2 / property 1: one row per account ───────────────────────────────────


def test_one_row_per_account_in_each_group():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    accounts = sorted(r["properties"]["account"] for r in ev.rows)
    expected = sorted(f"Solo Co {i}" for i in range(1, 7)) + \
        sorted(f"Team Co {i}" for i in range(1, 7))
    assert accounts == expected
    assert len(ev.rows) == 12   # one row per account, never one per group


def test_a_row_names_exactly_one_account():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    for row in ev.rows:
        assert set(row["properties"]) == {"account"}
        assert isinstance(row["properties"]["account"], str)


def test_no_account_column_produces_no_rows_and_does_not_guess():
    """`table_select._account_column` returning `None` (nothing on the table
    looks like a customer name) must cost the whole comparison, not a
    guessed column."""
    rows = [{"team_size": "solo" if i <= 6 else "team",
             "status": "Churned" if i % 3 == 0 else "Active"}
            for i in range(1, 13)]
    table = make_table("workbook:no_account.xlsx:Sheet1", rows,
                       columns=["team_size", "status"])

    def call(**kw):
        return {"comparisons": [{
            "table": kw["lead_table"], "dimension": "team_size",
            "outcome": "status", "measure": "rate", "level": "Churned",
            "why": "no account column present",
        }], "declined": []}

    ev = tf.produce(tables=[table], enterprise_id="e1", goal_text="x",
                    now=NOW, call=call)
    assert ev.rows == ()
    assert len(ev.computed) == 1
    assert ev.computed[0].account_column is None


def test_an_empty_selection_produces_no_rows_without_raising():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_empty_selection)
    assert ev.rows == ()
    assert ev.selection is not None
    assert ev.selection.comparisons == ()


# ── AC2 / property 4: a real valid_at, from `evidence.derive_as_of` ────────


def test_every_row_carries_a_real_valid_at():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    assert ev.rows
    for row in ev.rows:
        assert row["valid_at"]
        # Parses, and is not simply `now` fabricated per row — every row
        # from ONE table shares the table's own derived `as_of`.
        assert row["valid_at"] == ev.rows[0]["valid_at"]


# ── AC2 / property 2: a distinct artifact id per row, never one per table ──


def test_every_row_in_a_group_carries_a_distinct_artifact_id():
    """The echo-avoidance property: `pipeline._refute`'s `one_conversation`
    check is `len({claim.artifact_id}) == 1` — a shared per-table artifact id
    would kill every group this module ever produces (see the module
    docstring §2)."""
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    docs = [r["provenance"]["doc"] for r in ev.rows]
    assert len(docs) == len(set(docs)) == 12


# ── AC2 / property 3: subject_cluster_id via the theme map, never ungroupable


def test_theme_map_clusters_one_group_and_separates_the_other():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    solo_ids = {r["id"] for r in ev.rows
                if r["properties"]["account"].startswith("Solo")}
    team_ids = {r["id"] for r in ev.rows
                if r["properties"]["account"].startswith("Team")}
    solo_entities = {ev.theme_map[i][0] for i in solo_ids}
    team_entities = {ev.theme_map[i][0] for i in team_ids}
    assert len(solo_entities) == 1          # one group, one entity id
    assert len(team_entities) == 1
    assert solo_entities != team_entities   # two groups never merge
    for entity_id, label, relation in ev.theme_map.values():
        assert not entity_id.startswith(UNGROUPABLE_PREFIX)
        assert entity_id.startswith(tf.ENTITY_ID_PREFIX)
        assert label
        assert relation is None


# ── AC6 / totality ───────────────────────────────────────────────────────


def test_a_malformed_workbook_costs_its_own_rows_never_raises():
    class NotATable:
        """Something that will blow up the moment `evidence.with_as_of`
        tries to read `.columns`/`.rows` off it — a stand-in for a workbook
        that made it past `read_uploads` malformed in some way this module
        never anticipated."""
        name = "broken"

    ev = tf.produce(tables=[NotATable()], enterprise_id="e1",
                    goal_text="x", now=NOW)
    assert ev == tf.TabularEvidence()


def test_a_raising_model_call_costs_the_selection_never_the_run():
    def call(**kw):
        raise RuntimeError("gateway exploded")

    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW, call=call)
    assert ev.rows == ()
    # `select_comparisons` itself catches this and returns an empty,
    # non-None selection — the run recorded a real, empty draw, not nothing.
    assert ev.selection is not None
    assert ev.selection.comparisons == ()


def test_a_failed_selection_that_rejects_everything_produces_nothing():
    def call(**kw):
        return {"comparisons": [
            {"table": "not-a-real-table", "dimension": "x", "outcome": "y",
             "measure": "rate", "level": "z", "why": "bad table name"},
        ], "declined": []}

    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW, call=call)
    assert ev.rows == ()
    assert len(ev.selection.rejected) == 1


# ── AC7: the claim type is never named in a prompt or a schema ─────────────
#
# THE SAFETY ARGUMENT FOR THE BUCKET, restated as code. `moscow.type_bucket`
# ranks `computed_differential` above `constraint` ONLY because no model can
# ever emit it — see `tabular_findings`'s module docstring §1. If this test
# ever fails, that argument no longer holds and the bucket placement must be
# revisited before anything else.

_CLAIM_TYPE_NAME = "computed_differential"
_KIND_NAME = "computed_comparison"

#: The only files allowed to know either string exists — the deterministic
#: assignment tables and this producer. Anything else that names either
#: string is, by construction, either a prompt, a schema description, or a
#: second, undeclared producer — all three are the exact failure this test
#: exists to catch.
_ALLOWED = {
    "app/crucible/types.py",
    "app/crucible/claims.py",
    "app/crucible/moscow.py",
    "app/crucible/tabular_findings.py",
}


def test_the_claim_type_is_never_named_outside_the_deterministic_files():
    import pathlib

    app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in app_root.rglob("*.py"):
        rel = str(path.relative_to(app_root.parent))
        if rel in _ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _CLAIM_TYPE_NAME in text or _KIND_NAME in text:
            hits.append(rel)
    assert hits == [], (
        f"{_CLAIM_TYPE_NAME!r}/{_KIND_NAME!r} must only appear in the "
        f"deterministic assignment tables and the producer — found in: {hits}"
    )


# ── REAL PIPELINE: the rows this module builds, through the real stages ────


def _themed_claims(rows, theme_map) -> list[Claim]:
    """Exactly what `kg_themes.assign_themes` does to a themed claim — see
    that function. Reused here rather than imported so this stays a fixture
    over the CONTRACT (a `(entity_id, label, relation)` tuple keyed on claim
    id) rather than a second copy of the function under test elsewhere."""
    out = []
    for row in rows:
        c = project_signal(row, sides={})
        assert c is not None, row
        entity_id, label, relation = theme_map[row["id"]]
        out.append(replace(c, subject_cluster_id=f"kg:{entity_id}",
                           subject=label, graph_relation=relation))
    return out


def _constraint_claims(n: int, *, subject="stakeholder alignment") -> list[Claim]:
    """N single-account `constraint` claims, one document each, spread over
    months — survives refutation on its own merits, exactly like a real
    corroborated blocker. Reach is `n` distinct accounts."""
    out = []
    for i in range(n):
        out.append(Claim(
            id=f"blocker-{i}", assertion=f"Acct {i} has no budget approved",
            type="constraint", subject=subject, source_id="customer_voice",
            artifact_id=f"call-{i}", artifact_type="call", strength="reported",
            observed_at=NOW.replace(day=1) if NOW.day <= 28 else NOW,
            authoritative=True,
            population=PopulationFilter(
                segments={"accounts": (f"Acct {i}",),
                          "customer_side": (f"Acct {i}",)},
                estimated_size=1,
            ),
        ))
    return out


def test_a_computed_comparison_group_is_not_refuted():
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    claims = _themed_claims(ev.rows, ev.theme_map)
    out = build_findings(claims, currency="accounts", now=NOW)
    assert out.rejected == (), out.rejected
    assert out.stats["dropped"]["echo"] == 0
    assert out.stats["dropped"]["no_authority"] == 0
    assert out.stats["dropped"]["ungroupable"] == 0
    assert out.stats["dropped"]["anecdote"] == 0
    # Both groups survive as their own findings.
    assert len(out.findings) == 2


def test_a_computed_differential_outranks_a_bigger_stated_blocker():
    """The mechanism AC4 depends on, proven against the real ranker with a
    SYNTHETIC corpus — not a claim that this was verified on a real tenant.
    A 30-account `constraint` finding (bigger reach, wrong bucket) must not
    outrank a 6-account computed-differential finding (smaller reach, top
    bucket)."""
    ev = tf.produce(tables=[_churn_table()], enterprise_id="e1",
                    goal_text="reduce churn", now=NOW,
                    call=_select_facilitator_churn)
    computed = _themed_claims(ev.rows, ev.theme_map)
    blockers = _constraint_claims(30)
    assert len(blockers) > 12   # the blocker's reach genuinely is bigger

    out = build_findings(computed + blockers, currency="accounts", now=NOW)
    assert out.rejected == ()
    assert len(out.findings) == 3   # 2 computed groups + 1 blocker theme
    assert out.findings[0].confidence_inputs.claim_types[0] == "computed_differential"
    top_reach = out.impacts[0].affected_population
    blocker_reach = next(
        i.affected_population for f, i in zip(out.findings, out.impacts)
        if f.confidence_inputs.claim_types[0] == "constraint")
    assert blocker_reach > top_reach   # bucket beat size, not the reverse

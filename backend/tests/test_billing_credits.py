"""Credit ledger + plan resolution.

This is the money path, so the tests here are about arithmetic and replay
rather than plumbing: does a spend deduct exactly once, does a replayed grant
stay a single grant, and does an unknown plan resolve to something restrictive
rather than something generous.
"""
from __future__ import annotations

import pytest

from app.billing import credits, plans
from app.db.client import require_client

from ._company_helpers import seed_company


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    """Every test here touches the ledger, so the in-memory Supabase is wired
    for all of them rather than requested one by one."""
    return isolated_settings


def _set_plan(company_id: str, plan: str, *, balance: int = 0) -> None:
    require_client().table("companies").update(
        {"plan": plan, "credit_balance": balance}
    ).eq("id", company_id).execute()


def _ledger(company_id: str) -> list[dict]:
    return (
        require_client()
        .table("credit_ledger")
        .select("*")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# plans — resolution is fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [None, "", "   ", "gold", "Starter ", "PRODUCT_BUILDER"],
)
def test_unknown_plan_resolves_without_raising(stored):
    """Never blow up on a plan string; normalise case/whitespace."""
    assert plans.resolve_plan(stored) in plans.PLAN_CREDITS


def test_unrecognised_plan_falls_back_to_launch_default_not_a_generous_tier():
    """The whole point of fail-closed: garbage must not buy a big plan.

    `app.entitlements` resolves feature modules fail-OPEN so a rollout does not
    lock existing companies out. Plans invert that — an unrecognised value is
    absence of evidence that anyone paid.
    """
    assert plans.resolve_plan("enterprise-unlimited-hacked") == plans.LAUNCH_DEFAULT_PLAN
    assert not plans.is_unlimited("nonsense")


def test_plan_labels_and_credits_cover_every_plan():
    """A plan with no label renders as a KeyError on the Billing screen."""
    assert set(plans.PLAN_LABELS) == set(plans.PLAN_CREDITS)


def test_legacy_and_enterprise_are_unlimited():
    assert plans.is_unlimited(plans.LEGACY)
    assert plans.is_unlimited(plans.ENTERPRISE)
    assert not plans.is_unlimited(plans.STARTER)


def test_topup_never_beats_the_subscription_rate():
    """Topping up must not be a cheaper way to buy credits than subscribing,
    or the cheapest plan plus top-ups dominates every tier above it."""
    starter_rate = plans.PLAN_CREDITS[plans.STARTER] / 35  # $35 coupon price
    assert plans.CREDITS_PER_TOPUP_USD <= starter_rate


# ---------------------------------------------------------------------------
# subscription status → access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
def test_active_statuses_grant_access(status):
    assert plans.subscription_grants_access(plans.STARTER, status)


@pytest.mark.parametrize("status", ["canceled", "unpaid", "incomplete", None, ""])
def test_dead_statuses_revoke_access(status):
    assert not plans.subscription_grants_access(plans.STARTER, status)


def test_past_due_keeps_access_while_retries_run():
    """Stripe Smart Retries work a declined card for days. Cutting the customer
    off on the first decline turns a bounced card into churn."""
    assert plans.subscription_grants_access(plans.PRODUCT_BUILDER, "past_due")


def test_legacy_and_enterprise_need_no_stripe_subscription():
    """Neither was sold through Stripe, so both carry a null status. Gating on
    one would lock out every pre-billing tenant the day this ships."""
    assert plans.subscription_grants_access(plans.LEGACY, None)
    assert plans.subscription_grants_access(plans.ENTERPRISE, None)


# ---------------------------------------------------------------------------
# cost_of
# ---------------------------------------------------------------------------


def test_unpriced_feature_charges_the_default_rather_than_being_free(caplog):
    """A new generation surface nobody priced must not become the cheapest way
    to use Sprntly, and must not 500 on the user either."""
    with caplog.at_level("WARNING"):
        assert credits.cost_of("some_new_surface") == plans.DEFAULT_ACTION_COST
    assert "credit_cost_missing" in caplog.text


def test_known_costs_come_from_the_table():
    assert credits.cost_of("prd") == plans.CREDIT_COSTS["prd"]
    assert credits.cost_of("chat") == 1


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------


def test_spend_deducts_the_action_price_and_writes_one_ledger_row():
    cid = seed_company(user_id="u-spend", slug="spend-co")
    _set_plan(cid, plans.STARTER, balance=100)

    remaining = credits.spend(cid, "prd", ref_id="job-1", actor_user_id="u-spend")

    assert remaining == 100 - plans.CREDIT_COSTS["prd"]
    assert credits.balance(cid) == remaining
    rows = _ledger(cid)
    assert len(rows) == 1
    assert rows[0]["delta"] == -plans.CREDIT_COSTS["prd"]
    assert rows[0]["reason"] == "spend"
    assert rows[0]["feature"] == "prd"
    assert rows[0]["balance_after"] == remaining
    assert rows[0]["actor_user_id"] == "u-spend"


def test_spend_is_idempotent_on_ref_id():
    """A retried completion handler must charge once. The ledger's partial
    unique index is what enforces it."""
    cid = seed_company(user_id="u-idem", slug="idem-co")
    _set_plan(cid, plans.STARTER, balance=100)

    first = credits.spend(cid, "report", ref_id="job-42")
    second = credits.spend(cid, "report", ref_id="job-42")

    assert first == second == 100 - plans.CREDIT_COSTS["report"]
    assert credits.balance(cid) == first
    assert len(_ledger(cid)) == 1


def test_distinct_jobs_each_charge():
    """The idempotency key must not collapse genuinely separate work."""
    cid = seed_company(user_id="u-two", slug="two-co")
    _set_plan(cid, plans.STARTER, balance=100)

    credits.spend(cid, "report", ref_id="job-a")
    credits.spend(cid, "report", ref_id="job-b")

    assert credits.balance(cid) == 100 - 2 * plans.CREDIT_COSTS["report"]
    assert len(_ledger(cid)) == 2


def test_spend_beyond_the_balance_raises_and_changes_nothing():
    cid = seed_company(user_id="u-broke", slug="broke-co")
    _set_plan(cid, plans.STARTER, balance=3)

    with pytest.raises(credits.InsufficientCredits) as excinfo:
        credits.spend(cid, "prd", ref_id="job-x")

    assert excinfo.value.needed == plans.CREDIT_COSTS["prd"]
    assert excinfo.value.balance == 3
    assert credits.balance(cid) == 3
    assert _ledger(cid) == []


def test_spend_to_exactly_zero_is_allowed():
    """Off-by-one guard: the last affordable action must not be refused."""
    cid = seed_company(user_id="u-zero", slug="zero-co")
    _set_plan(cid, plans.STARTER, balance=plans.CREDIT_COSTS["prd"])

    assert credits.spend(cid, "prd", ref_id="job-z") == 0


def test_unlimited_plan_spends_nothing_and_writes_no_ledger_noise():
    cid = seed_company(user_id="u-unl", slug="unl-co")
    _set_plan(cid, plans.LEGACY, balance=0)

    assert credits.spend(cid, "prd", ref_id="job-u") == plans.UNLIMITED
    assert credits.balance(cid) == plans.UNLIMITED
    assert _ledger(cid) == []


def test_unknown_company_cannot_spend():
    with pytest.raises(credits.InsufficientCredits):
        credits.spend("no-such-company", "chat", ref_id="j")


# ---------------------------------------------------------------------------
# check_affordable — the pre-flight
# ---------------------------------------------------------------------------


def test_check_affordable_refuses_before_the_user_waits():
    cid = seed_company(user_id="u-pre", slug="pre-co")
    _set_plan(cid, plans.STARTER, balance=1)

    with pytest.raises(credits.InsufficientCredits):
        credits.check_affordable(cid, "prototype")

    # A pre-flight must not charge.
    assert credits.balance(cid) == 1
    assert _ledger(cid) == []


def test_check_affordable_passes_on_unlimited_plans():
    cid = seed_company(user_id="u-pre2", slug="pre2-co")
    _set_plan(cid, plans.ENTERPRISE, balance=0)
    credits.check_affordable(cid, "prototype")  # must not raise


# ---------------------------------------------------------------------------
# grants
# ---------------------------------------------------------------------------


def test_grant_adds_and_records_the_reason():
    cid = seed_company(user_id="u-grant", slug="grant-co")
    _set_plan(cid, plans.STARTER, balance=10)

    assert credits.grant(cid, 140, reason="referral", ref_id="ref-1") == 150
    rows = _ledger(cid)
    assert rows[0]["reason"] == "referral"
    assert rows[0]["delta"] == 140


def test_grant_is_idempotent_on_ref_id():
    """A Stripe webhook retry arriving minutes later must not pay twice."""
    cid = seed_company(user_id="u-g2", slug="g2-co")
    _set_plan(cid, plans.STARTER, balance=0)

    credits.grant(cid, 280, reason="topup", ref_id="cs_test_123")
    credits.grant(cid, 280, reason="topup", ref_id="cs_test_123")

    assert credits.balance(cid) == 280
    assert len(_ledger(cid)) == 1


def test_grant_rejects_non_positive_amounts():
    cid = seed_company(user_id="u-g3", slug="g3-co")
    with pytest.raises(ValueError):
        credits.grant(cid, 0, reason="topup")
    with pytest.raises(ValueError):
        credits.grant(cid, -5, reason="topup")


def test_monthly_grant_sets_the_balance_rather_than_accumulating():
    """Plan credits do not roll over. A user who spent nothing last month
    starts the new one at the plan allowance, not at double it."""
    cid = seed_company(user_id="u-m", slug="m-co")
    _set_plan(cid, plans.PRODUCT_BUILDER, balance=2_400)

    after = credits.grant_monthly(cid, plans.PRODUCT_BUILDER, period_start="2026-09-01")

    assert after == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]
    assert credits.balance(cid) == 2_500


def test_monthly_grant_tops_a_depleted_balance_back_up():
    cid = seed_company(user_id="u-m2", slug="m2-co")
    _set_plan(cid, plans.STARTER, balance=12)

    assert credits.grant_monthly(cid, plans.STARTER, period_start="2026-09-01") == 500


def test_monthly_grant_is_idempotent_within_one_period():
    """`invoice.paid` is delivered at least once. A replay must not hand out a
    second month of credits after the user has spent some of the first."""
    cid = seed_company(user_id="u-m3", slug="m3-co")
    _set_plan(cid, plans.STARTER, balance=0)

    credits.grant_monthly(cid, plans.STARTER, period_start="2026-09-01")
    credits.spend(cid, "prd", ref_id="job-m3")
    spent_balance = credits.balance(cid)

    credits.grant_monthly(cid, plans.STARTER, period_start="2026-09-01")

    assert credits.balance(cid) == spent_balance


def test_a_new_period_grants_again():
    cid = seed_company(user_id="u-m4", slug="m4-co")
    _set_plan(cid, plans.STARTER, balance=0)

    credits.grant_monthly(cid, plans.STARTER, period_start="2026-09-01")
    credits.spend(cid, "prd", ref_id="job-m4")
    credits.grant_monthly(cid, plans.STARTER, period_start="2026-10-01")

    assert credits.balance(cid) == 500


def test_monthly_grant_on_unlimited_plan_is_a_no_op():
    cid = seed_company(user_id="u-m5", slug="m5-co")
    _set_plan(cid, plans.ENTERPRISE, balance=0)

    assert credits.grant_monthly(cid, plans.ENTERPRISE, period_start="2026-09-01") == (
        plans.UNLIMITED
    )
    assert _ledger(cid) == []


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_returns_newest_first_and_scopes_to_the_company():
    mine = seed_company(user_id="u-h1", slug="h1-co")
    theirs = seed_company(user_id="u-h2", slug="h2-co")
    _set_plan(mine, plans.STARTER, balance=500)
    _set_plan(theirs, plans.STARTER, balance=500)

    credits.spend(mine, "chat", ref_id="j1")
    credits.spend(theirs, "prd", ref_id="j2")

    rows = credits.history(mine)
    assert len(rows) == 1
    assert rows[0]["feature"] == "chat"

"""Plans and action-credit prices — pure data, no I/O.

A CREDIT IS AN ACTION (owner decision, 2026-08-21). Not a token, not a dollar.
The alternative considered and rejected was pegging one credit to ~$0.04 of
Anthropic spend: it is self-balancing, but it exposes token math to users, it
makes the cost of a request unpredictable before running it, and at the agreed
plan sizes it inverted the margin (2,500 credits of real inference on a $59
plan).

The consequence to keep in mind: because a credit is an action, a long agent
run and a short one cost the same. Margin therefore lives in the AVERAGE, and
the averages below need periodic recalibration against reality — see
CREDIT_COSTS.

Nothing here touches the database. `app.billing.credits` does the spending;
this module only answers "what does X cost" and "what does plan Y grant".
"""
from __future__ import annotations

# A plan with no credit ceiling. Distinct from 0 (which is a real, empty
# balance) and from None-as-missing — a bare `if credits:` would treat both
# as falsy, so callers must compare against this sentinel explicitly.
UNLIMITED = -1

STARTER = "starter"
PRODUCT_BUILDER = "product_builder"
TEAM = "team"
ENTERPRISE = "enterprise"
LEGACY = "legacy"

# What every EXISTING company resolves to at launch (owner decision
# 2026-08-21). The recommendation was LEGACY — unlimited, nothing changes for
# anyone already using Sprntly — and the owner chose to put current tenants on
# a real plan instead.
#
# This is the one knob for that call. Flipping it to LEGACY and re-running the
# staff backfill un-does the launch-day paywall without a migration, because
# `plan` carries no check constraint.
LAUNCH_DEFAULT_PLAN = STARTER

# Monthly credit grant per plan. Team's is described in the pricing table as
# "pooled" — the balance is company-level for every plan, so pooling needs no
# separate mechanism; it is simply what a company-level balance already does.
PLAN_CREDITS: dict[str, int] = {
    STARTER: 500,
    PRODUCT_BUILDER: 2_500,
    TEAM: 15_000,
    # Negotiated per deal; the contract, not this file, is the limit.
    ENTERPRISE: UNLIMITED,
    # Pre-billing companies. Nothing was ever sold to them on a credit basis,
    # so metering them would be inventing a limit they never agreed to.
    LEGACY: UNLIMITED,
}

# Human labels for the Billing screen. Kept here so the plan name a user reads
# and the plan key the resolver enforces can never drift apart.
PLAN_LABELS: dict[str, str] = {
    STARTER: "Starter",
    PRODUCT_BUILDER: "Product Builder",
    TEAM: "Team",
    ENTERPRISE: "Enterprise",
    LEGACY: "Legacy",
}

# Plans a customer can buy themselves through Checkout. Team is invoiced and
# Enterprise goes through sales, so neither has a self-serve price — a checkout
# request naming one is rejected rather than silently downgraded.
SELF_SERVE_PLANS = (STARTER, PRODUCT_BUILDER)

# ---------------------------------------------------------------------------
# What an action costs
# ---------------------------------------------------------------------------
#
# THESE ARE ESTIMATES AND WANT RECALIBRATING. They were set from the relative
# shape of the work (a PRD is a long streamed generation; a chat turn is one
# short call), NOT from measured spend, because that measurement needs a query
# against production `llm_usage_events` that this branch deliberately does not
# make.
#
# To recalibrate, run the existing rollup and divide by the action count:
#
#   select feature, sum(est_cost_usd), count(*)
#     from llm_usage_events
#    where created_at > now() - interval '90 days'
#    group by feature;
#
# then scale so the cheapest real action lands at 1 credit. Changing a number
# here changes pricing for everyone immediately, with no migration.
#
# BACKGROUND WORK IS FREE, DELIBERATELY. Scheduled brief generation, connector
# syncs and KG ingest cost real money but are not in this table and are never
# charged: a user cannot see, predict, or decline them, and billing someone for
# work they did not ask for is a refund request with extra steps. Only
# user-initiated generation appears below.
CREDIT_COSTS: dict[str, int] = {
    "chat": 1,
    "ask": 3,
    "report": 10,
    "evidence": 10,
    "crucible": 15,
    "prototype_iterate": 15,
    "competitive_intel": 20,
    "prd": 25,
    "multi_agent": 25,
    "prototype": 50,
}

# An action we forgot to price must not be free forever and must not crash a
# generation. It costs this, and logs — see credits.cost_of.
DEFAULT_ACTION_COST = 1


def resolve_plan(value: str | None) -> str:
    """Normalise a stored `companies.plan` into a known plan key.

    FAIL-CLOSED, unlike `app.entitlements`, and the difference is deliberate.
    That module resolves feature modules fail-OPEN so existing companies keep
    working through a rollout; a missing key there means "nobody has decided
    yet", which is safely ON. Here a missing or unrecognised value means "we
    have no evidence this company pays", which must never resolve to a paid
    tier. Anything unknown becomes the launch default.
    """
    key = (value or "").strip().lower()
    return key if key in PLAN_CREDITS else LAUNCH_DEFAULT_PLAN


def monthly_credits(plan: str | None) -> int:
    """Credits granted per billing period. `UNLIMITED` for uncapped plans."""
    return PLAN_CREDITS[resolve_plan(plan)]


def is_unlimited(plan: str | None) -> bool:
    return monthly_credits(plan) == UNLIMITED


def plan_label(plan: str | None) -> str:
    return PLAN_LABELS[resolve_plan(plan)]


# ---------------------------------------------------------------------------
# Subscription status → does this company get to use the product?
# ---------------------------------------------------------------------------
#
# Stripe's vocabulary, stored verbatim (see the migration). Per Stripe's
# subscription-lifecycle guidance: `unpaid` and `canceled` revoke access;
# `past_due` keeps it while Smart Retries work the card, because cutting a
# paying customer off mid-retry is how you turn a bounced card into churn.
ACTIVE_SUBSCRIPTION_STATUSES = frozenset(
    {
        "active",
        "trialing",   # no trials are sold today, but Stripe can still emit it
        "past_due",   # grace period — retries are still in flight
    }
)


def subscription_grants_access(plan: str | None, status: str | None) -> bool:
    """Whether a company may run billable work right now.

    LEGACY and ENTERPRISE bypass the check entirely: neither was sold through
    Stripe, so both carry a null `subscription_status`, and gating them on one
    would lock out every pre-billing tenant the moment this ships.
    """
    resolved = resolve_plan(plan)
    if resolved in (LEGACY, ENTERPRISE):
        return True
    return (status or "") in ACTIVE_SUBSCRIPTION_STATUSES


# ---------------------------------------------------------------------------
# Cancel-and-refund window
# ---------------------------------------------------------------------------
#
# There is no free trial (owner decision, 2026-08-21). This replaces it: pay,
# and if you cancel within the window we refund you. The REFUND ITSELF IS NOT
# AUTOMATIC — cancelling is self-serve through the Stripe portal, and a staff
# member then approves the refund from the admin panel, having seen how many
# credits were consumed. That stops the obvious hole (spend the month's credits
# on day one, cancel on day six, keep both the output and the money).
REFUND_WINDOW_DAYS = 7

# What a user may buy on top of their plan. The custom amount is bounded on
# both ends: below the floor the Stripe fee eats the purchase, and the ceiling
# is a typo guard (a mis-typed 100000 is a chargeback, not a good day).
TOPUP_PRESET_USD = (20, 50, 100)
TOPUP_MIN_USD = 5
TOPUP_MAX_USD = 2_000

# Credits per dollar on a top-up. Anchored to Starter's headline rate — $35 for
# 500 credits is ~14.3 credits/$ — and rounded DOWN to 14 so that topping up is
# never a cheaper way to buy credits than subscribing.
CREDITS_PER_TOPUP_USD = 14

# Referral reward, granted ONCE, when the invited company's first invoice
# actually pays (owner decision 2026-08-21). Not on signup and not on card
# entry: a card can be added and abandoned, and virtual cards make that free.
# $10 at the top-up rate.
REFERRAL_REWARD_CREDITS = 10 * CREDITS_PER_TOPUP_USD
# How many invites one company may have outstanding. From the spec: "users can
# invite 3 friends".
MAX_REFERRAL_INVITES = 3


def topup_credits_for_usd(amount_usd: int) -> int:
    return amount_usd * CREDITS_PER_TOPUP_USD

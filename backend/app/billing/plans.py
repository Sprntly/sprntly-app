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

# THE ONE PLACE A CREDIT HAS A DOLLAR PRICE. Every other figure in this file is
# derived from it: the plan allowances below, the top-up conversion, and the
# referral reward.
#
# It was anchored to Starter's $35 promo-code rate (500 credits for $35 is
# ~14.3 credits/$) and rounded DOWN to 14 so a top-up is never a cheaper way to
# buy credits than subscribing. Note that invariant holds against the PROMO
# price and not the $59 list price — at list, a top-up buys more credits per
# dollar than the Starter subscription does. Left as it is pending a pricing
# decision; flagged here so it is not rediscovered as a surprise.
CREDITS_PER_TOPUP_USD = 14

# ---------------------------------------------------------------------------
# What a plan's monthly credits are, and WHY they are that number
# ---------------------------------------------------------------------------
#
# Owner decision, 2026-08-28. The allowance is DERIVED, not chosen:
#
#     credits = (list price per month - PLATFORM_FEE_USD) x CREDITS_PER_TOPUP_USD
#
# A customer paying $59 has $5 taken for the platform, and the remaining $54 is
# handed back to them as credits at the same rate a top-up buys them. The point
# of deriving it rather than picking round numbers is that the previous numbers
# had drifted badly against the prices they were supposed to reflect: Starter
# under-granted by ~250 credits while Product Builder granted almost DOUBLE what
# its price supported, which is why it was by a distance the best value in the
# lineup and Team was the worst.
#
# ONE ALLOWANCE PER PLAN, priced off the MONTHLY list price, deliberately. An
# annual customer pays less per month and gets the same monthly credits — the
# discount is on the money, not on the allowance — and `companies.plan` stores
# no interval to key a second number off anyway.
#
# WHAT THIS DOES NOT COVER, and it is the open question rather than an
# oversight: background pipeline work (`kg_ingest` and friends) is ~41% of real
# LLM spend and no credit is ever charged for it. It scales with how much data a
# customer connects, not with what they generate, so a flat $5 covers a light
# tenant and badly under-covers a heavy one. Flat is the deliberate call for now.
PLATFORM_FEE_USD = 5

# Monthly LIST price. The Stripe price objects are the billing authority — these
# exist so the allowance formula has an input and so a price change here shows up
# as a credit change rather than being silently forgotten.
PLAN_LIST_PRICE_USD: dict[str, float] = {
    STARTER: 59.0,
    PRODUCT_BUILDER: 99.0,
    # Team is sold annually at $20,000; the formula wants a monthly figure.
    TEAM: 20_000 / 12,
}


def _allowance(plan: str) -> int:
    """The derived monthly grant. Floored, never rounded up — the formula is a
    ceiling on what we owe, so a fractional credit is ours, not theirs."""
    creditable = PLAN_LIST_PRICE_USD[plan] - PLATFORM_FEE_USD
    return int(creditable * CREDITS_PER_TOPUP_USD)


# Monthly credit grant per plan. Team's is described in the pricing table as
# "pooled" — the balance is company-level for every plan, so pooling needs no
# separate mechanism; it is simply what a company-level balance already does.
PLAN_CREDITS: dict[str, int] = {
    STARTER: _allowance(STARTER),
    PRODUCT_BUILDER: _allowance(PRODUCT_BUILDER),
    TEAM: _allowance(TEAM),
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


def period_credits(plan: str | None, status: str | None) -> int:
    """What THIS period is worth, which a trial's is not the plan's.

    One helper rather than a `status == "trialing"` check at each site, because
    there are three that must agree: the grant, the low-balance warning that
    divides by the allowance, and the email that tells a customer the number.
    Two of them agreeing and one not is how somebody gets warned they are
    "running low" at 140 of 756 while actually holding 140 of 100.
    """
    if (status or "") == "trialing":
        return TRIAL_CREDITS
    return monthly_credits(plan)


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
# There is no free trial for a company that has already bought once (owner
# decision, 2026-08-21). This replaces it: pay, and if you cancel within the
# window we refund you. The REFUND ITSELF IS NOT AUTOMATIC — cancelling is
# self-serve through the Stripe portal, and a staff member then approves the
# refund from the admin panel, having seen how many credits were consumed. That
# stops the obvious hole (spend the month's credits on day one, cancel on day
# six, keep both the output and the money).
REFUND_WINDOW_DAYS = 7

# THE FIRST SUBSCRIPTION A COMPANY EVER BUYS GETS A TRIAL (owner decision,
# 2026-08-28), because payment is about to move to the front of onboarding.
# Asking a stranger for money at step one of ten, before a single brief exists,
# is the steepest drop-off available; asking for the CARD at step one and the
# money a week later is not. Stripe Checkout still collects the card up front
# on a trialling subscription, so nothing about "card on file" is given up.
#
# Deliberately keyed on whether the company has EVER paid rather than on which
# screen started the checkout. A client-supplied "this is onboarding" flag
# would be a free trial for anyone who could spell it, and a company coming
# back to resubscribe after cancelling has already seen the product — the trial
# is not for them.
TRIAL_DAYS = 7

# WHAT A TRIAL IS WORTH, and why it is not the plan's allowance.
#
# Owner decision, 2026-08-31. A trialling company used to be granted the full
# monthly allowance — 756 credits on Starter — for seven days in which nothing
# is charged. That is a month of product for free, and the customers who cost
# the most are exactly the ones who would take it and cancel on day six.
#
# 50 buys the DOCUMENT loop, and deliberately not a prototype. Measured against
# `CREDIT_COSTS` above:
#
#     a few chats (10) + an ask (3) + evidence (10) + a PRD (25)  =  48
#
# A trialist can go from question to a finished PRD, which is the thing most of
# them came to see, with a little slack. A prototype costs 50 on its own — the
# single most expensive action in the table — so at this allowance it is out of
# reach even before the PRD that would feed it (25 + 50 = 75). That is the
# intended shape, not an accident of the arithmetic: the free week proves the
# writing, and the prototype is what a plan is for.
#
# THIS REPLACES A 100-CREDIT TRIAL (owner decision 2026-08-31, reversed the
# same day) which was sized at exactly one full pass INCLUDING a prototype.
# The note it carried is worth keeping, because it is the argument against
# going lower still: below about 85 the full loop cannot complete, and 50 is
# well below that. The trade is accepted knowingly — a trialist who wants a
# prototype now has to subscribe, which is the point.
#
# WHAT TO WATCH. This is a conversion bet, not a cost saving. The cost was
# never the problem: measured from `llm_usage_events`, a typical 100-credit
# trial was ~$1.30 of tokens and the worst case (all of it spent on `ask`, the
# priciest action per credit) ~$4.60, against the $5/month platform fee.
# Halving it saves under a dollar a trial. If trial-to-paid conversion drops,
# this number is the first thing to put back — it moves on evidence, and the
# evidence is conversion, not spend.
TRIAL_CREDITS = 50

# Matches REFUND_WINDOW_DAYS on purpose: a first-time buyer gets seven days
# before any money moves, and a repeat buyer gets seven days to ask for it
# back. One promise, told two ways, so support never has to explain two
# different weeks.

# What a user may buy on top of their plan. The custom amount is bounded on
# both ends: below the floor the Stripe fee eats the purchase, and the ceiling
# is a typo guard (a mis-typed 100000 is a chargeback, not a good day).
TOPUP_PRESET_USD = (20, 50, 100)
TOPUP_MIN_USD = 5
TOPUP_MAX_USD = 2_000

# See CREDITS_PER_TOPUP_USD, defined above the allowance formula that needs it.

# Referral reward, granted ONCE, when the invited company's subscription goes
# LIVE — which now means a card on file and a trial started, not a first
# invoice paid (owner decision 2026-08-29, revising 2026-08-21).
#
# The earlier rule waited for money to actually move. That made sense before
# the trial existed; with one, the invitee's first charge is seven days after
# they subscribe, so a referrer who did everything right waited a week to see
# anything and had no way to tell whether it had worked. Subscribing IS the
# conversion — the card is on file and Stripe has accepted it.
#
# Still not on signup: an account with no card is not a referral converted, and
# rewarding one would make the programme free to farm.
#
# $10 at the top-up rate.
REFERRAL_REWARD_CREDITS = 10 * CREDITS_PER_TOPUP_USD

# NO CAP on how many people one company may invite (owner decision
# 2026-08-29). The previous limit of three was arbitrary, and the thing that
# actually bounds the cost is not the invite count: an invite pays nothing
# until the invitee subscribes with a real card, so an unconverted invite costs
# us precisely nothing. Capping invites capped the upside without capping the
# downside.
#
# Kept as a constant rather than deleted because the outstanding-invite count is
# still worth showing; `remaining_invites` reads it and returns None when there
# is no ceiling.
MAX_REFERRAL_INVITES: int | None = None


def topup_credits_for_usd(amount_usd: int) -> int:
    return amount_usd * CREDITS_PER_TOPUP_USD

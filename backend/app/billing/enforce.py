"""The two lines a generation surface adds to become billable.

    enforce.bill(company.company_id, "prd", actor_user_id=company.user_id)

which refuses the action if it cannot be paid for (402) and debits it if it
can. `require_credits` and `charge` are exposed separately for the rare caller
that needs to check early and charge later.

Kept deliberately tiny and dependency-light so wiring a new surface is a
two-line change rather than a refactor — a guard that is annoying to apply is a
guard that gets skipped on the next surface someone builds.

CHARGED AT START, NOT ON COMPLETION. The alternative — bill only work that
succeeded — is fairer, and it was not taken because it means threading a charge
through the success path of seven different runners, each with its own failure
and cancellation handling, in a change that is already large. The cost is real
and bounded: a generation that fails still bills, and the user needs a staff
adjustment to get it back.

# ponytail: charge-on-start bills for failed runs. The upgrade is a refund in
# each runner's terminal-failure path, which is one call to `credits.grant`
# with the job id as ref_id — the ledger's idempotency index already makes it
# safe to call more than once. Do it when the first support ticket arrives, or
# sooner if failure rates on prototype builds stay high.

INERT UNLESS BOTH `plans.BILLING_ENABLED` AND `BILLING_ENFORCED` ARE SET.
The first is the payments-hidden switch and is currently False, which makes
every call here a no-op whatever the environment says. Both halves return immediately when
the flag is off, which is the default. That keeps CI, local dev and the initial
production deploy of this feature completely unaffected — a paywall that
switched itself on at merge would start refusing real customers before anyone
had looked at it, and staging shares the prod database.

Where a surface has a natural job id, pass it as `ref_id`: it makes a
double-submitted request charge once. Where there is none, omit it and accept
that a retry is a second action, which it genuinely is.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.billing import credits, emails, plans
from app.config import settings
from app.db import billing as billing_db

logger = logging.getLogger(__name__)


# THE ONE THING A LAPSED CUSTOMER KEEPS (owner decision, 2026-08-28).
#
# When `subscription_lock_mode` is "read_only", chat stays open after the
# subscription ends — with or without a credit balance. Everything else stops.
#
# It is a retention allowance, paid out of our own margin, and it is cheap
# enough to be one: measured against real usage, a chat turn costs about
# $0.004, roughly a hundredth of a PRD. Someone who can still talk to the
# product while they sort their card out is a customer who might come back;
# someone who hits a wall on every screen has already left.
#
# Deliberately NOT extended to "hard" mode, where the whole app routes to
# Billing and an open chat box would contradict the lock.
_LOCK_EXEMPT_FEATURES = frozenset({"chat"})


def _is_retention_exempt(feature: str) -> bool:
    return (
        settings.subscription_lock_mode == "read_only"
        and feature in _LOCK_EXEMPT_FEATURES
    )


def require_credits(company_id: str, feature: str) -> None:
    """Refuse the action if the company cannot pay for it.

    Raises 402 Payment Required — a real status for this, and one the web
    client can branch on without string-matching a message. The detail carries
    the numbers so the UI can say "this costs 25, you have 8" and offer the top
    up, rather than rendering a generic failure.

    Also refuses when the subscription itself is dead (canceled or unpaid), so
    a company sitting on leftover credits after cancelling cannot keep
    generating.
    """
    if not (plans.BILLING_ENABLED and settings.billing_enforced):
        return

    # Chat is exempt under read_only, and exempt from BOTH gates below: the
    # subscription check (which is what strands a cancelled company's leftover
    # credits) and the affordability check (so it keeps working at a zero
    # balance). `charge` below still debits whatever balance exists, so a
    # customer spends what they bought first and only then chats on us.
    if _is_retention_exempt(feature):
        return

    row = billing_db.get_billing(company_id) or {}
    plan = plans.resolve_plan(row.get("plan"))

    if not plans.subscription_grants_access(plan, row.get("subscription_status")):
        raise HTTPException(
            402,
            detail={
                "error": "subscription_inactive",
                "message": "Your subscription is not active. Update billing to continue.",
                "plan": plan,
                "subscription_status": row.get("subscription_status"),
            },
        )

    try:
        credits.check_affordable(company_id, feature)
    except credits.InsufficientCredits as exc:
        # THEY HAVE JUST HIT A WALL MID-WORK. Nothing else in the product will
        # explain it: the 402 shows a message on whatever screen they were on,
        # and if they were mid-generation they may not see it at all.
        #
        # Sent from the refusal rather than from the spend that emptied the
        # balance, because "you are out" is only true once something has
        # actually been refused — a balance of 3 is not out of credits until a
        # 25-credit action arrives.
        _notify(
            lambda: emails.credits_exhausted(
                company_id, period=_period_of(row), feature=feature
            )
        )
        raise HTTPException(
            402,
            detail={
                "error": "insufficient_credits",
                "message": (
                    f"This needs {exc.needed} credits and you have {exc.balance}."
                ),
                "needed": exc.needed,
                "balance": exc.balance,
                "feature": feature,
                "plan": plan,
            },
        ) from None


def _notify(fn) -> None:
    """Run an email without letting it near the caller's outcome.

    A generation must not fail because a mailbox bounced, and `require_credits`
    is on the hot path of every billable action.
    """
    try:
        fn()
    except Exception:  # noqa: BLE001 — an email never breaks a generation
        logger.warning("billing_email: send raised, ignoring", exc_info=True)


def _period_of(row: dict) -> str:
    """The billing period a credit email belongs to.

    Used as the de-dup key, so "you are running low" can fire again next month
    without firing twice this month. Falls back to the plan so a company with
    no recorded period still gets exactly one.
    """
    return str(row.get("credits_granted_for") or row.get("plan") or "unknown")


def _maybe_warn_low(company_id: str) -> None:
    """Warn once per period when the balance drops under the threshold.

    Checked AFTER the debit, since the crossing is what matters, and only on
    the way down — every subsequent spend is also under the threshold, and the
    de-dup key is the period rather than the balance so they are told once.
    """
    row = billing_db.get_billing(company_id) or {}
    plan = plans.resolve_plan(row.get("plan"))
    # The TRIAL allowance while trialling, or the warning fires at a fifth of a
    # month's credits against a balance that was never a month's worth.
    allowance = plans.period_credits(plan, row.get("subscription_status"))
    if allowance == plans.UNLIMITED or allowance <= 0:
        return
    balance = int(row.get("credit_balance") or 0)
    # Zero is not "running low", it is exhausted — and that email is sent from
    # the refusal, not from here. Warning as well would be two emails about the
    # same wall.
    if balance <= 0 or balance > allowance * emails.LOW_CREDIT_FRACTION:
        return
    _notify(
        lambda: emails.credits_low(
            company_id, period=_period_of(row), balance=balance, allowance=allowance
        )
    )


def charge(
    company_id: str,
    feature: str,
    *,
    ref_id: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """Debit the action. Never raises.

    By the time this runs, `require_credits` has already said the company can
    pay and the work has been started. A failure here is a database problem,
    not an affordability one, and taking down a generation the user is already
    waiting on to report a billing error would be the wrong trade — so it logs
    loudly and lets the work through. The gap shows up as a ledger row that
    never appeared, which the balance/`balance_after` pairing makes visible.
    """
    if not (plans.BILLING_ENABLED and settings.billing_enforced):
        return

    try:
        credits.spend(
            company_id, feature, ref_id=ref_id, actor_user_id=actor_user_id
        )
    except credits.InsufficientCredits:
        # Raced another action between the pre-flight and here. Deliberately
        # allowed through: the work has started, and the overdraft is at most
        # one action deep.
        logger.warning(
            "credit_overdraft company=%s feature=%s ref=%s", company_id, feature, ref_id
        )
    except Exception:
        logger.exception(
            "credit_charge_failed company=%s feature=%s ref=%s",
            company_id,
            feature,
            ref_id,
        )

    # AFTER the debit, because the crossing is what matters. Only fires on the
    # way down and only once per period — see `_maybe_warn_low`.
    _maybe_warn_low(company_id)


def bill(
    company_id: str,
    feature: str,
    *,
    ref_id: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """Gate and debit one action. The single line a generation surface adds.

    Raises 402 when the company cannot pay; otherwise debits and returns. The
    two halves stay separately importable for a caller that must check before
    doing expensive setup and charge only once the work is definitely starting.
    """
    require_credits(company_id, feature)
    charge(company_id, feature, ref_id=ref_id, actor_user_id=actor_user_id)

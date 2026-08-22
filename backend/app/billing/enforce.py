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

INERT UNLESS `BILLING_ENFORCED` IS SET. Both halves return immediately when
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

from app.billing import credits, plans
from app.config import settings
from app.db import billing as billing_db

logger = logging.getLogger(__name__)


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
    if not settings.billing_enforced:
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
    if not settings.billing_enforced:
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

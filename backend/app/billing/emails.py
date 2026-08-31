"""The eight billing emails, and the rules about who gets them.

WHAT STRIPE ALREADY SENDS, AND THIS DELIBERATELY DOES NOT: receipts, invoices,
dunning for a failed card, and upcoming-renewal warnings. Those are configured
at Settings -> Billing -> Subscriptions and emails, and duplicating them earns
two emails per event and a support question about which is real.

WHAT ONLY WE CAN SEND: everything about CREDITS. Stripe knows money and nothing
about the balance a customer actually spends, so a $0 trial receipt cannot say
"you have 756 credits" and a proration invoice cannot say what the new
allowance is. That is the gap these fill.

ONE STRIPE EMAIL IS ACTIVELY WRONG FOR US. Its trial reminder is hard-wired to
7 days before the trial ends, and `plans.TRIAL_DAYS` is 7 — so it would arrive
the moment the trial began, telling somebody who just signed up that their
trial is ending. `trial_ending` below replaces it; turn Stripe's off.

EVERY TRIGGER FIRES MORE THAN ONCE. Stripe redelivers webhooks for days, the
scheduler tick re-runs, and a balance under the low-credit threshold stays
under it. So nothing here decides whether to send by reasoning about the
moment — `billing_email_sends` records the OCCASION, and a second attempt at
the same occasion is a no-op. See the migration for what identifies one.

BEST EFFORT, ALWAYS. Every function returns the number of emails sent and
raises nothing. A webhook must not 500 because a mailbox was full, and a
scheduler tick must not die halfway through the tenant list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import mailer
from app.billing import plans
from app.config import settings
from app.db import billing as billing_db
from app.db.client import require_client

logger = logging.getLogger(__name__)

# The email kinds, as stored in `billing_email_sends.kind`. Slugs, not subject
# lines: copy changes, the identity of an occasion does not.
TRIAL_STARTED = "trial_started"
TRIAL_ENDING = "trial_ending"
CREDITS_LOW = "credits_low"
CREDITS_EXHAUSTED = "credits_exhausted"
REFERRAL_CONVERTED = "referral_converted"
SUBSCRIPTION_CANCELLED = "subscription_cancelled"
PLAN_CHANGED = "plan_changed"
TOPUP_PURCHASED = "topup_purchased"

#: Emails a customer may switch off.
#:
#: Everything else here is TRANSACTIONAL — it reports something that happened
#: to their money or their access, and a person who has been charged is
#: entitled to be told regardless of a notifications toggle they set for
#: weekly briefs. `credits_low` is the only nudge in the set: nothing has
#: happened yet, we are guessing they would like a warning, and a guess is
#: exactly the kind of email a preference should govern.
SUPPRESSIBLE = frozenset({CREDITS_LOW})

#: Warn once the balance drops below this share of the monthly allowance.
LOW_CREDIT_FRACTION = 0.2

#: How many days before the trial ends to send the reminder. Two rather than
#: Stripe's seven, because our whole trial is seven — a reminder at day 0 is
#: not a reminder.
TRIAL_ENDING_LEAD_DAYS = 2


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def billing_recipients(company_id: str) -> list[dict]:
    """Who gets billing mail: owners and admins, nobody else.

    A workspace can have a dozen members and one of them holds the card. Only
    an owner or admin can act on any of this — `/v1/billing/*` refuses everyone
    else — so mailing the rest is telling people about a problem they are not
    allowed to fix.
    """
    try:
        members = (
            require_client()
            .table("company_members")
            .select("user_id, role")
            .eq("company_id", company_id)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning("billing_email: could not read members for %s", company_id, exc_info=True)
        return []

    user_ids = [m["user_id"] for m in members if (m.get("role") or "") in ("owner", "admin")]
    if not user_ids:
        return []

    try:
        rows = (
            require_client()
            .table("profiles")
            .select("id, email, first_name")
            .in_("id", user_ids)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning("billing_email: could not read profiles for %s", company_id, exc_info=True)
        return []

    return [r for r in rows if (r.get("email") or "").strip()]


def _emails_enabled(company_id: str) -> bool:
    """Whether this company has notification email switched on at all.

    Read with its OWN query rather than off the billing row. `get_billing`
    selects billing columns, and `notification_settings` is not one of them —
    so checking `company.get("notification_settings")` found nothing, decided
    that meant "no preference set", and let every suppressible email through.
    A preference that silently always says yes is worse than no preference,
    because it looks like it is working.

    Absent or unreadable means ENABLED: a company that has never touched the
    setting should still hear about their credits, and a failed read is not a
    request for silence.
    """
    try:
        rows = (
            require_client()
            .table("companies")
            .select("notification_settings")
            .eq("id", company_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning(
            "billing_email: could not read notification settings for %s",
            company_id, exc_info=True,
        )
        return True
    ns = (rows[0] or {}).get("notification_settings") if rows else None
    if not isinstance(ns, dict):
        return True
    return ns.get("email_enabled", True) is not False


# ---------------------------------------------------------------------------
# The once-per-occasion guard
# ---------------------------------------------------------------------------


def _already_sent(company_id: str, kind: str, ref_id: str, email: str) -> bool:
    try:
        rows = (
            require_client()
            .table("billing_email_sends")
            .select("id")
            .eq("company_id", company_id)
            .eq("kind", kind)
            .eq("ref_id", ref_id)
            .eq("email", email)
            .limit(1)
            .execute()
            .data
            or []
        )
        return bool(rows)
    except Exception:
        # FAIL CLOSED. If we cannot tell whether this was already sent, do not
        # send: a duplicate billing email is worse than a missing one, and the
        # occasion will come round again on the next webhook redelivery.
        logger.warning(
            "billing_email: de-dup check failed company=%s kind=%s — not sending",
            company_id, kind, exc_info=True,
        )
        return True


def _record(company_id: str, kind: str, ref_id: str, email: str, status: str) -> None:
    try:
        require_client().table("billing_email_sends").insert(
            {
                "company_id": company_id,
                "kind": kind,
                "ref_id": ref_id,
                "email": email,
                "status": status,
            }
        ).execute()
    except Exception as exc:
        text = f"{type(exc).__name__} {exc}".lower()
        if "unique" in text or "duplicate" in text or "23505" in text:
            return  # the race this table exists to lose safely
        logger.warning(
            "billing_email: could not record send company=%s kind=%s",
            company_id, kind, exc_info=True,
        )


def _deliver(
    *,
    company_id: str,
    kind: str,
    ref_id: str,
    subject: str,
    body: str,
    cta_label: str = "",
    cta_path: str = "/settings?section=billing",
    facts: list[tuple[str, str]] | None = None,
) -> int:
    """Send one email kind to every billing recipient. Returns how many went.

    `facts` is the panel of numbers under the copy — the plan, the balance, the
    date something happens. Billing mail is read for those, and putting them in
    a table means a reader finds them without parsing a sentence. The plain-text
    part gets the same lines, so the two halves of the email agree.

    The recording happens whether or not the send succeeded, and a skip is
    recorded as `skipped` rather than left absent. Without that, an environment
    with no RESEND_API_KEY accumulates a backlog of unsent occasions and mails
    the lot the day somebody sets the key.
    """
    if kind in SUPPRESSIBLE and not _emails_enabled(company_id):
        logger.info("billing_email: %s suppressed by preference for %s", kind, company_id)
        return 0

    cta_url = f"{settings.app_origin}{cta_path}" if cta_path else ""
    sent = 0
    for person in billing_recipients(company_id):
        email = person["email"].strip()
        if _already_sent(company_id, kind, ref_id, email):
            continue
        ok = mailer.send(
            to_email=email,
            subject=subject,
            body_text=_greeting(person) + body,
            cta_label=cta_label,
            cta_url=cta_url if cta_label else "",
            facts=facts,
        )
        _record(company_id, kind, ref_id, email, "sent" if ok else "skipped")
        sent += 1 if ok else 0
    if sent:
        logger.info("billing_email: sent %s x%s company=%s", kind, sent, company_id)
    return sent


def _greeting(person: dict) -> str:
    name = (person.get("first_name") or "").strip()
    return f"Hi {name},\n\n" if name else "Hi,\n\n"


def _plan_label(company: dict) -> str:
    return plans.plan_label(plans.resolve_plan(company.get("plan")))


def _fmt(n: int | None) -> str:
    return f"{int(n or 0):,}"


def _balance(company: dict) -> str:
    """The credit balance as the facts panel shows it."""
    return _fmt(company.get("credit_balance"))


def _date(iso: str | None) -> str:
    if not iso:
        return "soon"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%-d %B %Y")
    except Exception:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %B %Y")
        except Exception:
            return "soon"


# ---------------------------------------------------------------------------
# 1. Trial started
# ---------------------------------------------------------------------------


def trial_started(company_id: str, *, subscription_id: str) -> int:
    """Their card is on file, nothing has been charged, and they have credits.

    Stripe's own receipt for a trial says $0 and mentions no credits, which
    makes the most generous moment in the product read like a failed payment.
    """
    c = billing_db.get_billing(company_id) or {}
    # The TRIAL allowance, not the plan's. A trial is a flat
    # `plans.TRIAL_CREDITS` whichever plan is being trialled — see the note on
    # that constant — and this email is the first place a customer is told the
    # number, so it must be the number they will actually have.
    allowance = plans.TRIAL_CREDITS
    return _deliver(
        company_id=company_id,
        kind=TRIAL_STARTED,
        ref_id=subscription_id,
        subject=f"Your {_plan_label(c)} trial has started",
        body=(
            f"You are on {_plan_label(c)} with {_fmt(allowance)} credits to "
            f"try it with, and nothing has been charged.\n\n"
            # WHAT THE ALLOWANCE ACTUALLY BUYS, AND NOTHING MORE. This promised
            # "a PRD and a prototype" while the trial was 100 credits, which
            # covered exactly that. At `plans.TRIAL_CREDITS` = 50 a prototype
            # costs the whole allowance on its own and is deliberately out of
            # reach — so the FIRST email a customer gets must not promise one.
            # If the constant moves back up, this sentence moves with it.
            f"That is enough to take one idea from a question to a written "
            f"PRD — a few questions, an evidence brief and a PRD.\n\n"
            f"Your card is saved. The first payment is on "
            f"{_date(c.get('current_period_end'))} — cancel any time before "
            f"then and you pay nothing."
        ),
        facts=[
            ("Plan", _plan_label(c)),
            ("Trial credits", _fmt(allowance)),
            ("Trial ends", _date(c.get("current_period_end"))),
            ("Charged so far", "$0.00"),
        ],
        cta_label="View billing",
    )


# ---------------------------------------------------------------------------
# 2. Trial ending
# ---------------------------------------------------------------------------


def trial_ending(company_id: str, *, subscription_id: str, days_left: int) -> int:
    """Two days out, not Stripe's seven.

    Stripe's reminder is hard-wired to seven days before the trial ends and our
    trial is seven days long, so it would land at signup. This is the one that
    arrives while there is still a decision to make.
    """
    c = billing_db.get_billing(company_id) or {}
    day_word = "day" if days_left == 1 else "days"
    return _deliver(
        company_id=company_id,
        kind=TRIAL_ENDING,
        ref_id=subscription_id,
        subject=f"Your trial ends in {days_left} {day_word}",
        body=(
            f"Your {_plan_label(c)} trial ends on "
            f"{_date(c.get('current_period_end'))}, and the first payment is "
            f"taken then.\n\n"
            f"Nothing to do if you are staying. If you are not, cancel before "
            f"that date and you will not be charged."
        ),
        facts=[
            ("Plan", _plan_label(c)),
            ("Trial ends", _date(c.get("current_period_end"))),
            ("Credits left", _balance(c)),
        ],
        cta_label="Manage subscription",
    )


# ---------------------------------------------------------------------------
# 3. Credits running low
# ---------------------------------------------------------------------------


def credits_low(company_id: str, *, period: str, balance: int, allowance: int) -> int:
    """The only nudge in the set, and the only suppressible one."""
    c = billing_db.get_billing(company_id) or {}
    return _deliver(
        company_id=company_id,
        kind=CREDITS_LOW,
        ref_id=period,
        subject=f"{_fmt(balance)} credits left this month",
        body=(
            f"You have {_fmt(balance)} of {_fmt(allowance)} credits left on "
            f"{_plan_label(c)}.\n\n"
            f"They reset on {_date(c.get('current_period_end'))}. If you need "
            f"more before then, you can top up or move to a larger plan."
        ),
        facts=[
            ("Credits left", _fmt(balance)),
            ("This period", _fmt(allowance)),
            ("Resets on", _date(c.get("current_period_end"))),
        ],
        cta_label="Buy more credits",
    )


# ---------------------------------------------------------------------------
# 4. Credits exhausted
# ---------------------------------------------------------------------------


def credits_exhausted(company_id: str, *, period: str, feature: str = "") -> int:
    """They have just hit a wall mid-work. Say why, and what fixes it.

    Not suppressible: a refused generation with no explanation is the worst
    moment in the product, and somebody who switched off weekly briefs did not
    ask to be left guessing about that.
    """
    c = billing_db.get_billing(company_id) or {}
    what = f" while running {feature}" if feature else ""
    return _deliver(
        company_id=company_id,
        kind=CREDITS_EXHAUSTED,
        ref_id=period,
        subject="You are out of credits",
        body=(
            f"Your {_plan_label(c)} credits ran out{what}, so new work is "
            f"paused.\n\n"
            f"Everything you have already made is still there and still "
            f"readable. Your allowance resets on "
            f"{_date(c.get('current_period_end'))}, or you can top up now."
        ),
        facts=[
            ("Credits left", "0"),
            ("Resets on", _date(c.get("current_period_end"))),
            ("Your existing work", "Still available"),
        ],
        cta_label="Buy more credits",
    )


# ---------------------------------------------------------------------------
# 5. Referral converted
# ---------------------------------------------------------------------------


def referral_converted(company_id: str, *, referral_id: str, credits_awarded: int) -> int:
    """The one with no other surface.

    Somebody shared a link, a stranger subscribed because of it, and the only
    way they would ever find out is by opening the billing screen and noticing
    a bigger number.
    """
    c = billing_db.get_billing(company_id) or {}
    return _deliver(
        company_id=company_id,
        kind=REFERRAL_CONVERTED,
        ref_id=referral_id,
        subject=f"You earned {_fmt(credits_awarded)} credits",
        body=(
            f"Someone signed up through your referral link and started a "
            f"subscription, so {_fmt(credits_awarded)} credits have been added "
            f"to your balance.\n\n"
            f"Your link keeps working — share it with as many people as you "
            f"like."
        ),
        facts=[
            ("Credits earned", _fmt(credits_awarded)),
            ("New balance", _balance(c)),
        ],
        cta_label="View balance",
    )


# ---------------------------------------------------------------------------
# 6. Subscription cancelled
# ---------------------------------------------------------------------------


def subscription_cancelled(company_id: str, *, subscription_id: str) -> int:
    """Stripe says "cancelled". Only we can say what happens to their work."""
    c = billing_db.get_billing(company_id) or {}
    return _deliver(
        company_id=company_id,
        kind=SUBSCRIPTION_CANCELLED,
        ref_id=subscription_id,
        subject="Your subscription has ended",
        body=(
            f"Your {_plan_label(c)} subscription has ended and no further "
            f"payments will be taken.\n\n"
            f"Your PRDs, reports and briefs are still there — nothing has been "
            f"deleted. Generating new work needs an active plan, so pick one "
            f"whenever you want to carry on."
        ),
        facts=[
            ("Plan", _plan_label(c)),
            ("Further payments", "None"),
            ("Your existing work", "Still available"),
        ],
        cta_label="Choose a plan",
    )


# ---------------------------------------------------------------------------
# 7. Plan changed
# ---------------------------------------------------------------------------


def plan_changed(company_id: str, *, subscription_id: str, previous_plan: str) -> int:
    """Stripe's proration invoice says what moved in money, not in credits."""
    c = billing_db.get_billing(company_id) or {}
    new_plan = plans.resolve_plan(c.get("plan"))
    allowance = plans.monthly_credits(new_plan)
    was = plans.plan_label(plans.resolve_plan(previous_plan))
    credits_line = (
        "unlimited credits"
        if allowance == plans.UNLIMITED
        else f"{_fmt(allowance)} credits a month"
    )
    return _deliver(
        company_id=company_id,
        kind=PLAN_CHANGED,
        ref_id=f"{subscription_id}:{new_plan}",
        subject=f"You are now on {plans.plan_label(new_plan)}",
        body=(
            f"Your plan moved from {was} to {plans.plan_label(new_plan)}, which "
            f"gives you {credits_line}.\n\n"
            f"Stripe has adjusted your invoice for the part of the month "
            f"remaining, so you are not paying twice for the same days."
        ),
        facts=[
            ("Previous plan", was),
            ("New plan", plans.plan_label(new_plan)),
            (
                "Credits a month",
                "Unlimited" if allowance == plans.UNLIMITED else _fmt(allowance),
            ),
        ],
        cta_label="View billing",
    )


# ---------------------------------------------------------------------------
# 8. Top-up purchased
# ---------------------------------------------------------------------------


def topup_purchased(company_id: str, *, ref_id: str, credits_purchased: int) -> int:
    """Stripe receipts the money. Only we can confirm the credits arrived."""
    c = billing_db.get_billing(company_id) or {}
    return _deliver(
        company_id=company_id,
        kind=TOPUP_PURCHASED,
        ref_id=ref_id,
        subject=f"{_fmt(credits_purchased)} credits added",
        body=(
            f"Your top-up went through and {_fmt(credits_purchased)} credits "
            f"are on your balance now.\n\n"
            f"Purchased credits sit on top of your {_plan_label(c)} allowance "
            f"and are spent the same way."
        ),
        facts=[
            ("Credits added", _fmt(credits_purchased)),
            ("New balance", _balance(c)),
            ("Expires", "Never, while your plan is active"),
        ],
        cta_label="View balance",
    )

"""Invite a friend; get credit when they actually pay.

NOT the same thing as `workspace_invites`, and the distinction is the whole
anti-abuse story. A workspace invite adds a teammate INSIDE your company and
consumes a seat you already pay for. A referral brings a whole new company onto
Sprntly. Conflating them would let one person farm referral credit by inviting
their own colleagues.

THE REWARD FIRES ON THE FRIEND'S FIRST PAID INVOICE, not on signup and not on
card entry (owner decision, 2026-08-21). "Adds a credit card" was the original
trigger and was rejected as too cheap: virtual card numbers are free and
disposable, so it pays out for an intent that never becomes revenue. Waiting
for `invoice.paid` means the reward is funded by money that actually arrived.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

from app.billing import credits, plans
from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

PENDING = "pending"
SIGNED_UP = "signed_up"
REWARDED = "rewarded"
VOID = "void"

_COLUMNS = (
    "id, referrer_company_id, referrer_user_id, invitee_email, code, status, "
    "invitee_company_id, reward_credits, created_at, signed_up_at, rewarded_at"
)


class ReferralLimitReached(Exception):
    """This company has used all its invites."""


class AlreadyInvited(Exception):
    """That address already has a live invite from this company."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@retry_on_disconnect
def list_for_company(company_id: str) -> list[dict]:
    return (
        require_client()
        .table("referrals")
        .select(_COLUMNS)
        .eq("referrer_company_id", company_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


def remaining_invites(company_id: str) -> int | None:
    """Invites left. VOID rows do not count against the cap — a self-referral
    we rejected should not cost the user one of their three."""
    live = [r for r in list_for_company(company_id) if r.get("status") != VOID]
    if plans.MAX_REFERRAL_INVITES is None:
        return None
    return max(0, plans.MAX_REFERRAL_INVITES - len(live))


@retry_on_disconnect
def create_invite(
    *, referrer_company_id: str, referrer_user_id: str | None, invitee_email: str
) -> dict:
    """Mint an invite. Raises if the cap is reached or the address is a repeat."""
    email = (invitee_email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("a valid email address is required")

    existing = list_for_company(referrer_company_id)
    if any(r.get("invitee_email") == email and r.get("status") != VOID for r in existing):
        raise AlreadyInvited(email)
    if (
        plans.MAX_REFERRAL_INVITES is not None
        and len([r for r in existing if r.get("status") != VOID])
        >= plans.MAX_REFERRAL_INVITES
    ):
        raise ReferralLimitReached()

    row = {
        "id": uuid.uuid4().hex,
        "referrer_company_id": referrer_company_id,
        "referrer_user_id": referrer_user_id,
        "invitee_email": email,
        # Unguessable: the code is the only thing carried in the invite link,
        # and a guessable one would let anyone attribute their signup to a
        # stranger's company.
        "code": secrets.token_urlsafe(12),
        "status": PENDING,
    }
    require_client().table("referrals").insert(row).execute()
    return row


@retry_on_disconnect
def code_for_company(company_id: str) -> str:
    """This company's permanent referral code, minted on first read.

    ONE CODE, FOREVER, rather than one per invited email address. The old model
    put a form between someone and sharing a link, capped how many people they
    could tell, and produced codes that were useless to anyone but the address
    they were cut for.

    Lazy rather than backfilled: a company that has never opened the referrals
    screen does not need a code minted for a link nobody has asked for.

    The alphabet excludes look-alikes (0/O, 1/I/l) because this is read aloud
    and retyped from screenshots, and a code that cannot survive that is a
    support ticket.
    """
    client = require_client()
    rows = (
        client.table("companies")
        .select("referral_code")
        .eq("id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    existing = (rows[0] or {}).get("referral_code") if rows else None
    if existing:
        return existing

    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for _ in range(5):
        code = "".join(secrets.choice(alphabet) for _ in range(10))
        try:
            client.table("companies").update({"referral_code": code}).eq(
                "id", company_id
            ).execute()
            return code
        except Exception as exc:
            # The unique index rejected a collision. Astronomically unlikely at
            # 31^10, but retrying is cheaper than reasoning about it.
            text = f"{type(exc).__name__} {exc}".lower()
            if not ("unique" in text or "duplicate" in text or "23505" in text):
                raise
    raise RuntimeError("could not mint a referral code")


def company_for_code(code: str) -> str | None:
    """The company a referral code belongs to, or None."""
    clean = (code or "").strip().upper()
    if not clean:
        return None
    rows = (
        require_client()
        .table("companies")
        .select("id")
        .eq("referral_code", clean)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def get_by_code(code: str) -> dict | None:
    rows = (
        require_client()
        .table("referrals")
        .select(_COLUMNS)
        .eq("code", code)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def _update(referral_id: str, patch: dict) -> None:
    require_client().table("referrals").update(patch).eq("id", referral_id).execute()


def claim_on_signup(*, code: str, invitee_company_id: str) -> dict | None:
    """Record that a new company arrived through someone's referral link.

    CREATES the referral row rather than updating a pre-minted one. With a
    single permanent code per company there is nothing to pre-mint: the row is
    the arrival, one per person who actually came, instead of one per address
    somebody typed into a form.

    Returns the new referral, or None if the code is unknown, self-referring, or
    this company already arrived through one. NO CREDIT IS GRANTED HERE — this
    records who to pay when the invitee subscribes.
    """
    referrer_id = company_for_code(code)
    if not referrer_id:
        return None

    # Self-referral: signing up again through your own link. The remaining hole
    # is one person running two companies under two addresses, which no in-app
    # check can see — but it is gated on a real card and a real subscription, so
    # the worst case costs them a subscription to earn $10 of credits.
    if referrer_id == invitee_company_id:
        logger.warning("referral_self_ignored company=%s", invitee_company_id)
        return None

    # One referral per invitee, ever. Without this, signing up, deleting the
    # company and signing up again would pay the referrer twice.
    existing = (
        require_client()
        .table("referrals")
        .select("id")
        .eq("invitee_company_id", invitee_company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return None

    row = {
        "id": str(uuid.uuid4()),
        "referrer_company_id": referrer_id,
        "code": (code or "").strip().upper(),
        "status": SIGNED_UP,
        "invitee_company_id": invitee_company_id,
        "signed_up_at": _now(),
    }
    require_client().table("referrals").insert(row).execute()
    logger.info(
        "referral_claimed referrer=%s invitee=%s", referrer_id, invitee_company_id
    )
    return row


@retry_on_disconnect
def _pending_reward_for_invitee(invitee_company_id: str) -> dict | None:
    rows = (
        require_client()
        .table("referrals")
        .select(_COLUMNS)
        .eq("invitee_company_id", invitee_company_id)
        .eq("status", SIGNED_UP)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def reward_for_subscription(invitee_company_id: str) -> dict | None:
    """Pay the referrer, if this company SUBSCRIBING converts a referral.

    Called when the invitee's subscription goes live — `active` or `trialing`,
    which both mean a card is on file and Stripe has accepted it. It used to be
    called from `invoice.paid` instead, and that stopped making sense when the
    trial arrived: the invitee's first charge is seven days after they
    subscribe, so a referrer who did everything right waited a week with no
    signal that it had worked.

    Deliberately NOT on signup. An account with no card is not a conversion,
    and paying for one would make the programme free to farm.

    Safe to call on every sync: only a referral still in SIGNED_UP is eligible,
    and the grant is keyed on the referral id, so the status transition and the
    ledger's idempotency index each stop a repeat independently.

    Returns the rewarded referral, or None when there was nothing to pay.
    """
    referral = _pending_reward_for_invitee(invitee_company_id)
    if not referral:
        return None

    reward = plans.REFERRAL_REWARD_CREDITS
    credits.grant(
        referral["referrer_company_id"],
        reward,
        reason="referral",
        ref_id=referral["id"],
    )
    _update(
        referral["id"],
        {"status": REWARDED, "reward_credits": reward, "rewarded_at": _now()},
    )
    logger.info(
        "referral_rewarded referral=%s referrer=%s credits=%s",
        referral["id"],
        referral["referrer_company_id"],
        reward,
    )
    return referral


# The pre-trial name. Kept so nothing silently stops paying referrers if a
# caller elsewhere still uses it; the behaviour is identical.
reward_for_first_payment = reward_for_subscription

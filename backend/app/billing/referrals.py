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


def remaining_invites(company_id: str) -> int:
    """Invites left. VOID rows do not count against the cap — a self-referral
    we rejected should not cost the user one of their three."""
    live = [r for r in list_for_company(company_id) if r.get("status") != VOID]
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
    if len([r for r in existing if r.get("status") != VOID]) >= plans.MAX_REFERRAL_INVITES:
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
    """Attach a freshly created company to the referral that brought it.

    Returns the updated referral, or None if the code is unknown or spent. NO
    CREDIT IS GRANTED HERE — this only records who to pay when the invoice
    lands.
    """
    referral = get_by_code(code)
    if not referral or referral.get("status") != PENDING:
        return None

    # Self-referral: inviting yourself back into your own company. The
    # remaining hole is one person running two companies under two addresses,
    # which no in-app check can see; it is bounded at three invites and gated
    # on a real payment, so the worst case costs a real subscription.
    if referral.get("referrer_company_id") == invitee_company_id:
        _update(referral["id"], {"status": VOID})
        logger.warning(
            "referral_self_void referral=%s company=%s",
            referral["id"],
            invitee_company_id,
        )
        return None

    patch = {
        "status": SIGNED_UP,
        "invitee_company_id": invitee_company_id,
        "signed_up_at": _now(),
    }
    _update(referral["id"], patch)
    return {**referral, **patch}


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


def reward_for_first_payment(invitee_company_id: str) -> dict | None:
    """Pay the referrer, if this company's payment converts a referral.

    Called from the `invoice.paid` webhook. Safe to call on every invoice: only
    a referral still in SIGNED_UP is eligible, and the grant is keyed on the
    referral id, so both the status transition and the ledger's idempotency
    index stop a repeat.

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

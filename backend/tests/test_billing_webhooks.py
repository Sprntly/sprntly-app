"""Stripe webhook handlers.

`handle_event` takes an already-verified event dict and returns a description,
so every rule is testable by calling one function — no HTTP, no signatures, no
Stripe SDK. Signature verification is the route's job and is tested in
test_billing_routes.py.

The themes worth the coverage: a replayed event must not pay twice, an
out-of-order event must not write stale status, and a payload must never be
able to choose which company gets the money.
"""
from __future__ import annotations

import pytest

from app.billing import plans, referrals
from app.billing import webhooks as billing_webhooks
from app.db import billing as billing_db
from app.db.client import require_client

from ._company_helpers import seed_company


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    return isolated_settings


@pytest.fixture
def company(monkeypatch):
    """A company already linked to a Stripe customer, with Stripe stubbed.

    `get_subscription` is the only outbound call the handlers make; stubbing it
    keeps the suite free of both the SDK and any credentials.
    """
    cid = seed_company(user_id="u-wh", slug="wh-co")
    billing_db.set_billing(cid, {"stripe_customer_id": "cus_live"})

    state = {
        "id": "sub_1",
        "status": "active",
        "customer": "cus_live",
        "metadata": {"company_id": cid, "plan": plans.PRODUCT_BUILDER},
        "current_period_end": 1_790_000_000,
        "items": {"data": [{"price": {"id": "price_pb_monthly"}}]},
    }
    monkeypatch.setattr(
        billing_webhooks.stripe_client, "get_subscription", lambda _id: dict(state)
    )
    return type("Ctx", (), {"id": cid, "sub": state})()


def _event(event_type: str, obj: dict, *, event_id: str = "evt_1") -> dict:
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


def _row(company_id: str) -> dict:
    return billing_db.get_billing(company_id) or {}


def _balance(company_id: str) -> int:
    return int(_row(company_id).get("credit_balance") or 0)


# ---------------------------------------------------------------------------
# Tenant resolution — invariant 1
# ---------------------------------------------------------------------------


def test_company_comes_from_the_stripe_customer_not_the_payload(company):
    """A webhook body must not be able to redirect credits to another tenant.

    The customer id is ours, written when we created it; `metadata.company_id`
    is only a fallback for the window before that write lands. When they
    disagree, the customer wins.
    """
    victim = seed_company(user_id="u-victim", slug="victim-co")
    billing_db.set_billing(victim, {"stripe_customer_id": "cus_victim"})

    spoofed = {
        "id": "cs_1",
        "customer": "cus_victim",
        "metadata": {"company_id": company.id, "purpose": "topup", "credits": "500"},
    }
    billing_webhooks.handle_event(_event("checkout.session.completed", spoofed))

    assert _balance(victim) == 500
    assert _balance(company.id) == 0


def test_metadata_is_the_fallback_when_the_customer_is_not_linked_yet():
    """Checkout can complete before our own stripe_customer_id write lands."""
    cid = seed_company(user_id="u-fb", slug="fb-co")
    session = {
        "id": "cs_2",
        "customer": "cus_brand_new",
        "metadata": {"company_id": cid, "purpose": "topup", "credits": "140"},
    }

    billing_webhooks.handle_event(_event("checkout.session.completed", session))

    assert _balance(cid) == 140
    # And the customer link is backfilled, so later events resolve directly.
    assert _row(cid)["stripe_customer_id"] == "cus_brand_new"


def test_an_unresolvable_event_changes_nothing():
    session = {"id": "cs_3", "customer": "cus_unknown", "metadata": {}}
    result = billing_webhooks.handle_event(_event("checkout.session.completed", session))
    assert "ignored" in result


# ---------------------------------------------------------------------------
# Subscriptions — invariant 2
# ---------------------------------------------------------------------------


def test_checkout_completion_records_the_subscription(company):
    session = {
        "id": "cs_sub",
        "customer": "cus_live",
        "subscription": "sub_1",
        "metadata": {"company_id": company.id},
    }

    billing_webhooks.handle_event(_event("checkout.session.completed", session))

    row = _row(company.id)
    assert row["plan"] == plans.PRODUCT_BUILDER
    assert row["subscription_status"] == "active"
    assert row["stripe_subscription_id"] == "sub_1"
    assert row["current_period_end"].startswith("2026-")


def test_subscription_state_is_refetched_not_read_from_the_payload(company):
    """Stripe does not guarantee delivery order. A stale payload arriving late
    must not overwrite current status — so the payload's own fields are
    ignored and the API is the source of truth."""
    company.sub["status"] = "past_due"
    stale = {"id": "sub_1", "customer": "cus_live", "status": "active"}

    billing_webhooks.handle_event(_event("customer.subscription.updated", stale))

    assert _row(company.id)["subscription_status"] == "past_due"


def test_deleted_subscription_revokes_access_but_keeps_the_plan_on_record(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )
    billing_webhooks.handle_event(
        _event("customer.subscription.deleted", {"id": "sub_1", "customer": "cus_live"})
    )

    row = _row(company.id)
    assert row["subscription_status"] == "canceled"
    assert row["plan"] == plans.PRODUCT_BUILDER
    assert not plans.subscription_grants_access(row["plan"], row["subscription_status"])


def test_plan_falls_back_to_the_price_id_when_metadata_is_absent(company, monkeypatch):
    """A subscription created by hand in the dashboard carries no metadata."""
    monkeypatch.setattr(
        billing_webhooks.stripe_client,
        "price_id",
        lambda plan, interval: (
            "price_starter_m"
            if (plan, interval) == (plans.STARTER, "monthly")
            else ""
        ),
    )
    company.sub["metadata"] = {}
    company.sub["items"] = {"data": [{"price": {"id": "price_starter_m"}}]}

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id)["plan"] == plans.STARTER


# ---------------------------------------------------------------------------
# invoice.paid — the money event
# ---------------------------------------------------------------------------


def _invoice(company_sub: str | None = "sub_1", **extra) -> dict:
    inv = {
        "id": "in_1",
        "customer": "cus_live",
        "created": 1_787_000_000,
        "period_start": 1_787_000_000,
    }
    if company_sub:
        inv["parent"] = {"subscription_details": {"subscription": company_sub}}
    inv.update(extra)
    return inv


def test_first_payment_starts_the_refund_clock_and_grants_the_period(company):
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))

    row = _row(company.id)
    assert row["first_paid_at"] is not None
    assert row["credit_balance"] == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]
    assert row["credits_granted_for"] is not None


def test_a_replayed_invoice_does_not_grant_a_second_month(company):
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    # Spend some, then let the same invoice arrive again.
    from app.billing import credits

    credits.spend(company.id, "prd", ref_id="job-1")
    after_spend = _balance(company.id)

    billing_webhooks.handle_event(_event("invoice.paid", _invoice(), event_id="evt_2"))

    assert _balance(company.id) == after_spend


def test_the_next_period_grants_again(company):
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    from app.billing import credits

    credits.spend(company.id, "prd", ref_id="job-2")

    billing_webhooks.handle_event(
        _event("invoice.paid", _invoice(period_start=1_789_600_000), event_id="evt_3")
    )

    assert _balance(company.id) == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]


def test_first_paid_at_is_not_moved_by_later_invoices(company):
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    first = _row(company.id)["first_paid_at"]

    billing_webhooks.handle_event(
        _event(
            "invoice.paid",
            _invoice(created=1_789_600_000, period_start=1_789_600_000),
            event_id="evt_4",
        )
    )

    assert _row(company.id)["first_paid_at"] == first


def test_flat_subscription_field_is_still_read(company):
    """`invoice.subscription` was nested under `parent` in 2025-03-31.basil.
    Both shapes must resolve, so the API pin can move either way."""
    inv = _invoice(company_sub=None)
    inv["subscription"] = "sub_1"

    billing_webhooks.handle_event(_event("invoice.paid", inv))

    assert _row(company.id)["stripe_subscription_id"] == "sub_1"


def test_payment_failure_refetches_rather_than_assuming_past_due(company):
    """Stripe decides the next status from the dashboard's retry settings; it
    may already be canceled by the time we hear about the failure."""
    company.sub["status"] = "canceled"

    billing_webhooks.handle_event(_event("invoice.payment_failed", _invoice()))

    assert _row(company.id)["subscription_status"] == "canceled"


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------


def test_topup_grants_the_credits_recorded_on_the_session(company):
    """The rate at purchase time wins. If plans.py changes between checkout and
    webhook delivery, the customer still gets what they paid for."""
    session = {
        "id": "cs_topup",
        "customer": "cus_live",
        "metadata": {"company_id": company.id, "purpose": "topup", "credits": "700"},
    }

    billing_webhooks.handle_event(_event("checkout.session.completed", session))

    assert _balance(company.id) == 700


def test_a_replayed_topup_grants_once(company):
    session = {
        "id": "cs_topup",
        "customer": "cus_live",
        "metadata": {"company_id": company.id, "purpose": "topup", "credits": "700"},
    }

    billing_webhooks.handle_event(_event("checkout.session.completed", session))
    billing_webhooks.handle_event(
        _event("checkout.session.completed", session, event_id="evt_dup")
    )

    assert _balance(company.id) == 700


def test_a_topup_with_no_credits_is_refused(company):
    session = {
        "id": "cs_bad",
        "customer": "cus_live",
        "metadata": {"company_id": company.id, "purpose": "topup", "credits": "0"},
    }

    result = billing_webhooks.handle_event(_event("checkout.session.completed", session))

    assert "ignored" in result
    assert _balance(company.id) == 0


# ---------------------------------------------------------------------------
# Referral conversion
# ---------------------------------------------------------------------------


def test_the_referrer_is_paid_on_the_friends_first_invoice(company):
    """Not on signup and not on card entry — a card can be added and
    abandoned, and virtual cards make that free."""
    referrer = seed_company(user_id="u-ref", slug="ref-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-ref", invitee_email="f@x.com"
    )
    referrals.claim_on_signup(code=invite["code"], invitee_company_id=company.id)

    assert _balance(referrer) == 0  # signup alone pays nothing

    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))

    assert _balance(referrer) == plans.REFERRAL_REWARD_CREDITS


def test_the_referrer_is_paid_only_once(company):
    referrer = seed_company(user_id="u-ref2", slug="ref2-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-ref2", invitee_email="g@x.com"
    )
    referrals.claim_on_signup(code=invite["code"], invitee_company_id=company.id)

    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    billing_webhooks.handle_event(
        _event(
            "invoice.paid",
            _invoice(created=1_789_600_000, period_start=1_789_600_000),
            event_id="evt_r2",
        )
    )

    assert _balance(referrer) == plans.REFERRAL_REWARD_CREDITS


# ---------------------------------------------------------------------------
# Replay guard + unknown events
# ---------------------------------------------------------------------------


def test_claiming_an_event_twice_reports_the_replay():
    assert billing_db.claim_stripe_event("evt_x", "invoice.paid") is True
    assert billing_db.claim_stripe_event("evt_x", "invoice.paid") is False


@pytest.mark.parametrize(
    "event_type",
    ["invoice.upcoming", "customer.created", "payment_intent.succeeded", ""],
)
def test_unhandled_events_are_quiet_no_ops(event_type, company):
    result = billing_webhooks.handle_event(_event(event_type, {"customer": "cus_live"}))
    assert result.startswith("ignored")
    assert _row(company.id).get("subscription_status") is None


def test_handled_event_set_matches_the_dispatch_table(company):
    """A type listed as handled but not dispatched would silently do nothing."""
    for event_type in billing_webhooks.HANDLED_EVENTS:
        obj = {"id": "sub_1", "customer": "cus_live", "metadata": {}}
        result = billing_webhooks.handle_event(_event(event_type, obj))
        assert not result.startswith(f"ignored: {event_type}")


def test_ledger_records_every_grant_with_a_reason(company):
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    rows = (
        require_client()
        .table("credit_ledger")
        .select("reason")
        .eq("company_id", company.id)
        .execute()
        .data
    )
    assert [r["reason"] for r in rows] == ["monthly_grant"]


# ---------------------------------------------------------------------------
# Referral claiming
# ---------------------------------------------------------------------------


def test_an_unknown_code_claims_nothing():
    cid = seed_company(user_id="u-unk", slug="unk-co")
    assert referrals.claim_on_signup(code="not-a-real-code", invitee_company_id=cid) is None


def test_a_code_can_only_be_claimed_once():
    """Otherwise one invite link pays for every company that opens it."""
    referrer = seed_company(user_id="u-once", slug="once-co")
    first = seed_company(user_id="u-first", slug="first-co")
    second = seed_company(user_id="u-second", slug="second-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-once", invitee_email="a@x.com"
    )

    assert referrals.claim_on_signup(code=invite["code"], invitee_company_id=first)
    assert referrals.claim_on_signup(code=invite["code"], invitee_company_id=second) is None


def test_self_referral_is_voided_and_never_pays(company):
    """Inviting yourself back into your own company is the cheapest possible
    abuse. The remaining hole — one person running two companies under two
    addresses — is bounded at three invites and gated on a real payment."""
    referrer = seed_company(user_id="u-self", slug="self-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-self", invitee_email="me@x.com"
    )

    assert referrals.claim_on_signup(code=invite["code"], invitee_company_id=referrer) is None

    rows = referrals.list_for_company(referrer)
    assert rows[0]["status"] == referrals.VOID
    assert _balance(referrer) == 0


def test_a_voided_invite_does_not_cost_the_user_one_of_their_three():
    referrer = seed_company(user_id="u-cap", slug="cap-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-cap", invitee_email="v@x.com"
    )
    referrals.claim_on_signup(code=invite["code"], invitee_company_id=referrer)

    assert referrals.remaining_invites(referrer) == plans.MAX_REFERRAL_INVITES


def test_signing_up_does_not_consume_a_further_invite():
    referrer = seed_company(user_id="u-cap2", slug="cap2-co")
    invitee = seed_company(user_id="u-inv", slug="inv-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-cap2", invitee_email="w@x.com"
    )
    referrals.claim_on_signup(code=invite["code"], invitee_company_id=invitee)

    assert referrals.remaining_invites(referrer) == plans.MAX_REFERRAL_INVITES - 1

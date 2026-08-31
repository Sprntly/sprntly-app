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

from datetime import datetime

import pytest

from app.billing import credits, plans, referrals
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
    """The period that matters is the SUBSCRIPTION's, not the invoice's.

    This used to advance only the invoice's `period_start`, because the grant
    was keyed on that. It is keyed on the subscription now — a trial pays no
    invoice, so an invoice period is not something every live subscription
    has — and this rolls the subscription forward the way Stripe actually
    does before invoicing it."""
    billing_webhooks.handle_event(_event("invoice.paid", _invoice()))
    from app.billing import credits

    credits.spend(company.id, "prd", ref_id="job-2")

    company.sub["current_period_start"] = 1_789_600_000
    company.sub["current_period_end"] = 1_792_200_000
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

def test_signing_up_does_not_consume_a_further_invite():
    referrer = seed_company(user_id="u-cap2", slug="cap2-co")
    invitee = seed_company(user_id="u-inv", slug="inv-co")
    invite = referrals.create_invite(
        referrer_company_id=referrer, referrer_user_id="u-cap2", invitee_email="w@x.com"
    )
    referrals.claim_on_signup(code=invite["code"], invitee_company_id=invitee)

    # No cap any more, so "remaining" is unbounded — the assertion that matters
    # is that signing up did not VOID or consume the invite.
    assert referrals.remaining_invites(referrer) is None


# ---------------------------------------------------------------------------
# A stale cancellation must not revoke a live subscription
# ---------------------------------------------------------------------------


def test_a_cancel_for_a_superseded_subscription_is_ignored(company):
    """Cancel, resubscribe, and the OLD subscription's `deleted` event is still
    in flight. Stripe does not order deliveries, so it routinely lands after the
    new subscription's events.

    Observed in testing: four events put the company on an active plan, then a
    stale delete two seconds later turned it off — revoking access for someone
    who had just paid.
    """
    # The company is on the NEW subscription.
    billing_db.set_billing(
        company.id,
        {"stripe_subscription_id": "sub_NEW", "subscription_status": "active"},
    )

    # The OLD one's cancellation arrives late.
    billing_webhooks.handle_event(
        _event(
            "customer.subscription.deleted",
            {"id": "sub_OLD", "customer": "cus_live"},
        )
    )

    row = _row(company.id)
    assert row["subscription_status"] == "active"
    assert row["stripe_subscription_id"] == "sub_NEW"


def test_a_cancel_for_the_current_subscription_still_revokes(company):
    """The guard must not swallow the case it exists to permit."""
    billing_db.set_billing(
        company.id,
        {"stripe_subscription_id": "sub_NEW", "subscription_status": "active"},
    )

    billing_webhooks.handle_event(
        _event(
            "customer.subscription.deleted",
            {"id": "sub_NEW", "customer": "cus_live"},
        )
    )

    assert _row(company.id)["subscription_status"] == "canceled"


def test_a_cancel_before_any_subscription_is_recorded_still_applies(company):
    """No stored id means nothing to compare against — a company that never
    completed checkout but has a dead subscription should still read as
    cancelled rather than silently staying blank."""
    billing_db.set_billing(company.id, {"stripe_subscription_id": None})

    billing_webhooks.handle_event(
        _event("customer.subscription.deleted", {"id": "sub_X", "customer": "cus_live"})
    )

    assert _row(company.id)["subscription_status"] == "canceled"


# ---------------------------------------------------------------------------
# A trial gets its credits
# ---------------------------------------------------------------------------
#
# Payment moved to the front of onboarding behind a trial, and that broke an
# assumption this file was built on: that a live subscription has paid an
# invoice. A free trial has not. Stripe's trial docs are explicit — a free
# trial sits in `trialing` and emits `customer.subscription.*`; the real
# invoice, and `invoice.paid` with it, arrives only when the trial ENDS.
#
# Granting on the invoice alone therefore gave a trialling company a plan, a
# countdown, and a balance of zero: seven days of a product that refuses every
# generation, which is worse than offering no trial at all.


def _trialing(company, *, period_start=1_789_000_000, period_end=1_790_000_000):
    company.sub["status"] = "trialing"
    company.sub["current_period_start"] = period_start
    company.sub["current_period_end"] = period_end


def test_a_trialing_subscription_is_granted_its_credits(company):
    _trialing(company)

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    row = _row(company.id)
    assert row["subscription_status"] == "trialing"
    # The TRIAL allowance, not the plan's. Seven days that hand over a full
    # month of Product Builder credits is a month of product for free, and
    # the ones who cost the most are the ones who would take it and cancel
    # on day six — see plans.TRIAL_CREDITS.
    assert row["credit_balance"] == plans.TRIAL_CREDITS


def test_the_trial_grant_does_not_pretend_the_company_has_paid(company):
    """`first_paid_at` is what decides whether a LATER checkout gets a trial.
    Setting it here would mean cancelling and resubscribing during the trial
    silently charged on day one."""
    _trialing(company)

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id).get("first_paid_at") in (None, "")


def test_a_replayed_subscription_event_does_not_grant_twice(company):
    # Stripe delivers at least once, and `customer.subscription.updated` fires
    # for changes that have nothing to do with the period.
    _trialing(company)
    for event_id in ("evt_a", "evt_b", "evt_c"):
        billing_webhooks.handle_event(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "customer": "cus_live"},
                event_id=event_id,
            )
        )

    assert _row(company.id)["credit_balance"] == plans.TRIAL_CREDITS


def test_the_invoice_does_not_re_grant_the_period_the_subscription_already_did(company):
    """Both doors lead to the same grant; whichever arrives first for a period
    does the work and the other is a no-op. Otherwise a trial that converts
    would be granted twice for one period."""
    _trialing(company)
    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )
    granted_for = _row(company.id)["credits_granted_for"]

    # Spend some of it, then replay the paid invoice for the SAME period.
    credits.spend(company.id, "prd", ref_id="job-a")
    spent = _row(company.id)["credit_balance"]

    billing_webhooks.handle_event(
        _event(
            "invoice.paid",
            {
                "id": "in_1",
                "customer": "cus_live",
                "created": 1_789_000_100,
                "subscription": "sub_1",
                "period_start": int(
                    datetime.fromisoformat(granted_for).timestamp()
                ),
            },
            event_id="evt_inv",
        )
    )

    # The balance is untouched — the spend is not refunded by a second grant.
    assert _row(company.id)["credit_balance"] == spent


def test_the_period_after_a_trial_grants_again(company):
    """At trial end the period rolls over. That IS a new grant."""
    _trialing(company)
    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )
    credits.spend(company.id, "prd", ref_id="job-b")

    # Trial converts: new period, now paying.
    company.sub["status"] = "active"
    company.sub["current_period_start"] = 1_790_000_000
    company.sub["current_period_end"] = 1_792_600_000
    billing_webhooks.handle_event(
        _event(
            "customer.subscription.updated",
            {"id": "sub_1", "customer": "cus_live"},
            event_id="evt_next",
        )
    )

    assert _row(company.id)["credit_balance"] == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]


def test_a_dead_subscription_is_granted_nothing(company):
    for status in ("canceled", "unpaid", "incomplete"):
        company.sub["status"] = status
        company.sub["current_period_start"] = 1_789_000_000
        billing_db.set_billing(company.id, {"credit_balance": 0, "credits_granted_for": None})

        billing_webhooks.handle_event(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "customer": "cus_live"},
                event_id=f"evt_{status}",
            )
        )

        assert _row(company.id)["credit_balance"] == 0, status


def test_the_period_is_read_off_the_item_when_stripe_moved_it(company):
    """`current_period_start` moved onto the subscription ITEM in recent API
    versions, the same move `current_period_end` made. Reading only the
    top-level field would leave a trialling company on zero credits on any
    account whose API version has moved."""
    company.sub["status"] = "trialing"
    company.sub.pop("current_period_start", None)
    company.sub.pop("current_period_end", None)
    company.sub["items"] = {
        "data": [
            {
                "price": {"id": "price_pb_monthly"},
                "current_period_start": 1_789_000_000,
                "current_period_end": 1_790_000_000,
            }
        ]
    }

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id)["credit_balance"] == plans.TRIAL_CREDITS


# ---------------------------------------------------------------------------
# Subscription history
# ---------------------------------------------------------------------------
#
# `companies` carries only the CURRENT state and every webhook overwrites it,
# so the database could say "Starter, active, today" and nothing more — not
# that they were on Product Builder last month, not that they cancelled and
# came back. Stripe's dashboard had that history; we did not.


def _events(company_id: str) -> list[dict]:
    return billing_db.list_subscription_events(company_id)


def test_a_first_subscription_starts_the_history(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    rows = _events(company.id)
    assert len(rows) == 1
    assert rows[0]["plan"] == plans.PRODUCT_BUILDER
    assert rows[0]["status"] == "active"
    # Null previous: this is where their billing history begins, and the null
    # is the signal that says so.
    assert rows[0]["previous_status"] is None


def test_one_purchase_writes_ONE_row_not_three(company):
    """A single purchase produces three webhooks and each re-syncs the
    subscription. Logging every sync would bury one real transition under two
    identical rows and make the table useless for the questions it exists to
    answer."""
    for i, kind in enumerate(
        ("invoice.paid", "customer.subscription.created", "checkout.session.completed")
    ):
        obj = (
            {"id": "in_1", "customer": "cus_live", "created": 1_787_000_000,
             "parent": {"subscription_details": {"subscription": "sub_1"}}}
            if kind == "invoice.paid"
            else {"id": "cs_1", "customer": "cus_live", "subscription": "sub_1"}
            if kind == "checkout.session.completed"
            else {"id": "sub_1", "customer": "cus_live"}
        )
        billing_webhooks.handle_event(_event(kind, obj, event_id=f"evt_{i}"))

    assert len(_events(company.id)) == 1


def test_a_real_transition_is_recorded_with_what_it_came_from(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )
    company.sub["status"] = "past_due"
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"},
               event_id="evt_2")
    )

    rows = _events(company.id)          # newest first
    assert len(rows) == 2
    assert rows[0]["status"] == "past_due"
    assert rows[0]["previous_status"] == "active"
    assert rows[0]["plan"] == rows[0]["previous_plan"]      # plan did not move


def test_the_row_names_which_door_it_arrived_through(company):
    """'How did we learn this?' is the first question asked when two records
    disagree — a pushed webhook, the pull reconcile, or an in-app switch."""
    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )
    assert _events(company.id)[0]["source"] == "customer.subscription.created"


def test_the_reconcile_path_labels_itself(company, monkeypatch):
    monkeypatch.setattr(billing_webhooks.stripe_client, "configured", lambda: True)
    monkeypatch.setattr(
        billing_webhooks.stripe_client,
        "latest_subscription_for_customer",
        lambda _c: {"id": "sub_1"},
    )
    billing_webhooks.reconcile_from_stripe(company.id)

    assert _events(company.id)[0]["source"] == "reconcile"


def test_a_cancellation_is_history_too(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )
    company.sub["status"] = "canceled"
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"},
               event_id="evt_x")
    )

    rows = _events(company.id)
    assert rows[0]["status"] == "canceled"
    assert rows[0]["previous_status"] == "active"


def test_recording_history_never_raises(company, monkeypatch):
    """BEST EFFORT on purpose. Losing an audit line is bad; refusing someone's
    paid-for access because we could not write one is worse.

    Tested on the function directly rather than through a webhook: if this can
    never raise, every caller is safe by construction, and there is no way to
    break only the history insert mid-sync without proxying the client and
    testing the proxy instead of the guard."""

    class EverythingIsBroken:
        def table(self, *_a, **_k):
            raise RuntimeError("history table is having a bad day")

    monkeypatch.setattr(billing_db, "require_client", EverythingIsBroken)

    # No exception, no return value, no drama.
    assert (
        billing_db.record_subscription_event(
            company.id,
            plan=plans.STARTER,
            status="active",
            previous_plan=None,
            previous_status=None,
        )
        is None
    )


# ---------------------------------------------------------------------------
# An upgrade mid-period pays the difference in credits too
# ---------------------------------------------------------------------------
#
# Credits are granted per BILLING PERIOD, and a plan change does not start one:
# same subscription, same period end, so the period grant is skipped. The
# customer was charged a prorated difference by Stripe and kept the smaller
# allowance until renewal — paying Product Builder money to hold a Starter
# balance. Found on a real upgrade, 2026-08-29.


def _on_starter(company, *, spent: int = 0):
    """A company mid-period on Starter with its allowance granted."""
    billing_db.set_billing(
        company.id, {"plan": plans.STARTER, "subscription_status": "active"}
    )
    credits.grant_monthly(company.id, plans.STARTER, period_start="2026-09-21T14:13:20+00:00")
    billing_db.set_billing(company.id, {"credits_granted_for": "2026-09-21T14:13:20+00:00"})
    if spent:
        # `grant` refuses a negative amount by design; spending goes through
        # `spend`, which charges a feature's real price.
        for i in range(spent // plans.CREDIT_COSTS["prd"]):
            credits.spend(company.id, "prd", ref_id=f"seed-spend-{i}")
    company.sub["metadata"] = {"company_id": company.id, "plan": plans.PRODUCT_BUILDER}


def test_upgrading_tops_up_by_the_DIFFERENCE(company):
    _on_starter(company)
    starter, pb = plans.PLAN_CREDITS[plans.STARTER], plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id)["plan"] == plans.PRODUCT_BUILDER
    assert _balance(company.id) == pb


def test_the_uplift_preserves_what_was_already_spent(company):
    """Adding the difference, not setting the new figure. Setting it would
    quietly refund a month's usage."""
    _on_starter(company, spent=100)
    spent_before = plans.PLAN_CREDITS[plans.STARTER] - _balance(company.id)
    assert spent_before > 0

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _balance(company.id) == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER] - spent_before


def test_a_redelivered_event_does_not_grant_the_uplift_twice(company):
    _on_starter(company)

    for i in range(3):
        billing_webhooks.handle_event(
            _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"},
                   event_id=f"evt_up_{i}")
        )

    assert _balance(company.id) == plans.PLAN_CREDITS[plans.PRODUCT_BUILDER]


def test_a_DOWNGRADE_takes_nothing_back(company):
    """They have paid for this month and may have spent it. The smaller
    allowance applies from the next period, which the ordinary grant handles.
    Clawing back credits someone bought is how you earn a chargeback."""
    billing_db.set_billing(
        company.id, {"plan": plans.PRODUCT_BUILDER, "subscription_status": "active"}
    )
    credits.grant_monthly(company.id, plans.PRODUCT_BUILDER, period_start="2026-09-21T14:13:20+00:00")
    billing_db.set_billing(company.id, {"credits_granted_for": "2026-09-21T14:13:20+00:00"})
    before = _balance(company.id)
    company.sub["metadata"] = {"company_id": company.id, "plan": plans.STARTER}

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id)["plan"] == plans.STARTER
    assert _balance(company.id) == before


def test_no_uplift_when_the_plan_did_not_move(company):
    _on_starter(company)
    company.sub["metadata"] = {"company_id": company.id, "plan": plans.STARTER}
    before = _balance(company.id)

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _balance(company.id) == before


def test_no_uplift_onto_an_unlimited_plan(company):
    """LEGACY and ENTERPRISE are not metered; an 'uplift' against UNLIMITED
    would be arithmetic on a sentinel."""
    _on_starter(company)
    company.sub["metadata"] = {"company_id": company.id, "plan": plans.ENTERPRISE}

    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _row(company.id)["plan"] == plans.ENTERPRISE


# ---------------------------------------------------------------------------
# The money record — one row per invoice actually paid
# ---------------------------------------------------------------------------
#
# `subscription_events` answers "when did I move tier". Nobody opens billing to
# ask that. They ask what they have been charged, when, and for what — and
# nothing in this database could answer it: `credit_ledger` deals only in
# credits and `companies` holds one current period.


def _paid_invoice(**over) -> dict:
    inv = {
        "id": "in_paid_1",
        "customer": "cus_live",
        "created": 1_787_000_000,
        # Degenerate on purpose: this is what Stripe actually sends on a
        # subscription's first invoice, and reading it was the bug.
        "period_start": 1_787_000_000,
        "period_end": 1_787_000_000,
        "amount_paid": 5900,
        "total": 5900,
        "currency": "USD",
        "status": "paid",
        "number": "SPR-0001",
        "hosted_invoice_url": "https://invoice.stripe.test/i/abc",
        "invoice_pdf": "https://invoice.stripe.test/i/abc.pdf",
        "status_transitions": {"paid_at": 1_787_000_050},
        # A real subscription invoice carries the SERVICE period on its line,
        # not on the invoice. The invoice-level pair below is deliberately
        # degenerate — both the same instant — exactly as Stripe sends it on a
        # first invoice.
        "lines": {
            "data": [
                {
                    "proration": False,
                    "period": {"start": 1_787_000_000, "end": 1_789_600_000},
                }
            ]
        },
        "parent": {"subscription_details": {"subscription": "sub_1"}},
    }
    inv.update(over)
    return inv


def test_a_paid_invoice_is_recorded_in_MONEY(company):
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))

    rows = billing_db.list_invoices(company.id)
    assert len(rows) == 1
    row = rows[0]
    # Minor units, exactly as Stripe reports them — storing dollars as a float
    # is how money quietly goes missing.
    assert row["amount_paid_cents"] == 5900
    assert row["currency"] == "usd"
    assert row["status"] == "paid"
    assert row["invoice_number"] == "SPR-0001"


def test_it_keeps_the_period_the_invoice_COVERS(company):
    """What a reader wants beside the amount is the service period, not the
    moment we happened to receive the webhook."""
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))

    row = billing_db.list_invoices(company.id)[0]
    assert row["period_start"] and row["period_end"]
    assert row["period_start"] < row["period_end"]


def test_it_keeps_stripes_own_pdf_link(company):
    """Stripe already renders the PDF and hosts the receipt page, so "download
    invoice" needs no document generator of ours."""
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))

    row = billing_db.list_invoices(company.id)[0]
    assert row["invoice_pdf_url"].endswith(".pdf")
    assert row["hosted_invoice_url"]


def test_a_redelivered_invoice_does_not_appear_twice(company):
    """Stripe retries for days, and two events can describe one payment. The
    guard is the INVOICE id, not the event id — otherwise a customer sees the
    same charge twice on a page they read as their receipts."""
    for i in range(3):
        billing_webhooks.handle_event(
            _event("invoice.paid", _paid_invoice(), event_id=f"evt_dup_{i}")
        )

    assert len(billing_db.list_invoices(company.id)) == 1


def test_each_month_adds_a_row(company):
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))
    company.sub["current_period_start"] = 1_789_600_000
    company.sub["current_period_end"] = 1_792_200_000
    billing_webhooks.handle_event(
        _event(
            "invoice.paid",
            _paid_invoice(id="in_paid_2", number="SPR-0002", period_start=1_789_600_000),
            event_id="evt_m2",
        )
    )

    rows = billing_db.list_invoices(company.id)
    assert [r["invoice_number"] for r in rows] == ["SPR-0002", "SPR-0001"]  # newest first


def test_the_row_names_the_plan_the_invoice_was_FOR(company):
    """Resolved after the sync, so a later plan change cannot rewrite what an
    old invoice says it paid for."""
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))

    assert billing_db.list_invoices(company.id)[0]["plan"] == plans.PRODUCT_BUILDER


def test_a_failed_invoice_write_does_not_break_the_payment(company, monkeypatch):
    """Losing a receipt line is bad; refusing to record the payment itself
    would be worse."""
    class Broken:
        def table(self, *_a, **_k):
            raise RuntimeError("invoice table is having a bad day")

    monkeypatch.setattr(billing_db, "require_client", Broken)

    assert billing_db.record_invoice(company.id, {"stripe_invoice_id": "in_x"}) is None


# ---------------------------------------------------------------------------
# A cancellation is history too
# ---------------------------------------------------------------------------
#
# The deleted path returns BEFORE `_sync_subscription`, which is where every
# other transition is recorded — so cancellations were invisible in the
# history, and a cancellation is precisely the event a support question is
# about. Found live, 2026-08-29.


def test_a_cancellation_is_recorded_in_the_history(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )
    before = len(billing_db.list_subscription_events(company.id))

    billing_webhooks.handle_event(
        _event("customer.subscription.deleted", {"id": "sub_1", "customer": "cus_live"},
               event_id="evt_del")
    )

    rows = billing_db.list_subscription_events(company.id)
    assert len(rows) == before + 1
    assert rows[0]["status"] == "canceled"
    assert rows[0]["previous_status"] == "active"
    assert rows[0]["source"] == "customer.subscription.deleted"
    # The plan is kept, not blanked: it is the record of what they had.
    assert rows[0]["plan"] == plans.PRODUCT_BUILDER


def test_a_redelivered_cancellation_is_not_recorded_twice(company):
    billing_webhooks.handle_event(
        _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"})
    )
    for i in range(3):
        billing_webhooks.handle_event(
            _event("customer.subscription.deleted", {"id": "sub_1", "customer": "cus_live"},
                   event_id=f"evt_del_{i}")
        )

    cancels = [
        r for r in billing_db.list_subscription_events(company.id)
        if r["status"] == "canceled"
    ]
    assert len(cancels) == 1


# ---------------------------------------------------------------------------
# A referral converts when the invitee SUBSCRIBES
# ---------------------------------------------------------------------------
#
# It used to convert on `invoice.paid`. That stopped making sense when the
# trial arrived: the invitee's first charge is seven days after they subscribe,
# so a referrer who did everything right waited a week with no signal that it
# had worked. A card on file IS the conversion.


def _referred(company, monkeypatch):
    """`company` was invited by someone, and has signed up but not converted."""
    referral = {"id": "ref-1", "referrer_company_id": "referrer-co"}
    monkeypatch.setattr(
        billing_webhooks.referrals, "_pending_reward_for_invitee",
        lambda _cid: referral,
    )
    return referral


def test_subscribing_converts_the_referral(company, monkeypatch):
    paid = {}
    _referred(company, monkeypatch)
    monkeypatch.setattr(
        billing_webhooks.referrals.credits, "grant",
        lambda cid, amount, **kw: paid.update(company=cid, amount=amount, ref=kw.get("ref_id")),
    )
    monkeypatch.setattr(billing_webhooks.referrals, "_update", lambda *a, **k: None)

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert paid["company"] == "referrer-co"
    assert paid["amount"] == plans.REFERRAL_REWARD_CREDITS      # $10 at the top-up rate
    # Tied to the referral, which is what makes it idempotent and auditable.
    assert paid["ref"] == "ref-1"


def test_a_TRIAL_converts_it_too_because_the_card_is_on_file(company, monkeypatch):
    paid = {}
    _referred(company, monkeypatch)
    monkeypatch.setattr(
        billing_webhooks.referrals.credits, "grant",
        lambda cid, amount, **kw: paid.update(company=cid),
    )
    monkeypatch.setattr(billing_webhooks.referrals, "_update", lambda *a, **k: None)
    company.sub["status"] = "trialing"

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert paid.get("company") == "referrer-co"


def test_re_syncing_a_live_subscription_does_not_pay_again(company, monkeypatch):
    calls = []
    _referred(company, monkeypatch)
    monkeypatch.setattr(
        billing_webhooks.referrals.credits, "grant",
        lambda cid, amount, **kw: calls.append(cid),
    )
    monkeypatch.setattr(billing_webhooks.referrals, "_update", lambda *a, **k: None)

    for i in range(3):
        billing_webhooks.handle_event(
            _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"},
                   event_id=f"evt_sub_{i}")
        )

    # Only the transition INTO a live status pays.
    assert len(calls) == 1


def test_signing_up_without_subscribing_pays_nobody(company, monkeypatch):
    """An account with no card is not a conversion, and paying for one would
    make the programme free to farm."""
    calls = []
    _referred(company, monkeypatch)
    monkeypatch.setattr(
        billing_webhooks.referrals.credits, "grant",
        lambda cid, amount, **kw: calls.append(cid),
    )
    company.sub["status"] = "incomplete"          # never became live

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert calls == []


def test_there_is_no_cap_on_invites(company):
    """The invite count never bounded the cost — an unconverted invite pays
    nothing — so capping it capped the upside and not the downside."""
    assert plans.MAX_REFERRAL_INVITES is None
    assert billing_webhooks.referrals.remaining_invites(company.id) is None


def test_a_link_pays_the_referrer_when_the_friend_subscribes(company):
    """End to end on the new model: a permanent link, a company arriving
    through it, and the reward landing when that company subscribes — not when
    it signs up, and not a week later when its first invoice clears."""
    referrer = seed_company(user_id="u-ref", slug="ref-co")
    code = referrals.code_for_company(referrer)
    referrals.claim_on_signup(code=code, invitee_company_id=company.id)

    assert _balance(referrer) == 0          # arriving alone pays nothing

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    assert _balance(referrer) == plans.REFERRAL_REWARD_CREDITS


def test_the_referrer_is_paid_once_however_many_events_arrive(company):
    referrer = seed_company(user_id="u-ref2", slug="ref2-co")
    referrals.claim_on_signup(
        code=referrals.code_for_company(referrer), invitee_company_id=company.id
    )

    for i in range(3):
        billing_webhooks.handle_event(
            _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_live"},
                   event_id=f"evt_ref_{i}")
        )

    assert _balance(referrer) == plans.REFERRAL_REWARD_CREDITS


def test_one_link_pays_for_MANY_people(company, monkeypatch):
    """The point of a shareable link. The old per-address code was spent by the
    first person to use it."""
    referrer = seed_company(user_id="u-many", slug="many-co")
    code = referrals.code_for_company(referrer)
    other = seed_company(user_id="u-other", slug="other-co")

    assert referrals.claim_on_signup(code=code, invitee_company_id=company.id)
    assert referrals.claim_on_signup(code=code, invitee_company_id=other)
    assert len(referrals.list_for_company(referrer)) == 2


def test_your_own_link_never_pays_you():
    referrer = seed_company(user_id="u-self", slug="self-co")
    code = referrals.code_for_company(referrer)

    assert referrals.claim_on_signup(code=code, invitee_company_id=referrer) is None
    assert referrals.list_for_company(referrer) == []
    assert _balance(referrer) == 0


def test_the_reward_is_tied_to_the_referral_row(company):
    """Auditable and idempotent: the ledger entry names the referral it paid
    for, so 'why did we grant these 140 credits' has an answer."""
    referrer = seed_company(user_id="u-tie", slug="tie-co")
    ref = referrals.claim_on_signup(
        code=referrals.code_for_company(referrer), invitee_company_id=company.id
    )

    billing_webhooks.handle_event(
        _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_live"})
    )

    rows = [r for r in credits.history(referrer) if r["reason"] == "referral"]
    assert len(rows) == 1
    assert rows[0]["delta"] == plans.REFERRAL_REWARD_CREDITS
    assert referrals.list_for_company(referrer)[0]["status"] == referrals.REWARDED
    assert ref["id"]


def test_the_invoice_records_the_period_it_actually_COVERS(company):
    """REPORTED BUG: the billing screen showed "Aug 29, 2026 — Aug 29, 2026"
    against a month of service.

    `invoice.period_start` / `period_end` are not the service period. Stripe's
    reference says so plainly — they are the window in which invoice items may
    be ASSOCIATED with the invoice, and on a subscription's first invoice, made
    at the instant of signup, both collapse to that instant. The service period
    lives on the line item.
    """
    billing_webhooks.handle_event(_event("invoice.paid", _paid_invoice()))

    row = billing_db.list_invoices(company.id)[0]
    assert row["period_start"] != row["period_end"], "a month of service is not one instant"
    assert row["period_start"] < row["period_end"]


def test_a_plan_change_reports_the_SUBSCRIPTION_line_not_the_proration(company):
    """A switch produces both a proration line (the days remaining) and a
    recurring line (the month bought). Taking lines[0] blindly would report a
    fortnight where a month was paid for."""
    inv = _paid_invoice(
        id="in_switch",
        lines={
            "data": [
                {"proration": True, "period": {"start": 1_788_000_000, "end": 1_789_000_000}},
                {"proration": False, "period": {"start": 1_789_000_000, "end": 1_791_600_000}},
            ]
        },
    )

    billing_webhooks.handle_event(_event("invoice.paid", inv, event_id="evt_switch"))

    row = billing_db.list_invoices(company.id)[0]
    assert row["period_start"].startswith("2026-")
    # The recurring line's window, not the proration's.
    from datetime import datetime, timezone
    expected = datetime.fromtimestamp(1_789_000_000, timezone.utc).isoformat()
    assert row["period_start"] == expected


def test_an_invoice_with_no_lines_still_records_something(company):
    """Better a degenerate date than none, if a payload ever arrives without
    lines — the amount and the receipt still matter."""
    inv = _paid_invoice(id="in_nolines", lines={"data": []})

    billing_webhooks.handle_event(_event("invoice.paid", inv, event_id="evt_nolines"))

    row = billing_db.list_invoices(company.id)[0]
    assert row["period_start"] is not None

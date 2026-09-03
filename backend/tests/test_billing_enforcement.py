"""Credit enforcement at the generation surfaces.

Two things are being pinned. First, that the guard actually binds: an
out-of-credit or unsubscribed company is refused with 402 and a machine-
readable reason. Second — and this is the one that would hurt if it regressed —
that the whole mechanism is INERT unless `BILLING_ENFORCED` is set, so merging
it does not start refusing real customers on staging, which shares the
production database.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.billing import credits, enforce, plans
from app.config import settings
from app.db import billing as billing_db

from ._company_helpers import seed_company


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    return isolated_settings


@pytest.fixture
def broke():
    """A company on a paid plan with nothing left and no subscription."""
    cid = seed_company(user_id="u-enf", slug="enf-co")
    billing_db.set_billing(cid, {"plan": plans.STARTER})
    return cid


@pytest.fixture
def enforced(monkeypatch):
    # PAYMENTS HIDDEN: the fixture means "billing is switched on", and that is
    # BOTH halves of the switch. Flipping only the env flag would leave every
    # test below asserting against a module constant that is currently False —
    # the whole paywall would read as working while it was inert. The
    # payments-hidden default gets its own test instead, at the top of the file.
    monkeypatch.setattr(plans, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "billing_enforced", True)


# ---------------------------------------------------------------------------
# The default: off
# ---------------------------------------------------------------------------


def test_payments_hidden_refuses_nothing_even_with_the_env_flag_on(broke, monkeypatch):
    """The payments-hidden switch outranks the environment, deliberately.

    `BILLING_ENFORCED` is an env var and `plans.BILLING_ENABLED` is not, which
    is the whole point: a stray `BILLING_ENFORCED=1` on a box running this
    branch would otherwise 402 every generation while the web app shows no
    trial, no countdown and no plan step to act on it — a wall with no door.
    """
    monkeypatch.setattr(settings, "billing_enforced", True)
    billing_db.set_billing(broke, {"subscription_status": "canceled"})

    assert plans.BILLING_ENABLED is False
    enforce.bill(broke, "prd")  # must not raise
    assert credits.balance(broke) == 0  # and must not debit


def test_enforcement_is_off_by_default(broke):
    """The single most important test in this file.

    `BILLING_ENFORCED` defaults to false, so merging the paywall changes
    nothing for anyone until it is deliberately switched on.
    """
    assert settings.billing_enforced is False
    enforce.bill(broke, "prd")  # must not raise
    assert credits.balance(broke) == 0  # and must not debit


def test_nothing_is_debited_while_enforcement_is_off(broke):
    billing_db.set_billing(broke, {"subscription_status": "active"})
    credits.grant(broke, 500, reason="monthly_grant", ref_id="p1")

    enforce.bill(broke, "prd")

    assert credits.balance(broke) == 500


# ---------------------------------------------------------------------------
# Switched on
# ---------------------------------------------------------------------------


def test_an_unsubscribed_company_is_refused(broke, enforced):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        enforce.bill(broke, "prd")

    assert excinfo.value.status_code == 402
    assert excinfo.value.detail["error"] == "subscription_inactive"


def test_an_out_of_credit_company_is_refused_with_the_numbers(broke, enforced):
    """The detail carries needed/balance so the UI can say "this costs 25, you
    have 8" and offer the top-up, rather than a generic failure."""
    from fastapi import HTTPException

    billing_db.set_billing(broke, {"subscription_status": "active"})
    credits.grant(broke, 8, reason="adjustment", ref_id="a1")

    with pytest.raises(HTTPException) as excinfo:
        enforce.bill(broke, "prd")

    detail = excinfo.value.detail
    assert excinfo.value.status_code == 402
    assert detail["error"] == "insufficient_credits"
    assert detail["needed"] == plans.CREDIT_COSTS["prd"]
    assert detail["balance"] == 8


def test_a_cancelled_company_cannot_spend_leftover_credits(broke, enforced):
    """Credits are not a hoard that survives cancellation."""
    from fastapi import HTTPException

    billing_db.set_billing(broke, {"subscription_status": "canceled"})
    credits.grant(broke, 500, reason="monthly_grant", ref_id="p2")

    with pytest.raises(HTTPException) as excinfo:
        enforce.bill(broke, "prd")

    assert excinfo.value.detail["error"] == "subscription_inactive"


def test_past_due_still_generates(broke, enforced):
    """Smart Retries work a declined card for days; cutting the customer off on
    the first decline turns a bounced card into churn."""
    billing_db.set_billing(broke, {"subscription_status": "past_due"})
    credits.grant(broke, 500, reason="monthly_grant", ref_id="p3")

    enforce.bill(broke, "prd")

    assert credits.balance(broke) == 500 - plans.CREDIT_COSTS["prd"]


def test_a_paid_company_is_debited_the_action_price(broke, enforced):
    billing_db.set_billing(broke, {"subscription_status": "active"})
    credits.grant(broke, 500, reason="monthly_grant", ref_id="p4")

    enforce.bill(broke, "chat", actor_user_id="u-enf")

    assert credits.balance(broke) == 500 - plans.CREDIT_COSTS["chat"]


def test_unlimited_plans_are_never_refused_or_debited(enforced):
    """Legacy and Enterprise carry no Stripe subscription at all. Gating them
    would lock out every pre-billing tenant the day enforcement goes on."""
    cid = seed_company(user_id="u-leg", slug="leg-co")
    billing_db.set_billing(cid, {"plan": plans.LEGACY})

    enforce.bill(cid, "prototype")

    assert credits.balance(cid) == plans.UNLIMITED


def test_charge_never_raises_even_when_the_balance_moved(broke, enforced, monkeypatch):
    """A failure at debit time is a database problem, not an affordability one.
    Taking down a generation the user is already waiting on to report a billing
    error would be the wrong trade."""

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(credits, "spend", _boom)
    enforce.charge(broke, "prd")  # must not raise


def test_an_overdraft_race_is_allowed_through(broke, enforced, monkeypatch):
    """Two actions started at the same instant can both pass the pre-flight.
    The second is let through rather than failing work already in progress —
    the overdraft is at most one action deep."""

    def _insufficient(*a, **kw):
        raise credits.InsufficientCredits(needed=25, balance=0, feature="prd")

    monkeypatch.setattr(credits, "spend", _insufficient)
    enforce.charge(broke, "prd")  # must not raise


# ---------------------------------------------------------------------------
# Every priced feature is reachable
# ---------------------------------------------------------------------------


def test_every_wired_surface_has_a_price():
    """A surface billed under a name absent from CREDIT_COSTS silently falls
    back to the default rate. These are the names passed at the call sites."""
    wired = {
        "ask",
        "prd",
        "evidence",
        "multi_agent",
        "chat",
        "crucible",
        "prototype",
        "prototype_iterate",
    }
    assert wired <= set(plans.CREDIT_COSTS)


# ---------------------------------------------------------------------------
# Chat survives a lapsed subscription (owner decision, 2026-08-28)
# ---------------------------------------------------------------------------
#
# Under `subscription_lock_mode = "read_only"`, chat keeps working after the
# subscription ends — with or without a credit balance. Everything else stops.
# It is a retention allowance out of our own margin: a chat turn costs about
# $0.004 against real usage, and someone who can still talk to the product
# while sorting their card out is a customer who might come back.


@pytest.fixture
def read_only(monkeypatch, isolated_settings):
    monkeypatch.setattr(plans, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setattr(settings, "subscription_lock_mode", "read_only")


def test_chat_survives_a_cancelled_subscription(read_only):
    cid = seed_company(user_id="u-ro", slug="ro-co")
    billing_db.set_billing(cid, {"subscription_status": "canceled"})

    enforce.require_credits(cid, "chat")   # must not raise


def test_chat_survives_a_zero_balance(read_only):
    """`whether you have token credit or not` — the affordability check is
    waived too, not just the subscription check."""
    cid = seed_company(user_id="u-ro2", slug="ro-co2")
    billing_db.set_billing(cid, {"subscription_status": "active"})

    enforce.require_credits(cid, "chat")


def test_a_customer_spends_what_they_BOUGHT_before_chatting_on_us(read_only):
    """Exempt from the gate, not from the meter. `charge` still debits, so a
    leftover balance is consumed first and only then is chat on our margin."""
    cid = seed_company(user_id="u-ro3", slug="ro-co3")
    billing_db.set_billing(cid, {"subscription_status": "canceled"})
    credits.grant(cid, 40, reason="topup", ref_id="seed")

    enforce.bill(cid, "chat")

    assert credits.balance(cid) == 40 - plans.CREDIT_COSTS["chat"]


def test_nothing_ELSE_survives_a_cancelled_subscription(read_only):
    cid = seed_company(user_id="u-ro4", slug="ro-co4")
    billing_db.set_billing(cid, {"subscription_status": "canceled"})
    credits.grant(cid, 5_000, reason="topup", ref_id="seed4")

    for feature in ("prd", "ask", "evidence", "prototype", "multi_agent"):
        with pytest.raises(HTTPException) as exc:
            enforce.require_credits(cid, feature)
        assert exc.value.status_code == 402, feature


def test_the_exemption_does_not_apply_in_hard_mode(monkeypatch, isolated_settings):
    """`hard` routes the whole app to Billing; an open chat box would
    contradict the lock."""
    monkeypatch.setattr(plans, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setattr(settings, "subscription_lock_mode", "hard")
    cid = seed_company(user_id="u-hard", slug="hard-co")
    billing_db.set_billing(cid, {"subscription_status": "canceled"})

    with pytest.raises(HTTPException):
        enforce.require_credits(cid, "chat")


def test_the_exemption_does_not_apply_when_the_lock_is_off(monkeypatch, isolated_settings):
    monkeypatch.setattr(plans, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setattr(settings, "subscription_lock_mode", "off")
    cid = seed_company(user_id="u-off", slug="off-co")
    billing_db.set_billing(cid, {"subscription_status": "canceled"})

    with pytest.raises(HTTPException):
        enforce.require_credits(cid, "chat")

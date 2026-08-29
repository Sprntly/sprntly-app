"""The two surfaces that close the loop on billing.

1. `POST /v1/billing/referrals/claim` — companies are created client-side
   through Supabase, so this is the only moment the backend learns a new tenant
   exists and can attribute it to whoever referred them. Without it a referral
   can never convert and the whole feature is decorative.

2. The staff billing surface — refunds are staff-approved (owner decision), so
   "a person decides" has to be an endpoint a person can actually reach.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.billing import credits, plans, referrals, stripe_client
from app.db import billing as billing_db
from app.db.client import require_client

from ._company_helpers import seed_company, setup_supabase_auth, supabase_bearer

STAFF_ID = "staff-test"
STAFF_PASSWORD = "staff-password-123"


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    return isolated_settings


def _client(monkeypatch, *, slug: str) -> SimpleNamespace:
    from tests.conftest import reload_app_layer

    setup_supabase_auth(monkeypatch)
    reload_app_layer()
    import app.main as main_mod

    user_id = "u-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug=slug)
    client = TestClient(main_mod.app, headers=supabase_bearer(user_id))
    return SimpleNamespace(client=client, company_id=company_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Referral claim
# ---------------------------------------------------------------------------


@pytest.fixture
def invitee(monkeypatch):
    return _client(monkeypatch, slug="invitee-co")


def _link_code(referrer_company: str) -> str:
    """A company's permanent referral code. No address, no pre-minted invite —
    the code exists as soon as anyone asks for it and is shared with whoever."""
    return referrals.code_for_company(referrer_company)


def test_claiming_records_the_referrer_without_paying_yet(invitee):
    """Signing up is free and infinitely repeatable, so it must grant nothing.
    The reward lands when the friend SUBSCRIBES."""
    referrer = seed_company(user_id="u-r1", slug="r1-co")
    code = _link_code(referrer)

    res = invitee.client.post("/v1/billing/referrals/claim", json={"code": code})

    assert res.status_code == 200
    assert res.json()["claimed"] is True
    assert credits.balance(referrer) == 0
    assert referrals.list_for_company(referrer)[0]["status"] == referrals.SIGNED_UP


def test_an_unknown_code_is_a_quiet_no_op_not_an_error(invitee):
    """Onboarding calls this best-effort. A stale link must never block someone
    creating their workspace."""
    res = invitee.client.post("/v1/billing/referrals/claim", json={"code": "nope"})

    assert res.status_code == 200
    assert res.json()["claimed"] is False


def test_the_SAME_link_works_for_more_than_one_person(invitee, monkeypatch):
    """The whole point of a shareable link. The old model spent a code on the
    first person to use it, because it had been cut for one address."""
    referrer = seed_company(user_id="u-r2", slug="r2-co")
    code = _link_code(referrer)
    other = _client(monkeypatch, slug="other-co")

    first = invitee.client.post("/v1/billing/referrals/claim", json={"code": code})
    second = other.client.post("/v1/billing/referrals/claim", json={"code": code})

    assert first.json()["claimed"] is True
    assert second.json()["claimed"] is True
    assert len(referrals.list_for_company(referrer)) == 2


def test_one_company_cannot_arrive_through_a_link_twice(invitee):
    """Otherwise signing up, deleting the company and signing up again would
    pay the referrer twice."""
    referrer = seed_company(user_id="u-r3", slug="r3-co")
    code = _link_code(referrer)

    invitee.client.post("/v1/billing/referrals/claim", json={"code": code})
    second = invitee.client.post("/v1/billing/referrals/claim", json={"code": code})

    assert second.json()["claimed"] is False
    assert len(referrals.list_for_company(referrer)) == 1


def test_the_code_is_stable(invitee):
    """One code, forever — a link already shared must not stop working."""
    referrer = seed_company(user_id="u-r4", slug="r4-co")
    assert _link_code(referrer) == _link_code(referrer)


def test_your_own_link_does_not_pay_you(monkeypatch):
    """Signing up again through your own link. The remaining hole is one person
    running two companies under two addresses, which no in-app check can see —
    but it is gated on a real card, so the worst case costs them a subscription
    to earn $10 of credits."""
    me = _client(monkeypatch, slug="r5-co")
    code = _link_code(me.company_id)

    res = me.client.post("/v1/billing/referrals/claim", json={"code": code})

    assert res.json()["claimed"] is False
    assert referrals.list_for_company(me.company_id) == []


def test_only_the_owner_may_claim(invitee):
    referrer = seed_company(user_id="u-r3", slug="r3-co")
    code = _link_code(referrer)
    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", invitee.company_id
    ).eq("user_id", invitee.user_id).execute()

    res = invitee.client.post(
        "/v1/billing/referrals/claim", json={"code": code}
    )

    assert res.status_code == 403


def test_claiming_is_origin_gated(invitee):
    referrer = seed_company(user_id="u-r4", slug="r4-co")
    code = _link_code(referrer)

    res = invitee.client.post(
        "/v1/billing/referrals/claim",
        json={"code": code},
        headers={"origin": "https://evil.example"},
    )

    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Staff billing
# ---------------------------------------------------------------------------


@pytest.fixture
def staff(monkeypatch):
    """A staff-JWT client, plus a company with a live subscription."""
    from argon2 import PasswordHasher

    ctx = _client(monkeypatch, slug="staff-target")

    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "staff_admin_id", STAFF_ID)
    monkeypatch.setattr(
        auth_mod.settings,
        "staff_admin_password_hash",
        PasswordHasher().hash(STAFF_PASSWORD),
    )
    res = ctx.client.post(
        "/v1/staff/login", json={"id": STAFF_ID, "password": STAFF_PASSWORD}
    )
    assert res.status_code == 200, res.text
    ctx.client.headers["Authorization"] = f"Bearer {res.json()['token']}"

    billing_db.set_billing(
        ctx.company_id,
        {
            "plan": plans.STARTER,
            "stripe_customer_id": "cus_s",
            "stripe_subscription_id": "sub_s",
            "subscription_status": "active",
        },
    )
    monkeypatch.setattr(stripe_client, "configured", lambda: True)
    monkeypatch.setattr(
        stripe_client, "refund_latest_payment", lambda **kw: "re_test_1"
    )
    monkeypatch.setattr(stripe_client, "cancel_subscription", lambda sid: {"id": sid})
    return ctx


def test_staff_sees_how_much_was_consumed_before_deciding(staff):
    """The whole reason refunds are not automatic: a person needs to see this
    number before handing money back."""
    credits.grant(staff.company_id, 500, reason="monthly_grant", ref_id="p1")
    credits.spend(staff.company_id, "prd", ref_id="j1")

    body = staff.client.get(f"/v1/staff/companies/{staff.company_id}/billing").json()

    assert body["credits_used"] == plans.CREDIT_COSTS["prd"]
    assert body["monthly_credits"] == plans.PLAN_CREDITS[plans.STARTER]
    assert len(body["ledger"]) == 2


def test_refund_window_is_reported_from_the_first_payment(staff):
    from datetime import datetime, timedelta, timezone

    billing_db.set_billing(
        staff.company_id,
        {"first_paid_at": datetime.now(timezone.utc).isoformat()},
    )
    fresh = staff.client.get(f"/v1/staff/companies/{staff.company_id}/billing").json()
    assert fresh["within_refund_window"] is True

    billing_db.set_billing(
        staff.company_id,
        {
            "first_paid_at": (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()
        },
    )
    old = staff.client.get(f"/v1/staff/companies/{staff.company_id}/billing").json()
    assert old["within_refund_window"] is False


def test_a_company_that_never_paid_is_not_in_the_window(staff):
    body = staff.client.get(f"/v1/staff/companies/{staff.company_id}/billing").json()
    assert body["within_refund_window"] is False


def test_refunding_also_stops_the_service_by_default(staff):
    """Handing the money back while the subscription keeps running is almost
    never what is meant."""
    res = staff.client.post(
        f"/v1/staff/companies/{staff.company_id}/billing/refund", json={}
    )

    assert res.status_code == 200
    assert res.json()["refund_id"] == "re_test_1"
    assert res.json()["cancelled"] is True
    assert (billing_db.get_billing(staff.company_id) or {})[
        "subscription_status"
    ] == "canceled"


def test_a_refund_can_leave_the_subscription_running(staff):
    res = staff.client.post(
        f"/v1/staff/companies/{staff.company_id}/billing/refund",
        json={"cancel": False},
    )

    assert res.json()["cancelled"] is False
    assert (billing_db.get_billing(staff.company_id) or {})[
        "subscription_status"
    ] == "active"


def test_refunding_a_company_with_no_subscription_is_refused(staff):
    billing_db.set_billing(staff.company_id, {"stripe_subscription_id": None})
    res = staff.client.post(
        f"/v1/staff/companies/{staff.company_id}/billing/refund", json={}
    )
    assert res.status_code == 400


def test_staff_credit_grant_lands_in_the_ledger_with_a_reason(staff):
    """A balance that silently changed is a support ticket. The Billing screen
    reads this ledger, so the customer can see where the credits came from."""
    res = staff.client.post(
        f"/v1/staff/companies/{staff.company_id}/billing/credits",
        json={"credits": 250, "note": "failed prototype build"},
    )

    assert res.status_code == 200
    assert res.json()["credit_balance"] == 250
    assert credits.history(staff.company_id)[0]["reason"] == "adjustment"


@pytest.mark.parametrize("amount", [0, -50, 200_000])
def test_staff_credit_grant_rejects_nonsense_amounts(staff, amount):
    """Positive only — every real reason to REDUCE a balance already has a path
    that writes a ledger row explaining itself."""
    res = staff.client.post(
        f"/v1/staff/companies/{staff.company_id}/billing/credits",
        json={"credits": amount},
    )
    assert res.status_code == 422


def test_staff_billing_needs_a_staff_token(invitee):
    """A normal signed-in user must not reach the refund surface."""
    for path, method in (
        (f"/v1/staff/companies/{invitee.company_id}/billing", "get"),
        (f"/v1/staff/companies/{invitee.company_id}/billing/refund", "post"),
        (f"/v1/staff/companies/{invitee.company_id}/billing/credits", "post"),
    ):
        res = getattr(invitee.client, method)(
            path, **({"json": {"credits": 10}} if method == "post" else {})
        )
        assert res.status_code in (401, 403, 404), path


def test_unknown_company_is_404(staff):
    assert (
        staff.client.get("/v1/staff/companies/no-such-id/billing").status_code == 404
    )


def test_end_to_end_a_shared_link_pays_the_referrer(invitee, monkeypatch):
    """THE WHOLE CHAIN, in one test: a link is shared, someone signs up through
    it, they subscribe, and the referrer is paid.

    The pieces are covered individually elsewhere; this is the one that would
    catch them being wired together wrongly.
    """
    from app.billing import webhooks as billing_webhooks

    referrer = seed_company(user_id="u-e2e", slug="e2e-co")
    code = referrals.code_for_company(referrer)

    # 1. The URL the referrer shares carries this code; the web client stashes
    #    it and posts it once the invitee's company exists.
    claimed = invitee.client.post(
        "/v1/billing/referrals/claim", json={"code": code}
    )
    assert claimed.json()["claimed"] is True
    assert credits.balance(referrer) == 0, "arriving alone must pay nothing"

    # 2. The invitee subscribes. A trial counts — the card is on file.
    monkeypatch.setattr(
        billing_webhooks.stripe_client,
        "get_subscription",
        lambda _id: {
            "id": "sub_e2e",
            "status": "trialing",
            "customer": "cus_e2e",
            "metadata": {"company_id": invitee.company_id, "plan": plans.STARTER},
            "current_period_start": 1_789_000_000,
            "current_period_end": 1_790_000_000,
            "items": {"data": [{"price": {"id": "price_starter_monthly"}}]},
        },
    )
    billing_webhooks.handle_event(
        {
            "id": "evt_e2e",
            "type": "customer.subscription.created",
            "data": {"object": {"id": "sub_e2e", "customer": "cus_e2e",
                                "metadata": {"company_id": invitee.company_id}}},
        }
    )

    # 3. The referrer is paid, and the ledger says what for.
    assert credits.balance(referrer) == plans.REFERRAL_REWARD_CREDITS
    row = [r for r in credits.history(referrer) if r["reason"] == "referral"][0]
    assert row["delta"] == plans.REFERRAL_REWARD_CREDITS
    assert referrals.list_for_company(referrer)[0]["status"] == referrals.REWARDED

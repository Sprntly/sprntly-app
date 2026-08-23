"""Billing HTTP surface — gating, validation, and the webhook's exemptions.

The webhook is the interesting one. It is the only route in the app with no
tenant dependency and no Origin gate, because Stripe sends neither a session
nor an Origin header. Those absences are deliberate, so they are pinned here:
if someone later "tidies up" by adding `require_same_origin` to every mutating
route, these tests fail rather than production silently rejecting every Stripe
delivery.
"""
from __future__ import annotations

import pytest

from app.billing import plans, stripe_client
from app.db import billing as billing_db
from app.db.client import require_client

from ._company_helpers import seed_company, setup_supabase_auth, supabase_bearer


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    return isolated_settings


@pytest.fixture
def ctx(monkeypatch, isolated_settings):
    """An owner-authenticated client with Stripe fully stubbed.

    Builds the client here rather than reusing conftest's `company_client`,
    which composes on an `env` fixture each suite defines for itself.
    """
    import uuid
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from tests.conftest import reload_app_layer

    setup_supabase_auth(monkeypatch)
    reload_app_layer()

    import app.main as main_mod

    user_id = "billing-user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug="billing-co")
    client = TestClient(main_mod.app, headers=supabase_bearer(user_id))

    monkeypatch.setattr(stripe_client, "configured", lambda: True)
    monkeypatch.setattr(
        stripe_client, "ensure_customer", lambda **kw: "cus_test"
    )
    monkeypatch.setattr(
        stripe_client,
        "price_id",
        lambda plan, interval: f"price_{plan}_{interval}",
    )
    monkeypatch.setattr(
        stripe_client,
        "create_subscription_checkout",
        lambda **kw: "https://checkout.stripe.test/sub",
    )
    monkeypatch.setattr(
        stripe_client,
        "create_topup_checkout",
        lambda **kw: "https://checkout.stripe.test/topup",
    )
    monkeypatch.setattr(
        stripe_client,
        "create_portal_session",
        lambda **kw: "https://portal.stripe.test",
    )
    return SimpleNamespace(client=client, company_id=company_id, user_id=user_id)


def _set_role(company_id: str, user_id: str, role: str) -> None:
    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_reports_the_launch_default_plan_for_a_fresh_company(ctx):
    body = ctx.client.get("/v1/billing/summary").json()

    assert body["plan"] == plans.LAUNCH_DEFAULT_PLAN
    assert body["monthly_credits"] == plans.PLAN_CREDITS[plans.LAUNCH_DEFAULT_PLAN]
    assert body["credit_balance"] == 0
    assert body["refund_window_days"] == plans.REFUND_WINDOW_DAYS
    assert body["referral_invites_remaining"] == plans.MAX_REFERRAL_INVITES


def test_summary_renders_unlimited_as_null_not_as_the_sentinel(ctx):
    """`UNLIMITED` is -1 internally. Leaking that to the client would put
    "-1 credits" on the Billing screen."""
    billing_db.set_billing(ctx.company_id, {"plan": plans.ENTERPRISE})

    body = ctx.client.get("/v1/billing/summary").json()

    assert body["unlimited"] is True
    assert body["credit_balance"] is None
    assert body["monthly_credits"] is None


def test_summary_is_owner_or_admin_only(ctx):
    """What the company pays is commercially sensitive, same as the usage and
    Claude-key pages."""
    _set_role(ctx.company_id, ctx.user_id, "viewer")
    assert ctx.client.get("/v1/billing/summary").status_code == 403


def test_summary_needs_a_session():
    from fastapi.testclient import TestClient

    import app.main as main_mod

    anon = TestClient(main_mod.app)
    assert anon.get("/v1/billing/summary").status_code in (401, 403)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def test_checkout_returns_a_hosted_url(ctx):
    res = ctx.client.post(
        "/v1/billing/checkout", json={"plan": plans.STARTER, "interval": "monthly"}
    )

    assert res.status_code == 200
    assert res.json()["url"] == "https://checkout.stripe.test/sub"


def test_checkout_refuses_the_invoiced_tiers(ctx):
    """Team is invoiced and Enterprise goes through sales. Refusing loudly beats
    silently selling someone a Starter plan they did not choose."""
    for plan in (plans.TEAM, plans.ENTERPRISE):
        res = ctx.client.post("/v1/billing/checkout", json={"plan": plan})
        assert res.status_code == 400


def test_checkout_rejects_an_unknown_interval(ctx):
    res = ctx.client.post(
        "/v1/billing/checkout", json={"plan": plans.STARTER, "interval": "weekly"}
    )
    assert res.status_code == 400


def test_checkout_reports_a_missing_price_as_configuration_not_a_bad_request(
    ctx, monkeypatch
):
    monkeypatch.setattr(stripe_client, "price_id", lambda plan, interval: "")
    res = ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})
    assert res.status_code == 503


def test_checkout_is_owner_or_admin_only(ctx):
    _set_role(ctx.company_id, ctx.user_id, "viewer")
    res = ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})
    assert res.status_code == 403


def test_checkout_persists_the_stripe_customer(ctx):
    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})
    assert (billing_db.get_billing(ctx.company_id) or {})["stripe_customer_id"] == (
        "cus_test"
    )


def test_billing_routes_are_inert_without_stripe(ctx, monkeypatch):
    """Local dev and CI have no Stripe credentials. Routes must say so rather
    than raise."""
    monkeypatch.setattr(stripe_client, "configured", lambda: False)

    assert ctx.client.post(
        "/v1/billing/checkout", json={"plan": plans.STARTER}
    ).status_code == 503
    assert ctx.client.post("/v1/billing/portal").status_code == 503
    # The read-only summary still works — it reports billing_configured=false.
    body = ctx.client.get("/v1/billing/summary").json()
    assert body["billing_configured"] is False


def test_checkout_is_origin_gated(ctx):
    """Authed mutating routes carry the CSRF backstop."""
    res = ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER},
        headers={"origin": "https://evil.example"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Portal
# ---------------------------------------------------------------------------


def test_portal_needs_an_existing_customer(ctx):
    assert ctx.client.post("/v1/billing/portal").status_code == 400


def test_portal_returns_a_url_once_a_customer_exists(ctx):
    billing_db.set_billing(ctx.company_id, {"stripe_customer_id": "cus_test"})
    res = ctx.client.post("/v1/billing/portal")
    assert res.json()["url"] == "https://portal.stripe.test"


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------


def test_topup_quotes_the_credits_it_will_buy(ctx):
    res = ctx.client.post("/v1/billing/topup", json={"amount_usd": 50})

    assert res.status_code == 200
    assert res.json()["credits"] == plans.topup_credits_for_usd(50)


@pytest.mark.parametrize("amount", [0, -20, plans.TOPUP_MIN_USD - 1, plans.TOPUP_MAX_USD + 1])
def test_topup_bounds_are_enforced(ctx, amount):
    """The floor stops the Stripe fee eating the purchase; the ceiling is a
    typo guard — a mis-typed 100000 is a chargeback, not a good day."""
    res = ctx.client.post("/v1/billing/topup", json={"amount_usd": amount})
    assert res.status_code == 422


def test_topup_is_owner_or_admin_only(ctx):
    _set_role(ctx.company_id, ctx.user_id, "viewer")
    res = ctx.client.post("/v1/billing/topup", json={"amount_usd": 20})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


def test_referral_invite_returns_a_code(ctx):
    res = ctx.client.post("/v1/billing/referrals", json={"email": "Friend@Example.com"})

    assert res.status_code == 200
    body = res.json()
    assert body["invitee_email"] == "friend@example.com"  # normalised
    assert body["code"]
    assert body["invites_remaining"] == plans.MAX_REFERRAL_INVITES - 1


def test_referral_cap_is_enforced(ctx):
    for i in range(plans.MAX_REFERRAL_INVITES):
        assert (
            ctx.client.post(
                "/v1/billing/referrals", json={"email": f"f{i}@example.com"}
            ).status_code
            == 200
        )

    res = ctx.client.post("/v1/billing/referrals", json={"email": "one-too-many@x.com"})
    assert res.status_code == 400


def test_the_same_address_cannot_be_invited_twice(ctx):
    ctx.client.post("/v1/billing/referrals", json={"email": "dup@example.com"})
    res = ctx.client.post("/v1/billing/referrals", json={"email": "dup@example.com"})
    assert res.status_code == 400


def test_a_malformed_address_is_rejected(ctx):
    res = ctx.client.post("/v1/billing/referrals", json={"email": "not-an-email"})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Webhook — the deliberate exemptions
# ---------------------------------------------------------------------------


def test_webhook_takes_no_session_and_no_allowed_origin(ctx, monkeypatch):
    """Stripe sends neither a JWT nor a browser Origin. A foreign Origin proves
    `require_same_origin` is not attached — if someone adds it to every mutating
    route, this fails here instead of in production."""
    monkeypatch.setattr(
        stripe_client,
        "verify_webhook",
        lambda payload, sig: {"id": "evt_1", "type": "customer.created", "data": {"object": {}}},
    )

    from fastapi.testclient import TestClient

    import app.main as main_mod

    anon = TestClient(main_mod.app)  # no Authorization header
    res = anon.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=x", "origin": "https://stripe.example"},
    )

    assert res.status_code == 200
    assert res.json()["received"] is True


def test_webhook_rejects_an_unverifiable_signature(ctx, monkeypatch):
    def _boom(payload, sig):
        raise ValueError("bad signature")

    monkeypatch.setattr(stripe_client, "verify_webhook", _boom)

    res = ctx.client.post(
        "/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "nope"}
    )

    assert res.status_code == 400


def test_webhook_is_503_without_configuration(ctx, monkeypatch):
    """No key means nothing can be verified. Accepting an unverified body would
    be a self-serve credit-granting endpoint."""
    monkeypatch.setattr(stripe_client, "configured", lambda: False)
    res = ctx.client.post("/v1/billing/webhook", content=b"{}")
    assert res.status_code == 503


def test_a_replayed_event_is_acknowledged_without_reprocessing(ctx, monkeypatch):
    event = {
        "id": "evt_dup",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_1",
                "customer": "cus_x",
                "metadata": {
                    "company_id": ctx.company_id,
                    "purpose": "topup",
                    "credits": "100",
                },
            }
        },
    }
    monkeypatch.setattr(stripe_client, "verify_webhook", lambda p, s: event)

    first = ctx.client.post(
        "/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "x"}
    )
    second = ctx.client.post(
        "/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "x"}
    )

    assert first.json()["result"] != "duplicate"
    assert second.json()["result"] == "duplicate"
    assert (billing_db.get_billing(ctx.company_id) or {})["credit_balance"] == 100


def test_a_failing_handler_still_returns_200(ctx, monkeypatch):
    """Stripe retries non-2xx for three days and disables endpoints that keep
    failing. One broken handler must not stall the whole event stream."""
    monkeypatch.setattr(
        stripe_client,
        "verify_webhook",
        lambda p, s: {"id": "evt_boom", "type": "invoice.paid", "data": {"object": {}}},
    )

    import app.routes.billing as billing_routes

    def _explode(event):
        raise RuntimeError("handler bug")

    monkeypatch.setattr(billing_routes.billing_webhooks, "handle_event", _explode)

    res = ctx.client.post(
        "/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "x"}
    )

    assert res.status_code == 200
    assert res.json()["result"] == "error"


# ---------------------------------------------------------------------------
# Misconfiguration must be legible, not a 500
# ---------------------------------------------------------------------------


def test_a_dollar_amount_in_place_of_a_price_id_is_caught_before_stripe(ctx, monkeypatch):
    """The easy mistake: pasting 59 into STRIPE_PRICE_STARTER_MONTHLY.

    Stripe only reports it at checkout time ("The `price` parameter should be
    the ID of a price object, rather than the literal numerical price"), by
    which point the user has clicked Choose and got a 500 with nothing on
    screen. Caught here instead, naming the variable to fix.
    """
    # `price_id` returns "" for a malformed value (unit-tested below); the
    # route's job is to turn that into a message naming the variable to fix.
    monkeypatch.setattr(stripe_client, "price_id", lambda plan, interval: "")

    res = ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["error"] == "price_not_configured"
    assert "STRIPE_PRICE_STARTER_MONTHLY" in detail["message"]
    assert "price_" in detail["message"]


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("price_1AbCdEf", "price_1AbCdEf"),   # a real id passes through
        ("  price_1AbCdEf  ", "price_1AbCdEf"),  # whitespace from a paste
        ("59", ""),        # the dollar amount mistake
        ("590", ""),
        ("prod_1AbCdEf", ""),  # a PRODUCT id, not a price id
        ("", ""),
    ],
)
def test_price_id_accepts_only_real_price_ids(monkeypatch, stored, expected, caplog):
    """Patched on `stripe_client.settings` — the object the function actually
    reads — because `isolated_settings` reloads app modules and a separately
    imported `app.config.settings` can be a different instance."""
    monkeypatch.setattr(
        stripe_client.settings, "stripe_price_starter_monthly", stored
    )
    with caplog.at_level("ERROR"):
        assert stripe_client.price_id(plans.STARTER, "monthly") == expected
    if stored.strip() and not expected:
        assert "stripe_price_malformed" in caplog.text


def test_a_stripe_failure_becomes_a_readable_502_not_a_500(ctx, monkeypatch):
    """Stripe's own message is almost always the actionable part, so it is
    passed through rather than replaced with a generic failure."""

    class FakeStripeError(Exception):
        user_message = "Your card was declined by the issuer."

    def _boom(**kw):
        raise FakeStripeError("raw sdk text")

    monkeypatch.setattr(stripe_client, "create_subscription_checkout", _boom)

    res = ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert res.status_code == 502
    detail = res.json()["detail"]
    assert detail["error"] == "stripe_error"
    assert detail["op"] == "checkout"
    assert detail["message"] == "Your card was declined by the issuer."


def test_a_stripe_failure_on_the_portal_is_also_reported(ctx, monkeypatch):
    billing_db.set_billing(ctx.company_id, {"stripe_customer_id": "cus_test"})

    def _boom(**kw):
        raise RuntimeError("No configuration provided for the customer portal")

    monkeypatch.setattr(stripe_client, "create_portal_session", _boom)

    res = ctx.client.post("/v1/billing/portal")

    assert res.status_code == 502
    assert "customer portal" in res.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# Stripe SDK objects are not dicts
# ---------------------------------------------------------------------------
#
# These exist because every earlier test in this file mocked the SDK with plain
# dicts, so `dict(stripe_object)` looked fine under test and raised TypeError
# against the real API. The symptom in production was the worst possible shape:
# every webhook logged "stripe_webhook_rejected reason=TypeError", which reads
# as a signature problem, while the signature was perfectly valid — the
# exception came from the line AFTER the check.


class _FakeStripeObject:
    """Mimics the one behaviour that matters: subscript access works, but it is
    NOT a Mapping, so `dict()` and `.get()` both fail exactly as the real SDK's
    objects do."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, k):
        return self._data[k]

    def __iter__(self):  # what makes dict() fail the way the SDK does
        raise TypeError("StripeObject is not iterable as a mapping")

    def __getattr__(self, name):
        if name == "to_dict":
            return lambda: {
                k: (v.to_dict() if isinstance(v, _FakeStripeObject) else v)
                for k, v in self._data.items()
            }
        raise AttributeError(name)


def test_verify_webhook_converts_the_sdk_event_rather_than_calling_dict(monkeypatch):
    """The regression that broke every delivery."""
    event = _FakeStripeObject(
        {"id": "evt_1", "type": "invoice.paid", "data": _FakeStripeObject({"object": {}})}
    )

    class _FakeWebhook:
        @staticmethod
        def construct_event(payload, sig, secret, **kw):
            return event

    fake_sdk = type("SDK", (), {"Webhook": _FakeWebhook})()
    monkeypatch.setattr(stripe_client, "_stripe", lambda: fake_sdk)
    monkeypatch.setattr(stripe_client.settings, "stripe_webhook_secret", "whsec_x")

    out = stripe_client.verify_webhook(b"{}", "t=1,v1=abc")

    assert isinstance(out, dict)
    assert out["type"] == "invoice.paid"
    # Nested objects must be plain dicts too — the handlers walk
    # data.object.parent.subscription_details with ordinary .get() chains.
    assert out["data"].get("object") == {}


def test_get_subscription_converts_the_sdk_object(monkeypatch):
    sub = _FakeStripeObject({"id": "sub_1", "status": "active"})
    fake_sdk = type("SDK", (), {"Subscription": type("S", (), {"retrieve": staticmethod(lambda _id: sub)})})()
    monkeypatch.setattr(stripe_client, "_stripe", lambda: fake_sdk)

    out = stripe_client.get_subscription("sub_1")

    assert out == {"id": "sub_1", "status": "active"}


def test_as_dict_still_accepts_a_plain_dict():
    """Belt and braces: the helper must not break if given something that is
    already a mapping."""
    assert stripe_client._as_dict({"a": 1}) == {"a": 1}


# ---------------------------------------------------------------------------
# Cancel at period end
# ---------------------------------------------------------------------------


@pytest.fixture
def subscribed(ctx, monkeypatch):
    billing_db.set_billing(
        ctx.company_id,
        {
            "stripe_customer_id": "cus_test",
            "stripe_subscription_id": "sub_1",
            "subscription_status": "active",
            "current_period_end": "2026-09-23T00:00:00+00:00",
        },
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        stripe_client,
        "schedule_cancellation",
        lambda sid: calls.append(("schedule", sid)) or {"cancel_at": 1_790_000_000},
    )
    monkeypatch.setattr(
        stripe_client,
        "resume_subscription",
        lambda sid: calls.append(("resume", sid)) or {"cancel_at": None},
    )
    ctx.calls = calls
    return ctx


def test_cancelling_schedules_it_for_the_period_end_not_now(subscribed):
    """The customer paid for this period. Taking it away the moment they click
    Cancel removes something already bought and turns every cancellation into a
    refund request."""
    res = subscribed.client.post("/v1/billing/cancel")

    assert res.status_code == 200
    assert res.json()["cancel_at_period_end"] is True
    assert subscribed.calls == [("schedule", "sub_1")]


def test_access_is_untouched_by_a_pending_cancellation(subscribed):
    """Stripe keeps status `active` with cancel_at_period_end set, so the
    access rule needs no special case — the customer keeps generating until the
    period actually ends."""
    subscribed.client.post("/v1/billing/cancel")

    row = billing_db.get_billing(subscribed.company_id) or {}
    assert row["subscription_status"] == "active"
    assert plans.subscription_grants_access(row["plan"], row["subscription_status"])


def test_cancelling_without_a_subscription_is_refused(ctx):
    assert ctx.client.post("/v1/billing/cancel").status_code == 400


def test_resume_reverses_a_pending_cancellation(subscribed):
    subscribed.client.post("/v1/billing/cancel")
    res = subscribed.client.post("/v1/billing/resume")

    assert res.status_code == 200
    assert res.json()["cancel_at_period_end"] is False
    assert subscribed.calls[-1] == ("resume", "sub_1")


def test_resuming_an_already_ended_subscription_says_so(subscribed):
    """Stripe cannot reactivate a subscription it has already cancelled. Saying
    that plainly beats appearing to work."""
    billing_db.set_billing(subscribed.company_id, {"subscription_status": "canceled"})

    res = subscribed.client.post("/v1/billing/resume")

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "subscription_ended"


def test_cancel_is_owner_or_admin_only(subscribed):
    _set_role(subscribed.company_id, subscribed.user_id, "viewer")
    assert subscribed.client.post("/v1/billing/cancel").status_code == 403


def test_cancel_is_origin_gated(subscribed):
    res = subscribed.client.post(
        "/v1/billing/cancel", headers={"origin": "https://evil.example"}
    )
    assert res.status_code == 403


def test_summary_reports_a_pending_cancellation(subscribed, monkeypatch):
    monkeypatch.setattr(
        stripe_client,
        "get_subscription",
        lambda sid: {"cancel_at_period_end": True, "cancel_at": 1_790_000_000},
    )

    body = subscribed.client.get("/v1/billing/summary").json()

    assert body["cancel_at_period_end"] is True
    assert body["cancels_at"].startswith("2026-")


def test_summary_survives_an_unreadable_subscription(subscribed, monkeypatch):
    """A Stripe hiccup must not blank the whole pane — the rest of the screen
    still renders and the cancellation flag simply reads false."""

    def _boom(sid):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(stripe_client, "get_subscription", _boom)

    body = subscribed.client.get("/v1/billing/summary").json()

    assert body["cancel_at_period_end"] is False
    assert body["plan"] == plans.STARTER
    assert body["credit_balance"] == 0

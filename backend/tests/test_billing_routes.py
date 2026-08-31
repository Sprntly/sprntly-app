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
from app.config import settings
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


# ---------------------------------------------------------------------------
# Trial, and where Checkout sends the browser back to
# ---------------------------------------------------------------------------
#
# Payment is moving to the front of onboarding, which makes two things load
# bearing that were not before: the first subscription a company buys gets a
# trial so a stranger is not charged before seeing anything, and Checkout has
# to return the user to the step they left rather than to Settings.


def _capture_checkout(monkeypatch) -> dict:
    """Record the kwargs the route hands Stripe."""
    seen: dict = {}

    def _fake(**kw):
        seen.update(kw)
        return "https://checkout.stripe.test/sub"

    monkeypatch.setattr(stripe_client, "create_subscription_checkout", _fake)
    return seen


def test_a_companys_first_subscription_gets_the_trial(ctx, monkeypatch):
    """Still mid-onboarding: this is the case the trial exists for."""
    seen = _capture_checkout(monkeypatch)

    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert seen["trial_days"] == plans.TRIAL_DAYS


def test_buying_from_settings_after_onboarding_gets_NO_trial(ctx, monkeypatch):
    """THE TRIAL IS AN ONBOARDING OFFER, and only that. It exists so a stranger
    is not asked for money before seeing a single brief. Someone buying from
    Settings has already used the product — they pay on the day they buy."""
    require_client().table("companies").update(
        {"onboarding_completed_at": "2026-07-21T00:00:00Z"}
    ).eq("id", ctx.company_id).execute()
    seen = _capture_checkout(monkeypatch)

    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert seen["trial_days"] is None


def test_the_onboarding_return_path_cannot_buy_a_trial(ctx, monkeypatch):
    """Keyed on a SERVER fact, not on which screen claims to have started the
    checkout. Pointing `return_path` at the onboarding gate must not resurrect
    a trial for a company that has finished signup."""
    require_client().table("companies").update(
        {"onboarding_completed_at": "2026-07-21T00:00:00Z"}
    ).eq("id", ctx.company_id).execute()
    seen = _capture_checkout(monkeypatch)

    ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER, "return_path": "/onboarding/plan"},
    )

    assert seen["trial_days"] is None


def test_a_company_that_has_paid_before_gets_no_trial(ctx, monkeypatch):
    """A cancel-and-resubscribe has already seen the product. The trial exists
    to stop us charging someone who has seen nothing — not to hand a free week
    to anyone willing to cancel first."""
    billing_db.set_billing(ctx.company_id, {"first_paid_at": "2026-01-01T00:00:00Z"})
    seen = _capture_checkout(monkeypatch)

    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert seen["trial_days"] is None


def test_the_trial_is_not_a_client_flag(ctx, monkeypatch):
    """Nothing in the request body can ask for a trial. If it could, the trial
    would be available to anyone who could spell the field name."""
    billing_db.set_billing(ctx.company_id, {"first_paid_at": "2026-01-01T00:00:00Z"})
    seen = _capture_checkout(monkeypatch)

    ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER, "trial_days": 30, "trial": True},
    )

    assert seen["trial_days"] is None


def test_checkout_returns_to_the_path_the_caller_asked_for(ctx, monkeypatch):
    seen = _capture_checkout(monkeypatch)

    ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER, "return_path": "/onboarding/plan"},
    )

    assert seen["success_url"].endswith("/onboarding/plan?checkout=success")
    assert seen["cancel_url"].endswith("/onboarding/plan?checkout=cancelled")


def test_checkout_defaults_to_the_configured_return_url(ctx, monkeypatch):
    seen = _capture_checkout(monkeypatch)

    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert seen["success_url"].startswith(settings.billing_return)
    assert seen["success_url"].endswith("checkout=success")


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/steal",       # absolute URL
        "//evil.example/steal",             # protocol-relative — absolute to a browser
        "http://evil.example",              # absolute, plain http
        "javascript:alert(1)",              # scheme, no slash
        r"/\evil.example",                  # backslash some parsers fold to "/"
        "onboarding/plan",                  # relative, would resolve off our path
        "/onboarding\r\n/plan",             # header/URL splitting characters
    ],
)
def test_checkout_refuses_to_return_the_browser_off_site(ctx, monkeypatch, hostile):
    """OPEN REDIRECT. `return_path` becomes Stripe's success_url, so an
    unchecked value sends someone who has just typed their card number to
    whatever host asked for it — on a link that genuinely came from us, at the
    most credible phishing moment this product has.

    A bad path falls back to the configured default rather than 400ing: it is a
    bug in our own caller, not a reason to fail a purchase."""
    seen = _capture_checkout(monkeypatch)

    ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER, "return_path": hostile},
    )

    assert seen["success_url"].startswith(settings.billing_return)
    assert "evil.example" not in seen["success_url"]
    assert "javascript" not in seen["success_url"]


# ---------------------------------------------------------------------------
# Reconcile — the gate does not depend on a webhook arriving
# ---------------------------------------------------------------------------
#
# A webhook is a push we do not control: it can be unconfigured, firewalled,
# undeliverable to a local machine, or simply late. Until one lands, a customer
# who has genuinely paid is indistinguishable from one who has not — and the
# onboarding gate reads exactly that state to decide whether to let someone in.
#
# So `summary` asks Stripe directly when the row looks unsettled. That is what
# lets the gate refuse a hand-typed `?checkout=success` while still admitting a
# real customer whose webhook never showed up.


def test_summary_asks_stripe_when_it_holds_a_customer_but_no_subscription(
    ctx, monkeypatch
):
    billing_db.set_billing(ctx.company_id, {"stripe_customer_id": "cus_test"})
    monkeypatch.setattr(
        stripe_client,
        "latest_subscription_for_customer",
        lambda customer_id: {
            "id": "sub_recon",
            "status": "trialing",
            "customer": customer_id,
            "metadata": {"company_id": ctx.company_id, "plan": plans.STARTER},
            "current_period_start": 1_789_000_000,
            "current_period_end": 1_790_000_000,
            "items": {"data": [{"price": {"id": "price_starter_monthly"}}]},
        },
    )
    monkeypatch.setattr(
        stripe_client, "get_subscription", lambda _id: {
            "id": "sub_recon",
            "status": "trialing",
            "customer": "cus_test",
            "metadata": {"company_id": ctx.company_id, "plan": plans.STARTER},
            "current_period_start": 1_789_000_000,
            "current_period_end": 1_790_000_000,
            "items": {"data": [{"price": {"id": "price_starter_monthly"}}]},
        },
    )

    body = ctx.client.get("/v1/billing/summary").json()

    assert body["subscription_status"] == "trialing"
    assert body["has_access"] is True
    # And the trial's credits landed — a trial pays no invoice, so nothing else
    # would ever have granted them. The TRIAL allowance, flat across plans,
    # rather than a free month of Starter.
    assert body["credit_balance"] == plans.TRIAL_CREDITS


def test_reconcile_does_not_invent_a_subscription_that_is_not_there(ctx, monkeypatch):
    """Someone who never paid must stay unpaid. This is the case the gate is
    for, and the reason it can refuse a hand-typed `?checkout=success`."""
    billing_db.set_billing(ctx.company_id, {"stripe_customer_id": "cus_test"})
    monkeypatch.setattr(
        stripe_client, "latest_subscription_for_customer", lambda _c: None
    )

    body = ctx.client.get("/v1/billing/summary").json()

    assert body["subscription_status"] is None
    assert body["has_access"] is False


def test_reconcile_is_skipped_once_a_subscription_is_on_file(ctx, monkeypatch):
    """One API call in the window between paying and being recorded — not on
    every render of the billing screen forever."""
    called = []
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_customer_id": "cus_test", "stripe_subscription_id": "sub_known",
         "subscription_status": "active"},
    )
    monkeypatch.setattr(
        stripe_client,
        "latest_subscription_for_customer",
        lambda _c: called.append(1),
    )

    ctx.client.get("/v1/billing/summary")

    assert called == []


def test_reconcile_is_skipped_for_a_company_stripe_has_never_seen(ctx, monkeypatch):
    called = []
    monkeypatch.setattr(
        stripe_client,
        "latest_subscription_for_customer",
        lambda _c: called.append(1),
    )

    ctx.client.get("/v1/billing/summary")

    assert called == []


def test_a_stripe_outage_does_not_break_the_billing_screen(ctx, monkeypatch):
    """Fail soft. A screen someone is waiting on must not 500 because Stripe
    is having a bad minute — it reports what we know and moves on."""
    billing_db.set_billing(ctx.company_id, {"stripe_customer_id": "cus_test"})

    def _boom(_c):
        raise RuntimeError("stripe is down")

    monkeypatch.setattr(stripe_client, "latest_subscription_for_customer", _boom)

    res = ctx.client.get("/v1/billing/summary")

    assert res.status_code == 200
    assert res.json()["subscription_status"] is None


# ---------------------------------------------------------------------------
# Changing plan swaps the subscription; it never buys a second one
# ---------------------------------------------------------------------------
#
# Checkout ALWAYS creates a new subscription — it has no concept of replacing
# one. So an active customer sent through it to "switch plans" ends up paying
# for both, and until now nothing on either side stopped that.


def test_checkout_refuses_a_company_that_already_pays(ctx):
    """The double-billing guard, at the SERVER. The screen that calls checkout
    today is correct; the fourth screen that calls it will not be."""
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_live", "subscription_status": "active",
         "plan": plans.STARTER},
    )

    res = ctx.client.post("/v1/billing/checkout", json={"plan": plans.PRODUCT_BUILDER})

    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "already_subscribed"


def test_checkout_still_works_for_a_cancelled_company(ctx):
    """A cancelled subscription is not a live one — buying again is exactly
    what restores access, and refusing it would strand them."""
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_dead", "subscription_status": "canceled"},
    )

    assert ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER}).status_code == 200


def test_change_plan_modifies_the_existing_subscription(ctx, monkeypatch):
    seen = {}

    def _change(**kw):
        seen.update(kw)
        return {"id": kw["subscription_id"], "status": "active"}

    monkeypatch.setattr(stripe_client, "change_subscription_plan", _change)
    monkeypatch.setattr(
        stripe_client, "get_subscription",
        lambda _id: {
            "id": "sub_live", "status": "active", "customer": "cus_test",
            "metadata": {"company_id": ctx.company_id, "plan": plans.PRODUCT_BUILDER},
            "current_period_start": 1_789_000_000, "current_period_end": 1_790_000_000,
            "items": {"data": [{"price": {"id": "price_pb"}}]},
        },
    )
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_live", "subscription_status": "active",
         "plan": plans.STARTER, "stripe_customer_id": "cus_test"},
    )

    res = ctx.client.post(
        "/v1/billing/change-plan",
        json={"plan": plans.PRODUCT_BUILDER, "interval": "monthly"},
    )

    assert res.status_code == 200
    # The SAME subscription, not a new one.
    assert seen["subscription_id"] == "sub_live"
    assert seen["plan"] == plans.PRODUCT_BUILDER
    assert billing_db.get_billing(ctx.company_id)["plan"] == plans.PRODUCT_BUILDER


def test_change_plan_refuses_when_there_is_nothing_to_change(ctx):
    """Says which door to use rather than failing vaguely."""
    res = ctx.client.post("/v1/billing/change-plan", json={"plan": plans.STARTER})
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "no_subscription"


def test_change_plan_refuses_the_plan_you_are_already_on(ctx):
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_live", "subscription_status": "active",
         "plan": plans.STARTER},
    )
    res = ctx.client.post("/v1/billing/change-plan", json={"plan": plans.STARTER})
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "same_plan"


def test_change_plan_refuses_the_invoiced_tiers(ctx):
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_live", "subscription_status": "active"},
    )
    for plan in (plans.TEAM, plans.ENTERPRISE):
        assert ctx.client.post("/v1/billing/change-plan", json={"plan": plan}).status_code == 400


def test_change_plan_is_owner_or_admin_only(ctx):
    _set_role(ctx.company_id, ctx.user_id, "member")
    billing_db.set_billing(
        ctx.company_id,
        {"stripe_subscription_id": "sub_live", "subscription_status": "active"},
    )
    res = ctx.client.post("/v1/billing/change-plan", json={"plan": plans.PRODUCT_BUILDER})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Referrals are ONE PERMANENT LINK, not an email invite
# ---------------------------------------------------------------------------
#
# The old endpoint took an address, minted a code for that one person, and
# created a referral row before anybody had done anything. That put a form
# between someone and sharing a link, capped how many people they could tell,
# and produced codes useless to anyone but the address they were cut for.


def test_the_summary_carries_a_shareable_link(ctx):
    body = ctx.client.get("/v1/billing/summary").json()

    assert body["referral_code"]
    assert body["referral_url"].endswith(f"/sign-up?ref={body['referral_code']}")


def test_the_link_is_the_same_every_time(ctx):
    """One code, forever — a link already shared must not stop working."""
    first = ctx.client.get("/v1/billing/summary").json()["referral_code"]
    second = ctx.client.get("/v1/billing/summary").json()["referral_code"]
    assert first == second


def test_the_code_avoids_characters_that_do_not_survive_retyping(ctx):
    """It gets read aloud and copied out of screenshots. 0/O and 1/I/l are a
    support ticket waiting to happen."""
    code = ctx.client.get("/v1/billing/summary").json()["referral_code"]
    assert not set(code) & set("01OIl")
    assert len(code) >= 8


def test_the_email_invite_endpoint_is_gone(ctx):
    """Nothing replaces it. There is no call to make — the link already exists."""
    res = ctx.client.post("/v1/billing/referrals", json={"email": "f@example.com"})
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Customer-visible URLs come from where the app actually lives
# ---------------------------------------------------------------------------
#
# `billing_return_url` defaulted to a literal localhost URL and nothing sets it
# on any deployed environment — `sync-backend-env.yml` writes GOOGLE_*,
# TOKEN_ENCRYPTION_KEY, FRONTEND_URL and INTERNAL_API_KEY, and not this. So
# staging served customers a referral link to `http://localhost:3000`, and
# would have returned a PAYING customer from Stripe Checkout to a machine that
# is not theirs.


def _settings(frontend: str, explicit: str = ""):
    from app.config import Settings

    return Settings(frontend_url=frontend, billing_return_url=explicit)


def test_the_return_url_follows_the_frontend_when_nothing_sets_it():
    s = _settings("https://staging.sprntly.ai")
    assert s.billing_return == "https://staging.sprntly.ai/settings?section=billing"


def test_an_explicit_return_url_still_wins():
    """For an environment that genuinely needs a different destination."""
    s = _settings("https://app.sprntly.ai", "https://somewhere.else/billing")
    assert s.billing_return == "https://somewhere.else/billing"


def test_a_trailing_slash_does_not_produce_a_double_slash():
    assert _settings("https://staging.sprntly.ai/").app_origin == "https://staging.sprntly.ai"


def test_the_origin_drops_any_path_the_setting_carries():
    """`frontend_url` is an origin by contract, but a stray path must not end
    up inside a customer's referral link."""
    assert _settings("https://app.sprntly.ai/app/").app_origin == "https://app.sprntly.ai"


def test_localhost_is_still_localhost_locally():
    assert _settings("http://localhost:3000").app_origin == "http://localhost:3000"


def test_the_referral_link_is_not_localhost_on_a_deployed_host(ctx, monkeypatch):
    """The reported bug, end to end: a staging customer was shown
    `http://localhost:3000/sign-up?ref=...` as their link to share."""
    import app.routes.billing as billing_routes

    monkeypatch.setattr(billing_routes.settings, "frontend_url", "https://staging.sprntly.ai")
    monkeypatch.setattr(billing_routes.settings, "billing_return_url", "")

    body = ctx.client.get("/v1/billing/summary").json()

    assert body["referral_url"].startswith("https://staging.sprntly.ai/sign-up?ref=")
    assert "localhost" not in body["referral_url"]


def test_checkout_returns_to_the_deployed_host_too(ctx, monkeypatch):
    """The same misconfiguration would have sent someone who had just paid back
    to a laptop. Worth its own assertion — it is the more expensive half."""
    import app.routes.billing as billing_routes

    monkeypatch.setattr(billing_routes.settings, "frontend_url", "https://staging.sprntly.ai")
    monkeypatch.setattr(billing_routes.settings, "billing_return_url", "")
    seen = _capture_checkout(monkeypatch)

    ctx.client.post("/v1/billing/checkout", json={"plan": plans.STARTER})

    assert seen["success_url"].startswith("https://staging.sprntly.ai/")
    assert "localhost" not in seen["success_url"]


def test_an_onboarding_return_path_also_lands_on_the_deployed_host(ctx, monkeypatch):
    import app.routes.billing as billing_routes

    monkeypatch.setattr(billing_routes.settings, "frontend_url", "https://staging.sprntly.ai")
    monkeypatch.setattr(billing_routes.settings, "billing_return_url", "")
    seen = _capture_checkout(monkeypatch)

    ctx.client.post(
        "/v1/billing/checkout",
        json={"plan": plans.STARTER, "return_path": "/onboarding/plan"},
    )

    assert seen["success_url"] == (
        "https://staging.sprntly.ai/onboarding/plan?checkout=success"
    )

"""The eight billing emails.

What these actually protect, in order of how expensive the mistake is:

  1. NOBODY IS MAILED TWICE. Every trigger fires repeatedly — Stripe redelivers
     webhooks for days, the trial reminder is an hourly tick, and a balance
     under the low threshold stays under it. Eleven copies of "your trial has
     started" is the failure this whole design exists to prevent.
  2. THE RIGHT PEOPLE. Billing mail goes to owners and admins. A workspace can
     have a dozen members and one card; telling the rest about a problem they
     cannot fix is noise at best.
  3. NEVER BREAKS THE CALLER. A webhook that 500s because a mailbox bounced
     gets retried, and the retry re-runs the subscription sync.
"""
from __future__ import annotations

import pytest

from app import mailer
from app.billing import emails, plans
from app.db import billing as billing_db
from app.db.client import require_client

from ._company_helpers import seed_company


@pytest.fixture(autouse=True)
def _db(isolated_settings):
    return isolated_settings


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have gone on the wire."""
    outbox: list[dict] = []

    def _send(**kw):
        outbox.append(kw)
        return True

    monkeypatch.setattr(mailer, "send", _send)
    return outbox


def _company_with(*roles: str) -> str:
    """A company whose members hold the given roles, each with an email."""
    cid = seed_company(user_id="u-owner", slug="mail-co")
    client = require_client()
    client.table("profiles").upsert(
        {"id": "u-owner", "email": "owner@example.com", "first_name": "Ada"}
    ).execute()
    for i, role in enumerate(roles):
        uid = f"u-extra-{i}"
        client.table("profiles").upsert(
            {"id": uid, "email": f"{role}{i}@example.com", "first_name": role.title()}
        ).execute()
        client.table("company_members").insert(
            {"company_id": cid, "user_id": uid, "role": role}
        ).execute()
    return cid


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def test_only_owners_and_admins_are_mailed(sent):
    """A workspace can have a dozen members and one card. Only an owner or
    admin can act on any of this — `/v1/billing/*` refuses everyone else."""
    cid = _company_with("admin", "member", "viewer")

    emails.subscription_cancelled(cid, subscription_id="sub_1")

    recipients = {m["to_email"] for m in sent}
    assert "owner@example.com" in recipients
    assert any(r.startswith("admin") for r in recipients)
    assert not any(r.startswith("member") or r.startswith("viewer") for r in recipients)


def test_a_company_with_no_reachable_admin_is_a_quiet_no_op(sent, monkeypatch):
    cid = seed_company(user_id="u-none", slug="none-co")
    monkeypatch.setattr(emails, "billing_recipients", lambda _c: [])

    assert emails.subscription_cancelled(cid, subscription_id="sub_1") == 0
    assert sent == []


# ---------------------------------------------------------------------------
# Once per occasion
# ---------------------------------------------------------------------------


def test_a_redelivered_webhook_does_not_mail_twice(sent):
    """Stripe retries for days. This is the failure the send log exists for."""
    cid = _company_with()

    for _ in range(4):
        emails.trial_started(cid, subscription_id="sub_1")

    assert len(sent) == 1


def test_a_different_occasion_of_the_same_kind_still_sends(sent):
    """Two subscriptions, two trials. The de-dup key is the occasion, not the
    kind — otherwise a customer who resubscribed would be told nothing."""
    cid = _company_with()

    emails.trial_started(cid, subscription_id="sub_1")
    emails.trial_started(cid, subscription_id="sub_2")

    assert len(sent) == 2


def test_credit_warnings_repeat_next_PERIOD_but_not_this_one(sent):
    """Keyed on the billing period: once a month, not once ever."""
    cid = _company_with()

    for _ in range(3):
        emails.credits_low(cid, period="2026-08-01", balance=90, allowance=756)
    emails.credits_low(cid, period="2026-09-01", balance=90, allowance=756)

    assert len(sent) == 2


def test_a_skipped_send_is_recorded_so_it_does_not_blast_later(monkeypatch):
    """Without RESEND_API_KEY the send is a no-op. Recording the skip stops the
    whole backlog going out the day somebody sets the key."""
    monkeypatch.setattr(mailer, "send", lambda **kw: False)
    cid = _company_with()

    emails.trial_started(cid, subscription_id="sub_1")

    rows = (
        require_client()
        .table("billing_email_sends")
        .select("status")
        .eq("company_id", cid)
        .execute()
        .data
    )
    assert rows and rows[0]["status"] == "skipped"


def test_a_broken_dedup_check_fails_CLOSED(monkeypatch, sent):
    """A duplicate billing email is worse than a missing one, and the occasion
    comes round again on the next redelivery."""
    cid = _company_with()
    monkeypatch.setattr(
        emails, "_already_sent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db"))
    )

    with pytest.raises(RuntimeError):
        emails.trial_started(cid, subscription_id="sub_1")


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_the_low_credit_nudge_respects_the_email_preference(sent):
    """The only guess in the set: nothing has happened yet, we are predicting
    they would like a warning — and a guess is what a preference should
    govern."""
    cid = _company_with()
    require_client().table("companies").update(
        {"notification_settings": {"email_enabled": False}}
    ).eq("id", cid).execute()

    assert emails.credits_low(cid, period="p1", balance=10, allowance=756) == 0
    assert sent == []


def test_transactional_billing_mail_ignores_that_preference(sent):
    """Somebody who switched off weekly briefs did not ask to be left guessing
    about their subscription ending."""
    cid = _company_with()
    require_client().table("companies").update(
        {"notification_settings": {"email_enabled": False}}
    ).eq("id", cid).execute()

    assert emails.subscription_cancelled(cid, subscription_id="sub_1") == 1
    assert emails.credits_exhausted(cid, period="p1") == 1
    assert len(sent) == 2


# ---------------------------------------------------------------------------
# What each one says
# ---------------------------------------------------------------------------


def test_the_trial_email_names_the_credits_stripe_cannot(sent):
    """Stripe's receipt for a trial says $0 and mentions no credits, which
    makes the most generous moment in the product read like a failed payment."""
    cid = _company_with()
    billing_db.set_billing(cid, {"plan": plans.STARTER})

    emails.trial_started(cid, subscription_id="sub_1")

    body = sent[0]["body_text"]
    # The TRIAL allowance — see the sibling test that guards which number
    # this is. What matters here is that a number appears at all.
    assert f"{plans.TRIAL_CREDITS:,}" in body
    assert "nothing has been charged" in body.lower()


def test_the_cancellation_email_says_the_work_survives(sent):
    """Stripe says "cancelled". Only we can say the artifacts are still there —
    which is the actual question somebody has at that moment."""
    cid = _company_with()

    emails.subscription_cancelled(cid, subscription_id="sub_1")

    body = sent[0]["body_text"].lower()
    assert "nothing has been deleted" in body or "still there" in body


def test_the_referral_email_names_the_credits_earned(sent):
    cid = _company_with()

    emails.referral_converted(cid, referral_id="ref-1", credits_awarded=140)

    assert "140" in sent[0]["subject"]


def test_every_email_greets_by_name_when_there_is_one(sent):
    cid = _company_with()
    emails.trial_started(cid, subscription_id="sub_1")
    assert sent[0]["body_text"].startswith("Hi Ada,")


def test_the_cta_points_at_this_deployment_not_localhost(sent, monkeypatch):
    """The referral-link bug, one module over: a customer-facing URL built from
    a setting nothing configures on a deployed box."""
    monkeypatch.setattr(emails.settings, "frontend_url", "https://app.sprntly.ai")
    cid = _company_with()

    emails.trial_started(cid, subscription_id="sub_1")

    assert sent[0]["cta_url"].startswith("https://app.sprntly.ai/")


# ---------------------------------------------------------------------------
# The mailer itself
# ---------------------------------------------------------------------------


def test_no_api_key_is_a_no_op_not_an_error(monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(config_mod.settings, "resend_api_key", "")
    assert mailer.send(to_email="a@b.c", subject="s", body_text="b") is False


def test_the_mailer_never_raises(monkeypatch):
    """An email is never the reason a webhook 500s."""
    from app import config as config_mod

    monkeypatch.setattr(config_mod.settings, "resend_api_key", "key")

    def _boom(*a, **k):
        raise RuntimeError("network is gone")

    monkeypatch.setattr(mailer.httpx, "post", _boom)
    assert mailer.send(to_email="a@b.c", subject="s", body_text="b") is False


def test_the_html_escapes_interpolated_values():
    """The copy is ours; a company name interpolated into it is not."""
    html = mailer.render_html(subject="Hi", body_text='<script>alert("x")</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# The branded shell
# ---------------------------------------------------------------------------


def test_the_html_carries_the_logo_and_a_signoff(sent):
    """"There's no logo, there's no conclusion" — the raw version was a bare
    column of paragraphs, which reads like a system alert rather than mail from
    a company you pay."""
    cid = _company_with()
    emails.trial_started(cid, subscription_id="sub_1")

    html = mailer.render_html(
        subject=sent[0]["subject"],
        body_text=sent[0]["body_text"],
        facts=sent[0]["facts"],
    )
    assert mailer.LOGO_URL in html
    assert "Sprntly<span" in html          # the wordmark, for image-blocked clients
    assert mailer.DEFAULT_SIGNOFF in html


def test_the_facts_panel_and_the_text_part_say_the_same_thing(sent):
    """A multipart email whose halves disagree is a worse read AND a spam
    signal, so the numbers are in both."""
    cid = _company_with()
    emails.trial_started(cid, subscription_id="sub_1")

    facts = sent[0]["facts"]
    assert ("Charged so far", "$0.00") in facts
    text = mailer._as_text(sent[0]["body_text"], facts, mailer.DEFAULT_SIGNOFF)
    assert "Charged so far: $0.00" in text
    assert mailer.DEFAULT_SIGNOFF in text


def test_the_shell_survives_an_email_with_no_facts_and_no_cta():
    """Not every sender has a panel to show. The card must not render an empty
    grey box where the numbers would be."""
    html = mailer.render_html(subject="Hi", body_text="One line.")
    assert "background-color:#f6f5f1;border-radius:10px" not in html
    assert "<h1" in html


# ---------------------------------------------------------------------------
# A trial is worth TRIAL_CREDITS, not a free month of the plan
# ---------------------------------------------------------------------------


def test_the_trial_email_quotes_the_trial_allowance_not_the_plan_allowance(sent):
    """The number in this email is the first one a customer is told, so it has
    to be the number they will actually have. Starter grants 756 a month; a
    seven-day trial grants 100."""
    cid = _company_with()
    billing_db.set_billing(cid, {"plan": plans.STARTER})

    emails.trial_started(cid, subscription_id="sub_1")

    body = sent[0]["body_text"]
    assert f"{plans.TRIAL_CREDITS:,}" in body
    assert f"{plans.PLAN_CREDITS[plans.STARTER]:,}" not in body
    assert ("Trial credits", f"{plans.TRIAL_CREDITS:,}") in sent[0]["facts"]


def test_a_trial_period_grants_the_trial_allowance():
    """The grant follows the subscription period, so `trialing` has to be told
    apart there — otherwise seven free days hand over a full month of credits."""
    cid = _company_with()
    from app.billing import credits

    credits.grant_monthly(cid, plans.STARTER, period_start="p1", status="trialing")

    row = billing_db.get_billing(cid) or {}
    assert int(row.get("credit_balance") or 0) == plans.TRIAL_CREDITS


def test_converting_off_the_trial_grants_the_real_allowance():
    """Day eight, the card is charged, and the plan's own allowance applies."""
    cid = _company_with()
    from app.billing import credits

    credits.grant_monthly(cid, plans.STARTER, period_start="p1", status="trialing")
    credits.grant_monthly(cid, plans.STARTER, period_start="p2", status="active")

    row = billing_db.get_billing(cid) or {}
    assert int(row.get("credit_balance") or 0) == plans.PLAN_CREDITS[plans.STARTER]


def test_the_low_credit_warning_measures_against_the_trial_allowance(monkeypatch, sent):
    """20% of 756 is 151, and a trialist holding 90 of 100 credits is not
    running low. Warning off the plan's allowance would tell them they were."""
    from app.billing import enforce

    cid = _company_with()
    billing_db.set_billing(
        cid, {"plan": plans.STARTER, "subscription_status": "trialing"}
    )
    from app.billing import credits

    credits.grant(cid, 90, reason="manual_adjustment", ref_id="seed")

    enforce._maybe_warn_low(cid)

    assert sent == []

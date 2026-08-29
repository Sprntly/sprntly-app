"""The scheduler does not spend our money on tenants who have stopped paying.

Scheduled work — connector refreshes, KG synthesis, brief generation — is not
charged to anyone's credit balance. It is our Anthropic bill, on a timer, for
every row in `companies`. A lapsed tenant otherwise keeps costing us money
indefinitely while being unable to use a single thing that money produces.

The guard is keyed on `billing_enforced`, not on the lock mode: what a lapsed
customer SEES is a product question, what we spend on them is not.
"""
from __future__ import annotations

import pytest

from app.billing import plans


@pytest.fixture
def gate(monkeypatch, isolated_settings):
    from app import scheduler

    monkeypatch.setattr(scheduler.settings, "billing_enforced", True)
    return scheduler


def _co(**over) -> dict:
    return {"id": "c1", "slug": "acme", "plan": "starter",
            "subscription_status": "active", **over}


def test_a_paying_tenant_is_worked(gate):
    assert gate._billing_allows_scheduled_work(_co()) is True


def test_a_trialling_tenant_is_worked(gate):
    """They have a card on file and full access; the brief is part of what
    they are trialling."""
    assert gate._billing_allows_scheduled_work(_co(subscription_status="trialing")) is True


def test_past_due_is_STILL_worked(gate):
    """Same reason it still grants access: Stripe is working the card and the
    customer may not know yet. Cutting their briefs off mid-retry is how a
    bounced card becomes a cancellation."""
    assert gate._billing_allows_scheduled_work(_co(subscription_status="past_due")) is True


def test_a_lapsed_tenant_is_skipped(gate):
    for status in ("canceled", "unpaid", "incomplete_expired", None):
        assert gate._billing_allows_scheduled_work(_co(subscription_status=status)) is False, status


def test_legacy_and_enterprise_are_never_skipped(gate):
    """Neither was sold through Stripe, so both carry a null status. Skipping
    them would silently stop every pre-billing tenant's brief."""
    for plan in (plans.LEGACY, plans.ENTERPRISE):
        assert gate._billing_allows_scheduled_work(
            _co(plan=plan, subscription_status=None)
        ) is True, plan


def test_everything_runs_when_billing_is_not_enforced(gate, monkeypatch):
    """CI, local dev, and any deploy that has not turned billing on."""
    monkeypatch.setattr(gate.settings, "billing_enforced", False)
    assert gate._billing_allows_scheduled_work(_co(subscription_status="canceled")) is True


def test_an_absent_signal_fails_OPEN(gate):
    """THE OUTAGE THIS PREVENTS. `list_companies` selects plan and status
    best-effort, and its fallback select drops them entirely. Treating absent
    as lapsed would read as "nobody is subscribed" for EVERY tenant and stop
    the whole scheduler in one deploy — silently, because skipping is a log
    line, not an error."""
    assert gate._billing_allows_scheduled_work({"id": "c1", "slug": "acme"}) is True


def test_the_filter_is_applied_at_the_source(gate, monkeypatch):
    """One filter, not four call sites. A guard that has to be remembered in
    four loops is a guard that will be missing from the fifth."""
    monkeypatch.setattr(
        gate, "list_companies",
        lambda: [_co(id="live"), _co(id="dead", subscription_status="canceled")],
    )
    assert [c["id"] for c in gate._billable_companies()] == ["live"]


# ---------------------------------------------------------------------------
# The lock mode does not turn itself off when someone documents it
# ---------------------------------------------------------------------------
#
# `SUBSCRIPTION_LOCK_MODE=hard # off|read_only|hard` in a .env arrives as the
# whole string, comment included. It matched none of the three values, so the
# lock silently disabled itself — "unrecognised" and "off" were the same answer,
# and it cost a testing session to find.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hard", "hard"),
        ("hard # off|read_only|hard", "hard"),      # the one that bit
        ("  read_only  ", "read_only"),
        ("HARD", "hard"),
        ('"hard"', "hard"),
        ("off", "off"),
        ("", "off"),
        (None, "off"),
        ("enabled", "off"),                          # unrecognised -> off, loudly
    ],
)
def test_lock_mode_is_normalised(raw, expected):
    from app.config import Settings

    assert Settings(subscription_lock_mode=raw).subscription_lock_mode == expected


def test_an_unrecognised_mode_says_so_rather_than_failing_silently(caplog):
    """Falling back to 'off' is right — failing closed would wall every customer
    out of a working app over a typo. Doing it in silence is not."""
    from app.config import Settings

    with caplog.at_level("WARNING"):
        Settings(subscription_lock_mode="hrad")
    assert any("subscription_lock_mode" in r.message for r in caplog.records)

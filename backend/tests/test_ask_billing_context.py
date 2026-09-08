"""Chat knows what the account is paying for.

"What plan are we on", "how many credits are left", "how do I buy more" are
questions about the product the customer is holding, and the answer path had
nothing to answer them from — every other grounding block describes the
customer's DATA. Worse than silence: the app map (app/app_map.py) already
tells the model a Billing screen exists at a real path, so a model asked what
was on it invented a plausible tier and a number.

`billing_facts_block` is that record, written down, and it is READABLE BY
EVERY MEMBER — deliberately wider than `/v1/billing/summary`, whose
`_require_admin` 403s anyone below admin (owner decision 2026-09-08). Credits
gate everyone's work, and a member whose generation is refused for a balance
they cannot see has no way to understand why. What stays admin-only is
ACTING — buying, upgrading, cancelling — which the block states in words.

Covered here:
- the block carries plan, status, balance-of-allowance, period end and how to
  top up
- the period-end LABEL follows the status (a trial ends; a plan renews)
- unlimited plans do not render a number they do not have
- every degradation path returns "" rather than raising, because grounding
  must never break an answer
- payments switched off renders nothing at all — a balance means nothing when
  nothing is charged
"""
from __future__ import annotations

import pytest

from app import ask_runner
from app.billing import plans


@pytest.fixture
def company(tenant_client):
    return tenant_client.make(slug="acme", user_id="user-a")


def _set_billing(company_id: str, **fields):
    """Plan/status/period go through `set_billing`'s allow-list; the BALANCE
    does not. `credit_balance` is ledger-derived on purpose — every movement
    writes a row explaining itself — so a test that set the column directly
    would be exercising a state the product cannot produce."""
    from app.billing import credits
    from app.db import billing as billing_db

    balance = fields.pop("credit_balance", None)
    if fields:
        billing_db.set_billing(company_id, fields)
    if balance:
        credits.grant(company_id, balance, reason="adjustment")


def test_the_block_states_plan_status_credits_and_how_to_buy(company):
    _set_billing(
        company.company_id,
        plan=plans.STARTER,
        subscription_status="active",
        credit_balance=742,
        current_period_end="2026-10-06T00:00:00Z",
    )

    block = ask_runner.billing_facts_block(company.company_id)

    assert ask_runner.BILLING_HEADER in block
    assert plans.plan_label(plans.STARTER) in block
    assert "active" in block
    # The balance is stated against the allowance — "742 left" alone does not
    # tell anyone whether that is comfortable or nearly out.
    assert "742" in block
    assert f"{plans.monthly_credits(plans.STARTER):,}" in block
    assert "2026-10-06" in block
    # How to buy, and the fact that reading is not the same as being able to.
    assert "Settings > Billing" in block
    assert "owner or admin" in block


def test_a_trial_end_is_not_described_as_a_renewal(company):
    """Same column, opposite meaning. Calling a trial's last day a renewal is
    how someone finds out they were charged by being charged."""
    _set_billing(
        company.company_id,
        plan=plans.STARTER,
        subscription_status="trialing",
        credit_balance=50,
        current_period_end="2026-09-15T00:00:00Z",
    )

    block = ask_runner.billing_facts_block(company.company_id)

    assert "Trial ends: 2026-09-15" in block
    assert "Current period ends" not in block


def test_an_uncapped_plan_states_no_number(company):
    """`monthly_credits` returns the UNLIMITED sentinel (-1) for these. Printed
    raw it becomes "-1 credits", which is worse than saying nothing."""
    _set_billing(
        company.company_id, plan=plans.ENTERPRISE, subscription_status="active"
    )

    block = ask_runner.billing_facts_block(company.company_id)

    assert "unlimited" in block.lower()
    assert "-1" not in block


def test_nothing_is_rendered_while_payments_are_hidden(monkeypatch, company):
    """`BILLING_ENABLED` false means nothing is charged and no balance means
    anything. Stating a plan and a credit count then is fiction with a number
    in it."""
    _set_billing(company.company_id, plan=plans.STARTER)
    monkeypatch.setattr(plans, "BILLING_ENABLED", False)

    assert ask_runner.billing_facts_block(company.company_id) == ""


def test_no_tenant_renders_nothing(company):
    assert ask_runner.billing_facts_block(None) == ""
    assert ask_runner.billing_facts_block("") == ""


def test_a_failed_read_answers_without_billing_rather_than_raising(monkeypatch, company):
    """GROUNDING MUST NEVER BREAK AN ANSWER. A billing read that throws costs
    the reader the billing block, not their question."""
    from app.db import billing as billing_db

    def boom(_company_id):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(billing_db, "get_billing", boom)

    assert ask_runner.billing_facts_block(company.company_id) == ""


def test_a_company_with_no_billing_row_still_renders_its_default_plan(company):
    """`resolve_plan` is fail-CLOSED — an unknown/missing plan resolves to the
    launch default rather than to a paid tier — so a row-less company gets the
    truth about where it stands rather than an empty block."""
    block = ask_runner.billing_facts_block(company.company_id)

    assert ask_runner.BILLING_HEADER in block
    assert plans.plan_label(plans.LAUNCH_DEFAULT_PLAN) in block

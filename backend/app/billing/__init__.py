"""Billing: subscription plans, action credits, Stripe, referrals.

Layering, innermost first:

  * ``plans``    — pure data. What a plan is, what it grants, what an action
                   costs. No I/O, no DB, importable from anywhere.
  * ``credits``  — the balance. Spend/grant against ``companies.credit_balance``
                   with an append-only ``credit_ledger`` audit trail.
  * ``stripe_client`` — the SDK wrapper. The only module that talks to Stripe.
  * ``referrals``— invite-a-friend, rewarded on the friend's first paid invoice.

Routes live in ``app/routes/billing.py``; nothing in this package imports from
there.
"""

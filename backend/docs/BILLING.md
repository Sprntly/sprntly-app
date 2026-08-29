# Billing — operator guide

How Sprntly charges for itself, and the dashboard work only a human can do.

Companion to `CONNECTORS.md`. Same posture: the code is done, the provider-side
setup is not, and nothing here can be automated from a PR.

---

## The shape of it

| Piece | Where |
|---|---|
| Plans, credit prices, refund window | `app/billing/plans.py` — pure data, no I/O |
| Balance + ledger | `app/billing/credits.py`, `credit_ledger` table |
| Stripe SDK (the only module that talks to it) | `app/billing/stripe_client.py` |
| Webhook handlers | `app/billing/webhooks.py` |
| The one line a surface adds to become billable | `app/billing/enforce.py` |
| Routes | `app/routes/billing.py`, plus `/v1/staff/companies/{id}/billing*` |
| Screen | `web/app/components/screens/app/settings/BillingSettings.tsx` |

**A credit is an ACTION**, not a token and not a dollar. One chat message costs
1, a PRD costs 25. Prices live in `plans.CREDIT_COSTS`; changing a number there
changes pricing for everyone immediately, with no migration.

**Scheduled work is free and absent from the price table entirely** — the Top
Insights brief, connector syncs, KG ingest. A user cannot see, predict or
decline those, and billing someone for work they did not ask for is a refund
request with extra steps.

---

## Operator setup

Everything below is Stripe dashboard work. None of it can be done from a PR.

### 1. Account

Create the Stripe account and note the **country the Sprntly entity is
incorporated in**. It decides which payment methods are available (notably
whether the wallet-based ones can ride on Stripe at all), tax handling, and
payouts.

### 2. Products and prices

Create two Products, each with a monthly and an annual Price:

| Product | Monthly | Annual |
|---|---|---|
| Starter | $59 | $590 |
| Product Builder | $99 | $990 |

Prices are created **in the dashboard, not in code**, so a price change does not
need a deploy. Copy the four `price_…` ids into the env vars in
`.env.example`. An unset one makes that plan/interval unbuyable and the route
returns 503 rather than silently selling something else.

Team and Enterprise deliberately have **no price**. Both are invoiced; the
checkout route rejects them by name and the screen shows a "Talk to sales" link.

### 3. Discount codes

The pricing table's "monthly w/ code" column ($35 against $59, $59 against $99)
is **one Stripe Coupon plus promotion codes**, not application code. There is no
code generator and no codes table.

- Create a Coupon at ~40.7% off (`$59 → $35`) — or whatever rate is current.
- Generate promotion codes off it in the dashboard.

Checkout sessions are created with `allow_promotion_codes=true`, so a code
entered at checkout just works. Staff can mint new codes without a deploy.

### 4. Webhook endpoint

Point it at `POST /v1/billing/webhook` on the target environment, e.g.
`https://api.staging.sprntly.ai/v1/billing/webhook`.

Subscribe to exactly these:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

Copy the signing secret into `STRIPE_WEBHOOK_SECRET`. **Verification fails
closed without it** — an unverified webhook body is attacker input that grants
credits and changes plans, so a missing secret must never mean "accept
anything".

Two behaviours worth knowing when reading the dashboard's delivery log:

- The endpoint returns **200 even when a handler raises**. Stripe retries
  non-2xx for three days and disables endpoints that keep failing; one broken
  handler must not stall the whole event stream. Failures log loudly instead,
  and every handler is idempotent, so replaying an event from the dashboard is
  safe.
- Events we do not act on are acknowledged and ignored, so the log will show
  plenty of 200s that did nothing.

### 5. Customer portal

Settings → Billing → Customer portal. Enable it, and allow **cancellation** and
**plan switching**.

This is deliberately load-bearing: the portal supplies card updates, invoice
history, receipts and cancellation, and none of those are built in-app. `web/`
is a static export with no server, so hosted checkout and hosted portal are also
what keep card data out of our PCI scope entirely.

**Cancelling does not refund.** See below.

---

## Turning the paywall on

`BILLING_ENFORCED` defaults to **false** and is separate from having credentials
on purpose.

Two reasons. Staging shares the **production** Supabase project, so a paywall
that switched itself on at merge would start refusing real customers'
generations before anyone had looked at it — and every existing tenant is
sitting on a zero balance. And keying it on `bool(STRIPE_SECRET_KEY)` would mean
an env var going missing in production silently makes everything free, which is
a fail-open on a money path.

Rollout order:

1. Merge with `BILLING_ENFORCED=false`. Nothing changes for anyone.
2. Configure Stripe per above, in **test mode**.
3. Put a test company through checkout. Watch `checkout.session.completed` and
   `invoice.paid` land, and confirm the Billing screen shows the plan and a
   full credit balance.
4. Recalibrate `CREDIT_COSTS` against real data (below).
5. Decide what existing tenants get (below).
6. Flip `BILLING_ENFORCED=true`.

---

## Two decisions still outstanding

### Credit prices are estimates

`plans.CREDIT_COSTS` was set from the relative *shape* of the work — a PRD is a
long streamed generation, a chat turn is one short call — **not from measured
spend**. To recalibrate, use the usage ledger that already exists:

```sql
select feature, sum(est_cost_usd), count(*)
  from llm_usage_events
 where created_at > now() - interval '90 days'
 group by feature;
```

Divide to get cost per action, then scale so the cheapest real action lands at
1 credit. `llm_usage_events` is analytics and is deliberately lossy — never sum
it against `credit_ledger`, which measures a different thing and will not agree.

### Existing tenants start on Starter

`companies.plan` defaults to `'starter'`, so every company that already exists
lands on a 500-credit plan the moment enforcement goes on. That was an explicit
choice over grandfathering them as unlimited.

If it proves too aggressive — a tenant mid-evaluation hitting a wall on day one
— flip `plans.LAUNCH_DEFAULT_PLAN` to `LEGACY` and update the existing rows.
`plan` deliberately carries **no check constraint**, so that is an edit and a
backfill, not a migration.

---

## Refunds

**No free trial.** Instead: pay, and cancel within `REFUND_WINDOW_DAYS` (7) for
a refund.

**Cancelling is self-serve; refunding is not.** An automatic refund on cancel is
trivially farmed — spend the month's credits on day one, cancel on day six, keep
both the output and the money. So:

1. The customer cancels in the Stripe portal.
2. Staff open `GET /v1/staff/companies/{id}/billing` and look at
   `credits_used` and `within_refund_window`.
3. Staff `POST …/billing/refund`, which refunds the latest invoice and by
   default also cancels the subscription.

Refunding outside the window is permitted and reported, so a goodwill refund is
possible without the endpoint pretending the policy was met.

`POST …/billing/credits` grants credits by hand — goodwill, a failed generation,
a support fix. Grants land in the same ledger tagged `adjustment` so the
customer's Billing screen explains where they came from. **Positive only**:
every real reason to reduce a balance already has a path that writes a row
explaining itself.

---

## Referrals

A referral brings a **whole new company** onto Sprntly. It is not
`workspace_invites`, which adds a teammate inside a company you already pay for
— conflating them would let someone farm credit by inviting their own
colleagues.

The flow:

1. The referrer creates an invite in Settings → Billing (max 3) and gets a
   **link** to send themselves. Deliberately not an email: `send_invite_email`
   creates a Supabase user and lands them as a *member* of the referrer's
   company, which is the exact confusion to avoid.
2. The friend opens `/sign-up?ref=<code>`. The code is stashed in localStorage,
   because the company is not created until several onboarding steps later and a
   Google sign-up leaves the site entirely.
3. At company creation the client calls `POST /v1/billing/referrals/claim`.
   **This grants nothing** — signing up is free and infinitely repeatable.
4. The reward fires on that company's **first paid invoice**, in the
   `invoice.paid` handler.

Self-referral (claiming your own code) voids the invite without consuming one of
the three. The remaining hole is one person running two companies under two
addresses, which no in-app check can see; it is bounded at three invites and
gated on a real payment, so the worst case costs a real subscription.

---

## Known gaps

- **A failed generation still bills.** Charging happens at the start of the
  work, not on completion, because billing only successful work means threading
  a charge through the success path of seven runners. The fix is one
  `credits.grant` in each runner's terminal-failure branch — the ledger's
  idempotency index already makes it safe to call twice. Marked with a
  `ponytail:` note in `enforce.py`. Until then, staff adjustments cover it.
- **Top-ups do not survive the next monthly grant.** The balance is one number
  with no per-bucket expiry, and `grant_monthly` sets rather than adds. Splitting
  into granted-vs-purchased buckets is the fix if anyone complains.
- **Wallet-based payment methods are not supported.** Whether they can ride on
  Stripe at all depends on the merchant entity's country; where they cannot, it
  means a second processor with its own subscription state machine and webhook
  set.

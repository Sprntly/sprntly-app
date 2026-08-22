"""The credit balance — spend, grant, and the ledger behind both.

`companies.credit_balance` is the authoritative counter and `credit_ledger` is
the append-only explanation of how it got there. Every mutation goes through
`_apply`, which writes both.

WHY COMPARE-AND-SWAP RATHER THAN A POSTGRES FUNCTION. The obvious shape for
"decrement a balance atomically" is a stored procedure, and it was rejected for
one concrete reason: `FakeSupabaseClient.rpc()` returns canned values and never
executes anything, so the arithmetic on the money path — the single piece of
logic here most worth a test — would have no test coverage at all. A read,
then an `UPDATE … WHERE credit_balance = <the value we read>`, is equally
race-free (the WHERE fails if anyone moved it under us), runs identically on
Postgres and on the SQLite fake, and is testable.

FAIL-CLOSED, unlike the metering next door. `db/llm_usage.py` swallows its own
errors by design because a broken analytics ledger must not break generation.
This module does the opposite: if we cannot tell whether a company can afford
an action, we refuse the action. Silently granting free work on a DB blip is
the failure that costs money.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.billing import plans
from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

# Bounded retries on a lost compare-and-swap. Contention here is two of one
# company's own generations finishing at the same instant, not a hot row shared
# across tenants, so a couple of retries is plenty. Exhausting them raises
# rather than charging nothing.
_CAS_ATTEMPTS = 5


class InsufficientCredits(Exception):
    """Not enough credits to run the requested action.

    Carries the numbers so the route can render "this costs 25, you have 8"
    instead of a bare failure — a message that tells the user what to do next
    (top up, or upgrade) rather than that something went wrong.
    """

    def __init__(self, *, needed: int, balance: int, feature: str) -> None:
        self.needed = needed
        self.balance = balance
        self.feature = feature
        super().__init__(
            f"{feature} costs {needed} credits; balance is {balance}"
        )


def cost_of(feature: str) -> int:
    """Credit price of one action.

    An unpriced feature is charged `DEFAULT_ACTION_COST` and logged rather than
    being free or raising: a new generation surface that nobody added to the
    table should leak a little margin and produce a log line, not 500 on the
    user or silently become the cheapest way to use Sprntly.
    """
    cost = plans.CREDIT_COSTS.get(feature)
    if cost is None:
        logger.warning(
            "credit_cost_missing feature=%s falling_back_to=%s",
            feature,
            plans.DEFAULT_ACTION_COST,
        )
        return plans.DEFAULT_ACTION_COST
    return cost


@retry_on_disconnect
def _read_company(company_id: str) -> dict[str, Any] | None:
    rows = (
        require_client()
        .table("companies")
        .select("id, plan, credit_balance, subscription_status")
        .eq("id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def balance(company_id: str) -> int:
    """Current balance, or `plans.UNLIMITED` for an uncapped plan."""
    row = _read_company(company_id)
    if not row:
        return 0
    if plans.is_unlimited(row.get("plan")):
        return plans.UNLIMITED
    return int(row.get("credit_balance") or 0)


def _is_duplicate(exc: Exception) -> bool:
    """Whether an insert failed because the idempotency index rejected it.

    String matching because the two engines raise unrelated types — psycopg
    surfaces a PostgREST `APIError` with code 23505 through supabase-py, SQLite
    raises `IntegrityError` — and neither is importable here without pulling a
    test dependency into production code.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return "unique" in text or "duplicate" in text or "23505" in text


@retry_on_disconnect
def _insert_ledger(
    *,
    company_id: str,
    delta: int,
    reason: str,
    feature: str | None,
    ref_id: str | None,
    balance_after: int,
    actor_user_id: str | None,
) -> None:
    require_client().table("credit_ledger").insert(
        {
            "company_id": company_id,
            "delta": delta,
            "reason": reason,
            "feature": feature,
            "ref_id": ref_id,
            "balance_after": balance_after,
            "actor_user_id": actor_user_id,
        }
    ).execute()


@retry_on_disconnect
def _compare_and_set(company_id: str, expected: int, new_value: int) -> bool:
    """`UPDATE … SET credit_balance = new WHERE id = ? AND credit_balance = expected`.

    Returns whether the row moved. A false means someone else changed the
    balance between our read and our write, and the caller re-reads.
    """
    rows = (
        require_client()
        .table("companies")
        .update({"credit_balance": new_value})
        .eq("id", company_id)
        .eq("credit_balance", expected)
        .execute()
        .data
        or []
    )
    return bool(rows)


def _apply(
    company_id: str,
    delta: int,
    *,
    reason: str,
    feature: str | None = None,
    ref_id: str | None = None,
    actor_user_id: str | None = None,
) -> int:
    """Move the balance by `delta` and record why. Returns the new balance.

    The ledger row is written FIRST and acts as the idempotency gate: the
    partial unique index on (company_id, reason, ref_id) rejects a replay, and
    we return the unchanged balance instead of applying the delta twice. That
    ordering matters most for grants, where the replay is a Stripe webhook
    retry arriving minutes later.

    # ponytail: crashing between the ledger insert and the balance update
    # loses that one grant permanently — the ledger already claims it happened,
    # so a retry is refused. Chosen deliberately over the reverse ordering,
    # which fails towards granting credits twice; under-granting is visible to
    # the user and fixable with a staff adjustment, over-granting is neither.
    # `balance_after` on every row makes the drift detectable. If this ever
    # needs to be exact, the upgrade is a single transaction, which needs
    # Postgres-side execution and therefore a real DB in the test suite.
    """
    if delta == 0:
        return balance(company_id)

    wrote_ledger = False
    for attempt in range(_CAS_ATTEMPTS):
        row = _read_company(company_id)
        if not row:
            raise InsufficientCredits(
                needed=abs(delta), balance=0, feature=feature or reason
            )

        # Uncapped plans are not metered at all: no balance move, no ledger
        # row. Writing spend rows for a company that can never run out would
        # be pure noise in the one place a support question gets answered.
        if plans.is_unlimited(row.get("plan")):
            return plans.UNLIMITED

        current = int(row.get("credit_balance") or 0)
        new_value = current + delta
        if new_value < 0:
            raise InsufficientCredits(
                needed=abs(delta), balance=current, feature=feature or reason
            )

        if not wrote_ledger:
            try:
                _insert_ledger(
                    company_id=company_id,
                    delta=delta,
                    reason=reason,
                    feature=feature,
                    ref_id=ref_id,
                    balance_after=new_value,
                    actor_user_id=actor_user_id,
                )
            except Exception as exc:  # noqa: BLE001 — re-raised unless duplicate
                if ref_id and _is_duplicate(exc):
                    logger.info(
                        "credit_apply_replayed company=%s reason=%s ref=%s",
                        company_id,
                        reason,
                        ref_id,
                    )
                    return current
                raise
            wrote_ledger = True

        if _compare_and_set(company_id, current, new_value):
            return new_value

        logger.info(
            "credit_cas_retry company=%s reason=%s attempt=%s",
            company_id,
            reason,
            attempt + 1,
        )

    # Every attempt lost the race. The ledger row stands and the balance did
    # not move, which `balance_after` will show — deliberately loud.
    raise RuntimeError(
        f"credit balance for {company_id} could not be updated after "
        f"{_CAS_ATTEMPTS} attempts"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_affordable(company_id: str, feature: str) -> None:
    """Raise `InsufficientCredits` if this action cannot be paid for.

    A pre-flight, called at the START of a generation so the user is told
    before waiting. It does NOT reserve anything: two jobs started at the same
    moment can both pass and the second will fail at `spend`. Reserving would
    mean a hold/release lifecycle and a reaper for holds orphaned by a crashed
    job, which is a lot of machinery to prevent a small overdraft on a balance
    the user tops up anyway.
    """
    row = _read_company(company_id)
    if not row:
        raise InsufficientCredits(needed=cost_of(feature), balance=0, feature=feature)
    if plans.is_unlimited(row.get("plan")):
        return
    needed = cost_of(feature)
    current = int(row.get("credit_balance") or 0)
    if current < needed:
        raise InsufficientCredits(needed=needed, balance=current, feature=feature)


def spend(
    company_id: str,
    feature: str,
    *,
    ref_id: str | None = None,
    actor_user_id: str | None = None,
) -> int:
    """Charge for one completed action. Returns the new balance.

    Called on COMPLETION, not on start: a generation that fails should not bill
    the user for output they never received. Pair it with `check_affordable` at
    the start so a doomed job is refused up front rather than after the wait.

    `ref_id` should be the job/run id, which makes a retried completion
    handler charge once.
    """
    return _apply(
        company_id,
        -cost_of(feature),
        reason="spend",
        feature=feature,
        ref_id=ref_id,
        actor_user_id=actor_user_id,
    )


def grant(
    company_id: str,
    amount: int,
    *,
    reason: str,
    ref_id: str | None = None,
    actor_user_id: str | None = None,
) -> int:
    """Add credits. `reason` is one of the ledger's grant reasons."""
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    return _apply(
        company_id,
        amount,
        reason=reason,
        ref_id=ref_id,
        actor_user_id=actor_user_id,
    )


def grant_monthly(company_id: str, plan: str, *, period_start: str) -> int:
    """The billing-period credit grant.

    SET, not add: a plan's monthly credits do not roll over, so the balance is
    replaced rather than accumulated. Purchased top-ups are the deliberate
    exception and DO survive — see the note below.

    # ponytail: top-ups bought late in a period are wiped by the next grant,
    # because balance is one number with no per-bucket expiry. Splitting into
    # granted-vs-purchased buckets is the fix when someone actually complains;
    # until then this is one column instead of two plus an expiry sweep.

    Idempotent on `period_start`: Stripe delivers `invoice.paid` at least once
    and a replay must not hand out a second month of credits.
    """
    monthly = plans.monthly_credits(plan)
    if monthly == plans.UNLIMITED:
        return plans.UNLIMITED

    row = _read_company(company_id)
    current = int((row or {}).get("credit_balance") or 0)
    delta = monthly - current
    if delta == 0:
        # Already exactly at the grant. Still record the period so a later
        # replay is cheap, but there is nothing to move.
        return monthly
    return _apply(
        company_id,
        delta,
        reason="monthly_grant",
        ref_id=period_start,
    )


@retry_on_disconnect
def history(company_id: str, *, limit: int = 50) -> list[dict]:
    """Recent ledger rows, newest first — what the Billing screen renders."""
    return (
        require_client()
        .table("credit_ledger")
        .select(
            "id, delta, reason, feature, balance_after, actor_user_id, created_at"
        )
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def new_ref() -> str:
    """A ref_id for a caller that has no natural one (a manual adjustment)."""
    return uuid.uuid4().hex

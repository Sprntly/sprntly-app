"""Is pre-generating this company's brief drill-downs worth paying for?

Warming exists so the first click on a fresh brief renders instantly. That
trade only pays off when somebody clicks. Measured over the 30 days to
2026-08-24: 632 briefs fanned out ~1,900 speculative PRDs and ~1,900 evidence
pages — roughly $400/month — and of those PRDs, 72 were ever ticketed, edited
or re-saved. (Nothing logs a PRD being READ, so 72 is a floor on engagement,
not a headcount. The warm fires before anyone could have clicked, though, so
the fan-out is speculative whichever way the reads fall.)

This module is the smaller half of the response. Splitting that spend by the
rule below, only ~$73/month of it sat in workspaces nobody had opened in two
weeks; the rest was in workspaces people DO use, warming insights 2 and 3 that
nobody opened — which is what `settings.evidence_warm_count` and
`prd_warm_count` address. So the gate is worth having, but do not expect it to
carry the saving on its own.

The rule: warm only for a workspace showing a sign of life — a human
conversation inside `settings.warm_active_within_days`, or, for a workspace too
new to have had one, a signup date inside the same window so a first brief
still lands warm.

What a skipped workspace loses is ONLY the speculation. The brief still
generates and still sends, and every drill-down still generates on demand the
moment somebody does click (`routes/evidence.py` and `routes/prd.py` both check
for an existing row and generate when there is not one). The cost of being
wrong is one slow first click, which is exactly what warming was buying back.

Fails OPEN, deliberately. If a lookup raises we warm as before: an unnecessary
warm costs money, a wrongly-skipped warm costs a user their click, and this
module must never be the reason a brief looks broken.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.companies import company_created_at
from app.db.conversations import latest_conversation_at

logger = logging.getLogger(__name__)


def _parse_ts(raw: str | None) -> datetime | None:
    """Postgres timestamptz → aware datetime, or None if unparseable.

    PostgREST renders microseconds and a `+00:00` offset, which
    `datetime.fromisoformat` handles on 3.11+; the `Z` spelling does not appear
    today but is normalised anyway so a driver change can't silently turn every
    workspace inactive. A naive value is read as UTC, which is what the column
    stores.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("warm-gate: unparseable activity timestamp %r", raw)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _within_onboarding_grace(company_id: str, within_days: int) -> bool:
    """Whether a company with NO conversation history is new enough to warm for.

    A brand-new workspace has never had a conversation by definition, so the
    activity check alone would skip warming for exactly the user whose first
    click matters most. Its age stands in for activity until it has a real
    history: inside `within_days` of signup it warms, after that the silence is
    the answer. Fails open, like every other lookup in this module.
    """
    try:
        created = _parse_ts(company_created_at(company_id))
    except Exception:  # noqa: BLE001 — fail open; never block warming on a read
        logger.exception(
            "warm-gate: company lookup failed for company=%s — warming anyway",
            company_id,
        )
        return True
    if created is None:
        # Unknown age and no conversations. Nothing here says anybody is
        # waiting, and the on-demand path still covers the click.
        logger.info(
            "warm-gate: skipping warm for company=%s (no conversations, unknown age)",
            company_id,
        )
        return False
    fresh = (datetime.now(timezone.utc) - created) <= timedelta(days=within_days)
    if not fresh:
        logger.info(
            "warm-gate: skipping warm for company=%s (no conversations since signup)",
            company_id,
        )
    return fresh


def should_warm_drilldowns(company_id: str | None) -> bool:
    """Whether `company_id` has been active recently enough to warm for.

    True whenever the gate is switched off (`warm_active_within_days <= 0`),
    when there is no company to judge, or when the lookup fails — see the
    module docstring on failing open.
    """
    within_days = settings.warm_active_within_days
    if within_days <= 0 or not company_id:
        return True
    try:
        raw = latest_conversation_at(company_id)
    except Exception:  # noqa: BLE001 — fail open; never block warming on a read
        logger.exception(
            "warm-gate: activity lookup failed for company=%s — warming anyway",
            company_id,
        )
        return True
    last = _parse_ts(raw)
    if last is None and raw is not None:
        # A row exists but its timestamp did not parse. That is a fault in us,
        # not evidence the workspace is idle, so it takes the same fail-open
        # path as a failed read — `_parse_ts` has already logged the value.
        return True
    if last is None:
        # No conversation has ever happened here. That covers two very different
        # workspaces, so fall through to the workspace's own age rather than
        # treating them alike: one that was created last week and is about to
        # meet its first brief (warm — a slow first click is the worst possible
        # first impression), and one that has sat untouched since signup
        # (skip). See `_within_onboarding_grace`.
        return _within_onboarding_grace(company_id, within_days)
    age = datetime.now(timezone.utc) - last
    active = age <= timedelta(days=within_days)
    if not active:
        logger.info(
            "warm-gate: skipping drill-down warm for company=%s "
            "(last activity %.1f days ago, threshold %d)",
            company_id, age.total_seconds() / 86400, within_days,
        )
    return active

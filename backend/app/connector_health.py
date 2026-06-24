"""Scheduled connector health monitor.

Makes the connector status badge proactive. The on-open "Test connection" check
(routes/connectors.py) only runs when a user opens a connector's drawer — so a
connector whose OAuth/API token died (revoked, expired-with-no-refresh, secret
rotated) stays silently "Active" in the list until someone happens to open it
and an agent run hits a wall.

This job re-validates every active connector's stored credential on an interval
(default hourly) using the SAME per-provider probe the route uses
(app.connector_probe.probe_connection), persists the result onto the connection
row, and emails a single admin alert when a connector transitions
healthy→disconnected. It does NOT re-alert on a connector already known
disconnected (no hourly spam), and it does NOT alert on recovery
(disconnected→connected) — that's just logged.

Fail-OPEN per connection: a transport blip or unexpected probe error never marks
a good connector dead and never pages — it's logged and the row is left as-is,
mirroring signin_monitor's stance. We only persist 'disconnected' on a definitive
provider rejection.

A `connector_health_min_recheck_minutes` throttle skips any connection the on-open
test (or a previous sweep) checked recently, so the scheduled and interactive
checks don't double-probe the same token.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.config import settings
from app.connector_probe import ProbeError, probe_connection

logger = logging.getLogger(__name__)

HEALTH_CONNECTED = "connected"
HEALTH_DISCONNECTED = "disconnected"


def _parse_dt(value) -> datetime | None:
    """Parse a stored ISO timestamp into an aware UTC datetime, or None.

    Supabase hands timestamps back as ISO strings (sometimes with a trailing
    'Z'). Naive values are assumed UTC. Anything unparseable returns None so the
    throttle treats the row as 'never checked' rather than crashing the sweep."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _company_of(row: dict) -> str:
    """The owning tenant id from a connection row (new or legacy column)."""
    return str(row.get("company_id") or row.get("workspace_id") or "")


def _account_label(row: dict) -> str:
    return str(row.get("account_label") or row.get("google_email") or "")


async def run_connector_health_check() -> dict:
    """Scheduler entrypoint: probe every active connector, persist health, and
    alert once on each healthy→disconnected transition.

    Returns a summary dict ``{checked, healthy, disconnected, skipped}`` for
    logging and tests. Never raises — per-connection errors are isolated and
    fail open.
    """
    try:
        rows = db.list_all_active_connections() or []
    except Exception:
        logger.exception("connector_health: failed to list active connections")
        return {"checked": 0, "healthy": 0, "disconnected": 0, "skipped": 0}

    now = datetime.now(timezone.utc)
    throttle = timedelta(minutes=settings.connector_health_min_recheck_minutes)

    checked = healthy = disconnected = skipped = 0
    transitioned: list[dict] = []  # rows that went healthy→disconnected this run

    for row in rows:
        # Throttle: skip rows the on-open test (or a recent sweep) just checked,
        # so the two paths don't double-probe the same token.
        last = _parse_dt(row.get("last_health_check_at"))
        if last is not None and now - last < throttle:
            skipped += 1
            continue

        provider = (row.get("provider") or "").strip()
        if not provider:
            skipped += 1
            continue

        prev_health = row.get("health")
        checked_at = now.isoformat()

        # Probe off the event loop — the provider calls are blocking HTTP.
        try:
            is_healthy, detail = await asyncio.to_thread(
                probe_connection, provider, row
            )
        except ProbeError as e:
            if e.reason == "rejected":
                # Definitive provider rejection — this connector is dead.
                is_healthy, detail = False, str(e)
            else:
                # unreadable / unsupported token — fail OPEN: don't mark a row
                # dead off an internal decode/dispatch issue. Log and move on.
                logger.warning(
                    "connector_health: %s/%s probe inconclusive (%s) — leaving as-is",
                    _company_of(row), provider, e.reason,
                )
                skipped += 1
                continue
        except Exception as exc:  # noqa: BLE001 — a blip must never page
            logger.warning(
                "connector_health: %s/%s probe transport error: %s — failing open",
                _company_of(row), provider, type(exc).__name__,
            )
            skipped += 1
            continue

        checked += 1
        new_health = HEALTH_CONNECTED if is_healthy else HEALTH_DISCONNECTED
        try:
            db.set_connection_health(
                row["id"],
                health=new_health,
                error=None if is_healthy else detail,
                checked_at=checked_at,
            )
        except Exception:
            logger.exception(
                "connector_health: failed to persist health for %s/%s",
                _company_of(row), provider,
            )

        if is_healthy:
            healthy += 1
            if prev_health == HEALTH_DISCONNECTED:
                # Recovery: log only, never alert.
                logger.info(
                    "connector_health: %s/%s recovered (disconnected→connected)",
                    _company_of(row), provider,
                )
            continue

        disconnected += 1
        # Transition alert ONLY on healthy(or never-checked)→disconnected, never
        # on an already-disconnected row — that's how we avoid hourly spam.
        if prev_health != HEALTH_DISCONNECTED:
            logger.critical(
                "connector_health: %s/%s went disconnected — %s",
                _company_of(row), provider, detail,
            )
            transitioned.append({**row, "_health_error": detail})
        else:
            logger.info(
                "connector_health: %s/%s still disconnected (no repeat alert)",
                _company_of(row), provider,
            )

    if transitioned:
        _send_alert(transitioned)

    summary = {
        "checked": checked,
        "healthy": healthy,
        "disconnected": disconnected,
        "skipped": skipped,
    }
    logger.info("connector_health: sweep complete — %s", summary)
    return summary


def _format_alert(rows: list[dict]) -> tuple[str, str, str]:
    """Build ``(subject, text_body, html_body)`` for one owner's disconnected
    connector(s). Owner-addressed copy — it's *their* connector to reconnect."""
    from app.db.companies import slug_for_company_id

    lines: list[str] = []
    for row in rows:
        company = _company_of(row)
        try:
            company = slug_for_company_id(company) or company
        except Exception:  # noqa: BLE001 — name lookup must not break the alert
            pass
        provider = row.get("provider") or "?"
        label = _account_label(row) or "(no account label)"
        error = row.get("_health_error") or "credential rejected"
        lines.append(f"{provider} — {company} — {label} — {error}")

    n = len(rows)
    subject = f"🔴 Sprntly: reconnect your {'connector' if n == 1 else f'{n} connectors'}"
    intro = (
        f"{'A connector you' if n == 1 else 'Connectors you'} connected to Sprntly "
        f"{'has' if n == 1 else 'have'} stopped working — the stored credential was "
        "rejected. Reconnect in Settings → Connectors:"
    )
    text_body = intro + "\n\n" + "\n".join(f"  • {ln}" for ln in lines)
    html_body = (
        f"<p>{intro}</p><ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>"
    )
    return subject, text_body, html_body


def _send_alert(connection_rows: list[dict]) -> None:
    """Email each disconnected connector's OWNER — the user who connected it.

    Resolves ``connection.user_id`` → email via the ``profiles`` table and groups
    connectors by owner, so each owner gets ONE email about their own
    connector(s). Any connector whose owner email can't be resolved falls back to
    the configured admin address (``connector_health_alert_email``, then
    ``signin_monitor_alert_email``) so it's never silently dropped. With no
    RESEND_API_KEY — or nothing left to route — logs a warning and no-ops."""
    api_key = settings.resend_api_key
    if not api_key:
        logger.warning(
            "connector_health: RESEND_API_KEY not set; "
            "%d disconnected connector(s) logged only",
            len(connection_rows),
        )
        return

    from app.db.profiles import emails_for_user_ids
    from app.synthesis.email_delivery import _send_via_resend

    try:
        owner_emails = emails_for_user_ids([r.get("user_id") for r in connection_rows])
    except Exception:  # noqa: BLE001 — a profiles lookup failure falls back to admin
        logger.exception("connector_health: owner-email lookup failed; using fallback")
        owner_emails = {}

    fallback = (
        settings.connector_health_alert_email or settings.signin_monitor_alert_email
    )

    # Route each connector to its owner's email; unresolvable owners → the admin
    # fallback. Group by recipient so each person gets a single email.
    by_recipient: dict[str, list[dict]] = {}
    unrouted = 0
    for row in connection_rows:
        to = owner_emails.get(str(row.get("user_id") or "")) or fallback
        if not to:
            unrouted += 1
            continue
        by_recipient.setdefault(to, []).append(row)

    if unrouted:
        logger.warning(
            "connector_health: %d disconnected connector(s) had no resolvable "
            "owner email and no fallback configured — logged only",
            unrouted,
        )

    for to, rows in by_recipient.items():
        subject, text_body, html_body = _format_alert(rows)
        try:
            _send_via_resend(
                api_key, to=to, subject=subject,
                html_body=html_body, text_body=text_body,
            )
            logger.info(
                "connector_health: alert sent to %s (%d connector(s))", to, len(rows)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "connector_health: failed to send alert to %s: %s",
                to, type(exc).__name__,
            )

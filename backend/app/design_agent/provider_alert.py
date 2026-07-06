"""Best-effort operator alert for a Design Agent provider hard-stop.

When the run loop hits an alertable provider failure (a billing / credit
hard-stop the team must act on), we email the ops address once per cooldown
window. Mirrors the fail-open Resend pattern in routes/feedback.py: a disabled
or failed send NEVER raises and NEVER breaks the run — the caller is the
terminal catch of the agent loop.

No user PII and no raw provider text ever enter the alert body.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app import config
from app.design_agent.provider_errors import ProviderErrorClass, is_alertable

logger = logging.getLogger(__name__)

# Dedup: last send time (monotonic) per class value. Kept module-level so the
# cooldown survives across runs within a process.
_last_sent: dict[str, float] = {}
_COOLDOWN_SECONDS = 15 * 60


def maybe_alert_provider_outage(
    cls: ProviderErrorClass, *, context: dict | str
) -> None:
    """Fire a deduped, fail-open ops alert for an alertable provider hard-stop.

    Sync + fully self-guarded: safe to call directly from the run-loop catch.
    Never raises.
    """
    if not is_alertable(cls):
        return

    now = time.monotonic()
    last = _last_sent.get(cls.value)
    if last is not None and (now - last) < _COOLDOWN_SECONDS:
        return
    # Mark the attempt window whether or not the send succeeds, so a failing
    # provider can't turn into an alert storm.
    _last_sent[cls.value] = now

    settings = config.settings
    raw_recipients = getattr(settings, "design_agent_alert_email", "") or ""
    # Accept a comma-separated list: split, strip, drop empties, dedup
    # case-insensitively while preserving the first-seen order.
    seen: set[str] = set()
    recipients: list[str] = []
    for token in raw_recipients.split(","):
        addr = token.strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        recipients.append(addr)

    api_key = settings.resend_api_key
    if not recipients or not api_key:
        logger.warning(
            "provider alert skipped — no recipient/RESEND_API_KEY (class=%s)",
            cls.value,
        )
        return

    if isinstance(context, dict):
        reference = str(context.get("prototype_id", "unknown"))
    else:
        reference = str(context)

    when = datetime.now(timezone.utc).isoformat()
    subject = f"[Sprntly] Design Agent provider hard-stop: {cls.value}"
    text = (
        "A Design Agent run hit a provider hard-stop.\n\n"
        f"Class: {cls.value}\n"
        f"When (UTC): {when}\n"
        f"Reference (prototype id): {reference}\n\n"
        "Action: Top up Anthropic credits.\n"
    )
    html_body = (
        "<p>A Design Agent run hit a provider hard-stop.</p>"
        f"<p><strong>Class:</strong> {cls.value}<br/>"
        f"<strong>When (UTC):</strong> {when}<br/>"
        f"<strong>Reference (prototype id):</strong> {reference}</p>"
        "<p><strong>Action:</strong> Top up Anthropic credits.</p>"
    )

    try:
        from app.synthesis import email_delivery

        # One send per recipient, isolated: a bad address for one recipient
        # must never stop the alert from reaching the rest.
        for addr in recipients:
            try:
                email_delivery._send_via_resend(
                    api_key, to=addr, subject=subject, html_body=html_body,
                    text_body=text,
                )
            except Exception:  # noqa: BLE001 — one bad address never blocks the rest
                logger.exception("provider outage alert send failed for one recipient")
        logger.info(
            "provider outage alert sent to %d recipient(s) (class=%s)",
            len(recipients), cls.value,
        )
    except Exception:  # noqa: BLE001 — alert is best-effort; never break the run
        logger.exception("provider outage alert delivery failed (class=%s)", cls.value)

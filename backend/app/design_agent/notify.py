"""Channel-agnostic prototype-ready notification seam.

`notify_prototype_ready` is the ONLY entry point the completion path calls.
Delivery is dispatched through the `_PROVIDERS` registry with a normalized,
channel-neutral payload (workspace_id / recipient_user_id / prototype_id /
text / blocks), so adding a new channel later — Teams, email, a shared Slack
channel target — is one provider function + one registry entry + config; the
call-site in routes/design_agent.py never changes.

The first (and default) provider is a Slack DM to the user who generated the
prototype, reusing the existing per-user Slack delivery plumbing
(`connections` rows + `slack_oauth.post_to_target`) — no new sender, no new
OAuth scopes. The `{"target_type": "dm"}` override is the locked default:
this is a personal, transactional ping, so the user's weekly-brief channel
preference is deliberately NOT consulted.

Side-effect discipline (mirrors brief_nudge._deliver_to_one): this module
NEVER raises — every failure path returns a `reason` dict — and log lines
carry identifiers only (prototype ids, delivered flags, reason codes; never
PRD titles/bodies, Slack display names, or token material).

When the recipient's workspace has NO Slack connection at all
(`_deliver_slack` returns `reason="slack_not_connected"`), the entry point
retries once via the `email` provider — the same transactional-ping copy,
sent through the existing drip-email transport. A connection that EXISTS but
is broken (`token_unreadable` / `no_bot_token`) does NOT fall back to email;
those indicate a live connection-level problem an operator should see, not a
delivery gap to paper over.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app.config import settings
from app.connectors import slack_oauth
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db.connections import list_slack_connections
from app.db.prds import get_prd
from app.db.profiles import emails_for_user_ids
from app.db.prototypes import get_prototype
from app.drip_email import send_drip_email

logger = logging.getLogger(__name__)

# Fallback title for the message copy when the PRD row is missing/unreadable.
_FALLBACK_TITLE = "your PRD"


def _prototype_deep_link(prototype_id: int) -> str:
    """The one CTA target — the prototype page in the app. Mirrors
    brief_nudge.brief_deep_link's frontend_url pattern."""
    base = (settings.frontend_url or "https://app.sprntly.ai").rstrip("/")
    return f"{base}/prototype?pid={prototype_id}"


def _prd_title(prd_id: Any) -> str:
    """Best-effort PRD title for the message copy. Falls back to a neutral
    phrase — the notification must survive a missing/unreadable PRD row."""
    if prd_id is None:
        return _FALLBACK_TITLE
    try:
        prd = get_prd(prd_id)
    except Exception:  # noqa: BLE001 — copy fallback; never surfaces.
        return _FALLBACK_TITLE
    title = (prd or {}).get("title")
    return title if isinstance(title, str) and title.strip() else _FALLBACK_TITLE


def _deliver_slack(
    *,
    workspace_id: str,
    recipient_user_id: str,
    prototype_id: int,
    text: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Slack-DM provider arm: resolve the CREATOR's own connection row within
    the workspace, decrypt their bot token, and DM their authed_user_id."""
    rows = list_slack_connections(workspace_id)
    row = next((r for r in rows if r.get("user_id") == recipient_user_id), None)
    if row is None:
        return {"delivered": False, "provider": "slack", "reason": "slack_not_connected"}
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError, KeyError):
        return {"delivered": False, "provider": "slack", "reason": "token_unreadable"}
    bot_token = token_json.get("access_token") or ""
    if not bot_token:
        return {"delivered": False, "provider": "slack", "reason": "no_bot_token"}
    # LOCKED default: force the DM target. The connection's stored config
    # (the weekly-brief channel preference) is deliberately ignored here.
    # An un-scoped workspace / Slack rejection raises HTTPException inside
    # post_to_target — caught by the entry point's never-raises guard.
    slack_oauth.post_to_target(
        bot_token,
        config={"target_type": slack_oauth.TARGET_DM},
        authed_user_id=token_json.get("authed_user_id"),
        text=text,
        blocks=blocks,
    )
    return {"delivered": True, "provider": "slack", "reason": None}


def _deliver_email(
    *,
    workspace_id: str,
    recipient_user_id: str,
    prototype_id: int,
    text: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Email fallback provider arm — dispatched ONLY when the Slack provider
    reports 'slack_not_connected' (see the fallback branch in
    notify_prototype_ready). Resolves the recipient's email via the existing
    profiles lookup (emails_for_user_ids) and sends the SAME title+deep-link
    copy the Slack provider would have sent (the `text` field of the
    channel-neutral payload) through the existing drip-email Resend
    transport (send_drip_email) and its already-branded HTML template
    (render_drip_html, applied internally by send_drip_email) — no new
    sender, no new template.

    `workspace_id` (email has no per-workspace concept) and `blocks`
    (Slack-only) are accepted for call-site symmetry — every provider in the
    registry is dispatched with the same normalized payload — but unused
    here."""
    del workspace_id, blocks
    email = (emails_for_user_ids([recipient_user_id]) or {}).get(recipient_user_id)
    if not email:
        return {"delivered": False, "provider": "email", "reason": "no_email"}
    # Subject is a static string, not threaded through the shared payload:
    # adding a `subject` field would widen the channel-neutral payload and
    # break test_provider_registry_dispatch's exact-key-set pin (Check 6
    # below). Title + deep link both already live in `text`, which becomes
    # the email body — the recipient reads the identical content either
    # channel would have shown them.
    ok = send_drip_email(
        to_email=email,
        subject="Your prototype is ready",
        body_text=text,
    )
    if not ok:
        return {"delivered": False, "provider": "email", "reason": "send_failed"}
    return {"delivered": True, "provider": "email", "reason": None}


# Provider registry: name → callable taking the normalized payload. Adding a
# channel = one function above + one entry here (+ config to select it).
_PROVIDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "slack": _deliver_slack,
    "email": _deliver_email,
}

# Which provider the completion hook uses today. A future channel answer
# (Teams / shared channel) swaps this via config — not the call-site. Email
# is fallback-only (see notify_prototype_ready) and never the default.
_DEFAULT_PROVIDER = "slack"


def _log_outcome(prototype_id: int, outcome: dict[str, Any]) -> dict[str, Any]:
    """One structured line per attempt (Rule #24): identifiers only."""
    logger.info(
        "prototype_ready_notify prototype_id=%s delivered=%s reason=%s",
        prototype_id, outcome.get("delivered"), outcome.get("reason"),
    )
    return outcome


def notify_prototype_ready(*, prototype_id: int, workspace_id: str) -> dict[str, Any]:
    """Channel-agnostic prototype-ready notification. Never raises.

    Returns {delivered: bool, provider: str | None, reason: str | None}.
    Static copy only — a transactional ping, no LLM call. Guards, in order:
    kill switch off → 'disabled'; row missing or created_by_user_id NULL
    (legacy/pre-column rows) → 'no_recipient'; then the provider arm reports
    its own reasons (e.g. 'slack_not_connected'). Any exception is caught,
    logged as a WARNING (identifiers only), and returned as reason='error'.
    """
    provider_name: str | None = None
    try:
        if not settings.prototype_ready_notify_enabled:
            return _log_outcome(
                prototype_id,
                {"delivered": False, "provider": None, "reason": "disabled"},
            )
        row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
        recipient_user_id = (row or {}).get("created_by_user_id")
        if not recipient_user_id:
            return _log_outcome(
                prototype_id,
                {"delivered": False, "provider": None, "reason": "no_recipient"},
            )
        title = _prd_title(row.get("prd_id"))
        deep_link = _prototype_deep_link(prototype_id)
        text = f'Your prototype for "{title}" is ready — {deep_link}'
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f'Your prototype for "{title}" is ready.\n'
                        f"<{deep_link}|Open prototype>"
                    ),
                },
            }
        ]
        provider_name = _DEFAULT_PROVIDER
        deliver = _PROVIDERS[provider_name]
        # The normalized payload every provider receives — channel-neutral by
        # contract (the seam's whole point; pinned by the registry tests).
        payload = dict(
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            prototype_id=prototype_id,
            text=text,
            blocks=blocks,
        )
        outcome = deliver(**payload)
        # Slack -> email fallback (this ticket): ONLY when the default
        # provider is "slack" and it reports the recipient has NO Slack
        # connection at all ("slack_not_connected"). A connection that
        # EXISTS but is broken ("token_unreadable" / "no_bot_token") does
        # NOT fall back here — see the module docstring / Context.
        if (
            provider_name == "slack"
            and not outcome.get("delivered")
            and outcome.get("reason") == "slack_not_connected"
        ):
            provider_name = "email"
            outcome = _PROVIDERS["email"](**payload)
        return _log_outcome(prototype_id, outcome)
    except Exception as exc:  # noqa: BLE001 — a notify failure never propagates.
        # Identifiers + error class only: the exception text can carry Slack
        # API detail we don't want in logs.
        logger.warning(
            "prototype_ready_notify prototype_id=%s delivered=%s reason=%s error_class=%s",
            prototype_id, False, "error", type(exc).__name__,
        )
        return {"delivered": False, "provider": provider_name, "reason": "error"}

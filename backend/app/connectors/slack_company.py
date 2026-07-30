"""Company-level Slack resolution — the voice-of-customer side of Slack.

Slack plays two roles with two different scopes:

  Delivery (communication)  PER-USER. Each member installs the bot to pick
                            their own notification target (channel or DM),
                            and those rows stay private to their owner —
                            member B never reads member A's delivery config
                            or acts as A's bot (see test_slack_per_user).

  Voice of customer         COMPANY-LEVEL. One workspace install (in
  (corpus/KG pulling)       practice the PM's) serves the whole company:
                            every member sees the company connection on the
                            Voice shelf, admins pick ONE set of pull
                            channels, and the scheduled sync runs once per
                            company — never once per user.

The "company sync row" is the ACTIVE Slack connection that carries the
pull-channel selection (sync_channel_ids); before any selection is saved it
falls back to the oldest active install. All VoC reads and writes (channel
listing fallback, sync-channel saves, corpus syncs, the scheduled refresh)
resolve through here so the whole company converges on the same row.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app import db
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

logger = logging.getLogger(__name__)


class CompanySlackError(Exception):
    """Raised when the company Slack row exists but is unusable (bad token)."""


def row_config(row: dict[str, Any]) -> dict[str, Any]:
    """The connection row's config as a dict ({} on any parse failure)."""
    try:
        cfg = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def resolve_company_slack_row(company_id: str) -> dict[str, Any] | None:
    """The company's Slack connection for voice-of-customer work, or None.

    Preference order:
      1. the active row whose config carries a pull-channel selection
         (most recently updated wins — that's the one an admin configured);
      2. the oldest active install (the PM's, in practice).
    """
    from app.connectors.slack_sync import CONFIG_SYNC_CHANNEL_IDS

    rows = [
        r for r in db.list_slack_connections(company_id)
        if r.get("status") == "active"
    ]
    if not rows:
        return None
    configured = [r for r in rows if row_config(r).get(CONFIG_SYNC_CHANNEL_IDS)]
    if configured:
        return max(configured, key=lambda r: r.get("updated_at") or "")
    return min(rows, key=lambda r: r.get("created_at") or "")


def company_slack_token(company_id: str) -> tuple[str, dict[str, Any]] | None:
    """(bot_token, row) for the company sync row; None when no active install.

    Raises CompanySlackError when a row exists but its token can't be used —
    callers decide whether that's a 500 (routes) or a logged skip (scheduler).
    """
    row = resolve_company_slack_row(company_id)
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise CompanySlackError("Slack token unreadable") from e
    token = token_json.get("access_token")
    if not token:
        raise CompanySlackError("Slack token has no access_token")
    return token, row

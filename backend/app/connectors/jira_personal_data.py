"""Atlassian Personal Data Reporting for the Jira connector (GDPR compliance).

A distributed (public) Atlassian OAuth 2.0 app that stores personal data MUST
periodically report the Atlassian accountIds it holds data for, and erase data
for accounts Atlassian flags as closed. Unlike a webhook, this is an OUTBOUND
obligation: WE push our accountIds to Atlassian and act on the response.

Ref: https://developer.atlassian.com/cloud/jira/platform/user-privacy-developer-guide/

What personal data we store per Jira connection (in `connections.config.user`):
`accountId`, `emailAddress`, `displayName` — fetched from `/myself` at connect —
plus the OAuth tokens. We only store the *connecting* user's accountId (issue
assignees are stored as display-name strings, not accountIds), so the set of
reportable accounts is exactly the set of Jira connections.

Cycle (run daily; Atlassian's cycle period is 7 days):
    1. collect_reportable_accounts() → [{accountId, updatedAt}] from jira rows
    2. report to POST https://api.atlassian.com/app/report-accounts/ (≤90/batch)
    3. 204 → nothing to do; 200 → each account has status:
         "closed"  → the user closed their account / requested erasure → erase()
         "updated" → their profile changed → refresh() our stored copy
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from app import db
from app.connectors import jira_oauth
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

REPORT_URL = "https://api.atlassian.com/app/report-accounts/"
_BATCH_MAX = 90          # Atlassian caps each report at 90 accounts
_TIMEOUT = 30
JIRA_PROVIDER = "jira"


def _jira_rows() -> list[dict]:
    """Every Jira connection across all companies, regardless of status —
    a disconnected row still holds the user's personal data until we erase it,
    so it must be reported on. Direct query (list_all_active_connections filters
    to active)."""
    from app.db.client import require_client

    resp = (
        require_client().table("connections")
        .select("*")
        .eq("provider", JIRA_PROVIDER)
        .execute()
    )
    return resp.data or []


def _config_of(row: dict) -> dict:
    cfg = row.get("config")
    if isinstance(cfg, dict):
        return cfg
    try:
        return json.loads(row.get("config_json") or cfg or "{}")
    except (TypeError, ValueError):
        return {}


def collect_reportable_accounts() -> list[dict[str, str]]:
    """Return `[{accountId, updatedAt, company_id}]` for every Jira connection
    that stores a user accountId. `updatedAt` (RFC 3339) is when we retrieved
    that personal data — the connection's `created_at` (or a stored
    `user_fetched_at` when present). Deduped by accountId (earliest updatedAt
    kept isn't important; we keep one row per account for reporting, but retain
    company_id lists for erasure via a separate lookup)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in _jira_rows():
        cfg = _config_of(row)
        acct = ((cfg.get("user") or {}) or {}).get("accountId")
        if not acct or acct in seen:
            continue
        seen.add(acct)
        updated_at = (
            cfg.get("user_fetched_at")
            or row.get("created_at")
            or row.get("updated_at")
        )
        if not updated_at:
            continue
        out.append({"accountId": str(acct), "updatedAt": str(updated_at),
                    "company_id": row.get("company_id") or ""})
    return out


def _valid_token_from_row(row: dict) -> str | None:
    """Decrypt (and refresh + persist if expiring) a connection's Jira access
    token. Returns None if unusable. Used both to obtain an app bearer token for
    the report call and to refresh a user's profile."""
    try:
        tj = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, KeyError, ValueError):
        return None
    refresh = tj.get("refresh_token")
    obtained = tj.get("obtained_at", 0)
    expires_in = tj.get("expires_in", 3600)
    if refresh and time.time() > obtained + expires_in - 120:
        try:
            tj = json.loads(jira_oauth.token_payload_to_store(
                jira_oauth.refresh_access_token(refresh)))
            db.update_connection_tokens(
                row.get("company_id") or "", JIRA_PROVIDER,
                encrypt_token_json(json.dumps(tj)))
        except Exception:  # noqa: BLE001 — a dead token just can't be used here
            return None
    return tj.get("access_token") or None


def _app_bearer_token() -> str | None:
    """A valid 3LO access token issued to our app, used to authenticate the
    report-accounts call (Atlassian: "use the bearer token from the account that
    owns the app"). Any active connection's token identifies our app, so we use
    the first one we can validate/refresh."""
    for row in _jira_rows():
        if row.get("status") != "active":
            continue
        tok = _valid_token_from_row(row)
        if tok:
            return tok
    return None


def report_batch(token: str, accounts: list[dict[str, str]]) -> list[dict[str, str]]:
    """POST one batch (≤90) to report-accounts. Returns the list of
    `{accountId, status}` needing action (from a 200), or [] on 204 (nothing to
    do). Raises on unexpected transport errors so the caller can log per-cycle."""
    body = {"accounts": [{"accountId": a["accountId"], "updatedAt": a["updatedAt"]}
                         for a in accounts[:_BATCH_MAX]]}
    resp = requests.post(
        REPORT_URL, json=body,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 204:
        return []
    if resp.status_code == 200:
        return (resp.json() or {}).get("accounts", []) or []
    if resp.status_code == 429:
        logger.warning("report-accounts rate-limited (Retry-After=%s); skipping batch",
                       resp.headers.get("Retry-After"))
        return []
    logger.warning("report-accounts failed: %s %s", resp.status_code, resp.text[:200])
    return []


def erase_account(account_id: str) -> int:
    """Erase every Jira connection whose stored user accountId matches — deleting
    the row removes the tokens + email/name/accountId we held. The same Atlassian
    user may have connected to multiple Sprntly companies, so we delete them all.
    Returns the number of connections erased."""
    n = 0
    for row in _jira_rows():
        if ((_config_of(row).get("user") or {}) or {}).get("accountId") != account_id:
            continue
        company_id = row.get("company_id")
        if company_id and db.delete_connection(company_id, JIRA_PROVIDER):
            n += 1
            logger.info("personal-data: erased jira connection for account %s (company %s)",
                        account_id, company_id)
    return n


def refresh_account(account_id: str) -> int:
    """The user's profile changed ("updated") — re-fetch /myself and update our
    stored copy so we hold current data (and re-stamp user_fetched_at). Best-
    effort; returns the number of connections refreshed."""
    n = 0
    for row in _jira_rows():
        cfg = _config_of(row)
        if ((cfg.get("user") or {}) or {}).get("accountId") != account_id:
            continue
        company_id = row.get("company_id")
        cloud_id = cfg.get("cloud_id")
        token = _valid_token_from_row(row)
        if not (company_id and cloud_id and token):
            continue
        user = jira_oauth.fetch_authenticated_user(token, cloud_id)
        if not user:
            continue
        db.patch_connection_config(
            company_id, JIRA_PROVIDER,
            {"user": user, "user_fetched_at": _now_rfc3339()},
        )
        n += 1
    return n


def _now_rfc3339() -> str:
    from app.db.client import utc_now
    return utc_now()


def run_report_cycle() -> dict[str, Any]:
    """One full reporting cycle. Returns a summary for logging/tests."""
    accounts = collect_reportable_accounts()
    summary: dict[str, Any] = {"reported": len(accounts), "closed": 0,
                               "updated": 0, "erased": 0, "batches": 0, "skipped": None}
    if not accounts:
        summary["skipped"] = "no accounts store personal data"
        return summary
    token = _app_bearer_token()
    if not token:
        summary["skipped"] = "no active jira connection to authenticate report"
        return summary

    for i in range(0, len(accounts), _BATCH_MAX):
        batch = accounts[i:i + _BATCH_MAX]
        try:
            actions = report_batch(token, batch)
        except requests.RequestException as e:
            logger.warning("personal-data report batch failed: %s", e)
            continue
        summary["batches"] += 1
        for a in actions:
            acct, status = a.get("accountId"), a.get("status")
            if not acct:
                continue
            if status == "closed":
                summary["closed"] += 1
                summary["erased"] += erase_account(acct)
            elif status == "updated":
                summary["updated"] += 1
                refresh_account(acct)
    logger.info("personal-data cycle: %s", summary)
    return summary

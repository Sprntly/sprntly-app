"""Persisted call transcripts — the digest's store (see the migration for the
decision record).

One row per call, keyed (company_id, provider, external_id) exactly as the
call index keys its metadata; `payload` is the CallTranscript record as a
plain dict, so the digest rebuilds the same object it would have fetched
live. Best-effort by contract: a store failure costs persistence, never the
answer that was being composed when it happened.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "call_transcripts"


def store_call_transcripts(company_id: str, calls: list[Any]) -> int:
    """Upsert CallTranscript records; returns how many were written. Never
    raises — write-through persistence must not break the digest that
    triggered it."""
    if not company_id or not calls:
        return 0
    rows = []
    for call in calls:
        try:
            payload = asdict(call)
        except TypeError:
            payload = dict(call)
        external_id = str(payload.get("external_id") or "")
        if not external_id:
            continue
        rows.append(
            {
                "company_id": company_id,
                "provider": str(payload.get("provider") or "fireflies"),
                "external_id": external_id,
                "call_date": payload.get("date") or None,
                "payload": payload,
                "fetched_at": utc_now(),
            }
        )
    if not rows:
        return 0
    try:
        require_client().table(_TABLE).upsert(
            rows, on_conflict="company_id,provider,external_id"
        ).execute()
        return len(rows)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        logger.warning("call_transcripts: store failed for %s", company_id, exc_info=True)
        return 0


def load_call_transcripts(
    company_id: str, since_iso: str, until_iso: str
) -> dict[str, list[dict]]:
    """Stored transcript payloads for a window, grouped by provider.

    Window filtering happens on `call_date` in the query; rows with a NULL
    date (a provider that sent none) are excluded — the digest's live path
    would have included them, so a window that matters is better served by
    the fallback than by silently missing dated coverage. Never raises;
    a read failure returns {} and the digest falls back to the live fetch."""
    if not company_id:
        return {}
    try:
        # Lower bound in the query, upper bound in Python: ISO-8601 UTC strings
        # compare lexicographically, and the windows the digest asks for end at
        # "now" — so the gte does the real narrowing and the tail filter trims
        # at most a handful of rows. (Also what keeps this readable under the
        # test fake, whose query shim has no lte.)
        resp = (
            require_client().table(_TABLE)
            .select("provider,payload,call_date")
            .eq("company_id", company_id)
            .gte("call_date", since_iso)
            .execute()
        )
    except Exception:  # noqa: BLE001 — a read failure degrades to the live path
        logger.warning("call_transcripts: load failed for %s", company_id, exc_info=True)
        return {}
    grouped: dict[str, list[dict]] = {}
    for row in resp.data or []:
        if str(row.get("call_date") or "") > until_iso:
            continue
        payload = row.get("payload") or {}
        provider = str(row.get("provider") or payload.get("provider") or "fireflies")
        grouped.setdefault(provider, []).append(payload)
    return grouped

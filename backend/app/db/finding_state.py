"""brief_finding_state store — per-theme memory for brief de-duplication.

One row = the convergence FINGERPRINT a theme had the last time it was surfaced
as a weekly-brief finding. `synthesis/dedup.py` reads these to decide whether a
previously-surfaced theme has changed enough to resurface; `run_synthesis`
upserts one per surfaced theme after a brief is saved.

Unique on (enterprise_id, theme_id) — a re-surface refreshes the fingerprint in
place. Mirrors the db/backlog.py upsert pattern.
"""
from __future__ import annotations

import uuid
from typing import Optional

from app.db.client import require_client, utc_now


def get_finding_states(
    enterprise_id: str,
    theme_ids: Optional[list[str]] = None,
    *,
    client=None,
) -> dict[str, dict]:
    """Return {theme_id: row} for an enterprise's recorded finding states.

    `theme_ids` optionally narrows the set (filtered in Python so it behaves
    identically against real Supabase and the in-memory test fake; per-enterprise
    volumes are tiny — one row per theme ever surfaced)."""
    cli = client or require_client()
    rows = (
        cli.table("brief_finding_state").select("*")
        .eq("enterprise_id", enterprise_id).execute().data or []
    )
    wanted = set(theme_ids) if theme_ids is not None else None
    out: dict[str, dict] = {}
    for r in rows:
        tid = r.get("theme_id")
        if tid and (wanted is None or tid in wanted):
            out[tid] = r
    return out


def upsert_finding_state(
    enterprise_id: str,
    *,
    theme_id: str,
    signal_count: int,
    effective_weight: float,
    revenue_at_stake: float,
    breadth: int,
    latest_signal_at: Optional[str] = None,
    last_brief_id: Optional[int] = None,
    client=None,
) -> None:
    """Record/refresh the convergence fingerprint a theme had when surfaced.

    Idempotent on (enterprise_id, theme_id). `created_at` is omitted so the DB
    default sets it once; `id` is only used for the first insert (ON CONFLICT
    keeps the existing row)."""
    cli = client or require_client()
    now = utc_now()
    cli.table("brief_finding_state").upsert(
        {
            "id": str(uuid.uuid4()),
            "enterprise_id": enterprise_id,
            "theme_id": theme_id,
            "last_brief_id": last_brief_id,
            "last_surfaced_at": now,
            "fp_signal_count": int(signal_count),
            "fp_effective_weight": float(effective_weight),
            "fp_revenue_at_stake": float(revenue_at_stake),
            "fp_breadth": int(breadth),
            "fp_latest_signal_at": latest_signal_at,
            "updated_at": now,
        },
        on_conflict="enterprise_id,theme_id",
    ).execute()

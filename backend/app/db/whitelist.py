"""Whitelist / early-access signup persistence.

Backs the public `POST /v1/whitelist` route. One row per email in the
`whitelist` table (supabase/migrations/20260822140000_whitelist.sql), with the
`source` the front end reported.

No tenant column: the submitter has no account yet, so there is nothing to scope
the row to. That makes this the one write path with no ownership check — the
route in front of it is the only guard, and it is unauthenticated by design.

Access is via require_client() (service-role), like every other db module.
"""
from __future__ import annotations

import logging
import uuid

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)


@retry_on_disconnect
def add_to_whitelist(*, email: str, source: str | None) -> None:
    """Record one whitelist signup. Idempotent per email.

    `email` is lowercased here rather than at the route so every caller gets the
    same normalisation — the unique constraint is on the raw column, so a stray
    `Foo@bar.com` would otherwise sit alongside `foo@bar.com` as a second row.

    Upsert with `ignore_duplicates` (ON CONFLICT DO NOTHING) rather than an
    insert: a repeat signup keeps its ORIGINAL created_at and source, which is
    the honest answer to "when did this person first put their hand up", and it
    means the route never has to turn a duplicate-key error into a 200.
    """
    client = require_client()
    client.table("whitelist").upsert(
        {
            # Generated here, not left to the column default, matching every
            # other uuid-keyed table (app/db/feedback.py) — the id is discarded
            # anyway when the row already exists.
            "id": str(uuid.uuid4()),
            "email": email.strip().lower(),
            "source": source,
            "created_at": utc_now(),
        },
        on_conflict="email",
        ignore_duplicates=True,
    ).execute()
    logger.info("whitelist signup recorded: source=%s", source)

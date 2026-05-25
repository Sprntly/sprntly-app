"""SQLite connection helpers + ISO-8601 timestamp utility.

Shared by every domain submodule under app/db/. The Supabase migration
(PR #5) will add a sibling supabase client here.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings


@contextmanager
def conn():
    c = sqlite3.connect(settings.db_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision, suitable for SQLite TEXT columns."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Kept as `_utc_now` for backward-compat with the previous monolithic
# db.py. New code should import `utc_now`.
_utc_now = utc_now

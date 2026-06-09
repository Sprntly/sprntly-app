"""Dataset registry — one row per slug ("asurion", "wordpress", etc).

Memory note: the user-facing term is "company"; "dataset" is the
internal/DB name. The API/UI layer translates at the boundary.
"""
import logging

from app.db.client import require_client

logger = logging.getLogger(__name__)

# Postgres unique-violation SQLSTATE. supabase-py surfaces it on the raised
# error's `.code`; sqlite (the test fake) reports it via IntegrityError.
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception) -> bool:
    """True if `exc` is a duplicate-slug unique-constraint violation, across
    both real Supabase (PostgREST APIError, code 23505) and the SQLite test
    fake (sqlite3.IntegrityError on a UNIQUE/PRIMARY KEY)."""
    code = getattr(exc, "code", None)
    if code == _UNIQUE_VIOLATION:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "unique" in text or _UNIQUE_VIOLATION in text


def insert_dataset(slug: str, display_name: str) -> None:
    """Register a new dataset. Idempotent — a duplicate slug is a no-op
    (preserves the existing row's display_name). The route handler surfaces a
    409 before reaching this; the no-op here keeps seed-on-startup safe for
    re-runs AND closes the check-then-insert race: two concurrent creates can
    both pass the SELECT, so we also catch the unique-constraint violation from
    the INSERT (the `datasets.slug` unique index, migration
    2026*_datasets_slug_unique.sql) and treat it as "already exists".
    """
    c = require_client()
    if c.table("datasets").select("slug").eq("slug", slug).limit(1).execute().data:
        return
    try:
        c.table("datasets").insert({"slug": slug, "display_name": display_name}).execute()
    except Exception as exc:  # noqa: BLE001 — narrow to unique-violation below
        if _is_unique_violation(exc):
            # Lost the race to a concurrent create of the same slug — the row
            # now exists, which is exactly the idempotent outcome we want.
            logger.info("insert_dataset: slug %r already created concurrently — "
                        "treating as exists", slug)
            return
        raise


def dataset_exists(slug: str) -> bool:
    c = require_client()
    resp = c.table("datasets").select("slug").eq("slug", slug).limit(1).execute()
    return bool(resp.data)


def get_dataset(slug: str) -> dict | None:
    c = require_client()
    resp = c.table("datasets").select("*").eq("slug", slug).limit(1).execute()
    return resp.data[0] if resp.data else None


def list_datasets() -> list[dict]:
    """All datasets, newest first."""
    c = require_client()
    resp = c.table("datasets").select("*").order("created_at", desc=True).execute()
    return resp.data or []


def list_dataset_slugs() -> list[str]:
    c = require_client()
    resp = c.table("datasets").select("slug").order("slug", desc=False).execute()
    return [r["slug"] for r in (resp.data or [])]


def delete_dataset(slug: str) -> bool:
    """Remove a dataset row. Files on disk are left untouched (caller
    responsibility).

    Cascades: briefs/evidences/prds/cached_asks reference the slug as
    TEXT (not FK), so they survive. Re-uploading the same slug should
    not silently inherit old briefs (the corpus has changed); we don't
    auto-purge either. Use a separate admin op if needed.
    """
    c = require_client()
    resp = c.table("datasets").delete().eq("slug", slug).execute()
    # supabase-py exposes count for deletes.
    return bool(resp.count) if resp.count is not None else True

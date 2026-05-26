"""Dataset registry — one row per slug ("asurion", "wordpress", etc).

Memory note: the user-facing term is "company"; "dataset" is the
internal/DB name. The API/UI layer translates at the boundary.
"""
from app.db.client import require_client


def insert_dataset(slug: str, display_name: str) -> None:
    """Register a new dataset. Idempotent — duplicate slug is a no-op
    (preserves the existing row's display_name). The route handler
    surfaces a 409 before reaching this; the no-op here just keeps
    seed-on-startup safe for re-runs.
    """
    c = require_client()
    if c.table("datasets").select("slug").eq("slug", slug).limit(1).execute().data:
        return
    c.table("datasets").insert({"slug": slug, "display_name": display_name}).execute()


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

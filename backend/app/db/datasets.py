"""Dataset registry — one row per slug ("asurion", "wordpress", etc).

Memory note: the user-facing term is "company"; "dataset" is the
internal/DB name. The API/UI layer translates at the boundary.
"""
from app.db.client import conn, shadow_write


def insert_dataset(slug: str, display_name: str) -> None:
    """Register a new dataset. Idempotent — silently ignores duplicates
    so the seed-on-startup path for `asurion` doesn't have to
    special-case re-runs.
    """
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO datasets (slug, display_name) VALUES (?, ?)",
            (slug, display_name),
        )
    # `slug` is the PK; upsert keeps Supabase idempotent like SQLite's
    # `INSERT OR IGNORE`.
    shadow_write(
        "datasets",
        {"slug": slug, "display_name": display_name},
        on_conflict="slug",
    )


def dataset_exists(slug: str) -> bool:
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM datasets WHERE slug=?", (slug,)
        ).fetchone()
    return row is not None


def get_dataset(slug: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT slug, display_name, created_at FROM datasets WHERE slug=?",
            (slug,),
        ).fetchone()
    return dict(row) if row else None


def list_datasets() -> list[dict]:
    """All datasets, newest first."""
    with conn() as c:
        rows = c.execute(
            "SELECT slug, display_name, created_at FROM datasets "
            "ORDER BY created_at DESC, slug ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_dataset_slugs() -> list[str]:
    with conn() as c:
        rows = c.execute("SELECT slug FROM datasets ORDER BY slug ASC").fetchall()
    return [r["slug"] for r in rows]


def delete_dataset(slug: str) -> bool:
    """Remove a dataset row. Files on disk are left untouched (caller
    responsibility).

    Cascades: briefs, evidences, prds, cached_asks reference the slug
    as TEXT rather than FK, so they survive. This is intentional — a
    re-uploaded dataset under the same slug should not silently inherit
    old briefs (the corpus has changed), but we don't auto-purge either.
    Use a separate admin op if needed.
    """
    with conn() as c:
        cur = c.execute("DELETE FROM datasets WHERE slug=?", (slug,))
        return (cur.rowcount or 0) > 0

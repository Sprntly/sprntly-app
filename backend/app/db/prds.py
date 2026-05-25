"""PRDs — one row per generation attempt for a (brief, insight_index) pair.

`status` walks generating → ready (or failed/invalidated). `variant`
distinguishes the v1 sample-build template from v2; v1 rows in prod
remain readable through this column.
"""
from app.db.client import conn, shadow_write


def save_prd(brief_id: int, insight_index: int, title: str, md: str) -> int:
    """Insert a complete PRD (sync flow). Status='ready'."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md, status) "
            "VALUES (?, ?, ?, ?, 'ready')",
            (brief_id, insight_index, title, md),
        )
        new_id = cur.lastrowid
    shadow_write("prds", {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": md,
        "status": "ready",
    })
    return new_id


def start_prd(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
    variant: str = "v1",
) -> int:
    """Insert an empty PRD row in 'generating' state. Returns the new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md, status, template_version, variant) "
            "VALUES (?, ?, ?, '', 'generating', ?, ?)",
            (brief_id, insight_index, title, template_version, variant),
        )
        new_id = cur.lastrowid
    shadow_write("prds", {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": "",
        "status": "generating",
        "template_version": template_version,
        "variant": variant,
    })
    return new_id


def invalidate_stale_prds(current_version: int, variant: str = "v1") -> int:
    """Variant-scoped: mark any ready/generating PRD (of this variant)
    whose template_version differs from current_version as 'invalidated'.
    Returns affected row count.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE prds SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND variant = ? "
            "  AND (template_version IS NULL OR template_version != ?)",
            (variant, current_version),
        )
        return cur.rowcount or 0


def invalidate_orphan_generating_prds() -> int:
    """Mark every status='generating' PRD as 'invalidated'.

    Call from lifespan startup: in-flight rows are orphaned because the
    worker thread that was generating them died with the previous
    process. Leaving them stuck causes user clicks to dedupe to a row
    that will never complete.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE prds SET status='invalidated' WHERE status='generating'"
        )
        return cur.rowcount or 0


def complete_prd(prd_id: int, title: str, md: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE prds SET title=?, payload_md=?, status='ready', error=NULL "
            "WHERE id=?",
            (title, md, prd_id),
        )


def fail_prd(prd_id: int, error: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE prds SET status='failed', error=? WHERE id=?",
            (error[:500], prd_id),
        )


def get_prd(prd_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM prds WHERE id=?",
            (prd_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_prd(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    """Most recent ready/generating PRD (of the given variant) for a
    (brief, insight). Variant-scoped so distinct PRD formats don't
    dedupe against each other.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM prds "
            "WHERE brief_id=? AND insight_index=? AND variant=? "
            "  AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index, variant),
        ).fetchone()
    return dict(row) if row else None

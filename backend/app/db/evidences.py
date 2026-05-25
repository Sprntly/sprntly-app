"""Evidence pages — same shape as PRDs but for the Evidence Page generator.

Kept as a separate table (and module) because the two have different
lifecycles (evidence regenerates more often) and different templates.
"""
from app.db.client import conn, shadow_write


def start_evidence(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
    variant: str = "v1",
) -> int:
    """Insert an empty evidence row in 'generating' state. Returns the new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO evidences (brief_id, insight_index, title, payload_md, status, template_version, variant) "
            "VALUES (?, ?, ?, '', 'generating', ?, ?)",
            (brief_id, insight_index, title, template_version, variant),
        )
        new_id = cur.lastrowid
    shadow_write("evidences", {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": "",
        "status": "generating",
        "template_version": template_version,
        "variant": variant,
    })
    return new_id


def invalidate_stale_evidences(current_version: int, variant: str = "v1") -> int:
    """Variant-scoped: mark any ready/generating evidence (of this variant)
    whose template_version differs from current_version as 'invalidated'.
    Returns affected row count.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE evidences SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND variant = ? "
            "  AND (template_version IS NULL OR template_version != ?)",
            (variant, current_version),
        )
        return cur.rowcount or 0


def invalidate_orphan_generating_evidences() -> int:
    """Mark every status='generating' evidence row as 'invalidated'.

    Same rationale as invalidate_orphan_generating_prds — on startup, any
    in-flight generation is orphaned because the worker thread died with
    the previous process. Without this, a user clicking "View evidence"
    on an insight whose previous warming crashed mid-generation polls
    forever.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE evidences SET status='invalidated' WHERE status='generating'"
        )
        return cur.rowcount or 0


def complete_evidence(evidence_id: int, title: str, md: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE evidences SET title=?, payload_md=?, status='ready', error=NULL "
            "WHERE id=?",
            (title, md, evidence_id),
        )


def fail_evidence(evidence_id: int, error: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE evidences SET status='failed', error=? WHERE id=?",
            (error[:500], evidence_id),
        )


def get_evidence(evidence_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM evidences WHERE id=?",
            (evidence_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_evidence(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    """Most recent ready/generating evidence (of the given variant) for a
    (brief, insight). Variant-scoped so v1 and v2 generation paths don't
    dedupe against each other.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM evidences "
            "WHERE brief_id=? AND insight_index=? AND variant=? "
            "  AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index, variant),
        ).fetchone()
    return dict(row) if row else None

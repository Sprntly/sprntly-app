"""Weekly briefs — one is_current row per dataset, history retained."""
import json

from app.db.client import conn, shadow_write


def get_current_brief(dataset: str = "asurion") -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, dataset, generated_at, week_label, payload_json "
            "FROM briefs WHERE dataset=? AND is_current=1 "
            "ORDER BY generated_at DESC LIMIT 1",
            (dataset,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "generated_at": row["generated_at"],
        "week_label": row["week_label"],
        **json.loads(row["payload_json"]),
    }


def get_brief_by_id(brief_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, dataset, generated_at, week_label, payload_json "
            "FROM briefs WHERE id=?",
            (brief_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "generated_at": row["generated_at"],
        "week_label": row["week_label"],
        **json.loads(row["payload_json"]),
    }


def save_brief(
    dataset: str,
    week_label: str,
    payload: dict,
    schema_version: int | None = None,
) -> int:
    if schema_version is not None:
        payload = {**payload, "_schema_version": schema_version}
    with conn() as c:
        c.execute(
            "UPDATE briefs SET is_current=0 WHERE dataset=?", (dataset,)
        )
        cur = c.execute(
            "INSERT INTO briefs (dataset, week_label, payload_json, is_current) "
            "VALUES (?, ?, ?, 1)",
            (dataset, week_label, json.dumps(payload)),
        )
        new_id = cur.lastrowid
    # Supabase column is jsonb, named `payload` (not `payload_json`).
    shadow_write("briefs", {
        "dataset": dataset,
        "week_label": week_label,
        "payload": payload,
        "is_current": True,
    })
    return new_id


def invalidate_stale_briefs(current_version: int) -> int:
    """Demote any is_current brief whose `_schema_version` differs from
    `current_version`. Returns the count of rows invalidated.

    Called on service startup so a schema bump triggers auto-regeneration
    without manual /v1/brief/regenerate calls or DB surgery.
    """
    invalidated = 0
    with conn() as c:
        rows = c.execute(
            "SELECT id, payload_json FROM briefs WHERE is_current=1"
        ).fetchall()
        stale_ids: list[int] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            if payload.get("_schema_version") != current_version:
                stale_ids.append(row["id"])
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            c.execute(
                f"UPDATE briefs SET is_current=0 WHERE id IN ({placeholders})",
                stale_ids,
            )
            invalidated = len(stale_ids)
    return invalidated

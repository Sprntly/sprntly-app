"""Weekly briefs — backed by the `briefs` table in Supabase.

Reads + writes go through the supabase-py PostgREST client. The
returned dict shape stays the same as the prior SQLite implementation
so callers (routes, runners) don't have to change: `payload_json` was
exploded into the top-level dict before; we do the same for `payload`
(jsonb) now.
"""
from app.db.client import require_client, retry_on_disconnect


def _explode(row: dict) -> dict:
    """Match the old shape: payload's keys live at the top of the dict."""
    payload = row.get("payload") or {}
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "generated_at": row["generated_at"],
        "week_label": row.get("week_label"),
        **payload,
    }


@retry_on_disconnect
def get_current_brief(dataset: str = "asurion") -> dict | None:
    c = require_client()
    resp = (
        c.table("briefs")
        .select("*")
        .eq("dataset", dataset)
        .eq("is_current", True)
        .order("generated_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _explode(resp.data[0])


@retry_on_disconnect
def get_brief_by_id(brief_id: int) -> dict | None:
    c = require_client()
    resp = c.table("briefs").select("*").eq("id", brief_id).limit(1).execute()
    if not resp.data:
        return None
    return _explode(resp.data[0])


@retry_on_disconnect
def save_brief(
    dataset: str,
    week_label: str,
    payload: dict,
    schema_version: int | None = None,
) -> int:
    """Demote prior is_current row for this dataset, insert the new one."""
    if schema_version is not None:
        payload = {**payload, "_schema_version": schema_version}
    c = require_client()
    # Demote prior is_current row(s).
    c.table("briefs").update({"is_current": False}).eq("dataset", dataset).eq(
        "is_current", True
    ).execute()
    resp = c.table("briefs").insert({
        "dataset": dataset,
        "week_label": week_label,
        "payload": payload,
        "is_current": True,
    }).execute()
    return resp.data[0]["id"]


# The fixed week_label that identifies the per-dataset anchor brief uploaded PRDs
# hang off. Uploaded PRDs have no weekly brief / KG lineage, but prds.brief_id is
# NOT NULL — rather than make it nullable (a schema decoupling), every uploaded
# PRD for a company anchors to ONE synthetic brief. payload._uploads_anchor also
# marks it for humans reading the row; the get-or-create keys on the plain
# (dataset, week_label, is_current=false) columns so it works on both PostgREST
# and the in-memory test client.
_UPLOADS_BRIEF_LABEL = "Uploaded PRDs"
_UPLOADS_ANCHOR_KEY = "_uploads_anchor"


@retry_on_disconnect
def ensure_uploads_brief(dataset: str) -> int:
    """Get-or-create the per-dataset placeholder brief that uploaded PRDs anchor to.

    The anchor is `is_current=false` so it NEVER shadows the real weekly brief or
    shows in the brief UI. Idempotent: keyed on (dataset, week_label, is_current)
    so exactly one exists per dataset. Its `dataset` slug matches the company's so
    the uploaded PRD still lists under Artifacts (which scopes PRDs by the brief's
    dataset).
    """
    c = require_client()
    resp = (
        c.table("briefs")
        .select("id")
        .eq("dataset", dataset)
        .eq("week_label", _UPLOADS_BRIEF_LABEL)
        .eq("is_current", False)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["id"]
    ins = (
        c.table("briefs")
        .insert({
            "dataset": dataset,
            "week_label": _UPLOADS_BRIEF_LABEL,
            "payload": {_UPLOADS_ANCHOR_KEY: True, "insights": []},
            "is_current": False,
        })
        .execute()
    )
    return ins.data[0]["id"]


def invalidate_stale_briefs(current_version: int) -> int:
    """Demote any is_current brief whose `_schema_version` differs from
    `current_version`. Returns the count of rows invalidated.

    Called on service startup so a schema bump triggers auto-regeneration
    without manual /v1/brief/regenerate calls or DB surgery.
    """
    c = require_client()
    rows = c.table("briefs").select("id, payload").eq("is_current", True).execute().data
    stale_ids: list[int] = []
    for row in rows:
        payload = row.get("payload") or {}
        if payload.get("_schema_version") != current_version:
            stale_ids.append(row["id"])
    if stale_ids:
        c.table("briefs").update({"is_current": False}).in_("id", stale_ids).execute()
    return len(stale_ids)

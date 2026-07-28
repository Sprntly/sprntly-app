"""Custom skills — company-scoped, user-uploaded skill definitions
(PRD 1854; migration 20260728180000_custom_skills.sql).

Scoping is COMPANY-LEVEL for now: every read filters by company_id, so all
workspaces in a company share one skill library. workspace_id is still
stamped on each row (which workspace uploaded it) so a future move to
workspace-level scoping is a query change, not a backfill.

The row stores the PARSED skill content: `method` is the SKILL.md text the
gateway injects at invocation time, `modules`/`refs` are JSON-encoded
{filename: markdown} maps (TEXT columns — opaque payloads, never queried
into; `refs` dodges the `references` SQL keyword). The ORIGINAL upload bytes
live in Supabase Storage (skills_storage.py) under `storage_key`.

Rows are returned with `modules`/`references` decoded to dicts so callers
(routes, and later the invocation resolver) never see the JSON encoding.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.db.client import require_client

logger = logging.getLogger(__name__)

# Postgres unique-violation SQLSTATE. supabase-py surfaces it on the raised
# error's `.code`; sqlite (the test fake) reports it via IntegrityError.
_UNIQUE_VIOLATION = "23505"


class DuplicateSkillSlug(ValueError):
    """The company already has a skill with this slug (unique constraint)."""


def _now_iso() -> str:
    """Microsecond-precision UTC timestamp — unlike client.utc_now()'s
    second precision, so the library's newest-first ordering stays stable
    when two skills are uploaded within the same second."""
    return datetime.now(timezone.utc).isoformat()


def _is_unique_violation(exc: Exception) -> bool:
    """True if `exc` is a duplicate-slug unique-constraint violation, across
    both real Supabase (PostgREST APIError, code 23505) and the SQLite test
    fake (sqlite3.IntegrityError on a UNIQUE/PRIMARY KEY)."""
    code = getattr(exc, "code", None)
    if code == _UNIQUE_VIOLATION:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "unique" in text or _UNIQUE_VIOLATION in text


def _decode(row: dict) -> dict:
    """DB row → caller shape: JSON text columns decoded, `refs` → `references`."""
    out = dict(row)
    for col, key in (("modules", "modules"), ("refs", "references")):
        raw = out.pop(col, None) or "{}"
        try:
            out[key] = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            logger.warning("custom_skills row %s has undecodable %s; treating as empty",
                           row.get("id"), col)
            out[key] = {}
    return out


def insert_custom_skill(
    *,
    company_id: str,
    workspace_id: str,
    slug: str,
    name: str,
    description: str,
    method: str,
    modules: dict[str, str],
    references: dict[str, str],
    content_hash: str,
    storage_key: str | None,
    uploader_id: str,
    uploader_name: str,
) -> dict:
    """Create the skill row; returns the decoded row.

    Raises DuplicateSkillSlug when the (company_id, slug) unique constraint
    trips — the route surfaces it as a 409. The id/created_at are generated
    client-side (uuid4 / microsecond ISO) so the SQLite test fake behaves
    identically to Postgres."""
    c = require_client()
    row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "workspace_id": workspace_id,
        "slug": slug,
        "name": name,
        "description": description,
        "method": method,
        "modules": json.dumps(modules or {}),
        "refs": json.dumps(references or {}),
        "content_hash": content_hash,
        "storage_key": storage_key,
        "uploader_id": uploader_id,
        "uploader_name": uploader_name,
        "created_at": _now_iso(),
    }
    try:
        resp = c.table("custom_skills").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — narrow to unique-violation below
        if _is_unique_violation(exc):
            raise DuplicateSkillSlug(slug) from exc
        raise
    return _decode(resp.data[0] if resp.data else row)


# List view omits the bulky content columns — the library UI needs metadata
# only. get_custom_skill returns everything (invocation needs `method`).
_LIST_COLUMNS = "id, slug, name, description, content_hash, storage_key, uploader_id, uploader_name, created_at"


def list_custom_skills(company_id: str) -> list[dict]:
    """All custom skills in one company, newest first (metadata only)."""
    c = require_client()
    resp = (
        c.table("custom_skills")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [dict(r) for r in (resp.data or [])]


def get_custom_skill(company_id: str, slug: str) -> dict | None:
    """Full decoded row by company + slug (the invocation lookup), or None."""
    c = require_client()
    resp = (
        c.table("custom_skills")
        .select("*")
        .eq("company_id", company_id)
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return _decode(resp.data[0]) if resp.data else None


def get_custom_skill_by_id(company_id: str, skill_id: str) -> dict | None:
    """Full decoded row by company + row id (the file-link lookup), or None.
    Company-filtered so a foreign id 404s indistinguishably from a missing one."""
    c = require_client()
    resp = (
        c.table("custom_skills")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", skill_id)
        .limit(1)
        .execute()
    )
    return _decode(resp.data[0]) if resp.data else None

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
    source_id: str | None = None,
) -> dict:
    """Create the skill row; returns the decoded row.

    Raises DuplicateSkillSlug when the (company_id, slug) unique constraint
    trips — the route surfaces it as a 409. The id/created_at are generated
    client-side (uuid4 / microsecond ISO) so the SQLite test fake behaves
    identically to Postgres.

    `source_id` marks the skill as belonging to a synced GitHub folder, which
    makes it read-only everywhere a user could edit it. It defaults to None so
    every hand-upload path stays exactly what it was."""
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
        "source_id": source_id,
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
_LIST_COLUMNS = "id, slug, name, description, content_hash, storage_key, uploader_id, uploader_name, created_at, source_id"


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


def update_custom_skill(
    *,
    company_id: str,
    skill_id: str,
    workspace_id: str,
    name: str,
    description: str,
    method: str,
    modules: dict[str, str],
    references: dict[str, str],
    content_hash: str,
    storage_key: str | None,
    uploader_id: str,
    uploader_name: str,
    slug: str | None = None,
    source_id: str | None = None,
) -> dict | None:
    """Replace one company-owned skill's content and metadata in place; returns
    the decoded row, or None when the id is missing or belongs to another
    company (indistinguishable, like the by-id lookup).

    This is the re-upload path: a company uploading a skill under a name it has
    already used updates that row instead of getting a second entry. `created_at`
    is deliberately NOT in the patch — the library's newest-first order should
    not reshuffle because someone refreshed a skill's text. `workspace_id` and
    the uploader fields ARE refreshed: the row describes the version it now
    holds, so it records who last uploaded it and from where.

    `slug` DEFAULTS TO None meaning "leave the trigger alone", and the re-upload
    path relies on that: `/estimation-helper` has to keep working across a new
    version, and the router has to keep offering the same id. Only the in-place
    EDIT path passes a slug, and only when the edit renamed the skill — a new
    name derives a new trigger through the same deconfliction the upload uses.
    Passing one re-opens the (company_id, slug) unique constraint, so that call
    can raise DuplicateSkillSlug; the no-slug call still cannot trip it.

    `source_id` behaves the same way — None means "leave it alone", so the edit
    path never disturbs it and a sync can adopt a skill the company had uploaded
    by hand under the same name. There is deliberately no way to CLEAR it here:
    a skill stops being synced when its source row goes away, and the column's
    `on delete set null` is what does that.

    Both the existence check and the update are company-filtered, so a racing
    caller can never write a foreign row."""
    row = get_custom_skill_by_id(company_id, skill_id)
    if row is None:
        return None
    patch = {
        "workspace_id": workspace_id,
        "name": name,
        "description": description,
        "method": method,
        "modules": json.dumps(modules or {}),
        "refs": json.dumps(references or {}),
        "content_hash": content_hash,
        "storage_key": storage_key,
        "uploader_id": uploader_id,
        "uploader_name": uploader_name,
    }
    if slug is not None:
        patch["slug"] = slug
    if source_id is not None:
        patch["source_id"] = source_id
    c = require_client()
    try:
        resp = (
            c.table("custom_skills")
            .update(patch)
            .eq("company_id", company_id)
            .eq("id", skill_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — narrow to unique-violation below
        # Only reachable on a slug-changing edit: another row in this company
        # took the trigger between the caller's library read and this write.
        if slug is not None and _is_unique_violation(exc):
            raise DuplicateSkillSlug(slug) from exc
        raise
    # PostgREST returns the updated representation; fall back to the row we
    # already read merged with the patch rather than reporting a failed update
    # (the caller reads None as "the row is gone" and would then create a
    # second entry under the same name — exactly what this path prevents).
    return _decode(resp.data[0] if resp.data else {**row, **patch})


def detach_skills_from_source(company_id: str, source_id: str) -> int:
    """Clear `source_id` on every skill a synced folder produced; returns how
    many rows were touched.

    This is what "stop syncing" actually does to the library. Leaving the link
    in place would keep the skills read-only forever — the PATCH and DELETE
    guards key on `source_id` alone, deliberately, so that they cost no extra
    read on the hot path. Clearing it here means one write at the moment someone
    stops syncing, instead of a source lookup on every edit of every skill.

    The skills themselves are untouched otherwise: same id, same trigger, same
    text. They simply become ordinary uploaded skills that the company now owns.
    Re-enabling the folder re-adopts them through the normal name-matched
    replace in `store_skill`, so nothing is stranded by this.
    """
    c = require_client()
    # Counted from a SELECT before the update, not from the update's returned
    # representation: PostgREST returns the updated rows but the SQLite test
    # fake answers an empty list, and a caller that reports "0 skills released"
    # after releasing four would be lying in the one message that tells the user
    # their skills survived.
    found = (
        c.table("custom_skills")
        .select("id")
        .eq("company_id", company_id)
        .eq("source_id", source_id)
        .execute()
    )
    count = len(found.data or [])
    if not count:
        return 0
    (
        c.table("custom_skills")
        .update({"source_id": None})
        .eq("company_id", company_id)
        .eq("source_id", source_id)
        .execute()
    )
    return count


def delete_custom_skill(company_id: str, skill_id: str) -> dict | None:
    """Delete one company-owned skill row; returns the decoded deleted row
    (the route needs storage_key to clean up the original file), or None when
    the id is missing or belongs to another company — indistinguishable, like
    the by-id lookup. The delete itself is also company-filtered so a racing
    caller can never remove a foreign row."""
    row = get_custom_skill_by_id(company_id, skill_id)
    if row is None:
        return None
    c = require_client()
    (
        c.table("custom_skills")
        .delete()
        .eq("company_id", company_id)
        .eq("id", skill_id)
        .execute()
    )
    return row

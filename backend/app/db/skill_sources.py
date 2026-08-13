"""Synced skill folders — the GitHub folders a company keeps live
(migration 20260807170000_skill_sources.sql).

One row is one folder: company + repo + ref + path. The 30-minute sweep
(`app.skills.github_sync`) reads every ACTIVE row across every tenant, re-runs
GitHub discovery over that folder, and re-imports what it finds. The import
route writes the row; nothing else creates one.

Two reads with deliberately different scoping, and the difference matters:

  - `list_active_skill_sources()` is CROSS-TENANT by design — the sweep has no
    request and no caller, so it walks every company's folders. Everything it
    then does is scoped by the `company_id` ON THE ROW, never by anything the
    sweep chose.
  - every other function here is company-filtered, because they serve HTTP
    requests where the caller's company is the boundary.

`installation_id` is stored, not re-derived. It is written from the
company-filtered `find_github_installation_for_repo` during an authenticated
import, so the row carries an installation this company was proven to own. The
sweep must reuse that value rather than resolve one itself — resolving would
mean picking an installation with no caller to check it against.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.db.client import require_client

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(row: dict) -> dict:
    """DB row → caller shape. `active` is a real bool: SQLite stores the column
    as 0/1 and PostgREST returns a JSON boolean, so callers would otherwise have
    to know which backend they were talking to."""
    out = dict(row)
    out["active"] = bool(out.get("active"))
    return out


def upsert_skill_source(
    *,
    company_id: str,
    workspace_id: str | None,
    installation_id: int,
    repo: str,
    ref: str,
    path: str,
    created_by: str | None,
    active: bool = True,
) -> dict:
    """Create or re-activate the source for one folder; returns the row.

    Keyed on the (company_id, repo, ref, path) unique constraint, so importing
    the same folder twice updates one row instead of creating a second that
    would sync the same files and fight over the same skill names.

    `last_commit_sha` is deliberately NOT reset on an update. A re-import has
    just written the folder's skills through the normal store path, so the
    content is current; clearing the sha would only make the next sweep redo a
    full tree walk to reach the same result.
    """
    existing = get_skill_source_for_folder(
        company_id=company_id, repo=repo, ref=ref, path=path
    )
    c = require_client()
    if existing is not None:
        patch = {
            "workspace_id": workspace_id,
            "installation_id": int(installation_id),
            "created_by": created_by or existing.get("created_by"),
            "active": active,
            "updated_at": _now_iso(),
        }
        resp = (
            c.table("skill_sources")
            .update(patch)
            .eq("company_id", company_id)
            .eq("id", existing["id"])
            .execute()
        )
        return _decode(resp.data[0] if resp.data else {**existing, **patch})

    row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "workspace_id": workspace_id,
        "installation_id": int(installation_id),
        "repo": repo,
        "ref": ref,
        "path": path,
        "last_commit_sha": "",
        "last_synced_at": None,
        "last_error": "",
        "active": active,
        "created_by": created_by,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    resp = c.table("skill_sources").insert(row).execute()
    return _decode(resp.data[0] if resp.data else row)


def get_skill_source_for_folder(
    *, company_id: str, repo: str, ref: str, path: str
) -> dict | None:
    """The company's source row for exactly this folder, or None."""
    c = require_client()
    resp = (
        c.table("skill_sources")
        .select("*")
        .eq("company_id", company_id)
        .eq("repo", repo)
        .eq("ref", ref)
        .eq("path", path)
        .limit(1)
        .execute()
    )
    return _decode(resp.data[0]) if resp.data else None


def get_skill_source(company_id: str, source_id: str) -> dict | None:
    """One company-owned source by id, or None — company-filtered so a foreign
    id is indistinguishable from a missing one."""
    c = require_client()
    resp = (
        c.table("skill_sources")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", source_id)
        .limit(1)
        .execute()
    )
    return _decode(resp.data[0]) if resp.data else None


def list_skill_sources(company_id: str) -> list[dict]:
    """Every source this company has, newest first (active and inactive)."""
    c = require_client()
    resp = (
        c.table("skill_sources")
        .select("*")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_decode(r) for r in (resp.data or [])]


def list_active_skill_sources() -> list[dict]:
    """Every ACTIVE source across every tenant — the sweep's read.

    Cross-tenant on purpose (see the module docstring): the caller is a
    scheduler job, not a request. Each returned row carries its own company_id
    and installation_id, and the sweep does all of its work under those.
    """
    c = require_client()
    resp = (
        c.table("skill_sources")
        .select("*")
        .eq("active", True)
        .execute()
    )
    return [_decode(r) for r in (resp.data or [])]


def list_active_skill_sources_for_installation(installation_id: int) -> list[dict]:
    """Every ACTIVE source registered under one GitHub App installation — the
    push-webhook's read.

    Like `list_active_skill_sources`, the caller has no request and therefore no
    company: the installation id comes off a signature-verified webhook payload,
    and each returned row still carries the company_id/installation_id its
    writes are keyed on. Filtering here (rather than sweeping all tenants per
    push) is what keeps a busy repo's webhook traffic from scaling with the
    number of OTHER tenants using skill sync.
    """
    c = require_client()
    resp = (
        c.table("skill_sources")
        .select("*")
        .eq("active", True)
        .eq("installation_id", int(installation_id))
        .execute()
    )
    return [_decode(r) for r in (resp.data or [])]


def record_skill_source_sync(
    *,
    source_id: str,
    commit_sha: str | None = None,
    error: str = "",
) -> None:
    """Stamp the outcome of one sync attempt onto its source row.

    `commit_sha` is written only on success, so a failed sweep leaves the last
    KNOWN-GOOD sha in place and the next run still recognises the folder as
    changed. `error` is cleared on success — a source that recovered must not
    keep displaying the failure that has since gone away.

    Never raises: this is the bookkeeping at the end of a best-effort background
    job, and a stamp that fails must not lose the sync that already succeeded.
    """
    patch: dict = {"last_synced_at": _now_iso(), "last_error": error[:500], "updated_at": _now_iso()}
    if not error and commit_sha:
        patch["last_commit_sha"] = commit_sha
    try:
        c = require_client()
        c.table("skill_sources").update(patch).eq("id", source_id).execute()
    except Exception:  # noqa: BLE001 — bookkeeping must never fail the sweep
        logger.warning("skill_sources: could not stamp sync for %s", source_id, exc_info=True)


def deactivate_skill_source(*, company_id: str, source_id: str) -> None:
    """Stop syncing a folder without forgetting it.

    The row survives so `last_commit_sha` is still there if it is switched back
    on, and so the skills it produced keep pointing at something real. The
    skills themselves are untouched — turning sync off never removes a skill.
    """
    c = require_client()
    (
        c.table("skill_sources")
        .update({"active": False, "updated_at": _now_iso()})
        .eq("company_id", company_id)
        .eq("id", source_id)
        .execute()
    )

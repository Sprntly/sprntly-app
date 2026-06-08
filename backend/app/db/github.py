"""GitHub App installations + tracked PRs.

Fed by the webhook at `POST /v1/connectors/github/webhook`:
  - `installation` events → upsert/delete in github_installations
  - `installation_repositories` → re-upsert with new repo selection
  - `pull_request` events → upsert in github_pull_requests

Back-compat note: the prior SQLite shape exposed `permissions` /
`events` as JSON-string columns suffixed `_json`. We preserve those
keys in returned dicts so existing callers don't have to change.
"""
import json

from app.db.client import require_client, utc_now


def _legacy_install(row: dict) -> dict:
    """Add `permissions_json` and `events_json` (strings) for back-compat."""
    perms = row.get("permissions")
    events = row.get("events")
    row["permissions_json"] = json.dumps(perms) if isinstance(perms, (dict, list)) else (
        perms or "{}"
    )
    row["events_json"] = json.dumps(events) if isinstance(events, (dict, list)) else (
        events or "[]"
    )
    # Boolean -> 0/1 for callers (and tests) that used the SQLite int.
    if isinstance(row.get("suspended"), bool):
        row["suspended"] = 1 if row["suspended"] else 0
    return row


# ─────────────────────── github_installations ───────────────────────


def upsert_github_installation(
    *,
    installation_id: int,
    account_id: int,
    account_login: str,
    account_type: str,
    repository_selection: str = "selected",
    suspended: bool = False,
    permissions: dict | None = None,
    events: list | None = None,
) -> dict:
    c = require_client()
    now = utc_now()
    existing = get_github_installation(installation_id)
    payload = {
        "installation_id": installation_id,
        "account_id": account_id,
        "account_login": account_login,
        "account_type": account_type,
        "repository_selection": repository_selection,
        "suspended": suspended,
        "permissions": permissions or {},
        "events": events or [],
        "updated_at": now,
    }
    if not existing:
        payload["created_at"] = now
    c.table("github_installations").upsert(
        payload, on_conflict="installation_id"
    ).execute()
    return get_github_installation(installation_id)  # type: ignore[return-value]


def get_github_installation(installation_id: int) -> dict | None:
    c = require_client()
    resp = (
        c.table("github_installations")
        .select("*")
        .eq("installation_id", installation_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _legacy_install(resp.data[0])


def list_github_installations() -> list[dict]:
    c = require_client()
    resp = (
        c.table("github_installations")
        .select("*")
        .order("account_login", desc=False)
        .execute()
    )
    return [_legacy_install(r) for r in (resp.data or [])]


def find_github_installation_for_repo(repo_full_name: str) -> dict | None:
    """Return the non-suspended GitHub App installation for ``owner/repo``.

    GitHub App installations are account-scoped. Until we persist a per-repo
    installation inventory, the durable production-shaped lookup is by the repo
    owner/account login. A selected-repos installation may still reject a future
    extractor if the app was not granted this specific repo; that is an honest
    extraction-time failure, not a generate-time product decision.
    """
    owner = (repo_full_name or "").split("/", 1)[0].strip()
    if not owner:
        return None
    c = require_client()
    resp = (
        c.table("github_installations")
        .select("*")
        .eq("account_login", owner)
        .eq("suspended", False)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _legacy_install(resp.data[0])


def delete_github_installation(installation_id: int) -> bool:
    c = require_client()
    # Drop tracked PRs first — they're scoped to this install.
    c.table("github_pull_requests").delete().eq(
        "installation_id", installation_id
    ).execute()
    resp = (
        c.table("github_installations")
        .delete()
        .eq("installation_id", installation_id)
        .execute()
    )
    return bool(resp.count) if resp.count is not None else True


# ─────────────────────── github_pull_requests ───────────────────────


def upsert_github_pull_request(
    *,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    title: str,
    state: str,
    is_draft: bool = False,
    author_login: str | None = None,
    head_ref: str | None = None,
    base_ref: str | None = None,
    html_url: str | None = None,
    body_excerpt: str | None = None,
    pr_created_at: str | None = None,
    pr_updated_at: str | None = None,
) -> None:
    c = require_client()
    c.table("github_pull_requests").upsert(
        {
            "installation_id": installation_id,
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "title": title,
            "state": state,
            "is_draft": is_draft,
            "author_login": author_login,
            "head_ref": head_ref,
            "base_ref": base_ref,
            "html_url": html_url,
            "body_excerpt": body_excerpt,
            "pr_created_at": pr_created_at,
            "pr_updated_at": pr_updated_at,
            "last_event_at": utc_now(),
        },
        # Composite PK — pass primary key columns to PostgREST.
        on_conflict="repo_full_name,pr_number",
    ).execute()


def list_open_pull_requests(installation_id: int | None = None) -> list[dict]:
    c = require_client()
    q = c.table("github_pull_requests").select("*").eq("state", "open")
    if installation_id is not None:
        q = q.eq("installation_id", installation_id)
    resp = q.order("pr_updated_at", desc=True).execute()
    rows = resp.data or []
    # is_draft: Supabase bool -> back-compat int.
    for r in rows:
        if isinstance(r.get("is_draft"), bool):
            r["is_draft"] = 1 if r["is_draft"] else 0
    return rows

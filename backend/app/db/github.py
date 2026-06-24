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
    company_id: str | None = None,
) -> dict:
    """Upsert a GitHub App installation row.

    `company_id` is the tenant that owns this installation. It is set when
    the OAuth callback (which alone knows the company from the signed state)
    binds an installation to the company. The webhook path does NOT carry a
    company, so webhook-driven upserts pass company_id=None and must PRESERVE
    any company_id already on the row (we never blank it out on a refresh).
    """
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
    # Only ever SET company_id; never clear an existing binding. A webhook
    # refresh (company_id=None) on a row already bound to a company keeps the
    # binding intact.
    if company_id is not None:
        payload["company_id"] = company_id
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


def get_github_installation_for_company(
    installation_id: int, company_id: str
) -> dict | None:
    """Return the installation iff it belongs to ``company_id``.

    Tenant-scoped variant of :func:`get_github_installation`. Returns None for
    a non-existent install, an install owned by another company, OR a legacy
    install whose company_id is NULL — so callers can 404 on any of those
    without leaking the existence of another tenant's installation.
    """
    row = get_github_installation(installation_id)
    if not row:
        return None
    if not row.get("company_id") or row.get("company_id") != company_id:
        return None
    return row


def list_github_installations(company_id: str) -> list[dict]:
    """Installations owned by ``company_id``.

    Tenant-scoped: filters on company_id AND excludes legacy NULL-company rows
    (a NULL filter still matches because PostgREST `.eq` never matches NULL,
    but we are explicit so the contract is obvious to callers/readers).
    """
    if not company_id:
        return []
    c = require_client()
    resp = (
        c.table("github_installations")
        .select("*")
        .eq("company_id", company_id)
        .order("account_login", desc=False)
        .execute()
    )
    return [_legacy_install(r) for r in (resp.data or [])]


def find_github_installation_for_repo(
    repo_full_name: str, company_id: str
) -> dict | None:
    """Return the caller-company's non-suspended installation for ``owner/repo``.

    GitHub App installations are account-scoped. Until we persist a per-repo
    installation inventory, the durable production-shaped lookup is by the repo
    owner/account login. A selected-repos installation may still reject a future
    extractor if the app was not granted this specific repo; that is an honest
    extraction-time failure, not a generate-time product decision.

    Tenant-scoped: only matches an installation owned by ``company_id`` (legacy
    NULL-company rows never match), so one company can never resolve another
    company's installation for the same account login.
    """
    owner = (repo_full_name or "").split("/", 1)[0].strip()
    if not owner or not company_id:
        return None
    c = require_client()
    resp = (
        c.table("github_installations")
        .select("*")
        # GitHub logins contain no %/_ so ilike is an exact case-insensitive
        # match; hardens against owner-login casing drift only. The durable fix
        # is the company binding, not this lookup.
        .ilike("account_login", owner)
        .eq("company_id", company_id)
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
    company_id: str | None = None,
) -> None:
    """Upsert a tracked PR.

    `company_id` scopes the PR to its owning tenant. The webhook resolves it
    from the PR's installation (installation.company_id) so PR reads can be
    company-filtered. A PR whose installation has no company binding (legacy)
    is written with company_id=None and is excluded from all scoped reads.
    """
    c = require_client()
    payload = {
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
    }
    # Only set company_id when known; never blank an existing binding.
    if company_id is not None:
        payload["company_id"] = company_id
    c.table("github_pull_requests").upsert(
        payload,
        # Composite PK — pass primary key columns to PostgREST.
        on_conflict="repo_full_name,pr_number",
    ).execute()


def list_open_pull_requests(
    company_id: str, installation_id: int | None = None
) -> list[dict]:
    """Open PRs owned by ``company_id``.

    Tenant-scoped: filters on company_id (excluding legacy NULL-company rows),
    optionally narrowed to a single installation. A None/empty company yields
    no rows — never a global list.
    """
    if not company_id:
        return []
    c = require_client()
    q = (
        c.table("github_pull_requests")
        .select("*")
        .eq("state", "open")
        .eq("company_id", company_id)
    )
    if installation_id is not None:
        q = q.eq("installation_id", installation_id)
    resp = q.order("pr_updated_at", desc=True).execute()
    rows = resp.data or []
    # is_draft: Supabase bool -> back-compat int.
    for r in rows:
        if isinstance(r.get("is_draft"), bool):
            r["is_draft"] = 1 if r["is_draft"] else 0
    return rows

"""GitHub App installations + tracked PRs.

Fed by the GitHub App webhook (`POST /v1/connectors/github/webhook`):
  - `installation` events → upsert/delete in github_installations
  - `installation_repositories` → re-upsert with new repo selection
  - `pull_request` events → upsert in github_pull_requests

Kept here so the rest of the codebase can answer "show me open PRs"
without re-hitting the GitHub API.
"""
import json

from app.db.client import conn, utc_now


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
    now = utc_now()
    with conn() as c:
        existing = c.execute(
            "SELECT installation_id FROM github_installations WHERE installation_id=?",
            (installation_id,),
        ).fetchone()
        perms = json.dumps(permissions or {})
        evts = json.dumps(events or [])
        if existing:
            c.execute(
                "UPDATE github_installations SET account_id=?, account_login=?, "
                "account_type=?, repository_selection=?, suspended=?, "
                "permissions_json=?, events_json=?, updated_at=? "
                "WHERE installation_id=?",
                (account_id, account_login, account_type, repository_selection,
                 1 if suspended else 0, perms, evts, now, installation_id),
            )
        else:
            c.execute(
                "INSERT INTO github_installations (installation_id, account_id, "
                "account_login, account_type, repository_selection, suspended, "
                "permissions_json, events_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (installation_id, account_id, account_login, account_type,
                 repository_selection, 1 if suspended else 0, perms, evts, now, now),
            )
    return get_github_installation(installation_id)  # type: ignore[return-value]


def get_github_installation(installation_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT installation_id, account_id, account_login, account_type, "
            "repository_selection, suspended, permissions_json, events_json, "
            "created_at, updated_at FROM github_installations WHERE installation_id=?",
            (installation_id,),
        ).fetchone()
    return dict(row) if row else None


def list_github_installations() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT installation_id, account_id, account_login, account_type, "
            "repository_selection, suspended, permissions_json, events_json, "
            "created_at, updated_at FROM github_installations "
            "ORDER BY account_login ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_github_installation(installation_id: int) -> bool:
    with conn() as c:
        cur = c.execute(
            "DELETE FROM github_installations WHERE installation_id=?",
            (installation_id,),
        )
        # Also drop any tracked PRs for this install — they're inaccessible now.
        c.execute(
            "DELETE FROM github_pull_requests WHERE installation_id=?",
            (installation_id,),
        )
        return (cur.rowcount or 0) > 0


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
    now = utc_now()
    with conn() as c:
        c.execute(
            "INSERT INTO github_pull_requests (installation_id, repo_full_name, "
            "pr_number, title, state, is_draft, author_login, head_ref, base_ref, "
            "html_url, body_excerpt, pr_created_at, pr_updated_at, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(repo_full_name, pr_number) DO UPDATE SET "
            "installation_id=excluded.installation_id, title=excluded.title, "
            "state=excluded.state, is_draft=excluded.is_draft, "
            "author_login=excluded.author_login, head_ref=excluded.head_ref, "
            "base_ref=excluded.base_ref, html_url=excluded.html_url, "
            "body_excerpt=excluded.body_excerpt, pr_updated_at=excluded.pr_updated_at, "
            "last_event_at=excluded.last_event_at",
            (
                installation_id, repo_full_name, pr_number, title, state,
                1 if is_draft else 0, author_login, head_ref, base_ref,
                html_url, body_excerpt, pr_created_at, pr_updated_at, now,
            ),
        )


def list_open_pull_requests(installation_id: int | None = None) -> list[dict]:
    with conn() as c:
        if installation_id is not None:
            rows = c.execute(
                "SELECT installation_id, repo_full_name, pr_number, title, state, "
                "is_draft, author_login, head_ref, base_ref, html_url, body_excerpt, "
                "pr_created_at, pr_updated_at, last_event_at "
                "FROM github_pull_requests "
                "WHERE installation_id=? AND state='open' "
                "ORDER BY pr_updated_at DESC",
                (installation_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT installation_id, repo_full_name, pr_number, title, state, "
                "is_draft, author_login, head_ref, base_ref, html_url, body_excerpt, "
                "pr_created_at, pr_updated_at, last_event_at "
                "FROM github_pull_requests WHERE state='open' "
                "ORDER BY pr_updated_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]

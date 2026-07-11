"""Jira (Atlassian) OAuth 2.0 3LO helpers.

Flow:
    1. Frontend hits POST /v1/connectors/authorize?provider=jira
    2. We build a state JWT + return Atlassian's authorize URL
    3. Browser navigates to Atlassian's consent screen
    4. Atlassian redirects back to /v1/connectors/jira/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in, ...},
       resolve the accessible Jira site(s) → `cloud_id`, and store an encrypted
       JSON blob under provider="jira".

Atlassian specifics worth knowing (differ from ClickUp/HubSpot):
    - Auth + token endpoints live on `auth.atlassian.com`; the REST API lives on
      `api.atlassian.com/ex/jira/{cloud_id}/...`. You CANNOT call a customer's
      `*.atlassian.net` host directly with a 3LO token — every request is proxied
      through `api.atlassian.com` and needs the site's `cloud_id`.
    - `cloud_id` is NOT in the token response. You resolve it separately via
      `GET /oauth/token/accessible-resources` (one entry per site the user granted).
    - Access tokens expire in ~1 hour. To get a `refresh_token` at all you MUST
      request the `offline_access` scope AND `prompt=consent` on the authorize URL.
    - Refresh tokens ROTATE: each refresh returns a NEW refresh_token, so we must
      persist the whole new payload (mirrors GitHub, not HubSpot's stable refresh).
    - API auth is `Authorization: Bearer <access_token>` (unlike ClickUp's raw
      token). Accept header must be `application/json`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

JIRA_PROVIDER = "jira"
JIRA_AUTH_URL = "https://auth.atlassian.com/authorize"
JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
JIRA_ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
JIRA_API_BASE = "https://api.atlassian.com/ex/jira"  # + /{cloud_id}/rest/api/3/...

# Fixed scope set for the Sprntly Jira connector.
#   read:jira-work       — read issues + projects (KG ingest, project picker)
#   write:jira-work      — create issues (push stories/tickets)
#   read:jira-user       — resolve the authorizing user (myself) for the label
#   report:personal-data — call the Personal Data Reporting API (GDPR); required
#                          on a token to POST /app/report-accounts. Adding it here
#                          means every connection's token can serve as the app
#                          bearer token for the reporting cycle.
#   offline_access       — REQUIRED to receive a refresh_token (tokens last ~1h)
JIRA_SCOPES = (
    "read:jira-work write:jira-work read:jira-user "
    "report:personal-data offline_access"
)

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20


def jira_configured() -> bool:
    return bool(
        settings.jira_client_id
        and settings.jira_client_secret
        and settings.jira_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for the Atlassian consent screen.

    `audience=api.atlassian.com` and `prompt=consent` are both required: the
    former scopes the token to the Jira REST API, the latter (together with
    the `offline_access` scope) guarantees a refresh_token is issued.
    """
    if not jira_configured():
        raise HTTPException(500, "Jira OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.jira_client_id,
        "scope": JIRA_SCOPES,
        "redirect_uri": settings.jira_oauth_redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{JIRA_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a specific
    company. The callback (which has no user session) trusts only this
    signature to know which company gets the new token.

    `return_to` is an optional relative path the callback redirects to instead
    of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": JIRA_PROVIDER,
        "company_id": company_id,
        "return_to": return_to,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != JIRA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON
    {access_token, refresh_token, expires_in, scope, token_type}."""
    if not jira_configured():
        raise HTTPException(500, "Jira OAuth is not configured on the server")
    resp = requests.post(
        JIRA_TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.jira_client_id,
            "client_secret": settings.jira_client_secret,
            "code": code,
            "redirect_uri": settings.jira_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Jira token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "Jira token exchange failed")
    return resp.json()


class JiraAuthExpiredError(RuntimeError):
    """The stored Jira token was rejected and could not be refreshed (refresh
    token expired ~90 days / revoked). The only remedy is the user reconnecting.
    Raised so callers can surface a "reconnect Jira" message instead of a
    generic upstream failure."""


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh {access_token, refresh_token, ...}.

    Atlassian ROTATES refresh tokens — the response carries a new one, so the
    caller must persist the whole payload (see auto_sync / _jira_access_token).
    Raises JiraAuthExpiredError if Atlassian rejects the refresh token."""
    if not jira_configured():
        raise HTTPException(500, "Jira OAuth is not configured on the server")
    resp = requests.post(
        JIRA_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": settings.jira_client_id,
            "client_secret": settings.jira_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if resp.status_code in (400, 401, 403):
        logger.warning(
            "Jira token refresh rejected: %s %s", resp.status_code, resp.text[:200]
        )
        raise JiraAuthExpiredError(
            "Jira rejected the refresh token — reconnect Jira to continue"
        )
    if not resp.ok:
        logger.warning(
            "Jira token refresh failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "Jira token refresh failed")
    return resp.json()


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap Atlassian's token response with an obtained_at stamp before
    encryption, so the refresh scheduler can tell when it expires."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ── Site (cloud) resolution ──────────────────────────────────────────────────
#
# A 3LO token can be authorized against multiple Jira sites. Every REST call
# needs the target site's cloud_id, which the token response does NOT contain.


def get_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    """Return the Jira sites this token can act on, each as Atlassian's native
    {id (cloud_id), name, url, scopes, avatarUrl}. Returns [] on any non-2xx."""
    resp = requests.get(
        JIRA_ACCESSIBLE_RESOURCES_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira accessible-resources failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return []
    return resp.json() or []


def first_cloud_id(access_token: str) -> str | None:
    """Resolve the first accessible Jira site's cloud_id, or None if the token
    can't see any site. Used by the puller, which only carries the access token
    (no stored connection row to read a cached cloud_id from)."""
    sites = get_accessible_resources(access_token)
    return (sites[0].get("id") if sites else None) or None


def fetch_authenticated_user(access_token: str, cloud_id: str) -> dict[str, Any]:
    """Return Jira's /myself payload — {accountId, emailAddress, displayName, ...}.
    Returns {} on any non-2xx so callers can fall back to other label sources."""
    resp = requests.get(
        f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/myself",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning("Jira /myself failed: %s %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json() or {}


# ── Write side (push generated stories/tickets into Jira as issues) ──────────


def list_projects(access_token: str, cloud_id: str) -> list[dict[str, Any]]:
    """Return the Jira projects this token can create issues in, as
    `{id, key, name}` dicts. Used to let the caller pick a target project.

    Uses the paginated `/project/search` endpoint (the legacy `/project` list
    is deprecated). Best-effort pagination capped at a few pages for pilot scale.
    """
    out: list[dict[str, Any]] = []
    start = 0
    for _ in range(10):  # hard page cap — pilot scale
        resp = requests.get(
            f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/project/search",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"startAt": start, "maxResults": 50},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json() or {}
        for p in body.get("values", []):
            out.append({"id": p.get("id"), "key": p.get("key"), "name": p.get("name")})
        if body.get("isLast", True):
            break
        start += len(body.get("values", []) or [])
        if not body.get("values"):
            break
    return [p for p in out if p.get("key")]


def list_assignable_users(
    access_token: str,
    cloud_id: str,
    project_key: str,
    *,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Return the users who can be assigned issues in `project_key`, as
    `{accountId, displayName, email, active, avatarUrl}` dicts. Powers the
    assignee picker on the push UI.

    Uses `/user/assignable/search?project=KEY` — the project-scoped list Jira
    itself uses for its assignee dropdown (only users with the *Assignable User*
    permission, unlike the site-wide `/users/search`). `query` narrows by
    name/email server-side for type-ahead. Read via the `read:jira-user` scope.
    Best-effort pagination capped for pilot scale; returns [] on a bad token so
    the picker degrades to "unassigned" rather than erroring the whole push.
    """
    out: list[dict[str, Any]] = []
    start = 0
    for _ in range(10):  # hard page cap — pilot scale
        params: dict[str, Any] = {
            "project": project_key,
            "startAt": start,
            "maxResults": 50,
        }
        if query:
            params["query"] = query
        resp = requests.get(
            f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/user/assignable/search",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params=params,
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            logger.warning(
                "Jira assignable/search failed for %s: %s %s",
                project_key, resp.status_code, resp.text[:200],
            )
            break
        page = resp.json() or []
        for u in page:
            acct = u.get("accountId")
            if not acct:
                continue
            out.append({
                "accountId": acct,
                "displayName": u.get("displayName"),
                "email": u.get("emailAddress"),
                "active": u.get("active", True),
                "avatarUrl": (u.get("avatarUrls") or {}).get("24x24"),
            })
        # This endpoint returns a bare list (no isLast); a short page = the end.
        if len(page) < 50:
            break
        start += len(page)
    return out


def _adf_from_text(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal Atlassian Document Format (ADF) doc.

    Jira Cloud's v3 API requires `description` as ADF, not a string. We split
    on blank lines into paragraphs; a fuller markdown→ADF conversion is out of
    scope (the body is already human-reviewed markdown-ish text)."""
    blocks = [b for b in (text or "").split("\n\n") if b.strip()]
    if not blocks:
        blocks = [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": block}],
            }
            for block in blocks
        ],
    }


def create_issue(
    access_token: str,
    cloud_id: str,
    *,
    project_key: str,
    summary: str,
    description: str | None = None,
    issue_type: str = "Task",
    priority_name: str | None = None,
    assignee_account_id: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one Jira issue in `project_key`. Returns `{id, key, url}`.

    POST /rest/api/3/issue with a Bearer token. `description` is converted to
    ADF (Jira v3 requires it). `priority_name` maps to Jira's named priorities
    (e.g. "Highest"/"High"/"Medium"/"Low"); omitted when None because not every
    project defines a priority field and Jira 400s on unknown fields.
    `assignee_account_id` sets `fields.assignee` (an Atlassian accountId from
    list_assignable_users); omitted when None so the issue is created unassigned.

    Raises JiraAuthExpiredError on 401/403 so the caller can prompt a reconnect;
    any other non-OK raises HTTPException(502) so per-issue failures stay isolated.
    """
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description is not None:
        fields["description"] = _adf_from_text(description)
    if priority_name is not None:
        fields["priority"] = {"name": priority_name}
    if assignee_account_id is not None:
        fields["assignee"] = {"accountId": assignee_account_id}
    if extra_fields:
        fields.update(extra_fields)

    resp = requests.post(
        f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/issue",
        json={"fields": fields},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        logger.warning(
            "Jira create_issue auth rejected: %s %s",
            resp.status_code, resp.text[:200],
        )
        raise JiraAuthExpiredError(
            "Jira rejected the stored token — reconnect Jira to continue"
        )
    if not resp.ok:
        logger.warning(
            "Jira create_issue failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "Jira issue creation failed")
    data = resp.json() or {}
    key = data.get("key")
    # Build a human-facing browse URL from the site's base URL when we can.
    site_url = _site_url_for_cloud(access_token, cloud_id)
    url = f"{site_url}/browse/{key}" if (site_url and key) else None
    return {"id": data.get("id"), "key": key, "url": url}


def update_issue(
    access_token: str,
    cloud_id: str,
    issue_key: str,
    *,
    summary: str | None = None,
    description: str | None = None,
    priority_name: str | None = None,
    assignee_account_id: str | None = None,
) -> dict[str, Any]:
    """Update an existing Jira issue's editable fields. Returns `{key, url}`.

    Backs idempotent re-push: a ticket already mapped to an issue is UPDATEd in
    place rather than duplicated. Only summary/description/priority/assignee are
    touched (project + issuetype are immutable post-create). `assignee_account_id`
    reassigns the issue (pass an empty string to explicitly unassign). Same
    auth/error contract as create_issue: 401/403 → JiraAuthExpiredError, other
    non-OK → HTTPException.
    """
    fields: dict[str, Any] = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = _adf_from_text(description)
    if priority_name is not None:
        fields["priority"] = {"name": priority_name}
    if assignee_account_id is not None:
        # accountId=None is Jira's "unassign" sentinel; "" from our API maps to it.
        fields["assignee"] = {"accountId": assignee_account_id or None}
    if not fields:
        return {"key": issue_key, "url": None}

    resp = requests.put(
        f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/issue/{issue_key}",
        json={"fields": fields},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        logger.warning(
            "Jira update_issue auth rejected: %s %s",
            resp.status_code, resp.text[:200],
        )
        raise JiraAuthExpiredError(
            "Jira rejected the stored token — reconnect Jira to continue"
        )
    if not resp.ok:
        logger.warning(
            "Jira update_issue failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "Jira issue update failed")
    site_url = _site_url_for_cloud(access_token, cloud_id)
    url = f"{site_url}/browse/{issue_key}" if site_url else None
    return {"key": issue_key, "url": url}


def _text_from_adf(doc: Any) -> str:
    """Extract plain text from an ADF document — the inverse of _adf_from_text
    (paragraphs joined by blank lines, hard breaks as newlines, list items as
    '- item' lines). Lossy for rich marks (bold/links render as bare text),
    which is fine: the sync only needs comparable, editable text."""
    def node_text(n: dict[str, Any]) -> str:
        t = n.get("type")
        if t == "text":
            return n.get("text") or ""
        if t == "hardBreak":
            return "\n"
        return "".join(node_text(c) for c in n.get("content") or [])

    if not isinstance(doc, dict):
        return str(doc or "")
    blocks: list[str] = []
    for block in doc.get("content") or []:
        if block.get("type") in ("bulletList", "orderedList"):
            items = [
                "- " + node_text(li).strip() for li in block.get("content") or []
            ]
            blocks.append("\n".join(items))
        else:
            blocks.append(node_text(block))
    return "\n\n".join(b for b in blocks if b.strip()).strip()


def get_issue(
    access_token: str,
    cloud_id: str,
    issue_key: str,
    *,
    site_url: str | None = None,
) -> dict[str, Any]:
    """Fetch an issue's current state and normalize the fields the two-way
    sync reconciles: workflow state (status name, assignee display name,
    browse url) plus the CONTENT side (title, description as plain text) and
    Jira's last-update time (`updated_at`, ISO) so the sync can decide which
    side of an edit is newer. Mirrors clickup_oauth.get_task — returns {} on
    any failure so one stale/deleted issue never breaks a whole pull. Pass
    `site_url` (from _site_url_for_cloud) when fetching many issues so each
    call doesn't re-resolve accessible-resources."""
    try:
        resp = requests.get(
            f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/issue/{issue_key}",
            params={"fields": "status,assignee,summary,description,updated"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            logger.warning(
                "Jira get_issue failed for %s: %s", issue_key, resp.status_code
            )
            return {}
        fields = (resp.json() or {}).get("fields") or {}
    except Exception:  # noqa: BLE001 — a per-issue fetch failure is non-fatal
        logger.warning("Jira get_issue failed for %s", issue_key)
        return {}
    assignee = fields.get("assignee") or {}
    if site_url is None:
        site_url = _site_url_for_cloud(access_token, cloud_id)
    return {
        "status": (fields.get("status") or {}).get("name"),
        "assignee": assignee.get("displayName") or assignee.get("emailAddress"),
        "url": f"{site_url}/browse/{issue_key}" if site_url else None,
        "title": fields.get("summary"),
        "description": _text_from_adf(fields.get("description")),
        "updated_at": fields.get("updated"),
    }


def transition_issue(
    access_token: str, cloud_id: str, issue_key: str, target_status: str
) -> bool:
    """Move an issue to the workflow status named `target_status` (the
    Sprntly→tracker half of two-way status sync). Jira statuses change via
    TRANSITIONS, not a field write: list the available transitions and apply
    the one landing on the wanted status (case-insensitive). Returns False —
    never raises — when no matching transition exists or any call fails;
    workflows vary per project so this is inherently best-effort."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    base = f"{JIRA_API_BASE}/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
    try:
        resp = requests.get(base, headers=headers, timeout=_TIMEOUT)
        if not resp.ok:
            return False
        want = target_status.strip().lower()
        transition_id = next(
            (
                t.get("id")
                for t in (resp.json() or {}).get("transitions") or []
                if ((t.get("to") or {}).get("name") or "").strip().lower() == want
            ),
            None,
        )
        if not transition_id:
            return False
        done = requests.post(
            base, json={"transition": {"id": transition_id}},
            headers=headers, timeout=_TIMEOUT,
        )
        return done.ok
    except Exception:  # noqa: BLE001 — status push is best-effort by design
        logger.warning("Jira transition failed for %s → %s", issue_key, target_status)
        return False


def _site_url_for_cloud(access_token: str, cloud_id: str) -> str | None:
    """Best-effort lookup of a site's browse base URL (e.g.
    https://acme.atlassian.net) from accessible-resources, for building issue
    links. Returns None if unavailable — the caller then omits the url."""
    for site in get_accessible_resources(access_token):
        if site.get("id") == cloud_id:
            return site.get("url")
    return None

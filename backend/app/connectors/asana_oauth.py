"""Asana OAuth 2.0 helpers.

Asana is a `task-management` connector (catalog.py). CURRENT SCOPE: OAuth
connect only — no KG puller and no ticket-sync engine branch yet, so a
connected Asana shows up healthy in Settings → Connectors but does not
appear on the ticket sync button (SYNC_PROVIDERS) and ingests nothing
(PULLERS). Those are deliberate later phases.

Auth model (developers.asana.com/docs/oauth):
  - App registered in the Asana developer console → client id + secret.
  - Authorization-code flow. The scope param depends on the app's
    permission MODE in the console: full-permissions apps accept only the
    special "default" scope (anything granular → `forbidden_scopes`);
    scoped-permissions apps take space-separated "<resource>:<action>"
    scopes pre-selected on the app. settings.asana_scopes defaults to
    "default"; override for scoped apps.
  - Access tokens live ~1 hour; the long-lived refresh token mints new
    ones (the connector probe refreshes near expiry). Refresh responses
    may omit the refresh_token — keep the stored one in that case.
  - The token response also embeds the authorizing user under "data"
    ({gid, name, email}), which the callback uses for account_label
    without a second call.

Flow (mirrors hubspot/sprinklr):
    1. Frontend hits POST /v1/connectors/asana/start-oauth
    2. We build a state JWT + return Asana's authorize URL
    3. Browser navigates to Asana's consent screen
    4. Asana redirects back to /v1/connectors/asana/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in,
       token_type, data:{user}} and store an encrypted JSON blob under
       provider="asana"
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

ASANA_PROVIDER = "asana"
ASANA_AUTH_URL = "https://app.asana.com/-/oauth_authorize"
ASANA_TOKEN_URL = "https://app.asana.com/-/oauth_token"
ASANA_API = "https://app.asana.com/api/1.0"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def asana_configured() -> bool:
    return bool(
        settings.asana_client_id
        and settings.asana_client_secret
        and settings.asana_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    if not asana_configured():
        raise HTTPException(500, "Asana OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.asana_client_id,
        "redirect_uri": settings.asana_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.asana_scopes,
        "state": state,
    }
    return f"{ASANA_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company —
    the callback (no user session) trusts only this signature."""
    now = int(time.time())
    payload = {
        "provider": ASANA_PROVIDER,
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
    if payload.get("provider") != ASANA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def _token_request(grant_params: dict[str, str]) -> dict[str, Any]:
    if not asana_configured():
        raise HTTPException(500, "Asana OAuth is not configured on the server")
    resp = requests.post(
        ASANA_TOKEN_URL,
        data={
            "client_id": settings.asana_client_id,
            "client_secret": settings.asana_client_secret,
            "redirect_uri": settings.asana_oauth_redirect_uri,
            **grant_params,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Asana token request (%s) failed: %s %s",
            grant_params.get("grant_type"), resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "Asana token exchange failed")
    return resp.json()


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. The response embeds the
    authorizing user under "data" ({gid, name, email})."""
    return _token_request({"grant_type": "authorization_code", "code": code})


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Trade the long-lived refresh token for a fresh ~1h access token.
    NOTE: the response may omit refresh_token — callers must keep the
    stored one in that case (see token_payload_to_store's merge param)."""
    return _token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    """Identity of the token's user (GET /users/me → {gid, name, email}).
    Returns {} on any non-2xx so callers can fall back to other labels."""
    resp = requests.get(
        f"{ASANA_API}/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "Asana users/me failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    payload = resp.json() or {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def token_payload_to_store(
    token_json: dict[str, Any], *, keep_refresh_token: str | None = None,
) -> str:
    """Wrap Asana's token response with an obtained_at stamp (the probe's
    refresh-near-expiry check reads obtained_at + expires_in). A refresh
    response that omitted refresh_token keeps `keep_refresh_token`."""
    payload = dict(token_json)
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ─────────────────────── Ticket-sync API (tasks / sections / stories) ─────────
#
# The write/read surface the two-way ticket sync drives (app/stories/sync.py's
# _Tracker asana branch + app/stories/push.py). Contracts mirror clickup_oauth:
# get_task returns the SAME normalized dict the sync reconciles, and writes
# raise HTTPException(502) on non-auth failures so per-ticket errors stay
# isolated. Auth failures raise AsanaAuthExpiredError → "reconnect Asana".
#
# Asana's data model differs from ClickUp/Jira and shapes the mapping:
#   * No native status  → a task's "status" is the SECTION it sits in within
#     the project; moving status = addTask to another section. The `completed`
#     boolean is the real done signal, kept in step with a done-category move.
#   * No native priority / issue type → not synced (v1).
#   * notes are plain text → we store the same labeled/markdown body we send;
#     it round-trips byte-for-byte so content-hash change detection stays sound.

_WRITE_TIMEOUT = 20
_TASK_OPT_FIELDS = (
    "name,notes,completed,permalink_url,modified_at,"
    "assignee.name,assignee.email,"
    "memberships.project.gid,memberships.section.gid,memberships.section.name"
)


class AsanaAuthExpiredError(RuntimeError):
    """Asana rejected the stored token (401/403) — the caller surfaces a
    reconnect prompt rather than a generic failure."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _raise_for(resp: requests.Response, what: str) -> None:
    if resp.status_code in (401, 403):
        logger.warning("Asana %s auth rejected: %s", what, resp.status_code)
        raise AsanaAuthExpiredError(
            "Asana rejected the stored token — reconnect Asana to continue"
        )
    if not resp.ok:
        logger.warning("Asana %s failed: %s %s", what, resp.status_code, resp.text[:300])
        raise HTTPException(502, f"Asana {what} failed")


def _get(access_token: str, path: str, params: dict | None = None) -> Any:
    r = requests.get(f"{ASANA_API}{path}", params=params or {},
                     headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
    _raise_for(r, "read")
    return (r.json() or {}).get("data")


def list_workspaces(access_token: str) -> list[dict[str, Any]]:
    """The workspaces the token can see (GET /workspaces → [{gid, name}])."""
    return [w for w in (_get(access_token, "/workspaces") or []) if isinstance(w, dict)]


def _projects_in_workspace(access_token: str, ws: str) -> list[dict[str, Any]]:
    data = _get(access_token, "/projects",
                params={"workspace": ws, "archived": "false", "opt_fields": "name",
                        "limit": 100}) or []
    return [{"gid": p.get("gid"), "name": p.get("name")}
            for p in data if isinstance(p, dict) and p.get("gid")]


def list_projects(access_token: str, workspace_gid: str | None = None) -> list[dict[str, Any]]:
    """Projects a push can target ([{gid, name}]). Scoped to `workspace_gid`
    when given, else EVERY workspace the token can see — a user who belongs to
    several Asana organizations must be able to pick a project in any of them.
    Archived projects are excluded."""
    if workspace_gid:
        return _projects_in_workspace(access_token, workspace_gid)
    out: list[dict[str, Any]] = []
    for ws in list_workspaces(access_token):
        gid = ws.get("gid")
        if gid:
            out.extend(_projects_in_workspace(access_token, gid))
    return out


def list_project_sections(access_token: str, project_gid: str) -> list[dict[str, Any]]:
    """A project's sections ([{gid, name}]) — Sprntly treats these as the
    project's status columns. Returns [] on any failure (best-effort meta)."""
    try:
        data = _get(access_token, f"/projects/{project_gid}/sections",
                    params={"opt_fields": "name"}) or []
    except Exception:  # noqa: BLE001 — metadata reads are best-effort
        logger.warning("Asana list_project_sections failed for %s", project_gid)
        return []
    return [{"gid": s.get("gid"), "name": s.get("name")}
            for s in data if isinstance(s, dict) and s.get("gid")]


def _membership_section(task: dict[str, Any], project_gid: str) -> dict[str, Any] | None:
    """The section the task sits in WITHIN `project_gid` (Asana tasks can be in
    several projects). None when the task carries no section there."""
    memberships = task.get("memberships") or []
    for m in memberships:
        if (m.get("project") or {}).get("gid") == project_gid:
            return m.get("section") or None
    # A single membership that omits the project gid is unambiguous — it can
    # only be this task's one project. (A membership that NAMES a different
    # project is not a match and must not be hijacked.)
    if len(memberships) == 1 and not (memberships[0].get("project") or {}).get("gid"):
        return memberships[0].get("section") or None
    return None


def get_task(access_token: str, task_gid: str, *, project_gid: str) -> dict[str, Any]:
    """Fetch a task's state, normalized to the shape the two-way sync
    reconciles (mirror of clickup_oauth.get_task). `status` is the task's
    section NAME within `project_gid`; `completed` is Asana's done boolean.
    Returns {} on any failure so a single stale/deleted task never breaks the
    pull."""
    try:
        data = _get(access_token, f"/tasks/{task_gid}",
                    params={"opt_fields": _TASK_OPT_FIELDS})
    except Exception:  # noqa: BLE001 — a per-task fetch failure (incl. auth) is
        # non-fatal and ISOLATED to this ticket, exactly like clickup_oauth.
        # get_task: one deleted/forbidden task must never abort the whole
        # PRD's sync pass. A genuinely dead token simply makes every remote()
        # return {} → each ticket is skipped (keeps prev), same as ClickUp.
        logger.warning("Asana get_task failed for %s", task_gid)
        return {}
    if not isinstance(data, dict):
        return {}
    section = _membership_section(data, project_gid) or {}
    assignee = data.get("assignee") or {}
    return {
        "status": section.get("name"),
        "section_gid": section.get("gid"),
        "completed": bool(data.get("completed")),
        "assignee": assignee.get("name") or assignee.get("email"),
        "url": data.get("permalink_url"),
        "title": data.get("name"),
        "description": data.get("notes") or "",
        # Asana has no native priority / issue type / (v1) editable fields.
        "priority": None,
        "issue_type": None,
        "custom_fields": {},
        "updated_at": data.get("modified_at"),
    }


def create_task(
    access_token: str, project_gid: str, *, name: str, notes: str | None = None,
) -> dict[str, Any]:
    """Create one task in `project_gid`. Returns {gid, url}."""
    body: dict[str, Any] = {"data": {"name": name, "projects": [project_gid]}}
    if notes is not None:
        body["data"]["notes"] = notes
    r = requests.post(f"{ASANA_API}/tasks", json=body,
                      headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
    _raise_for(r, "create_task")
    data = (r.json() or {}).get("data") or {}
    return {"gid": data.get("gid"), "url": data.get("permalink_url")}


def update_task(
    access_token: str, task_gid: str, *,
    name: str | None = None, notes: str | None = None,
    completed: bool | None = None,
) -> dict[str, Any]:
    """Update an existing task (idempotent re-push). Only given fields are sent.
    Returns {gid, url}."""
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if notes is not None:
        fields["notes"] = notes
    if completed is not None:
        fields["completed"] = completed
    r = requests.put(f"{ASANA_API}/tasks/{task_gid}", json={"data": fields},
                     headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
    _raise_for(r, "update_task")
    data = (r.json() or {}).get("data") or {}
    return {"gid": data.get("gid") or task_gid, "url": data.get("permalink_url")}


def create_subtask(access_token: str, parent_gid: str, *, name: str) -> dict[str, Any]:
    """Create one native subtask under `parent_gid`
    (POST /tasks/{parent}/subtasks). Asana subtasks need no special type —
    every task can have them. Returns {gid, url}."""
    r = requests.post(f"{ASANA_API}/tasks/{parent_gid}/subtasks",
                      json={"data": {"name": name}},
                      headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
    _raise_for(r, "create_subtask")
    data = (r.json() or {}).get("data") or {}
    return {"gid": data.get("gid"), "url": data.get("permalink_url")}


def add_task_to_section(access_token: str, section_gid: str, task_gid: str) -> None:
    """Move a task into a section (POST /sections/{gid}/addTask). This is how a
    Sprntly status change lands in Asana — sections ARE the status columns."""
    r = requests.post(f"{ASANA_API}/sections/{section_gid}/addTask",
                      json={"data": {"task": task_gid}},
                      headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
    _raise_for(r, "add_task_to_section")


def add_task_comment(access_token: str, task_gid: str, text: str) -> str | None:
    """Post one comment as a story (POST /tasks/{gid}/stories). Returns the
    story gid, or None on any failure — comment push is best-effort; the sync
    pass retries unpushed comments."""
    try:
        r = requests.post(f"{ASANA_API}/tasks/{task_gid}/stories",
                          json={"data": {"text": text}},
                          headers=_headers(access_token), timeout=_WRITE_TIMEOUT)
        _raise_for(r, "add_task_comment")
        gid = ((r.json() or {}).get("data") or {}).get("gid")
        return str(gid) if gid is not None else None
    except AsanaAuthExpiredError:
        raise
    except Exception:  # noqa: BLE001 — comment push is best-effort by design
        logger.warning("Asana add_task_comment failed for %s", task_gid)
        return None

"""Live Jira reads for the Q&A agent — on-demand ticket / epic / search fetch.

The counterpart to app/call_digest.py (Fireflies), but shaped differently. A
call digest is a single deterministic pre-fetch feeding a fixed skill; a Jira
lookup is genuinely AGENTIC — the model has to decide WHICH issue or epic to
read, or SEARCH for the right one, from the question. So this module exposes
Jira as read-only TOOLS the agent calls in a loop (see app/jira_lookup.py),
rather than pre-fetching one thing.

Every read reuses the stored OAuth connection (app/connectors/jira_oauth.py) —
the same access token, cloud_id resolution, and rotating-refresh plumbing the
KG puller and the two-way sync already use. Nothing new to authorize.

READ-ONLY by design: search, get one issue (with comments + epic children), and
that's it. Create / update / transition already live in jira_oauth for the push
side and are deliberately NOT surfaced to the chat agent.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from app.connectors.jira_oauth import (
    JIRA_API_BASE,
    get_accessible_resources,
    refresh_access_token,
    token_payload_to_store,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30
# Refresh a token this many seconds before its nominal expiry (mirrors
# auto_sync._TOKEN_REFRESH_SKEW_S) so a lookup never races a just-expired token.
_TOKEN_REFRESH_SKEW_S = 300
# Bounds — pilot-scale, and small enough that a tool result stays inside the
# agent's context budget.
_SEARCH_LIMIT = 20
_COMMENT_LIMIT = 20
_CHILD_LIMIT = 50
_DESC_CHARS = 4000
_COMMENT_CHARS = 800
# Fields we render for a single issue (superset of the puller's — adds parent so
# the model can walk up from a child to its epic).
_ISSUE_FIELDS = (
    "summary,description,status,priority,issuetype,project,labels,assignee,"
    "reporter,parent,subtasks,updated,created,duedate"
)
# Leaner field set for search hits — a one-line-per-result list, no bodies.
_SEARCH_FIELDS = "summary,status,issuetype,priority,assignee,updated"


# ── Session (resolved token + site) ──────────────────────────────────────────


@dataclass
class JiraSession:
    """A tenant's live Jira access for the duration of one lookup: a valid
    access token, the target site's cloud_id (needed on every REST call), and
    the site's browse base URL (for building human issue links)."""

    access_token: str
    cloud_id: str
    site_url: str | None = None

    @property
    def base(self) -> str:
        return f"{JIRA_API_BASE}/{self.cloud_id}/rest/api/3"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }


def _token_is_fresh(token_json: dict) -> bool:
    """True iff the access token is provably still valid past a safety skew.
    Mirrors auto_sync._token_is_fresh; when freshness can't be proven we refresh
    rather than risk a 401 mid-lookup."""
    obtained = token_json.get("obtained_at")
    expires_in = token_json.get("expires_in")
    if not isinstance(obtained, (int, float)) or not isinstance(expires_in, (int, float)):
        return False
    return time.time() < obtained + expires_in - _TOKEN_REFRESH_SKEW_S


def _refresh_if_stale(company_id: str, token_json: dict) -> dict:
    """Refresh an expiring Jira access token, persist the rotated payload, and
    return the updated token_json. Jira access tokens live ~1h and its refresh
    tokens ROTATE, so we persist the whole new payload every time (mirrors
    auto_sync._maybe_refresh_token, kept local so the light lookup path doesn't
    import the sync runner). Best-effort: on any refresh failure we return the
    input unchanged and let the ensuing 401 surface as "reconnect Jira"."""
    refresh_token = token_json.get("refresh_token")
    if not refresh_token or _token_is_fresh(token_json):
        return token_json
    try:
        from app import db

        new_json_str = token_payload_to_store(refresh_access_token(refresh_token))
        db.update_connection_tokens(
            company_id, "jira", encrypt_token_json(new_json_str)
        )
        logger.info("jira-lookup: refreshed access token for %s", company_id)
        return json.loads(new_json_str)
    except Exception:  # noqa: BLE001 — refresh is best-effort
        logger.warning(
            "jira-lookup: token refresh failed for %s — surfacing reconnect",
            company_id, exc_info=True,
        )
        return token_json


def open_session(company_id: str) -> JiraSession | None:
    """Resolve a live JiraSession for a company, or None when Jira isn't
    connected / the credential can't be read / the token sees no site. Never
    raises — the caller degrades to a "connect Jira" message."""
    from app import db

    row = db.get_connection(company_id, "jira")
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning("jira-lookup: could not decrypt jira token for %s", company_id)
        return None
    token_json = _refresh_if_stale(company_id, token_json)
    access_token = token_json.get("access_token")
    if not access_token:
        return None
    sites = get_accessible_resources(access_token)
    if not sites:
        logger.warning("jira-lookup: token for %s can see no Jira site", company_id)
        return None
    site = sites[0]
    cloud_id = site.get("id")
    if not cloud_id:
        return None
    return JiraSession(
        access_token=access_token, cloud_id=cloud_id, site_url=site.get("url")
    )


# ── ADF + JQL helpers ────────────────────────────────────────────────────────


def _adf_text(node: object) -> str:
    """Flatten an Atlassian Document Format node (or None) to plain text by
    collecting every `text` leaf. Good enough for the agent to read."""
    if not isinstance(node, dict):
        return ""
    out: list[str] = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        out.append(node["text"])
    for child in node.get("content", []) or []:
        out.append(_adf_text(child))
    return " ".join(p for p in out if p)


def _jql_str(value: str) -> str:
    r"""Escape a user/model-supplied value for a double-quoted JQL string:
    backslash first, then the quote, so `"` → `\"` and `\` → `\\`. Bounds the
    injection surface of the model-proposed search terms."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_search_jql(
    *, text: str | None, project: str | None, status: str | None
) -> str:
    """Assemble a bounded JQL query from safe, escaped clauses. The enhanced
    /search/jql endpoint REJECTS unbounded JQL (a bare `ORDER BY` 400s), so when
    no filter is given we anchor with a wide `created` floor — same trick the
    puller uses — to bound the query while still matching everything."""
    clauses: list[str] = []
    if text and text.strip():
        clauses.append(f'text ~ "{_jql_str(text.strip())}"')
    if project and project.strip():
        clauses.append(f'project = "{_jql_str(project.strip())}"')
    if status and status.strip():
        clauses.append(f'status = "{_jql_str(status.strip())}"')
    if not clauses:
        clauses.append('created >= "2000-01-01"')
    return " AND ".join(clauses) + " ORDER BY updated DESC"


# ── Fetch ────────────────────────────────────────────────────────────────────


def search(
    session: JiraSession,
    *,
    text: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = _SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """Search issues via the enhanced /search/jql endpoint. Returns lean hit
    dicts (key/summary/type/status/priority/assignee/updated/url), newest-updated
    first. Raises on a hard API failure so the tool dispatch can surface it to
    the model."""
    jql = _build_search_jql(text=text, project=project, status=status)
    return _search_jql(session, jql, limit=limit)


def _get_comments(session: JiraSession, issue_key: str) -> list[dict[str, str]]:
    """Fetch the most recent comments on an issue as {author, text} dicts.
    Best-effort: returns [] on any failure — a comment-fetch hiccup must not sink
    the whole issue read."""
    try:
        r = requests.get(
            f"{session.base}/issue/{issue_key}/comment",
            params={"maxResults": _COMMENT_LIMIT, "orderBy": "-created"},
            headers=session.headers,
            timeout=_TIMEOUT,
        )
        if not r.ok:
            return []
        comments = (r.json() or {}).get("comments", []) or []
    except Exception:  # noqa: BLE001 — comments are best-effort
        logger.warning("jira-lookup: comment fetch failed for %s", issue_key)
        return []
    out: list[dict[str, str]] = []
    for c in comments[:_COMMENT_LIMIT]:
        body = _adf_text(c.get("body")).strip()
        if not body:
            continue
        out.append({
            "author": ((c.get("author") or {}) or {}).get("displayName") or "?",
            "text": body[:_COMMENT_CHARS],
        })
    return out


def _get_epic_children(session: JiraSession, epic_key: str) -> list[dict[str, Any]]:
    """List the issues parented to an epic (`parent = KEY`). Best-effort — team-
    managed and modern company-managed projects both parent children this way;
    if a project uses the legacy Epic Link only, this returns [] and the model
    still gets the epic itself. Returns lean hit dicts like search()."""
    try:
        return _search_jql(
            session, f'parent = "{_jql_str(epic_key)}" ORDER BY created ASC',
            limit=_CHILD_LIMIT,
        )
    except Exception:  # noqa: BLE001 — children are best-effort context
        logger.warning("jira-lookup: epic-children fetch failed for %s", epic_key)
        return []


def _search_jql(session: JiraSession, jql: str, *, limit: int) -> list[dict[str, Any]]:
    """Run a raw (already-bounded) JQL string and return lean hit dicts. Internal
    — external callers use search(), which builds a safe JQL from typed args."""
    r = requests.get(
        f"{session.base}/search/jql",
        params={"jql": jql, "maxResults": limit, "fields": _SEARCH_FIELDS},
        headers=session.headers,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    issues = (r.json() or {}).get("issues", []) or []
    out: list[dict[str, Any]] = []
    for it in issues:
        f = it.get("fields") or {}
        key = it.get("key") or it.get("id") or ""
        out.append({
            "key": key,
            "summary": f.get("summary") or "",
            "type": ((f.get("issuetype") or {}) or {}).get("name"),
            "status": ((f.get("status") or {}) or {}).get("name"),
            "priority": ((f.get("priority") or {}) or {}).get("name"),
            "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
            "updated": f.get("updated"),
            "url": f"{session.site_url}/browse/{key}" if session.site_url else None,
        })
    return out


def get_issue(session: JiraSession, issue_key: str) -> dict[str, Any] | None:
    """Fetch one issue in full: fields + comments + (for an epic) its children.
    Returns None when the issue doesn't exist / isn't visible (404/410) so the
    caller can tell the model "no such issue" rather than inventing one. Raises
    on a transient/auth failure so the dispatch surfaces it."""
    r = requests.get(
        f"{session.base}/issue/{issue_key}",
        params={"fields": _ISSUE_FIELDS},
        headers=session.headers,
        timeout=_TIMEOUT,
    )
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    f = (r.json() or {}).get("fields") or {}
    issue_type = ((f.get("issuetype") or {}) or {}).get("name")
    parent = f.get("parent") or {}
    key = issue_key
    out: dict[str, Any] = {
        "key": key,
        "summary": f.get("summary") or "",
        "type": issue_type,
        "status": ((f.get("status") or {}) or {}).get("name"),
        "priority": ((f.get("priority") or {}) or {}).get("name"),
        "project": ((f.get("project") or {}) or {}).get("name"),
        "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
        "reporter": ((f.get("reporter") or {}) or {}).get("displayName"),
        "labels": f.get("labels") or [],
        "due_date": f.get("duedate"),
        "updated": f.get("updated"),
        "created": f.get("created"),
        "description": _adf_text(f.get("description"))[:_DESC_CHARS],
        "url": f"{session.site_url}/browse/{key}" if session.site_url else None,
        "parent": {
            "key": parent.get("key"),
            "summary": ((parent.get("fields") or {}) or {}).get("summary"),
        } if parent.get("key") else None,
        "subtasks": [
            {
                "key": st.get("key"),
                "summary": ((st.get("fields") or {}) or {}).get("summary"),
                "status": (((st.get("fields") or {}).get("status") or {}) or {}).get("name"),
            }
            for st in (f.get("subtasks") or [])
        ],
        "comments": _get_comments(session, issue_key),
    }
    if (issue_type or "").lower() == "epic":
        out["children"] = _get_epic_children(session, issue_key)
    return out


# ── Rendering (dict → text for a tool result) ────────────────────────────────


def render_hit(h: dict[str, Any]) -> str:
    """One search hit as a single line."""
    bits = [f"{h['key']}: {h.get('summary') or ''}"]
    tags = [t for t in (h.get("type"), h.get("status"), h.get("priority")) if t]
    if tags:
        bits.append(f"[{' · '.join(tags)}]")
    if h.get("assignee"):
        bits.append(f"@{h['assignee']}")
    return " ".join(bits)


def render_search(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No matching Jira issues."
    return "\n".join(f"- {render_hit(h)}" for h in hits)


def render_issue(issue: dict[str, Any]) -> str:
    """Full issue → text block for the model: header, metadata, description,
    parent/subtasks/epic-children, then comments."""
    lines = [f"{issue['key']}: {issue.get('summary') or ''}"]
    meta = [t for t in (issue.get("type"), issue.get("status"), issue.get("priority")) if t]
    if meta:
        lines.append(" · ".join(meta))
    row = []
    if issue.get("project"):
        row.append(f"project: {issue['project']}")
    if issue.get("assignee"):
        row.append(f"assignee: {issue['assignee']}")
    if issue.get("reporter"):
        row.append(f"reporter: {issue['reporter']}")
    if issue.get("labels"):
        row.append("labels: " + ", ".join(issue["labels"]))
    if issue.get("due_date"):
        row.append(f"due: {issue['due_date']}")
    if issue.get("updated"):
        row.append(f"updated: {issue['updated']}")
    if row:
        lines.append(" · ".join(row))
    if issue.get("url"):
        lines.append(issue["url"])
    if issue.get("parent"):
        p = issue["parent"]
        lines.append(f"parent: {p.get('key')} — {p.get('summary') or ''}")
    if issue.get("description"):
        lines.append("\ndescription:\n" + issue["description"])
    if issue.get("subtasks"):
        lines.append("\nsubtasks:")
        lines.extend(
            f"  - {s.get('key')}: {s.get('summary') or ''}"
            + (f" [{s['status']}]" if s.get("status") else "")
            for s in issue["subtasks"]
        )
    if issue.get("children"):
        lines.append(f"\nepic children ({len(issue['children'])}):")
        lines.extend(f"  - {render_hit(c)}" for c in issue["children"])
    if issue.get("comments"):
        lines.append("\ncomments:")
        lines.extend(f'  - {c["author"]}: {c["text"]}' for c in issue["comments"])
    return "\n".join(lines)

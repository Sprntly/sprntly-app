"""Jira puller — issues → RawRecords.

Auth: Bearer access token proxied through api.atlassian.com. Unlike ClickUp,
Jira Cloud needs the site's `cloud_id` on every REST call — it isn't in the
token, so the puller resolves it from `/oauth/token/accessible-resources`
(the puller only carries the access-token string, no stored connection row).

Issues carry a real type in Jira (Bug/Story/Task/Epic), so we pass it straight
through as a property; the extractor still classifies downstream, but the native
type is a useful signal.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.connectors.jira_oauth import (
    JIRA_API_BASE,
    first_cloud_id,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_PAGE_LIMIT = 5          # pages per site — pilot-scale cap; bump when needed
_PAGE_SIZE = 100
# Only the fields we render — keeps the payload small and the extractor focused.
_FIELDS = "summary,description,status,priority,issuetype,project,labels,assignee,updated,created"


def _plain_text_from_adf(node: object) -> str:
    """Flatten an Atlassian Document Format description into plain text.

    Jira v3 returns `description` as a nested ADF doc (or None). We walk the
    tree collecting every `text` leaf; good enough for entity extraction.
    """
    if not isinstance(node, dict):
        return ""
    out: list[str] = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        out.append(node["text"])
    for child in node.get("content", []) or []:
        out.append(_plain_text_from_adf(child))
    return " ".join(p for p in out if p)


def pull(token: str) -> Iterator[RawRecord]:
    """Yield every accessible issue across the token's first Jira site.

    Uses the enhanced `/search/jql` endpoint with token-based pagination
    (`nextPageToken`), ordering by most-recently-updated so a page cap keeps
    the freshest issues.
    """
    cloud_id = first_cloud_id(token)
    if not cloud_id:
        logger.warning("Jira puller: no accessible site for token — nothing to pull")
        return

    base = f"{JIRA_API_BASE}/{cloud_id}/rest/api/3"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    next_token: str | None = None

    for _ in range(_PAGE_LIMIT):
        params: dict[str, object] = {
            "jql": "ORDER BY updated DESC",
            "maxResults": _PAGE_SIZE,
            "fields": _FIELDS,
        }
        if next_token:
            params["nextPageToken"] = next_token
        r = requests.get(f"{base}/search/jql", params=params,
                         headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json() or {}
        issues = data.get("issues", [])
        if not issues:
            break
        for it in issues:
            fields = it.get("fields") or {}
            yield RawRecord(
                provider="jira",
                kind="issue",
                external_id=str(it.get("key") or it.get("id") or ""),
                title=fields.get("summary", "") or "",
                text=_plain_text_from_adf(fields.get("description"))[:2000],
                properties={
                    "status": ((fields.get("status") or {}) or {}).get("name"),
                    "priority": ((fields.get("priority") or {}) or {}).get("name"),
                    "type": ((fields.get("issuetype") or {}) or {}).get("name"),
                    "project": ((fields.get("project") or {}) or {}).get("name"),
                    "labels": fields.get("labels") or [],
                    "assignee": ((fields.get("assignee") or {}) or {}).get("displayName"),
                },
                timestamp=fields.get("updated") or fields.get("created"),
            )
        next_token = data.get("nextPageToken")
        if data.get("isLast", False) or not next_token:
            break

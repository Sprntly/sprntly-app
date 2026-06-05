"""ClickUp puller — tasks → RawRecords.

Auth quirk (per #106): raw token in `Authorization`, no `Bearer ` prefix.
Tasks are untyped in ClickUp (product decision 2026-05-28): the agent
classifies bug/feature/fix downstream — we just deliver the raw task.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

API = "https://api.clickup.com/api/v2"
_TIMEOUT = 30
_PAGE_LIMIT = 5  # pages per list — pilot-scale cap; bump when needed


def _get(token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API}{path}", params=params or {},
                     headers={"Authorization": token}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pull(token: str) -> Iterator[RawRecord]:
    """Yield every accessible task across the user's teams/spaces/lists."""
    for team in _get(token, "/team").get("teams", []):
        team_id = team["id"]
        for page in range(_PAGE_LIMIT):
            data = _get(token, f"/team/{team_id}/task",
                        params={"page": page, "subtasks": "true",
                                "include_closed": "true"})
            tasks = data.get("tasks", [])
            if not tasks:
                break
            for t in tasks:
                status = (t.get("status") or {}).get("status", "")
                yield RawRecord(
                    provider="clickup",
                    kind="task",
                    external_id=str(t["id"]),
                    title=t.get("name", ""),
                    text=(t.get("text_content") or t.get("description") or "")[:2000],
                    properties={
                        "status": status,
                        "priority": ((t.get("priority") or {}) or {}).get("priority"),
                        "list": ((t.get("list") or {}) or {}).get("name"),
                        "tags": [g.get("name") for g in t.get("tags", [])],
                        "assignees": [a.get("username") for a in t.get("assignees", [])],
                    },
                    timestamp=t.get("date_updated") or t.get("date_created"),
                )
            if data.get("last_page", False) or len(tasks) < 100:
                break

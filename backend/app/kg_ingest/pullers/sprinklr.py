"""Sprinklr puller — CX cases + inbound social messages → RawRecords.

Sprinklr is the outside-in voice-of-customer source: support/CX cases
(pain + churn risk) and inbound social messages/mentions (what the market
says publicly across social channels, reviews, forums).

Two sub-pulls, each independently error-isolated:

  * cases     — POST api/v2/case/search      (Sprinklr Service cases)
  * messages  — POST api/v2/search/MESSAGE   (universal-search, inbound)

Sprinklr entitlements vary a lot per license (Service vs Insights vs Social),
so a sub-resource the account can't query (403/404/400-not-entitled) is
logged and skipped rather than failing the whole sync — same philosophy as
the HubSpot puller's scope-gating, cast wider because Sprinklr deployments
differ more.

DATA-MINIMIZATION: transient, paged, pilot-scale pulls distilled into
compact RawRecords — never a bulk row copy. Text is capped per record.

Auth: every call carries the user's Bearer token AND the developer-portal
API key as a `key` header (sprinklr_oauth.auth_headers). URLs are
environment-aware via sprinklr_oauth.api_base().
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.connectors import sprinklr_oauth
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_PAGE_ROWS = 100
_MAX_PAGES = 10  # pilot-scale cap, mirrors the HubSpot puller


def _search(token: str, path: str, page: int) -> list[dict]:
    """One page of a Sprinklr search POST. Returns the item list, tolerating
    the response-shape variants Sprinklr uses across endpoints/versions."""
    body = {
        "filter": {"type": "AND", "filters": []},
        "paginationInfo": {"start": page * _PAGE_ROWS, "rowCount": _PAGE_ROWS},
    }
    r = requests.post(
        f"{sprinklr_oauth.api_base()}{path}",
        json=body,
        headers=sprinklr_oauth.auth_headers(token),
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json() or {}
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("results", "cases", "messages", "searchResults"):
            items = data.get(key)
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
    return []


def _paged(token: str, path: str) -> Iterator[dict]:
    for page in range(_MAX_PAGES):
        items = _search(token, path, page)
        yield from items
        if len(items) < _PAGE_ROWS:
            break


def _first(obj: dict, *keys: str) -> str:
    for k in keys:
        v = obj.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _pull_cases(token: str) -> Iterator[RawRecord]:
    """Sprinklr Service cases — support pain signals + churn risk."""
    for c in _paged(token, "api/v2/case/search"):
        case_id = _first(c, "id", "caseId", "caseNumber")
        if not case_id:
            continue
        yield RawRecord(
            provider="sprinklr",
            kind="case",
            external_id=case_id,
            title=_first(c, "subject", "summary", "description")[:300],
            text=_first(c, "description", "summary")[:2000],
            properties={
                "status": _first(c, "status", "caseStatus"),
                "priority": _first(c, "priority"),
                "case_type": _first(c, "caseType", "type"),
                "channel": _first(c, "channel", "source", "snType"),
                "sentiment": _first(c, "sentiment"),
            },
            timestamp=_first(c, "modificationTime", "modifiedTime", "createdTime")
            or None,
        )


def _pull_messages(token: str) -> Iterator[RawRecord]:
    """Inbound social messages/mentions — public voice-of-customer."""
    for m in _paged(token, "api/v2/search/MESSAGE"):
        msg_id = _first(m, "messageId", "id", "universalMessageId")
        text = _first(m, "message", "text", "content", "description")
        if not msg_id or not text.strip():
            continue
        yield RawRecord(
            provider="sprinklr",
            kind="message",
            external_id=msg_id,
            title=text[:120],
            text=text[:2000],
            properties={
                "channel": _first(m, "snType", "channel", "source", "channelType"),
                "sentiment": _first(m, "sentiment"),
                "message_type": _first(m, "messageType", "type"),
                "permalink": _first(m, "permalink", "url"),
            },
            timestamp=_first(m, "snCreatedTime", "createdTime", "modificationTime")
            or None,
        )


# Sub-resource pullers, in pull order. Each is entitlement-gated: an HTTP
# error on one dataset is logged and skipped, keeping the rest alive.
_SUB_PULLERS: list[tuple[str, "callable"]] = [
    ("cases", _pull_cases),
    ("messages", _pull_messages),
]


def pull(token: str) -> Iterator[RawRecord]:
    """Yield distilled RawRecords across every dataset this Sprinklr license
    can query. Connection-level failures (both datasets rejecting with an
    auth error) still surface: if NOTHING yielded and every sub-pull failed,
    the last error propagates so a dead token fails loudly."""
    yielded = False
    last_error: Exception | None = None
    for label, sub in _SUB_PULLERS:
        try:
            for rec in sub(token):
                yielded = True
                yield rec
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.info("sprinklr: skipping %s (HTTP %s): %s", label, status, e)
            last_error = e
    if not yielded and last_error is not None:
        raise last_error

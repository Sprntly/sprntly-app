"""Superset puller — BI metadata → RawRecords (what the company measures).

Metadata-only by design (phase 1): dashboards, charts, datasets, and saved
queries give the KG a map of the company's metrics vocabulary — enough for
"what do we track for onboarding?" chat answers and for briefs to name the
right KPIs. Pulling actual chart DATA (/api/v1/chart/{id}/data) is a
deliberate later phase: it can be expensive on the customer's instance.

The credential arg is the JSON triple stored by superset_auth
(base_url/username/password — PULLERS key "superset_credential"); the
puller logs in fresh each sync, so instance-configured token lifetimes
never matter here.

Sub-resources are independently error-isolated (a Gamma service account
may lack access to some lists, and older instances differ) — same
philosophy as the Sprinklr/HubSpot pullers. A fully dead credential still
fails loudly: if nothing yielded and every sub-pull failed, the last error
propagates.

DATA-MINIMIZATION: transient, paged, pilot-scale pulls distilled into
compact RawRecords; saved-query SQL is capped, never a bulk copy.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.connectors import superset_auth
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_PAGE_SIZE = 100
_MAX_PAGES = 10  # pilot-scale cap, mirrors the other pullers


def _paged(base_url: str, token: str, path: str) -> Iterator[dict]:
    """Yield rows from a Superset list endpoint (Rison-paged, {result: []})."""
    for page in range(_MAX_PAGES):
        r = requests.get(
            f"{base_url}{path}",
            # Superset list endpoints take a Rison query object.
            params={"q": f"(page:{page},page_size:{_PAGE_SIZE})"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("result") or []
        yield from (x for x in rows if isinstance(x, dict))
        if len(rows) < _PAGE_SIZE:
            break


def _pull_dashboards(base_url: str, token: str) -> Iterator[RawRecord]:
    for d in _paged(base_url, token, "/api/v1/dashboard/"):
        yield RawRecord(
            provider="superset",
            kind="dashboard",
            external_id=str(d.get("id", "")),
            title=d.get("dashboard_title") or "dashboard",
            text="",
            properties={
                "status": d.get("status"),
                "published": d.get("published"),
                "url": f"{base_url}{d['url']}" if d.get("url") else None,
                "owners": ", ".join(
                    " ".join(x for x in [o.get("first_name"), o.get("last_name")] if x)
                    for o in (d.get("owners") or [])
                    if isinstance(o, dict)
                ) or None,
            },
            timestamp=d.get("changed_on_utc") or d.get("changed_on"),
        )


def _pull_charts(base_url: str, token: str) -> Iterator[RawRecord]:
    for c in _paged(base_url, token, "/api/v1/chart/"):
        yield RawRecord(
            provider="superset",
            kind="chart",
            external_id=str(c.get("id", "")),
            title=c.get("slice_name") or "chart",
            text=(c.get("description") or "")[:1000],
            properties={
                "viz_type": c.get("viz_type"),
                "dataset": c.get("datasource_name_text"),
            },
            timestamp=c.get("changed_on_utc") or c.get("changed_on"),
        )


def _pull_datasets(base_url: str, token: str) -> Iterator[RawRecord]:
    for ds in _paged(base_url, token, "/api/v1/dataset/"):
        database = ds.get("database") or {}
        yield RawRecord(
            provider="superset",
            kind="dataset",
            external_id=str(ds.get("id", "")),
            title=ds.get("table_name") or "dataset",
            text=(ds.get("description") or "")[:1000],
            properties={
                "schema": ds.get("schema"),
                "database": database.get("database_name")
                if isinstance(database, dict) else None,
                "kind": ds.get("kind"),
            },
            timestamp=ds.get("changed_on_utc") or ds.get("changed_on"),
        )


def _pull_saved_queries(base_url: str, token: str) -> Iterator[RawRecord]:
    for q in _paged(base_url, token, "/api/v1/saved_query/"):
        yield RawRecord(
            provider="superset",
            kind="saved_query",
            external_id=str(q.get("id", "")),
            title=q.get("label") or "saved query",
            text=((q.get("description") or "") + "\n" + (q.get("sql") or ""))
            .strip()[:2000],
            properties={"schema": q.get("schema")},
            timestamp=q.get("changed_on_utc") or q.get("changed_on"),
        )


# Sub-resource pullers, in pull order. Each is permission-gated: an HTTP
# error on one list is logged and skipped, keeping the rest alive.
_SUB_PULLERS: list[tuple[str, "callable"]] = [
    ("dashboards", _pull_dashboards),
    ("charts", _pull_charts),
    ("datasets", _pull_datasets),
    ("saved_queries", _pull_saved_queries),
]


def pull(credential: str) -> Iterator[RawRecord]:
    """Yield distilled RawRecords across every list this service account can
    read. Logs in fresh (see module docstring); a bad credential raises
    SupersetAuthError so the sync surfaces "reconnect required"."""
    base_url, username, password = superset_auth.parse_credential(credential)
    token = superset_auth.login(base_url, username, password)["access_token"]

    yielded = False
    last_error: Exception | None = None
    for label, sub in _SUB_PULLERS:
        try:
            for rec in sub(base_url, token):
                yielded = True
                yield rec
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.info("superset: skipping %s (HTTP %s): %s", label, status, e)
            last_error = e
    if not yielded and last_error is not None:
        raise last_error

"""HubSpot puller — deals (+ company associations) → RawRecords."""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

API = "https://api.hubapi.com"
_TIMEOUT = 30
_MAX_PAGES = 10  # 100/page — pilot-scale cap

_DEAL_PROPS = "dealname,amount,dealstage,pipeline,closedate,hs_lastmodifieddate,hubspot_owner_id,description"


def _get(token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API}{path}", params=params or {},
                     headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pull(token: str) -> Iterator[RawRecord]:
    """Yield all deals with company associations."""
    after: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict = {"limit": 100, "properties": _DEAL_PROPS,
                        "associations": "companies"}
        if after:
            params["after"] = after
        data = _get(token, "/crm/v3/objects/deals", params)
        for d in data.get("results", []):
            p = d.get("properties") or {}
            company_ids = [
                a.get("id")
                for a in ((d.get("associations") or {}).get("companies") or {}).get("results", [])
            ]
            yield RawRecord(
                provider="hubspot",
                kind="deal",
                external_id=str(d["id"]),
                title=p.get("dealname", ""),
                text=(p.get("description") or "")[:2000],
                properties={
                    "amount_usd": p.get("amount"),
                    "stage": p.get("dealstage"),
                    "pipeline": p.get("pipeline"),
                    "close_date": p.get("closedate"),
                    "company_ids": company_ids,
                },
                timestamp=p.get("hs_lastmodifieddate"),
            )
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break

"""HubSpot puller — distilled CRM signals → RawRecords.

Beyond deals, the broadened scope set (contacts/companies/deals/owners/
tickets/sales-email-read/line_items) lets us surface higher-value signals:

  * deals        — revenue blockers / feature gaps (the original puller).
  * tickets      — support pain (high-value voice-of-customer + churn risk).
  * engagements  — notes + emails (what customers actually said).
  * owners       — attribution context (who owns the relationship).
  * line items   — revenue detail (which SKUs / products are in play).

DATA-MINIMIZATION (§6): each sub-resource is a transient, paged, pilot-scale
pull distilled into a compact RawRecord — never a bulk row copy. A sub-resource
whose scope was NOT granted returns 403; we catch it, log, and continue so one
missing scope never fails the whole sync.
"""
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
_TICKET_PROPS = ("subject,content,hs_pipeline_stage,hs_ticket_priority,"
                 "hs_ticket_category,source_type,hs_lastmodifieddate,hubspot_owner_id")
_LINE_ITEM_PROPS = ("name,quantity,price,amount,hs_product_id,hs_sku,"
                    "hs_lastmodifieddate")
_OWNER_FIELDS = "id,email,firstName,lastName"
# Engagements we treat as voice-of-customer: notes + emails. Each object type
# carries its own body property.
_NOTE_PROPS = "hs_note_body,hs_timestamp,hs_lastmodifieddate,hubspot_owner_id"
_EMAIL_PROPS = ("hs_email_subject,hs_email_text,hs_email_direction,"
                "hs_timestamp,hs_lastmodifieddate,hubspot_owner_id")


def _get(token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API}{path}", params=params or {},
                     headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _paged_objects(token: str, path: str, props: str,
                   associations: str | None = None) -> Iterator[dict]:
    """Yield raw objects from a paged CRM v3 list endpoint (pilot-scale cap)."""
    after: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict = {"limit": 100, "properties": props}
        if associations:
            params["associations"] = associations
        if after:
            params["after"] = after
        data = _get(token, path, params)
        yield from data.get("results", [])
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break


def _is_forbidden(exc: requests.HTTPError) -> bool:
    """True iff the error is a 403 — the scope for this sub-resource was not
    granted. We skip the sub-resource rather than failing the whole sync."""
    resp = getattr(exc, "response", None)
    return resp is not None and resp.status_code == 403


def _pull_deals(token: str) -> Iterator[RawRecord]:
    for d in _paged_objects(token, "/crm/v3/objects/deals", _DEAL_PROPS,
                            associations="companies"):
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
                "owner_id": p.get("hubspot_owner_id"),
                "company_ids": company_ids,
            },
            timestamp=p.get("hs_lastmodifieddate"),
        )


def _pull_tickets(token: str) -> Iterator[RawRecord]:
    """Support tickets — pain signals + churn risk (high value)."""
    for t in _paged_objects(token, "/crm/v3/objects/tickets", _TICKET_PROPS,
                            associations="companies"):
        p = t.get("properties") or {}
        company_ids = [
            a.get("id")
            for a in ((t.get("associations") or {}).get("companies") or {}).get("results", [])
        ]
        yield RawRecord(
            provider="hubspot",
            kind="ticket",
            external_id=str(t["id"]),
            title=p.get("subject", ""),
            text=(p.get("content") or "")[:2000],
            properties={
                "stage": p.get("hs_pipeline_stage"),
                "priority": p.get("hs_ticket_priority"),
                "category": p.get("hs_ticket_category"),
                "source": p.get("source_type"),
                "owner_id": p.get("hubspot_owner_id"),
                "company_ids": company_ids,
            },
            timestamp=p.get("hs_lastmodifieddate"),
        )


def _pull_engagements(token: str) -> Iterator[RawRecord]:
    """Notes + emails — voice-of-customer (what was actually said)."""
    for n in _paged_objects(token, "/crm/v3/objects/notes", _NOTE_PROPS):
        p = n.get("properties") or {}
        body = (p.get("hs_note_body") or "").strip()
        if not body:
            continue
        yield RawRecord(
            provider="hubspot",
            kind="note",
            external_id=str(n["id"]),
            title="CRM note",
            text=body[:2000],
            properties={"owner_id": p.get("hubspot_owner_id")},
            timestamp=p.get("hs_timestamp") or p.get("hs_lastmodifieddate"),
        )
    for e in _paged_objects(token, "/crm/v3/objects/emails", _EMAIL_PROPS):
        p = e.get("properties") or {}
        body = (p.get("hs_email_text") or "").strip()
        if not body and not p.get("hs_email_subject"):
            continue
        yield RawRecord(
            provider="hubspot",
            kind="email",
            external_id=str(e["id"]),
            title=p.get("hs_email_subject", "") or "CRM email",
            text=body[:2000],
            properties={
                "direction": p.get("hs_email_direction"),
                "owner_id": p.get("hubspot_owner_id"),
            },
            timestamp=p.get("hs_timestamp") or p.get("hs_lastmodifieddate"),
        )


def _pull_owners(token: str) -> Iterator[RawRecord]:
    """Owners — attribution context (who owns each relationship). The owners
    API is a flat list, not a CRM object search, so it has its own shape."""
    after: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict = {"limit": 100}
        if after:
            params["after"] = after
        data = _get(token, "/crm/v3/owners", params)
        for o in data.get("results", []):
            name = " ".join(
                x for x in [o.get("firstName"), o.get("lastName")] if x
            ).strip()
            yield RawRecord(
                provider="hubspot",
                kind="owner",
                external_id=str(o.get("id")),
                title=name or o.get("email", "") or "owner",
                text="",
                properties={"email": o.get("email")},
                timestamp=o.get("updatedAt"),
            )
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break


def _pull_line_items(token: str) -> Iterator[RawRecord]:
    """Line items — revenue detail (which SKUs / products are in play)."""
    for li in _paged_objects(token, "/crm/v3/objects/line_items", _LINE_ITEM_PROPS,
                             associations="deals"):
        p = li.get("properties") or {}
        deal_ids = [
            a.get("id")
            for a in ((li.get("associations") or {}).get("deals") or {}).get("results", [])
        ]
        yield RawRecord(
            provider="hubspot",
            kind="line_item",
            external_id=str(li["id"]),
            title=p.get("name", "") or "line item",
            text="",
            properties={
                "quantity": p.get("quantity"),
                "price": p.get("price"),
                "amount_usd": p.get("amount"),
                "sku": p.get("hs_sku"),
                "product_id": p.get("hs_product_id"),
                "deal_ids": deal_ids,
            },
            timestamp=p.get("hs_lastmodifieddate"),
        )


# Sub-resource pullers, in pull order. Each is scope-gated: a missing scope
# yields a 403 we catch and skip (rather than failing the whole sync).
_SUB_PULLERS: list[tuple[str, "callable"]] = [
    ("deals", _pull_deals),
    ("tickets", _pull_tickets),
    ("engagements", _pull_engagements),
    ("owners", _pull_owners),
    ("line_items", _pull_line_items),
]


def pull(token: str) -> Iterator[RawRecord]:
    """Yield distilled RawRecords across every granted CRM sub-resource.

    Each sub-resource is independently scope-gated: if its scope was not
    granted HubSpot returns 403 — we log and skip it, keeping the rest of the
    sync alive. Other HTTP errors propagate (a bad token / outage should fail
    loudly, not silently drop data)."""
    for label, sub in _SUB_PULLERS:
        try:
            yield from sub(token)
        except requests.HTTPError as e:
            if _is_forbidden(e):
                logger.info("hubspot: skipping %s — scope not granted (403)", label)
                continue
            raise

"""HubSpot adapter — CRM search/read, honest about which scopes were granted.

The scope situation is the interesting part. Sprntly's code default requests
contacts-only scope (config.py), and broadening that is an env + re-consent
change, deliberately out of scope here. So HubSpot answers a chat question with
whatever the tenant's token actually covers, and says so when it doesn't: a 403
on `deals` means "that scope was not granted", not "there are no deals". Same
per-scope 403-skip the KG puller does (kg_ingest/pullers/hubspot.py:229-241),
surfaced as a sentence instead of a log line — a silent skip here would read to
the user as "HubSpot has nothing about that".

Read-only: search and read. No create/update path is reachable from chat.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests
from fastapi import HTTPException

from app.connector_lookup.base import HTTP_TIMEOUT, LookupSession, cap_items

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

DISPLAY_NAME = "HubSpot"
API = "https://api.hubapi.com"

#: Object types a PM asks about, with the properties worth rendering.
OBJECT_PROPS: dict[str, str] = {
    "contacts": "firstname,lastname,email,company,jobtitle,lifecyclestage,lastmodifieddate",
    "companies": "name,domain,industry,numberofemployees,lifecyclestage,hs_lastmodifieddate",
    "deals": "dealname,amount,dealstage,pipeline,closedate,hs_lastmodifieddate",
    "tickets": "subject,content,hs_pipeline_stage,hs_ticket_priority,hs_lastmodifieddate",
}
_MAX_HITS = 20

#: Singular labels for user-facing copy. Naive de-pluralisation
#: ("companies"[:-1]) yields "companie", which lands in a sentence a user reads.
SINGULAR: dict[str, str] = {
    "contacts": "contact",
    "companies": "company",
    "deals": "deal",
    "tickets": "ticket",
}

SYSTEM = (
    "Tools:\n"
    "- hubspot_search: search one CRM object type (contacts, companies, deals or "
    "tickets) by free-text `query`.\n"
    "- hubspot_get: read one record in full by object type and id.\n\n"
    "Honest limits you MUST respect: this connection's OAuth scopes may not "
    "cover every object type. When a tool reports that a scope was not granted, "
    "say exactly that — the data may well exist in HubSpot; we simply weren't "
    "given permission to read it. Never turn a missing scope into \"there are no "
    "deals\". Object types whose scope IS granted keep working in the same "
    "answer, so try the ones you need and report per type.\n"
    "Read-only: you cannot create or edit a HubSpot record."
)

SEARCH_TOOL = {
    "name": "hubspot_search",
    "description": (
        "Search a HubSpot CRM object type by free text. `object_type` is one of "
        "contacts, companies, deals, tickets. Returns matching records with their "
        "ids and key properties."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "object_type": {
                "type": "string",
                "enum": sorted(OBJECT_PROPS),
                "description": "Which CRM object to search.",
            },
            "query": {"type": "string", "description": "Free-text search terms."},
        },
        "required": ["object_type", "query"],
    },
}

GET_TOOL = {
    "name": "hubspot_get",
    "description": (
        "Read one HubSpot record in full by `object_type` and `record_id` (as "
        "returned by hubspot_search)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "object_type": {"type": "string", "enum": sorted(OBJECT_PROPS)},
            "record_id": {"type": "string", "description": "The HubSpot record id."},
        },
        "required": ["object_type", "record_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_TOOL]


def scope_message(object_type: str) -> str:
    """The honest 403 answer — a permissions gap, stated as one."""
    return (
        f"(HubSpot returned 403 for {object_type}: that scope was NOT granted to "
        "this connection, so I cannot read it. This is a permissions gap, not an "
        f"empty result — do not say there are no {object_type}. Connected scopes "
        "may cover contacts only; broadening them needs a reconnect by an admin.)"
    )


class HubSpotProvider:
    """LookupProvider over the HubSpot CRM v3 API."""

    provider = "hubspot"
    display_name = DISPLAY_NAME
    keywords = ("hubspot", "crm", "deal")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        from app.connectors import hubspot_sync

        try:
            access_token, _token_json = hubspot_sync._get_valid_access_token(
                enterprise_id
            )
        except HTTPException:
            # 404 not connected / 500 unreadable token → the framework's connect
            # copy is the right answer.
            return None
        except Exception:  # noqa: BLE001 — a failed refresh reads as not connected
            logger.warning(
                "hubspot-lookup: token resolve failed for %s", enterprise_id,
                exc_info=True,
            )
            return None
        if not access_token:
            return None
        return LookupSession(provider=self.provider, handle=access_token)

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        token = session.handle
        object_type = (inp.get("object_type") or "").strip().lower()
        if object_type not in OBJECT_PROPS:
            return (
                f"({name}: 'object_type' must be one of "
                f"{', '.join(sorted(OBJECT_PROPS))})"
            )
        try:
            if name == "hubspot_search":
                return self._search(token, object_type, inp)
            if name == "hubspot_get":
                return self._get(token, object_type, inp)
        except requests.Timeout:
            return f"(HubSpot timed out on {name} — no results from this call)"
        except requests.HTTPError as exc:
            return _http_error_text(name, object_type, exc)
        except requests.RequestException as exc:
            return f"(HubSpot {name} failed to reach HubSpot: {exc})"
        return f"(unknown tool {name})"

    def _search(self, token: str, object_type: str, inp: dict) -> str:
        query = (inp.get("query") or "").strip()
        if not query:
            return "(hubspot_search: 'query' is required)"
        resp = requests.post(
            f"{API}/crm/v3/objects/{object_type}/search",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": query,
                "limit": _MAX_HITS,
                "properties": OBJECT_PROPS[object_type].split(","),
            },
            timeout=HTTP_TIMEOUT,
        )
        _raise_for_status(resp)
        body = resp.json() or {}
        results = body.get("results") or []
        total = body.get("total") or len(results)
        if not results:
            return f"(no HubSpot {object_type} match {query!r})"
        kept, marker = cap_items(results, _MAX_HITS)
        lines = [_render_row(object_type, row) for row in kept]
        footer = marker or (
            f"(showing {len(kept)} of {total} matching {object_type})"
            if total > len(kept) else ""
        )
        return "\n".join(lines) + (f"\n{footer}" if footer else "")

    def _get(self, token: str, object_type: str, inp: dict) -> str:
        record_id = (inp.get("record_id") or "").strip()
        if not record_id:
            return "(hubspot_get: 'record_id' is required)"
        resp = requests.get(
            f"{API}/crm/v3/objects/{object_type}/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"properties": OBJECT_PROPS[object_type]},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 404:
            return f"(no HubSpot {SINGULAR[object_type]} with id {record_id})"
        _raise_for_status(resp)
        return _render_row(object_type, resp.json() or {}, full=True)

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `hubspot_search(object_type="deals")` — the
        only object type the sweep leg ever asks for (`sweep._LIVE_LEGS`
        hardcodes it). `None` for any other tool or object type: this ticket's
        scope is the five live sweep legs' OWN tool calls, and `deals` is the
        only one this adapter's sweep leg makes.

        Duplicates `_search`'s HTTP call, property list AND `dispatch`'s outer
        try/except rather than calling either, because `_search` returns only
        rendered text — the rows are discarded before it returns, which is the
        exact gap this ticket exists to close. `text` here is built the
        identical way `_search` builds it (same `_render_row`, same footer
        logic) and errors are turned into the identical friendly strings
        `dispatch` itself would return (`_http_error_text` et al.) — HubSpot is
        the one adapter of the five whose `dispatch` catches request failures
        itself rather than letting them propagate, so this leg must catch them
        too or a transient HubSpot timeout would newly surface as a sweep
        STATUS_ERROR instead of the STATUS_OK-with-friendly-text it is today.
        See `_deal_row_to_record` for why the records themselves are NOT
        byte-identical to the scheduled pull's.
        """
        if name != "hubspot_search":
            return None
        object_type = (inp.get("object_type") or "").strip().lower()
        if object_type != "deals":
            return None
        query = (inp.get("query") or "").strip()
        if not query:
            return "(hubspot_search: 'query' is required)", None
        token = session.handle
        try:
            resp = requests.post(
                f"{API}/crm/v3/objects/{object_type}/search",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "query": query,
                    "limit": _MAX_HITS,
                    "properties": OBJECT_PROPS[object_type].split(","),
                },
                timeout=HTTP_TIMEOUT,
            )
            _raise_for_status(resp)
            body = resp.json() or {}
        except requests.Timeout:
            return f"(HubSpot timed out on {name} — no results from this call)", None
        except requests.HTTPError as exc:
            return _http_error_text(name, object_type, exc), None
        except requests.RequestException as exc:
            return f"(HubSpot {name} failed to reach HubSpot: {exc})", None
        results = body.get("results") or []
        total = body.get("total") or len(results)
        if not results:
            return f"(no HubSpot {object_type} match {query!r})", None
        kept, marker = cap_items(results, _MAX_HITS)
        lines = [_render_row(object_type, row) for row in kept]
        footer = marker or (
            f"(showing {len(kept)} of {total} matching {object_type})"
            if total > len(kept) else ""
        )
        text = "\n".join(lines) + (f"\n{footer}" if footer else "")
        records = [_deal_row_to_record(r) for r in kept] if kept else None
        return text, records


def _deal_row_to_record(row: dict) -> "RawRecord":
    """One `hubspot_search` (object_type="deals") result row → the CLOSEST
    `RawRecord` it can build with NO new HTTP call.

    NOT byte-identical to `kg_ingest.pullers.hubspot._pull_deals`'s record for
    the same deal — property KEYS are kept matching the puller's for
    readability, but two of the six carry `None` (which `RawRecord.render()`
    then drops, same as an absent key):

      - `owner_id`: `OBJECT_PROPS["deals"]` (this module) requests
        `dealname,amount,dealstage,pipeline,closedate,hs_lastmodifieddate` —
        six properties. The puller's `_DEAL_PROPS` requests those SAME six
        plus `hubspot_owner_id` and `description`. The search call this sweep
        leg makes never asked HubSpot for the owner id, so it is not in the
        response to read.
      - `company_ids`: the puller's deal search runs with
        `associations="companies"`; the sweep's `hubspot_search` POST requests
        no associations at all, so there is nothing to build this list from.
      - `text`: empty for the same reason as `owner_id` — `description` was
        never in the requested property list.

    Closing any of these needs a wider `properties`/`associations` request on
    the search call, or a follow-up `hubspot_get` per hit — either a materially
    different request or a new HTTP call, both out of scope per the ticket. See
    the AC4 test for the assertion that this record and the puller's record for
    the same deal do NOT collide.
    """
    from app.kg_ingest.types import RawRecord

    props = row.get("properties") or {}
    return RawRecord(
        provider="hubspot",
        kind="deal",
        external_id=str(row.get("id") or ""),
        title=props.get("dealname", "") or "",
        text="",
        properties={
            "amount_usd": props.get("amount"),
            "stage": props.get("dealstage"),
            "pipeline": props.get("pipeline"),
            "close_date": props.get("closedate"),
            "owner_id": None,
            "company_ids": None,
        },
        timestamp=props.get("hs_lastmodifieddate"),
    )


def _raise_for_status(resp) -> None:
    if 200 <= resp.status_code < 300:
        return
    err = requests.HTTPError(f"http {resp.status_code}")
    err.response = resp
    raise err


def _http_error_text(name: str, object_type: str, exc: requests.HTTPError) -> str:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 403:
        return scope_message(object_type)
    if status in (401,):
        return (
            f"(HubSpot rejected the stored credentials on {name} — the connection "
            "needs reconnecting in Settings → Connectors.)"
        )
    if status == 429:
        return (
            f"(HubSpot rate-limited {name} — partial results at best. Say the "
            "answer may be incomplete.)"
        )
    return f"({name} failed: HubSpot returned {status})"


def _render_row(object_type: str, row: dict, *, full: bool = False) -> str:
    props = row.get("properties") or {}
    wanted = OBJECT_PROPS[object_type].split(",")
    pairs = [f"{k}: {props[k]}" for k in wanted if props.get(k)]
    if full:
        # Everything HubSpot returned, not just the curated list.
        extra = [f"{k}: {v}" for k, v in props.items() if k not in wanted and v]
        pairs += extra
        return f"{SINGULAR[object_type]} {row.get('id')}\n" + "\n".join(f"  {p}" for p in pairs)
    return f"- {row.get('id')}: " + " · ".join(pairs)


PROVIDER = HubSpotProvider()

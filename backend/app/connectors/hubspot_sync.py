"""Sync CRM data from HubSpot into a dataset corpus.

Fetches contacts, companies, and deals from the HubSpot CRM v3 API,
converts each to markdown, and writes them into DATA_DIR/{dataset}/
so the corpus loader picks them up for brief generation and DS Agent.

Token refresh is handled automatically — v3 tokens expire every 30 min.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from fastapi import HTTPException

from app import db
from app.config import settings
from app.connectors.hubspot_oauth import HUBSPOT_PROVIDER
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

HUBSPOT_CRM_BASE = "https://api.hubapi.com/crm/v3/objects"

# v1 and v3 share the same refresh endpoint format
HUBSPOT_REFRESH_URL_V1 = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_REFRESH_URL_V3 = "https://api.hubspot.com/oauth/v3/token"

CONTACT_PROPERTIES = (
    "email,firstname,lastname,company,phone,jobtitle,"
    "lifecyclestage,hs_lead_status,createdate,lastmodifieddate"
)
COMPANY_PROPERTIES = (
    "name,domain,industry,numberofemployees,annualrevenue,"
    "city,state,country,createdate,lastmodifieddate"
)
DEAL_PROPERTIES = (
    "dealname,amount,dealstage,pipeline,closedate,"
    "createdate,lastmodifieddate,hs_deal_stage_probability"
)


class HubSpotSyncError(Exception):
    """Raised when a HubSpot sync operation fails."""


@dataclass
class SyncResult:
    dataset: str
    contacts_count: int = 0
    companies_count: int = 0
    deals_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "contacts_count": self.contacts_count,
            "companies_count": self.companies_count,
            "deals_count": self.deals_count,
            "total_synced": self.contacts_count + self.companies_count + self.deals_count,
            "errors": self.errors,
        }


# ───── Token refresh ─────


def refresh_access_token(token_json: dict[str, Any]) -> dict[str, Any]:
    """Refresh an expired HubSpot access token.

    Returns the updated token dict with new access_token and timestamps.
    """
    refresh_token = token_json.get("refresh_token")
    if not refresh_token:
        raise HubSpotSyncError("No refresh_token available — user must re-authorize")

    version = token_json.get("oauth_version", "v3")
    url = HUBSPOT_REFRESH_URL_V1 if version == "v1" else HUBSPOT_REFRESH_URL_V3

    resp = requests.post(
        url,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.hubspot_client_id,
            "client_secret": settings.hubspot_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("HubSpot token refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise HubSpotSyncError(f"Token refresh failed ({resp.status_code})")

    new_tokens = resp.json()
    # Merge into existing token_json (preserve oauth_version etc.)
    token_json["access_token"] = new_tokens["access_token"]
    token_json["refresh_token"] = new_tokens.get("refresh_token", refresh_token)
    token_json["expires_in"] = new_tokens.get("expires_in", 1800)
    token_json["obtained_at"] = int(time.time())
    return token_json


def _get_valid_access_token() -> tuple[str, dict[str, Any]]:
    """Decrypt stored token, refresh if expired, return (access_token, token_json)."""
    row = db.get_connection(HUBSPOT_PROVIDER)
    if not row:
        raise HTTPException(404, "HubSpot is not connected")

    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "HubSpot token unreadable") from e

    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "HubSpot token has no access_token")

    # Check if token needs refresh (v3: 30 min, v1: ~6h)
    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 1800)
    # Refresh 2 minutes early to avoid race
    if time.time() > obtained_at + expires_in - 120:
        logger.info("HubSpot token expired, refreshing...")
        token_json = refresh_access_token(token_json)
        # Persist refreshed token
        try:
            encrypted = encrypt_token_json(json.dumps(token_json))
            db.update_connection_tokens(HUBSPOT_PROVIDER, encrypted)
        except Exception:
            logger.warning("Failed to persist refreshed HubSpot token", exc_info=True)
        access_token = token_json["access_token"]

    return access_token, token_json


# ───── CRM API fetchers ─────


def _fetch_crm_objects(
    access_token: str,
    object_type: str,
    properties: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Generic HubSpot CRM v3 objects fetcher with pagination."""
    url = f"{HUBSPOT_CRM_BASE}/{object_type}"
    all_results: list[dict[str, Any]] = []
    after: str | None = None

    while True:
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "properties": properties,
        }
        if after:
            params["after"] = after

        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=30,
        )
        if not resp.ok:
            logger.warning(
                "HubSpot CRM fetch %s failed: %s %s",
                object_type, resp.status_code, resp.text[:300],
            )
            break

        data = resp.json()
        results = data.get("results", [])
        all_results.extend(results)

        # Pagination
        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")
        if not after or len(all_results) >= limit:
            break

    return all_results[:limit]


def fetch_contacts(access_token: str, limit: int = 100) -> list[dict[str, Any]]:
    return _fetch_crm_objects(access_token, "contacts", CONTACT_PROPERTIES, limit)


def fetch_companies(access_token: str, limit: int = 100) -> list[dict[str, Any]]:
    return _fetch_crm_objects(access_token, "companies", COMPANY_PROPERTIES, limit)


def fetch_deals(access_token: str, limit: int = 100) -> list[dict[str, Any]]:
    return _fetch_crm_objects(access_token, "deals", DEAL_PROPERTIES, limit)


# ───── Markdown converters ─────


def _prop(obj: dict, key: str) -> str:
    """Safely extract a property from a HubSpot CRM object."""
    props = obj.get("properties") or {}
    val = props.get(key)
    return str(val) if val and val != "None" else ""


def contacts_to_markdown(contacts: list[dict[str, Any]]) -> str:
    lines = [
        "# HubSpot Contacts\n",
        f"**Total:** {len(contacts)} contacts\n",
    ]
    if not contacts:
        lines.append("_No contacts found._\n")
        return "\n".join(lines)

    lines.append("| Name | Email | Company | Job Title | Lifecycle Stage | Lead Status | Created |")
    lines.append("|------|-------|---------|-----------|-----------------|-------------|---------|")
    for c in contacts:
        name = f"{_prop(c, 'firstname')} {_prop(c, 'lastname')}".strip() or "—"
        email = _prop(c, "email") or "—"
        company = _prop(c, "company") or "—"
        title = _prop(c, "jobtitle") or "—"
        stage = _prop(c, "lifecyclestage") or "—"
        lead = _prop(c, "hs_lead_status") or "—"
        created = _prop(c, "createdate")[:10] if _prop(c, "createdate") else "—"
        lines.append(f"| {name} | {email} | {company} | {title} | {stage} | {lead} | {created} |")

    # Summary by lifecycle stage
    stages: dict[str, int] = {}
    for c in contacts:
        s = _prop(c, "lifecyclestage") or "unknown"
        stages[s] = stages.get(s, 0) + 1
    if stages:
        lines.append("\n## Contacts by Lifecycle Stage\n")
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            lines.append(f"- **{stage}:** {count}")

    return "\n".join(lines) + "\n"


def companies_to_markdown(companies: list[dict[str, Any]]) -> str:
    lines = [
        "# HubSpot Companies\n",
        f"**Total:** {len(companies)} companies\n",
    ]
    if not companies:
        lines.append("_No companies found._\n")
        return "\n".join(lines)

    lines.append("| Company | Domain | Industry | Employees | Revenue | Location | Created |")
    lines.append("|---------|--------|----------|-----------|---------|----------|---------|")
    for c in companies:
        name = _prop(c, "name") or "—"
        domain = _prop(c, "domain") or "—"
        industry = _prop(c, "industry") or "—"
        employees = _prop(c, "numberofemployees") or "—"
        revenue = _prop(c, "annualrevenue")
        revenue_str = f"${int(float(revenue)):,}" if revenue else "—"
        city = _prop(c, "city")
        state = _prop(c, "state")
        country = _prop(c, "country")
        location = ", ".join(filter(None, [city, state, country])) or "—"
        created = _prop(c, "createdate")[:10] if _prop(c, "createdate") else "—"
        lines.append(f"| {name} | {domain} | {industry} | {employees} | {revenue_str} | {location} | {created} |")

    # Summary by industry
    industries: dict[str, int] = {}
    for c in companies:
        ind = _prop(c, "industry") or "unknown"
        industries[ind] = industries.get(ind, 0) + 1
    if industries:
        lines.append("\n## Companies by Industry\n")
        for ind, count in sorted(industries.items(), key=lambda x: -x[1]):
            lines.append(f"- **{ind}:** {count}")

    return "\n".join(lines) + "\n"


def deals_to_markdown(deals: list[dict[str, Any]]) -> str:
    lines = [
        "# HubSpot Deals\n",
        f"**Total:** {len(deals)} deals\n",
    ]
    if not deals:
        lines.append("_No deals found._\n")
        return "\n".join(lines)

    # Total pipeline value
    total_value = 0.0
    for d in deals:
        amt = _prop(d, "amount")
        if amt:
            try:
                total_value += float(amt)
            except ValueError:
                pass
    lines.append(f"**Total Pipeline Value:** ${total_value:,.0f}\n")

    lines.append("| Deal | Amount | Stage | Pipeline | Probability | Close Date | Created |")
    lines.append("|------|--------|-------|----------|-------------|------------|---------|")
    for d in deals:
        name = _prop(d, "dealname") or "—"
        amt = _prop(d, "amount")
        amt_str = f"${float(amt):,.0f}" if amt else "—"
        stage = _prop(d, "dealstage") or "—"
        pipeline = _prop(d, "pipeline") or "—"
        prob = _prop(d, "hs_deal_stage_probability")
        prob_str = f"{float(prob)*100:.0f}%" if prob else "—"
        close = _prop(d, "closedate")[:10] if _prop(d, "closedate") else "—"
        created = _prop(d, "createdate")[:10] if _prop(d, "createdate") else "—"
        lines.append(f"| {name} | {amt_str} | {stage} | {pipeline} | {prob_str} | {close} | {created} |")

    # Summary by deal stage
    stages: dict[str, int] = {}
    stage_values: dict[str, float] = {}
    for d in deals:
        s = _prop(d, "dealstage") or "unknown"
        stages[s] = stages.get(s, 0) + 1
        amt = _prop(d, "amount")
        if amt:
            try:
                stage_values[s] = stage_values.get(s, 0) + float(amt)
            except ValueError:
                pass
    if stages:
        lines.append("\n## Deals by Stage\n")
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            val = stage_values.get(stage, 0)
            lines.append(f"- **{stage}:** {count} deals (${val:,.0f})")

    return "\n".join(lines) + "\n"


# ───── Sync orchestrator ─────


def sync_hubspot(dataset: str) -> SyncResult:
    """Full sync: fetch contacts, companies, deals → write markdown to corpus.

    Returns a SyncResult with counts and any errors.
    """
    result = SyncResult(dataset=dataset)

    access_token, _ = _get_valid_access_token()
    corpus_dir = settings.data_path / dataset
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Contacts
    try:
        contacts = fetch_contacts(access_token)
        md = contacts_to_markdown(contacts)
        (corpus_dir / "hubspot_contacts.md").write_text(md, encoding="utf-8")
        result.contacts_count = len(contacts)
        logger.info("Synced %d HubSpot contacts for %s", len(contacts), dataset)
    except Exception as exc:
        msg = f"contacts: {exc}"
        result.errors.append(msg)
        logger.warning("HubSpot contacts sync failed: %s", exc, exc_info=True)

    # Companies
    try:
        companies = fetch_companies(access_token)
        md = companies_to_markdown(companies)
        (corpus_dir / "hubspot_companies.md").write_text(md, encoding="utf-8")
        result.companies_count = len(companies)
        logger.info("Synced %d HubSpot companies for %s", len(companies), dataset)
    except Exception as exc:
        msg = f"companies: {exc}"
        result.errors.append(msg)
        logger.warning("HubSpot companies sync failed: %s", exc, exc_info=True)

    # Deals
    try:
        deals = fetch_deals(access_token)
        md = deals_to_markdown(deals)
        (corpus_dir / "hubspot_deals.md").write_text(md, encoding="utf-8")
        result.deals_count = len(deals)
        logger.info("Synced %d HubSpot deals for %s", len(deals), dataset)
    except Exception as exc:
        msg = f"deals: {exc}"
        result.errors.append(msg)
        logger.warning("HubSpot deals sync failed: %s", exc, exc_info=True)

    # Update sync status on connection
    try:
        error_msg = "; ".join(result.errors) if result.errors else None
        db.update_connection_sync(HUBSPOT_PROVIDER, error=error_msg)
    except Exception:
        logger.warning("Failed to update HubSpot sync status", exc_info=True)

    # Auto-enable hubspot input source
    try:
        db.upsert_input_source(
            dataset, "hubspot", enabled=True,
            config={"last_sync_at": db.utc_now()},
        )
    except Exception:
        logger.warning("Failed to auto-enable hubspot input source", exc_info=True)

    return result

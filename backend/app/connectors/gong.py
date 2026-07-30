"""Gong connector — credential validation + identity (voice of customer).

Gong is a revenue-intelligence platform that records and transcribes sales /
customer-success calls — first-party voice-of-customer evidence. Gong has no
self-serve OAuth for external apps (OAuth is reserved for listed partner
apps), so auth follows their standard integration path: a WORKSPACE-SCOPED
Access Key + Access Key Secret pair, created by a Gong *technical
administrator* (Gong → Company settings → Ecosystem → API), sent as HTTP
Basic auth: `Basic base64(access_key:access_key_secret)`.

This module owns credential handling: building the basic token, validating a
pair against the API (GET /v2/workspaces — the cheapest identity-ish call),
and the encrypted token payload shape. The data pull lives in
app/kg_ingest/pullers/gong.py.

Rate limits (Gong defaults): 3 calls/second, 10k calls/day — one workspaces
probe and a handful of paged pulls per sync sit far under both.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

GONG_PROVIDER = "gong"

API_BASE = "https://api.gong.io/v2"
_TIMEOUT = 30

#: token_json key holding the ready-to-use Basic credential. The KG-ingest
#: runner resolves a SINGLE token string per provider (see PULLERS), so the
#: base64(key:secret) form is precomputed at connect time.
BASIC_TOKEN_KEY = "basic_token"


class GongAuthError(Exception):
    """Raised when Gong rejects the credential pair."""


def basic_token(access_key: str, access_key_secret: str) -> str:
    """The HTTP Basic credential Gong expects: base64(key:secret)."""
    raw = f"{access_key}:{access_key_secret}".encode()
    return base64.b64encode(raw).decode()


def token_payload_to_store(access_key: str, access_key_secret: str) -> str:
    """The encrypted token_json blob for the connection row. Keeps the raw
    pair (re-derivable, shown nowhere) plus the precomputed basic token the
    puller and probe consume."""
    return json.dumps({
        "access_key": access_key,
        "access_key_secret": access_key_secret,
        BASIC_TOKEN_KEY: basic_token(access_key, access_key_secret),
    })


def fetch_workspaces(token: str) -> list[dict[str, Any]]:
    """Validate the credential and return the Gong workspaces it can see.

    GET /v2/workspaces is the cheapest authenticated call — it doubles as
    the identity probe (workspace name → account label). Raises
    GongAuthError on a 401/403 (bad pair or API access not enabled for the
    Gong plan); returns [] only when the credential is valid but the
    response is shaped unexpectedly."""
    try:
        r = requests.get(
            f"{API_BASE}/workspaces",
            headers={"Authorization": f"Basic {token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise GongAuthError(f"Could not reach Gong: {e}") from e
    if r.status_code in (401, 403):
        raise GongAuthError(
            "Gong rejected these credentials — double-check the Access Key "
            "and Secret (created by a Gong technical administrator under "
            "Company settings → Ecosystem → API)."
        )
    if not r.ok:
        raise GongAuthError(f"Gong API error: HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise GongAuthError("Gong returned an unreadable response") from e
    workspaces = body.get("workspaces") or []
    return [w for w in workspaces if isinstance(w, dict)]


def account_label_from_workspaces(workspaces: list[dict[str, Any]]) -> str:
    """A human label for the connection row: the (first) workspace name."""
    for w in workspaces:
        name = (w.get("name") or "").strip()
        if name:
            return name
    return "Gong workspace"

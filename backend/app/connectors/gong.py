"""Gong connector — credential validation + identity (voice of customer).

Gong is a revenue-intelligence platform that records and transcribes sales /
customer-success calls — first-party voice-of-customer evidence. Gong has no
self-serve OAuth for external apps (OAuth is reserved for listed partner
apps), so auth follows their standard integration path: an Access Key +
Access Key Secret pair created by a Gong ADMINISTRATOR (Admin center →
Settings → Ecosystem → API → "Get API Key"), used as HTTP Basic auth with
the key as username and the secret as password:
`Basic base64(access_key:access_key_secret)`.

BASE URL IS PER-COMPANY. Gong's docs tell each customer to read their own
API base URL off their Gong API page — most tenants are
`https://api.gong.io/v2`, but region-specific hosts exist, so the base URL
is stored per connection (defaulting to the common one) rather than
hardcoded. Following the Superset pattern, base URL + basic token live in
ONE credential string under CREDENTIAL_KEY, because kg_ingest's token_for()
hands the puller a single string.

Key pairs can EXPIRE (Gong warns ahead of time); the scheduled health probe
re-runs the workspaces call, so an expired pair surfaces as "reconnect
required" rather than silently empty syncs.

Rate limits (Gong defaults): 3 calls/second, 10k calls/day — one workspaces
probe and a handful of paged pulls per sync sit far under both.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

GONG_PROVIDER = "gong"

#: The base URL most Gong tenants use. Region-specific tenants override it
#: at connect time with the value from their own Gong API page.
DEFAULT_API_BASE = "https://api.gong.io/v2"

_TIMEOUT = 30

#: token_json key holding the whole credential (base URL + basic token) as
#: one JSON string, so kg_ingest's token_for() can hand it to the puller
#: intact — mirrors superset_auth.CREDENTIAL_KEY.
CREDENTIAL_KEY = "gong_credential"


class GongAuthError(Exception):
    """Raised when Gong rejects the credential pair or is unreachable."""


def basic_token(access_key: str, access_key_secret: str) -> str:
    """The HTTP Basic credential Gong expects: base64(key:secret)."""
    raw = f"{access_key}:{access_key_secret}".encode()
    return base64.b64encode(raw).decode()


def normalize_api_base(raw: str | None) -> str:
    """Validate + canonicalize a user-supplied Gong API base URL.

    Blank falls back to DEFAULT_API_BASE (what most tenants use). Anything
    else must be an absolute http(s) URL; a trailing slash is stripped and a
    missing `/v2` suffix is appended, so a user can paste either the host or
    the full API root shown on their own Gong API page. Raises ValueError so
    the route can 422 with a clear message.
    """
    candidate = (raw or "").strip().rstrip("/")
    if not candidate:
        return DEFAULT_API_BASE
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Gong API base URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("Gong API base URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "Gong API base URL must not contain a query string or fragment"
        )
    if not parsed.path.rstrip("/").endswith("/v2"):
        candidate = f"{candidate}/v2"
    return candidate


def credential_to_store(
    base_url: str, access_key: str, access_key_secret: str
) -> str:
    """The encrypted-storage payload: one JSON credential string under
    CREDENTIAL_KEY so kg_ingest's token_for() hands it to the puller whole.
    The raw pair is kept alongside it (surfaced nowhere) so the credential
    can be re-derived if the stored form ever changes."""
    return json.dumps({
        CREDENTIAL_KEY: json.dumps({
            "base_url": base_url,
            "basic_token": basic_token(access_key, access_key_secret),
        }),
        "access_key": access_key,
        "access_key_secret": access_key_secret,
    })


def parse_credential(credential: str) -> tuple[str, str]:
    """(base_url, basic_token) out of the stored credential string."""
    data = json.loads(credential)
    return data["base_url"], data["basic_token"]


def fetch_workspaces(base_url: str, token: str) -> list[dict[str, Any]]:
    """Validate the credential and return the Gong workspaces it can see.

    GET {base}/workspaces is the cheapest authenticated call — it doubles as
    the identity probe (workspace name → account label). Raises
    GongAuthError on 401/403 (bad or expired pair, or API access not enabled
    on the Gong plan); returns [] only when the credential is valid but the
    response is shaped unexpectedly."""
    try:
        r = requests.get(
            f"{base_url}/workspaces",
            headers={"Authorization": f"Basic {token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise GongAuthError(f"Could not reach Gong: {e}") from e
    if r.status_code in (401, 403):
        raise GongAuthError(
            "Gong rejected these credentials — check the Access Key and "
            "Secret (a Gong admin creates them under Admin center → "
            "Settings → Ecosystem → API), and that your Gong plan has API "
            "access enabled."
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

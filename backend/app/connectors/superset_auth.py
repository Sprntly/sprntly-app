"""Apache Superset credential connector.

Superset is a SELF-HOSTED BI platform (`analytics` type, catalog.py) — the
first analytics connector with a working integration. Unlike every OAuth
connector there is no vendor portal and no app registration: each customer
runs their own instance at their own URL, so the credential is a triple the
user enters in a modal:

    base_url + username + password  (a dedicated read-only service account)

Auth model (superset.apache.org/developer-docs/api):
  - POST {base}/api/v1/security/login {username, password, provider: "db",
    refresh: true} → {access_token, refresh_token} (JWTs; lifetimes are
    instance-configured and often short).
  - Because token lifetimes vary per instance, we do NOT persist or refresh
    tokens at all — every consumer (probe, puller) performs a fresh login
    with the stored credentials. One extra POST per sync is negligible and
    removes the refresh-expiry failure mode entirely.
  - Identity: GET {base}/api/v1/me/ → {"result": {username, email, ...}}.

Storage: the whole credential triple is one JSON string under the
`superset_credential` key of token_json (Fernet-encrypted like every other
connector credential). The kg_ingest runner's token_for() hands that string
to the puller verbatim (PULLERS key = "superset_credential").

SSRF note: this is the first connector where the CUSTOMER supplies the URL
our backend fetches. normalize_base_url() enforces an absolute http(s) URL
with a host and no query/fragment. Private/internal addresses are allowed
deliberately — self-hosted Superset commonly lives on private networks and
the credential belongs to the workspace admin entering it.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

SUPERSET_PROVIDER = "superset"

#: token_json key holding the JSON credential triple (also the PULLERS key).
CREDENTIAL_KEY = "superset_credential"

_TIMEOUT = 15


class SupersetAuthError(Exception):
    """Superset rejected the credentials or the instance was unreachable."""


def normalize_base_url(raw: str) -> str:
    """Validate + canonicalize the user-supplied instance URL.

    Accepts absolute http(s) URLs, keeps a sub-path deployment
    (https://bi.acme.com/superset), strips trailing slash/query/fragment.
    Raises ValueError on anything else so the route can 422 with a clear
    message.
    """
    candidate = (raw or "").strip().rstrip("/")
    if not candidate:
        raise ValueError("Base URL is required")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Base URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("Base URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL must not contain a query string or fragment")
    return candidate


def login(base_url: str, username: str, password: str) -> dict[str, Any]:
    """Exchange service-account credentials for JWTs.

    Returns {access_token, refresh_token} on success. Raises
    SupersetAuthError on bad credentials, a non-Superset URL, or an
    unreachable instance — one exception type so callers map it to their
    own surface (route → 400, probe → soft rejection).
    """
    try:
        resp = requests.post(
            f"{base_url}/api/v1/security/login",
            json={
                "username": username,
                "password": password,
                "provider": "db",
                "refresh": True,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise SupersetAuthError(f"Could not reach Superset at {base_url}") from e
    if not resp.ok:
        logger.warning(
            "Superset login failed (%s): %s %s",
            base_url, resp.status_code, resp.text[:200],
        )
        raise SupersetAuthError(
            "Superset rejected the credentials"
            if resp.status_code in (400, 401, 403)
            else f"Superset login failed (HTTP {resp.status_code})"
        )
    body = resp.json() or {}
    if not body.get("access_token"):
        raise SupersetAuthError("Superset did not return an access token")
    return body


def fetch_current_user(base_url: str, access_token: str) -> dict[str, Any]:
    """The service account's identity (GET /api/v1/me/ → result object).
    Returns {} on any non-2xx so callers can fall back to the username."""
    try:
        resp = requests.get(
            f"{base_url}/api/v1/me/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("Superset me lookup failed (%s): %s", base_url, e)
        return {}
    if not resp.ok:
        logger.warning(
            "Superset me lookup failed (%s): %s %s",
            base_url, resp.status_code, resp.text[:200],
        )
        return {}
    result = (resp.json() or {}).get("result")
    return result if isinstance(result, dict) else {}


def credential_to_store(base_url: str, username: str, password: str) -> str:
    """The encrypted-storage payload: one JSON credential string under
    CREDENTIAL_KEY so kg_ingest's token_for() hands it to the puller whole."""
    return json.dumps({
        CREDENTIAL_KEY: json.dumps({
            "base_url": base_url,
            "username": username,
            "password": password,
        }),
        "obtained_at": int(time.time()),
    })


def parse_credential(credential: str) -> tuple[str, str, str]:
    """(base_url, username, password) out of the stored credential string."""
    data = json.loads(credential)
    return data["base_url"], data["username"], data["password"]

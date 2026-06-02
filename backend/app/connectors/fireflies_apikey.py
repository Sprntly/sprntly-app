"""Fireflies.ai API key connector (commit J).

Unlike Drive/Figma/GitHub/ClickUp/HubSpot, Fireflies doesn't expose a
self-serve OAuth flow — their own docs only document Bearer-token auth
with a user-issued API key obtained at fireflies.ai → Settings →
Integrations → Fireflies API. OAuth endpoints exist but are
partner-gated.

Per Sprntly_Onboarding_Flow_Spec_v1 line 150 ("Connecting a source
initiates an OAuth or API key flow"), API-key auth is spec-allowed.

Flow:
    1. Frontend opens a modal: "Paste your Fireflies API key"
    2. User pastes; frontend POSTs to /v1/connectors/fireflies/apikey
    3. Backend validates by calling Fireflies' GraphQL `user` query
    4. If valid, store the key encrypted with account_label = user.email

API: GraphQL only. Single endpoint: https://api.fireflies.ai/graphql
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

FIREFLIES_PROVIDER = "fireflies"
FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

# Minimum query to validate the key + grab identity for account_label.
_USER_QUERY = "{ user { name email } }"


def fetch_authenticated_user(api_key: str) -> dict[str, Any]:
    """Validate the API key by querying Fireflies' authenticated user.

    Returns the user dict ({name, email, ...}) on success, or `{}` if the
    key is invalid or the request fails. Callers should treat empty dict
    as "this key doesn't work".

    Fireflies returns 200 + an `errors` array for auth failures (typical
    GraphQL behaviour); we also handle non-2xx for completeness.
    """
    try:
        resp = requests.post(
            FIREFLIES_GRAPHQL_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": _USER_QUERY},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning("Fireflies GraphQL request failed: %s", e)
        return {}

    if not resp.ok:
        logger.warning(
            "Fireflies validate failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}

    body = resp.json() or {}
    if body.get("errors"):
        logger.warning("Fireflies returned errors: %s", body.get("errors"))
        return {}
    return (body.get("data") or {}).get("user") or {}


def token_payload_to_store(api_key: str) -> str:
    """Wrap the API key with obtained_at for encrypted storage.

    We reuse the existing `token_json_encrypted` column on the
    connections table — semantically the API key IS the credential, so
    it lives in the same slot as OAuth tokens for other providers.
    """
    return json.dumps({"api_key": api_key, "obtained_at": int(time.time())})

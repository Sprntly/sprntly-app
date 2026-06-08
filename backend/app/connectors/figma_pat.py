"""Figma Personal Access Token (PAT) connector.

The Sprntly Figma public OAuth app is in Figma's review queue. Until it's
approved, customers can connect their Figma account by pasting a Personal
Access Token generated at Figma → Account settings → Personal Access
Tokens. Mirrors the Fireflies API-key pattern; once OAuth is approved,
both paths can coexist (flip `connectorsCatalog.ts` for the OAuth option).

Flow:
    1. Frontend opens the existing ApiKeyPromptModal: "Paste your Figma
       Personal Access Token"
    2. User pastes; frontend POSTs to /v1/connectors/figma/pat
    3. Backend validates by calling Figma's GET /v1/me with the PAT in
       the `X-Figma-Token` header (Figma's documented PAT header)
    4. If valid, store the PAT encrypted (token_json_encrypted column,
       same slot OAuth tokens use), with account_label = user.handle

API: REST. Header auth (NOT OAuth Bearer). PATs do not expire by default;
the user can revoke them in their Figma account settings at any time.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

FIGMA_PROVIDER = "figma"
FIGMA_ME_URL = "https://api.figma.com/v1/me"


def fetch_me(pat: str) -> dict[str, Any]:
    """Validate a PAT by fetching the authenticated user from Figma.

    Returns the user dict ({id, handle, email, img_url, ...}) on success,
    or `{}` if the PAT is invalid, the request fails, or the response is
    malformed. Callers treat empty dict as "this PAT doesn't work" and
    surface a friendly error.
    """
    try:
        resp = requests.get(
            FIGMA_ME_URL,
            headers={"X-Figma-Token": pat},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning("Figma /v1/me request failed: %s", e)
        return {}

    if not resp.ok:
        logger.warning(
            "Figma PAT validate failed: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        return {}

    try:
        body = resp.json() or {}
    except ValueError:
        logger.warning("Figma /v1/me returned non-JSON body")
        return {}

    # Sanity check — Figma always returns an id; if missing, treat as invalid.
    if not body.get("id"):
        return {}
    return body


def token_payload_to_store(pat: str) -> str:
    """Wrap the PAT with obtained_at for encrypted storage.

    Same shape Fireflies uses — the PAT lives in the existing
    `token_json_encrypted` column so disconnect/inspect helpers work
    uniformly regardless of credential type.
    """
    return json.dumps({"pat": pat, "obtained_at": int(time.time())})

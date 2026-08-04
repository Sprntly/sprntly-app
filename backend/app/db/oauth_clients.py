"""Dynamically-registered OAuth client store (RFC 7591).

Home for client credentials Sprntly minted ITSELF against a provider's
`registration_endpoint`, rather than credentials a human pasted out of a
developer portal into an env var. Marvin is the first such connector — see
`app/connectors/marvin_oauth.py`.

Scope is app-global, not per-company: one registered client serves every
workspace connecting to that provider+issuer, the same way `JIRA_CLIENT_ID`
does. Keyed by (provider, issuer) because a provider may run more than one
authorization server (Marvin: US and EU) and a client is only valid at the
issuer that minted it.

The secret is stored Fernet-encrypted under the same TOKEN_ENCRYPTION_KEY as
connector tokens. Reads that can't be decrypted are reported as "no client" so
the caller re-registers rather than trying to authenticate with garbage — a
key rotation should cost one re-registration, not a broken connector.
"""
from __future__ import annotations

import logging
from typing import Any

from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "oauth_dynamic_clients"


def get_oauth_client(provider: str, issuer: str, *, client=None) -> dict | None:
    """The registered client for (provider, issuer), or None.

    Returns ``{client_id, client_secret, registration}`` with the secret
    already decrypted (None for a public client). Returns None — never
    raises — when the row is missing, unreadable, or undecryptable, so the
    caller's contract is simply "register one if you get None".
    """
    try:
        cli = client or require_client()
        r = (
            cli.table(_TABLE)
            .select("client_id,client_secret_encrypted,registration_json")
            .eq("provider", provider)
            .eq("issuer", issuer)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001 — a lookup failure means "re-register"
        logger.warning(
            "oauth_dynamic_clients read failed for %s/%s", provider, issuer,
            exc_info=True,
        )
        return None

    rows = r.data or []
    if not rows:
        return None
    row = rows[0]
    if not row.get("client_id"):
        return None

    secret: str | None = None
    cipher = row.get("client_secret_encrypted")
    if cipher:
        try:
            secret = decrypt_token_json(cipher)
        except TokenEncryptionError:
            # Key rotated (or the row predates the current key). Treat as
            # "no usable client" so we mint a fresh one instead of sending a
            # secret we can't read.
            logger.warning(
                "oauth_dynamic_clients: secret for %s/%s is undecryptable — "
                "re-registering", provider, issuer,
            )
            return None

    return {
        "client_id": row["client_id"],
        "client_secret": secret,
        "registration": row.get("registration_json") or {},
    }


def save_oauth_client(
    provider: str,
    issuer: str,
    *,
    client_id: str,
    client_secret: str | None,
    registration: dict[str, Any] | None = None,
    client=None,
) -> None:
    """Persist a freshly registered client, replacing any previous row.

    `registration` is stored with the secret stripped — it's kept for
    debugging and for the optional RFC 7591 registration_access_token, not as
    a second copy of the credential.
    """
    payload: dict[str, Any] = {
        "provider": provider,
        "issuer": issuer,
        "client_id": client_id,
        "client_secret_encrypted": (
            encrypt_token_json(client_secret) if client_secret else None
        ),
        "registration_json": {
            k: v
            for k, v in (registration or {}).items()
            if k not in ("client_secret",)
        },
        "updated_at": utc_now(),
    }
    cli = client or require_client()
    existing = (
        cli.table(_TABLE)
        .select("client_id")
        .eq("provider", provider)
        .eq("issuer", issuer)
        .limit(1)
        .execute()
    )
    if existing.data:
        (
            cli.table(_TABLE)
            .update(payload)
            .eq("provider", provider)
            .eq("issuer", issuer)
            .execute()
        )
    else:
        payload["created_at"] = utc_now()
        cli.table(_TABLE).insert(payload).execute()

"""Google OAuth helpers for Drive (and future Google connectors)."""
from __future__ import annotations

import json
import logging
import time
import uuid

import jwt
import requests
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
# Google auto-adds openid / userinfo.email / userinfo.profile to the granted
# set whenever the OAuth client is also a sign-in client (ours is). If we only
# REQUEST drive.readonly, google-auth-oauthlib raises a "Scope has changed"
# error at token exchange because the returned set is a superset of what we
# asked for. Requesting the full set up front makes the requested and granted
# scopes match, so the exchange succeeds without relying on a relax flag. We
# also gain the user's verified email straight from the ID token. (The relax
# env default in app.main is kept as belt-and-suspenders for openid reordering.)
DRIVE_SCOPES = [
    DRIVE_READONLY_SCOPE,
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
GOOGLE_DRIVE_PROVIDER = "google_drive"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def google_oauth_configured() -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_oauth_redirect_uri
    )


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def build_flow() -> Flow:
    if not google_oauth_configured():
        raise HTTPException(500, "Google OAuth is not configured on the server")
    return Flow.from_client_config(
        _client_config(),
        scopes=DRIVE_SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def sign_oauth_state(
    *, company_id: str,
    dataset: str | None = None,
    return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token.

    `dataset` is the legacy Drive-only field used by the folder picker;
    it's still carried for back-compat but the canonical tenant key is
    company_id. `return_to` is an optional relative path the callback
    redirects to instead of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": GOOGLE_DRIVE_PROVIDER,
        "company_id": company_id,
        "dataset": dataset,
        "return_to": return_to,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if payload.get("provider") != GOOGLE_DRIVE_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def credentials_from_token_json(token_json: str) -> Credentials:
    return Credentials.from_authorized_user_info(
        json.loads(token_json),
        scopes=DRIVE_SCOPES,
    )


def email_from_id_token(credentials: Credentials) -> str | None:
    """Read the verified email out of the OpenID Connect ID token Google
    returns alongside the access token (we now request the openid +
    userinfo.email scopes). The ID token is signed by Google and already
    validated by google-auth during the exchange; for the email claim we
    decode without re-verifying the signature (no extra network call). Returns
    None if there's no ID token or no email claim — callers fall back to the
    Drive `about` lookup."""
    raw = getattr(credentials, "id_token", None)
    if not raw:
        return None
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    email = claims.get("email")
    return email or None


def fetch_google_account_email(credentials: Credentials) -> str | None:
    # Prefer the email straight from the ID token (no network round-trip,
    # available now that we request openid + userinfo.email). Fall back to the
    # Drive about() lookup for tokens minted before this scope change.
    email = email_from_id_token(credentials)
    if email:
        return email
    try:
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        about = service.about().get(fields="user").execute()
        user = about.get("user") or {}
        return user.get("emailAddress")
    except Exception:
        return None


def try_revoke_credentials(credentials: Credentials) -> None:
    """Best-effort revoke at Google; failures are logged and ignored."""
    token = credentials.token
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=5,
        )
    except Exception:
        logger.warning("Google token revoke failed", exc_info=True)

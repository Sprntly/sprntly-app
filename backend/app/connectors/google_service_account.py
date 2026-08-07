"""Service-account access mode for the Google Drive connector.

An alternative to the per-user OAuth/Picker route (see ``google_oauth`` +
``google_drive_sync``), selected by ``settings.google_drive_access_mode ==
"service_account"``. Instead of the user granting Sprntly access to files they
pick, Sprntly mints ONE service account per company, shows its email in the
connector UI, and the customer shares a Drive folder WITH that email
out-of-band (Viewer). Sprntly then enumerates everything the SA can see and
ingests it through the exact same download → drive_extract → KG path the OAuth
route uses (``google_drive_sync.sync_google_drive`` with an injected service +
entries).

Storage: the per-company SA's JSON key is encrypted with the existing
``encrypt_token_json`` and stored in its OWN dedicated column,
``connections.sa_key_encrypted`` — separate from the OAuth user token in
``token_json_encrypted`` so both credentials can coexist on one Drive
connection (the OAuth token still drives the individual-file Picker; the SA
key drives the shared-folder enumeration/ingest). ``sa_key_encrypted`` is
never returned by the connectors serializer (explicit allowlist), so the key
never reaches the client. The SA email and the walked ``folder_contents``
tree live in the existing, client-visible ``config`` blob.

Provisioning uses a BOOTSTRAP service account (from env) that holds
``iam.serviceAccountAdmin`` + ``iam.serviceAccountKeyAdmin`` on a GCP project
with the IAM API + Drive API enabled.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid

from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from app import db
from app.config import settings
from app.connectors import google_oauth
from app.connectors.tokens import decrypt_token_json, encrypt_token_json

logger = logging.getLogger(__name__)

# The SA is read-only over the whole Drive it is granted (a shared folder and
# its descendants), which is what makes the out-of-band folder share work.
SA_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_IAM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# The SA private key lives (Fernet-encrypted) in its OWN column,
# connections.sa_key_encrypted, so it coexists with the OAuth user token in
# token_json_encrypted (the file Picker path). It is a SECRET: the connectors
# serializer uses an explicit allowlist that excludes this column, so it never
# reaches the client. service_account_email + folder_contents stay in `config`
# (safe to show).

# GCP service-account account-ids must match ^[a-z]([-a-z0-9]*[a-z0-9])$ and be
# 6–30 chars. Layout: "sprntly-" (8) + 6 company hex + "-" + 6 random hex = 21.
# The random uniquifier matters: deleting an SA does NOT immediately free its
# account-id — GCP tombstones it for ~30 days — so a fixed per-company name
# would 409 on any re-mint after a delete (blocking re-connect for weeks). A
# fresh suffix per mint sidesteps the tombstone entirely. Per-company mint
# idempotency is enforced at the CONNECTION level (reuse the stored SA if one
# exists), not by the account-id being deterministic.
_SA_ID_PREFIX = "sprntly-"


class ServiceAccountModeError(RuntimeError):
    """SA mode is not configured, or provisioning/enumeration failed."""


def service_account_mode_enabled() -> bool:
    return settings.google_drive_access_mode == "service_account"


def service_account_mode_configured() -> bool:
    """True only when the bootstrap credential is present. SA mode without it is
    a clear, reportable "not configured" rather than a half-working state."""
    return bool(
        settings.gcp_sa_bootstrap_project
        and settings.gcp_sa_bootstrap_key_json
    )


def _require_configured() -> None:
    if not service_account_mode_configured():
        raise ServiceAccountModeError(
            "service-account mode not configured — set GCP_SA_BOOTSTRAP_PROJECT "
            "and GCP_SA_BOOTSTRAP_KEY_JSON (a bootstrap SA key with "
            "iam.serviceAccountAdmin + iam.serviceAccountKeyAdmin)."
        )


def _load_json_creds_material() -> dict:
    """The bootstrap key JSON, from a file path OR an inline JSON string."""
    raw = settings.gcp_sa_bootstrap_key_json.strip()
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as fh:
            return json.load(fh)
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ServiceAccountModeError(
            "GCP_SA_BOOTSTRAP_KEY_JSON is neither a readable file path nor "
            "valid inline JSON."
        ) from e


def _bootstrap_iam_service() -> Resource:
    _require_configured()
    from google.oauth2 import service_account  # lazy import

    creds = service_account.Credentials.from_service_account_info(
        _load_json_creds_material(), scopes=[_IAM_SCOPE]
    )
    return build("iam", "v1", credentials=creds, cache_discovery=False)


def _iam_error_message(e: HttpError) -> str:
    """Google's own error.message (which names the denied permission / policy),
    falling back to str(e). Keeps the surfaced error actionable, not a JSON wall."""
    try:
        payload = json.loads(e.content.decode())
        msg = (payload.get("error") or {}).get("message")
        if msg:
            return msg
    except (AttributeError, ValueError, TypeError):
        pass
    return str(e)


def _account_id_for(company_id: str) -> str:
    # Company prefix for human traceability + a fresh random uniquifier so a
    # re-mint never collides with a tombstoned (recently-deleted) account-id.
    short = company_id.replace("-", "").lower()[:6] or "cmpny0"
    suffix = uuid.uuid4().hex[:6]
    return f"{_SA_ID_PREFIX}{short}-{suffix}"


def _sa_email(account_id: str) -> str:
    return f"{account_id}@{settings.gcp_sa_bootstrap_project}.iam.gserviceaccount.com"


def mint_company_service_account(company_id: str) -> str:
    """Return this company's SA email, minting it (idempotently) if needed.

    If the company's connection already stores an SA key, reuse it — do NOT
    re-mint or issue a new key. Otherwise create the SA (tolerating an
    already-exists SA from a prior partial run), issue a fresh JSON key, and
    persist email + encrypted key on the connection.
    """
    _require_configured()
    provider = google_oauth.GOOGLE_DRIVE_PROVIDER
    row = db.get_connection(company_id, provider)
    if row:
        config = _load_config(row)
        if config.get("service_account_email") and row.get("sa_key_encrypted"):
            return config["service_account_email"]

    iam = _bootstrap_iam_service()
    project = settings.gcp_sa_bootstrap_project
    account_id = _account_id_for(company_id)
    email = _sa_email(account_id)

    # Create the SA (idempotent: an ALREADY_EXISTS from a prior partial run is
    # fine — we still (re)issue a key below against the deterministic email).
    try:
        iam.projects().serviceAccounts().create(
            name=f"projects/{project}",
            body={
                "accountId": account_id,
                "serviceAccount": {
                    "displayName": f"Sprntly Drive ingest ({company_id})",
                },
            },
        ).execute()
    except HttpError as e:
        if e.resp is None or e.resp.status != 409:
            raise ServiceAccountModeError(
                f"Could not create the service account: {_iam_error_message(e)}"
            ) from e
        logger.info("SA %s already exists — reusing, issuing a fresh key", email)

    # Issue a JSON key. A just-created SA is not immediately usable — GCP needs
    # a few seconds to propagate it, so keys.create can 404 ("does not exist")
    # right after create succeeds. Bounded retry with backoff to ride out that
    # eventual-consistency window (NOT an unbounded retry of the whole mint).
    key = None
    last_err: HttpError | None = None
    for attempt in range(5):
        try:
            key = (
                iam.projects()
                .serviceAccounts()
                .keys()
                .create(
                    name=f"projects/{project}/serviceAccounts/{email}",
                    body={"privateKeyType": "TYPE_GOOGLE_CREDENTIALS_FILE"},
                )
                .execute()
            )
            break
        except HttpError as e:
            last_err = e
            status = e.resp.status if e.resp is not None else None
            # Only retry the propagation race (404 not-found); anything else
            # (e.g. the disableServiceAccountKeyCreation policy → 403) is fatal.
            if status == 404 and attempt < 4:
                logger.info(
                    "SA %s not yet propagated for key creation (attempt %d) — retrying",
                    email, attempt + 1,
                )
                time.sleep(2 * (attempt + 1))
                continue
            raise ServiceAccountModeError(
                f"Could not create a service-account key: {_iam_error_message(e)}"
            ) from e
    if key is None:  # pragma: no cover — loop either sets key or raised
        raise ServiceAccountModeError(
            f"Could not create a service-account key: {_iam_error_message(last_err)}"
            if last_err else "Could not create a service-account key."
        )

    key_json = base64.b64decode(key["privateKeyData"]).decode("utf-8")
    encrypted_key = encrypt_token_json(key_json)

    # Persist so the SA key COEXISTS with any OAuth user token: the OAuth token
    # stays in token_json_encrypted (file Picker path), the SA key goes into its
    # dedicated sa_key_encrypted column. Ensure the row exists first (create a
    # minimal one if the user hasn't done OAuth), record the email in config,
    # then write the key to its own column — never touching token_json_encrypted.
    if not row:
        db.upsert_connection(
            company_id=company_id,
            provider=provider,
            token_encrypted="",  # no OAuth token yet — Picker awaits OAuth connect
            scopes=SA_DRIVE_SCOPE,
            google_email=email,
            account_label=email,
            config_json=json.dumps({"service_account_email": email}),
            status="active",
        )
    else:
        db.patch_connection_config(
            company_id, provider, {"service_account_email": email}
        )
    db.update_connection_sa_key(company_id, provider, encrypted_key)
    logger.info("Minted/keyed SA %s for company %s", email, company_id)
    return email


def _load_config(row: dict) -> dict:
    try:
        return json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        return {}


def google_drive_service_for_company(company_id: str) -> Resource:
    """A Drive v3 client authenticated as this company's service account."""
    _require_configured()
    from google.oauth2 import service_account  # lazy import

    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row or not row.get("sa_key_encrypted"):
        raise ServiceAccountModeError(
            "No service account provisioned for this company yet."
        )
    key_json = decrypt_token_json(row["sa_key_encrypted"])
    creds = service_account.Credentials.from_service_account_info(
        json.loads(key_json), scopes=[SA_DRIVE_SCOPE]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def enumerate_shared(service: Resource) -> list[dict]:
    """Top-level items shared WITH the service account (files + folders).

    ``sharedWithMe`` returns exactly what the customer shared with the SA's
    email out-of-band. Shared FOLDERS come back here; their descendants do not
    (they aren't directly shared) — but under drive.readonly a shared folder
    cascades, so ``google_drive_sync.expand_folder`` walks into them. Returns
    the same ``[{"id","name"}, ...]`` shape as picked files, so the existing
    resolve → download → ingest → KG loop handles them unchanged."""
    entries: list[dict] = []
    page_token = None
    q = "sharedWithMe = true and trashed = false"
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files") or []:
            entries.append({
                "id": f["id"],
                "name": f.get("name"),
                "mimeType": f.get("mimeType"),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    logger.info(
        "SA enumerate_shared: %d top-level item(s) shared with the SA", len(entries)
    )
    return entries


def sync_service_account(
    company_id: str, dataset: str | None = None, kg_inline: bool = False
):
    """SA-mode sync: enumerate what the SA can see, then run it through the
    EXACT same walk + download + ingest + KG path the OAuth route uses."""
    from app.connectors import google_drive_sync  # avoid import cycle

    service = google_drive_service_for_company(company_id)
    entries = enumerate_shared(service)
    result = google_drive_sync.sync_google_drive(
        company_id=company_id,
        dataset=dataset,
        kg_inline=kg_inline,
        service=service,
        entries=entries,
    )
    # Persist the enumerated top-level shared items (with names) so the UI can
    # label the tree roots — folder_contents is keyed by folder id and does not
    # carry each root's own name.
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if row:
        google_drive_sync.merge_config(row, {"sa_shared_roots": entries})
    return result

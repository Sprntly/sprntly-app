"""Uploaded-documents connector — the user's own business documents.

The one connector with NO third party behind it: instead of OAuth or an API
key, the "credential" is the corpus the user uploaded. A workspace admin
creates a named document SOURCE ("Q3 customer interviews"), optionally
describes what the documents are, and drops any number of files on it; the
files are converted with the shared ingest converter and stored in
`document_source_file` (see app/document_sources.py).

Auth model:
  - `POST /v1/connectors/uploads/sources` creates the first source AND the
    `connections` row (provider "uploads"), so the connector behaves exactly
    like every other one: it shows Active in Settings, carries last_sync_at /
    last_sync_error, is probed by the health monitor, and is pulled by the
    weekly scheduler.
  - The stored token payload is just the owning company id under
    `company_id` — that IS the credential kg_ingest's token_for() hands to the
    puller (PULLERS key = "company_id"), which reads the company's document
    sources out of the DB. Nothing secret is stored; the payload is still
    Fernet-encrypted like every other connector so the storage path is
    identical and no special-casing leaks into db.upsert_connection.

Evidence: `uploads` is typed `documents` (it IS a documentation tool) but is
listed in catalog._EVIDENCE_PROVIDER_EXCEPTIONS alongside intercom — a PM who
deliberately uploads their own research//support/strategy corpus HAS supplied
real evidence, unlike a generic Notion/Drive connection.
"""
from __future__ import annotations

import json
import time

UPLOADS_PROVIDER = "uploads"

#: token_json key holding the owning company id (also the PULLERS key).
CREDENTIAL_KEY = "company_id"

#: Shown as the connection's account_label in Settings.
ACCOUNT_LABEL = "Your uploaded documents"


def credential_to_store(company_id: str) -> str:
    """The encrypted-storage payload. Mirrors superset_auth.credential_to_store:
    one JSON string whose CREDENTIAL_KEY is what token_for() hands the puller."""
    return json.dumps({
        CREDENTIAL_KEY: company_id,
        "obtained_at": int(time.time()),
    })

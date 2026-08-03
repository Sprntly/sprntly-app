"""Uploaded document sources — the user's own business documents as a connector.

A "document source" is a NAMED bundle of files the user uploaded ("Q3 customer
interviews", "2026 pricing research") plus an OPTIONAL description of what the
documents are and why they matter. The name + description are not UI decoration:
they are carried into every RawRecord the puller emits, so the extractor knows
what it is reading (see app/kg_ingest/pullers/uploads.py).

Shape mirrors company_document.py — "store the original bytes + the extracted
text", same shared converter (app.ingest.convert), same fail-open reads — but
with a parent `document_source` row so many files can hang off one named source:

    document_source        id, company_id, workspace_id, name, description
    document_source_file   id, source_id, company_id, filename, content_type,
                           size_bytes, extracted_text, raw_b64

See supabase/migrations/20260723120000_document_sources.sql (and the SQLite
mirror in backend/tests/conftest.py).

Scoping: sources are COMPANY-scoped, matching the connector decision that
connections are company-wide (see 20260716124000_workspace_scope_columns.sql:
"connectors are company-wide by decision"). `workspace_id` is still recorded on
each source — like company_document — so a later per-workspace filter is a read
change, not a migration.
"""
from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from app.db.client import require_client
from app.ingest import convert

logger = logging.getLogger(__name__)

#: Per-file cap on the text we persist. Beyond this a document contributes
#: nothing an LLM can hold anyway, and the puller chunks what we keep.
MAX_EXTRACTED_CHARS = 200_000


class DocumentSourceFile(BaseModel):
    """One uploaded file inside a named source."""

    id: str
    source_id: str
    filename: str
    content_type: Optional[str] = None
    size_bytes: int = 0
    extracted_text: str = ""
    uploaded_at: Optional[str] = None


class DocumentSource(BaseModel):
    """A named bundle of uploaded documents (the connector's unit of data)."""

    id: str
    name: str
    description: str = ""
    created_at: Optional[str] = None
    #: Only populated by list_document_sources (a cheap count, not the files).
    file_count: int = 0


def _extract_text(filename: str, data: bytes) -> str:
    """Convert the upload to markdown via the shared ingest converter.

    Reuses app.ingest.convert — the SAME extraction the dataset / roadmap /
    template / company-document upload paths use, which accepts ANY file type
    (rich converters for pdf/docx/xlsx/csv/txt/md/pptx, textual passthrough for
    yaml/json/logs, a stored stub for binary). Never raises: a conversion
    failure degrades to empty text so one bad file can't fail the upload — the
    original bytes are kept either way."""
    try:
        return (convert(filename, data) or "")[:MAX_EXTRACTED_CHARS]
    except Exception:  # noqa: BLE001 — extraction is best-effort
        logger.warning(
            "document_source extraction failed for %s", filename, exc_info=True
        )
        return ""


def create_document_source(
    company_id: str,
    *,
    name: str,
    description: str = "",
    workspace_id: Optional[str] = None,
) -> DocumentSource:
    """Register a new named source. Files are added separately."""
    source_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    row = {
        "id": source_id,
        "company_id": company_id,
        "name": name,
        "description": description or "",
        "created_at": created_at,
    }
    if workspace_id:
        row["workspace_id"] = workspace_id
    require_client().table("document_source").insert(row).execute()
    return DocumentSource(
        id=source_id, name=name, description=description or "", created_at=created_at
    )


def add_document_file(
    company_id: str,
    source_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> DocumentSourceFile:
    """Store one uploaded file under an existing source (many allowed)."""
    extracted = _extract_text(filename, data)
    file_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc).isoformat()
    row = {
        "id": file_id,
        "source_id": source_id,
        "company_id": company_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "extracted_text": extracted,
        "raw_b64": base64.b64encode(data).decode("ascii"),
        "uploaded_at": uploaded_at,
    }
    require_client().table("document_source_file").insert(row).execute()
    return DocumentSourceFile(
        id=file_id,
        source_id=source_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        extracted_text=extracted,
        uploaded_at=uploaded_at,
    )


def get_document_source(company_id: str, source_id: str) -> Optional[DocumentSource]:
    """One source, or None when it doesn't exist / isn't this company's.
    Company-scoped so a guessed id can never reach another tenant's documents."""
    try:
        r = (
            require_client().table("document_source")
            .select("id,name,description,created_at")
            .eq("company_id", company_id)
            .eq("id", source_id)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001 — fail open, same rationale as the list read
        logger.warning(
            "document_source read failed for %s/%s", company_id, source_id,
            exc_info=True,
        )
        return None
    rows = r.data or []
    if not rows:
        return None
    return DocumentSource.model_validate(rows[0])


def list_document_sources(company_id: str) -> list[DocumentSource]:
    """Every named source for the company, newest first, with a file count.

    Empty list when none / on read error: the `document_source` table may not
    exist yet on a given environment (its migration deploys independently of
    this code), and a missing table must degrade to "no sources" rather than
    breaking the connectors pane — same fail-open contract as
    list_company_documents."""
    try:
        r = (
            require_client().table("document_source")
            .select("id,name,description,created_at")
            .eq("company_id", company_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 — fail open
        logger.warning(
            "document_source list failed for %s; treating as no sources",
            company_id, exc_info=True,
        )
        return []
    out: list[DocumentSource] = []
    for raw in (r.data or []):
        try:
            src = DocumentSource.model_validate(raw)
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            logger.warning("invalid document_source for %s; ignoring", company_id,
                           exc_info=True)
            continue
        src.file_count = len(list_source_files(company_id, src.id))
        out.append(src)
    out.sort(key=lambda s: (s.created_at or ""), reverse=True)
    return out


def list_source_files(company_id: str, source_id: str) -> list[DocumentSourceFile]:
    """Files under one source, oldest first. Fail-open like the list above."""
    try:
        r = (
            require_client().table("document_source_file")
            .select("id,source_id,filename,content_type,size_bytes,"
                    "extracted_text,uploaded_at")
            .eq("company_id", company_id)
            .eq("source_id", source_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 — fail open
        logger.warning(
            "document_source_file list failed for %s/%s", company_id, source_id,
            exc_info=True,
        )
        return []
    out: list[DocumentSourceFile] = []
    for raw in (r.data or []):
        if not raw.get("filename"):
            continue
        try:
            out.append(DocumentSourceFile.model_validate(raw))
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            logger.warning("invalid document_source_file for %s; ignoring",
                           company_id, exc_info=True)
    out.sort(key=lambda f: (f.uploaded_at or ""))
    return out


def delete_document_source(company_id: str, source_id: str) -> bool:
    """Drop a source and its files. False when the source wasn't this company's.

    The FK cascades in Postgres; we delete the children explicitly too so the
    SQLite test mirror (foreign_keys pragma off by default) behaves the same."""
    if get_document_source(company_id, source_id) is None:
        return False
    c = require_client()
    c.table("document_source_file").delete().eq("company_id", company_id).eq(
        "source_id", source_id
    ).execute()
    c.table("document_source").delete().eq("company_id", company_id).eq(
        "id", source_id
    ).execute()
    return True


def delete_document_file(company_id: str, source_id: str, file_id: str) -> bool:
    """Remove ONE file from a source. False when it isn't this company's."""
    if get_document_source(company_id, source_id) is None:
        return False
    existing = [f for f in list_source_files(company_id, source_id) if f.id == file_id]
    if not existing:
        return False
    (
        require_client().table("document_source_file")
        .delete()
        .eq("company_id", company_id)
        .eq("id", file_id)
        .execute()
    )
    return True


def has_document_sources(company_id: str) -> bool:
    """True iff the company has at least one named document source."""
    return bool(list_document_sources(company_id))


class DocumentFileRef(BaseModel):
    """Index entry for one uploaded file — metadata only, NO body.

    Deliberately not DocumentSourceFile: this shape exists so the answer path
    can know a document EXISTS without paying to read its text."""

    id: str
    source_id: str
    source_name: str
    filename: str
    uploaded_at: Optional[str] = None


def list_company_files(company_id: str) -> list[DocumentFileRef]:
    """Every uploaded file for the company, newest first.

    ONE query per table — deliberately not list_document_sources() +
    list_source_files() per source, which is N+1 AND selects extracted_text.
    `extracted_text` is never in the select list here: the index must stay
    cheap enough to build on every ask."""
    try:
        c = require_client()
        files_r = (
            c.table("document_source_file")
            .select("id,source_id,filename,uploaded_at")
            .eq("company_id", company_id)
            .execute()
        )
        sources_r = (
            c.table("document_source")
            .select("id,name")
            .eq("company_id", company_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 — fail open, matching every other read here
        logger.warning(
            "document_source_file index read failed for %s; treating as no files",
            company_id, exc_info=True,
        )
        return []
    # A file whose parent source row is missing keeps source_name="" rather
    # than being dropped — a document must never vanish from the index
    # because of a join miss.
    names_by_source = {s["id"]: (s.get("name") or "") for s in (sources_r.data or [])}
    out: list[DocumentFileRef] = []
    for raw in (files_r.data or []):
        if not raw.get("filename"):
            continue
        try:
            out.append(
                DocumentFileRef(
                    id=raw["id"],
                    source_id=raw["source_id"],
                    source_name=names_by_source.get(raw["source_id"], ""),
                    filename=raw["filename"],
                    uploaded_at=raw.get("uploaded_at"),
                )
            )
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            logger.warning("invalid document_source_file for %s; ignoring",
                           company_id, exc_info=True)
    # Newest first; "" (no uploaded_at) sorts last.
    out.sort(key=lambda f: (f.uploaded_at or ""), reverse=True)
    return out


def get_file_text(company_id: str, file_id: str) -> Optional[str]:
    """The stored extracted_text for one owned file, or None when absent —
    unknown id, wrong company, or any read failure. Company-scoped for the
    same reason get_document_source is: a guessed id must never reach
    another tenant's document."""
    try:
        r = (
            require_client().table("document_source_file")
            .select("extracted_text")
            .eq("company_id", company_id)
            .eq("id", file_id)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001 — fail open
        logger.warning(
            "document_source_file text read failed for %s/%s", company_id, file_id,
            exc_info=True,
        )
        return None
    rows = r.data or []
    if not rows:
        return None
    return rows[0].get("extracted_text") or ""

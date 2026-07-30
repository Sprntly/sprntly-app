"""Google Drive adapter — the files the user CONNECTED, and only those.

Sprntly holds the `drive.file` scope (google_oauth.py): access is granted per
file, through the Picker, at connect time. There is no Drive-wide search and
there never was — so the wording here matters more than the plumbing. Every
message says "your connected Drive files", never "your Drive", and the system
block tells the model in as many words that it cannot search the whole Drive. A
model that says "I searched your Drive and found nothing" would be describing a
capability we don't have, about a Drive we mostly can't see.

Reads are in-memory: the picked file is exported/downloaded and converted with
the same converters the corpus ingest uses (app.ingest.convert), with no corpus
write and no ledger side effects — a chat read must not mutate a sync.
"""
from __future__ import annotations

import logging

from app.connector_lookup.base import LookupSession, cap_text

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Google Drive"

_FILE_CHARS = 20_000

SYSTEM = (
    "Tools:\n"
    "- drive_list_connected_files: the Drive files this company explicitly "
    "connected through the file picker (id, name).\n"
    "- drive_read_file: read one of those files by `file_id`, converted to text.\n\n"
    "Honest limits you MUST respect — this is the important part for Drive. "
    "Sprntly can only see files the user PICKED when connecting Google Drive. "
    "There is no Drive-wide search: you cannot look through folders, shared "
    "drives or anything not on that list. So say \"your connected Drive files\", "
    "never \"your Drive\", and NEVER say you searched the user's Drive. If the "
    "answer isn't in the connected files, say which files you read and suggest "
    "connecting the relevant one."
)

LIST_TOOL = {
    "name": "drive_list_connected_files",
    "description": (
        "List the Google Drive files this company connected through the picker. "
        "This is the COMPLETE set of Drive files readable — there is no "
        "Drive-wide search."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

READ_TOOL = {
    "name": "drive_read_file",
    "description": (
        "Read one connected Drive file as text by the `file_id` from "
        "drive_list_connected_files. Google Docs/Sheets/Slides are exported; "
        "PDF/Word/Excel/CSV/text files are converted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The Drive file id."},
        },
        "required": ["file_id"],
    },
}

TOOLS = [LIST_TOOL, READ_TOOL]


class GoogleDriveProvider:
    """LookupProvider over connectors/google_drive_sync.py read plumbing."""

    provider = "google_drive"
    display_name = DISPLAY_NAME
    keywords = ("google drive", "gdrive", "drive files")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        from app import db
        from app.connectors import google_drive_sync, google_oauth

        try:
            row = db.get_connection(
                enterprise_id, google_oauth.GOOGLE_DRIVE_PROVIDER
            )
        except Exception:  # noqa: BLE001
            logger.warning("drive-lookup: connection lookup failed", exc_info=True)
            return None
        if not row:
            return None
        try:
            picked = google_drive_sync.normalize_picked_files(
                google_drive_sync.load_config(row).get("files")
            )
        except Exception:  # noqa: BLE001 — a malformed config is not a crash
            logger.warning("drive-lookup: picked-file list unreadable", exc_info=True)
            picked = []
        notes = [
            f"{len(picked)} Drive file(s) are connected and readable; nothing else "
            "in the user's Drive is visible."
            if picked else
            "NO Drive files are connected yet (the connection exists but no file "
            "was picked), so there is nothing to read — say that rather than "
            "reporting an empty search."
        ]
        return LookupSession(
            provider=self.provider,
            handle={"row": row, "picked": picked, "service": None},
            notes=notes,
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def _service(self, handle: dict):
        """Build the Drive client once per lookup (it refreshes credentials)."""
        if handle.get("service") is None:
            from app.connectors import google_drive_sync

            handle["service"] = google_drive_sync.build_drive_service(handle["row"])
        return handle["service"]

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle = session.handle
        if name == "drive_list_connected_files":
            picked = handle["picked"]
            if not picked:
                return (
                    "(no Google Drive files are connected. Sprntly can only read "
                    "files picked at connect time — there is no Drive-wide "
                    "search. Say this instead of reporting an empty search.)"
                )
            return "\n".join(
                f"- {entry['id']}: {entry.get('name') or '(name unknown)'}"
                for entry in picked
            ) + f"\n({len(picked)} connected file(s) — this is the complete set.)"

        if name == "drive_read_file":
            return self._read(handle, inp)
        return f"(unknown tool {name})"

    def _read(self, handle: dict, inp: dict) -> str:
        from app.connectors import google_drive_sync

        file_id = (inp.get("file_id") or "").strip()
        if not file_id:
            return "(drive_read_file: 'file_id' is required)"
        # Tenancy + scope guard in one: only ids on THIS company's picked list are
        # readable. A model-invented id never reaches the Drive API.
        allowed = {entry["id"] for entry in handle["picked"]}
        if file_id not in allowed:
            return (
                f"(file {file_id} is not one of this company's connected Drive "
                "files, so it cannot be read. Use "
                "drive_list_connected_files.)"
            )
        try:
            service = self._service(handle)
            meta = google_drive_sync.get_file_metadata(service, file_id)
            downloaded = google_drive_sync.download_file_content(service, meta)
        except Exception as exc:  # noqa: BLE001 — googleapiclient raises HttpError
            return _drive_error_text(exc)
        if downloaded is None:
            return (
                f"({meta.get('name')}: Sprntly can't convert this file type "
                f"({meta.get('mimeType')}) to text)"
            )
        filename, data = downloaded
        try:
            from app.ingest import convert

            text = convert(filename, data)
        except Exception as exc:  # noqa: BLE001
            return f"(could not read {filename}: {exc})"
        header = f"{meta.get('name') or filename} (Drive file {file_id})\n"
        return header + cap_text(text, limit=_FILE_CHARS)


def _drive_error_text(exc: Exception) -> str:
    """Reuse the connector's own user-facing Drive error copy where we can."""
    try:
        from googleapiclient.errors import HttpError

        from app.connectors.google_drive_sync import drive_http_error_message

        if isinstance(exc, HttpError):
            return f"(Google Drive: {drive_http_error_message(exc)})"
    except Exception:  # noqa: BLE001 — never let error handling raise
        logger.warning("drive-lookup: error rendering failed", exc_info=True)
    return f"(Google Drive read failed: {exc})"


PROVIDER = GoogleDriveProvider()

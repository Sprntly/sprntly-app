"""Bodies for catalogued documents that do NOT live in `document_source_file`.

The catalog is a POINTER, not a copy: every provider resolves its own body
from wherever that provider already keeps it. Uploads and chat attachments
already had a reader (`document_sources.get_file_text`, the turn row). The two
that did not are here:

    google_drive   the converted markdown `google_drive_sync` already writes
                   into the dataset corpus. The FILENAME of that markdown is
                   not reconstructible — `ingest.md_filename` slugifies the
                   stem and a collision appends `.1.md`, `.2.md` — so sync
                   records where it landed on the file's own `kg_source`
                   provenance row, and this module reads it back.

    confluence     the live wiki, fetched at read time through
                   `connectors.confluence_fetch`. Nothing is cached: see
                   `ConfluenceBody` below for why a cache would have delivered
                   LESS text than a fetch.

WHY A SEPARATE MODULE. `ask_runner` composes prompts; it should not also know
how a Drive file's markdown is named or which Confluence endpoint carries a
body. Keeping resolution here also keeps the heavy imports lazy — the graph
facade and the Confluence client are pulled inside the functions that use
them, so importing this module costs an ask nothing until it actually has a
Drive or Confluence document to read.

FAILURE IS A FACT, NOT AN ABSENCE. Every resolver returns `ResolvedBody`,
which distinguishes three outcomes that look identical to a caller that only
returns a string:

    text="wiki page contents"   read, has content
    text=""                     read, genuinely empty
    text=None + reason          NOT read, and `reason` says why

The third case is the one this whole line of work exists for. A page whose
fetch fails must reach the model as "this document exists and its contents
could not be loaded, because X" — never as "this document does not exist".
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.datasets import dataset_path
from app.document_catalog import PROVIDER_CONFLUENCE, PROVIDER_GOOGLE_DRIVE
from app.ingest import md_filename

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedBody:
    """A document's text, or the stated reason it could not be read.

    `text is None` means NOT READ. `text == ""` means read and empty. Callers
    must branch on `text is None`, never on falsiness — collapsing the two is
    exactly how an unreadable document turns into a missing one."""

    text: Optional[str]
    #: Model-facing, in the sentence "its contents could not be loaded because
    #: {reason}". Never carries credentials, ids or raw exception text.
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.text is not None


# ── Google Drive ───────────────────────────────────────────────────────────
#
# Sync writes each changed file's markdown with `datasets.ingest_file` and
# records the resulting basename on the file's `kg_source` row. That row's id
# is deterministic — uuid5 over (namespace, company, drive file id) — so a
# catalog row for provider `google_drive`, whose `external_id` IS the Drive
# file id, addresses its provenance row without a lookup table.

#: The namespace `kg_ingest.drive_extract` has always minted per-file Drive
#: source ids under. Moved here (unchanged) so the reader and the writer share
#: one definition instead of two that must be kept equal by hand.
DRIVE_SOURCE_NS = uuid.UUID("c0ffee00-0000-4000-8000-00000000d21e")

DRIVE_SOURCE_TYPE = "google_drive"

#: `kg_source.config` keys carrying where the converted markdown landed. A
#: dataset slug plus a BASENAME, not the absolute path `ingest_file` returns:
#: the absolute form embeds `settings.data_path`, so a re-mounted or
#: re-pathed data volume would strand every stored value at once, while the
#: slug + basename pair survives it. Nothing is lost — `ingest_file` always
#: writes directly under `dataset_path(slug)`, including the collision
#: variants, so the basename is the only part that was ever unguessable.
MD_DATASET_KEY = "md_dataset"
MD_FILE_KEY = "md_file"


def drive_source_id(company_id: str, file_id: str) -> str:
    """The deterministic `kg_source` id for one synced Drive file."""
    return str(uuid.uuid5(DRIVE_SOURCE_NS, f"gdrive-file|{company_id}|{file_id}"))


def markdown_location(dataset: str, md_path: str) -> dict[str, str]:
    """The `kg_source.config` fragment recording where a converted file landed.

    Takes the path `datasets.ingest_file` actually wrote and keeps the part
    that matters. Returns `{}` when either half is missing, so a caller can
    merge it unconditionally and an unknown location stays UNSET rather than
    being recorded as an empty string that later reads as "we looked and there
    was nothing there"."""
    slug = (dataset or "").strip()
    name = Path((md_path or "").strip()).name
    if not slug or not name:
        return {}
    return {MD_DATASET_KEY: slug, MD_FILE_KEY: name}


def stored_markdown_location(facade, company_id: str, file_id: str) -> dict[str, str]:
    """The location fragment ALREADY on this file's provenance row, or `{}`.

    `create_source` upserts the whole row, so a later sync that does not know
    where the markdown is would blank a location an earlier sync recorded —
    and that happens on a real path, the KG-only refresh (corpus already
    fresh, extraction retrying), which never calls `ingest_file` and so never
    learns a name. Carrying the stored value forward is what stops a retry
    from destroying the thing the retry exists to produce."""
    source = facade.get_source(company_id, drive_source_id(company_id, file_id))
    config = (source.config or {}) if source is not None else {}
    return markdown_location(
        str(config.get(MD_DATASET_KEY) or ""), str(config.get(MD_FILE_KEY) or "")
    )


def drive_markdown_path(company_id: str, file_id: str) -> Optional[Path]:
    """Where this Drive file's converted markdown was written, or None.

    None covers every "we do not know": no provenance row, a row synced before
    the location was recorded, or a stored value that is not a plain basename.
    That last check is not paranoia theatre — the value is read back out of a
    JSON config blob and joined onto a filesystem path, so anything containing
    a separator is rejected rather than allowed to walk out of the dataset
    directory."""
    from app.graph.facade import GraphFacade  # lazy — graph pulls LLM deps

    source = GraphFacade().get_source(
        company_id, drive_source_id(company_id, file_id)
    )
    if source is None:
        return None
    config = source.config or {}
    slug = str(config.get(MD_DATASET_KEY) or "").strip()
    name = str(config.get(MD_FILE_KEY) or "").strip()
    if not slug or not name or Path(name).name != name:
        return None
    return dataset_path(slug) / name


def resolve_drive_body(company_id: str, file_id: str) -> ResolvedBody:
    """The Drive file's converted markdown, or a stated reason it is missing."""
    try:
        path = drive_markdown_path(company_id, file_id)
    except Exception:  # noqa: BLE001 — an answer must never break on a read
        logger.warning(
            "drive body: could not read the stored location for %s/%s",
            company_id, file_id, exc_info=True,
        )
        return ResolvedBody(None, "its stored location could not be read")
    if path is None:
        return ResolvedBody(
            None, "Sprntly has no record of where this file's text was stored"
        )
    try:
        return ResolvedBody(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        logger.warning(
            "drive body: stored markdown unreadable for %s/%s",
            company_id, file_id, exc_info=True,
        )
        return ResolvedBody(None, "its stored copy could not be read")


def backfill_drive_markdown_paths(
    company_id: str, *, apply: bool = True
) -> dict[str, int]:
    """Record the markdown location for Drive files synced before it was kept.

    Derivation, and the reason it is allowed at all: `ingest_file` writes to
    `md_filename(name)`, and the `kg_source` row already stores that name as
    its label. So the FIRST file to claim a given normalised name is always at
    the unsuffixed path. What is unrecoverable is which file owns which
    variant once `.1.md` exists — nothing recorded the order.

    Therefore: derive only when the unsuffixed file exists AND no `.1.md`
    sibling does. Where a collision happened, leave every colliding row UNSET.
    A wrong path silently serves another document's text under this
    document's name, which is a worse failure than no text at all — the user
    cannot even tell it happened.

    Idempotent: rows that already carry a location are skipped, so a second
    run writes nothing. `apply=False` counts without writing.

    Returns {updated, ambiguous, already_set, unresolved}."""
    from app import db  # lazy — keeps this module importable without db config
    from app.graph.facade import GraphFacade
    from app.graph.types import Source

    counts = {"updated": 0, "ambiguous": 0, "already_set": 0, "unresolved": 0}

    row = db.get_connection(company_id, DRIVE_SOURCE_TYPE)
    if not row:
        return counts
    try:
        config = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    slug = str(config.get("dataset") or "").strip()
    if not slug:
        return counts

    base = dataset_path(slug)
    facade = GraphFacade()
    for source in facade.list_sources(company_id, DRIVE_SOURCE_TYPE):
        source_config = dict(source.config or {})
        if source_config.get(MD_FILE_KEY):
            counts["already_set"] += 1
            continue
        label = (source.label or "").strip()
        if not label:
            counts["unresolved"] += 1
            continue
        name = md_filename(label)
        stem = Path(name).stem
        if (base / f"{stem}.1.md").exists():
            # A collision happened under this name and nothing recorded which
            # file took which variant. Guessing serves the wrong document.
            counts["ambiguous"] += 1
            continue
        if not (base / name).exists():
            counts["unresolved"] += 1
            continue
        counts["updated"] += 1
        if not apply:
            continue
        source_config.update(markdown_location(slug, str(base / name)))
        facade.create_source(company_id, Source(
            id=source.id,
            enterprise_id=company_id,
            source_type=source.source_type,
            label=source.label,
            config=source_config,
            status=source.status,
        ))
    return counts


# ── Confluence ─────────────────────────────────────────────────────────────


class BodyResolver:
    """Resolves catalogued bodies for ONE ask.

    Scoped to a single question rather than being a free function because a
    Confluence session is worth opening once and reusing across the (at most
    MAX_SELECTED_DOCUMENTS) pages one answer may select — three token
    exchanges to read three pages would be two more than the work needs.

    Deliberately NOT a cache of BODIES, only of the session. Confluence pages
    are fetched live on every ask: a wiki page can change between two
    questions in the same conversation, and the obvious cache — the
    conversation turn row — is unusable here anyway. `ask_runner` has no write
    path into `conversation_turns`, the turn row does not exist while the
    answer is being composed, and a body replayed from a turn would come back
    clamped to that table's per-turn character cap — SMALLER than what a live
    fetch delivers. A cache that costs a round-trip to save nothing and
    returns less text is not an optimisation.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._confluence_session = None
        self._confluence_tried = False

    def _session(self):
        if not self._confluence_tried:
            self._confluence_tried = True
            try:
                from app.connectors import confluence_fetch

                self._confluence_session = confluence_fetch.open_session(
                    self.company_id
                )
            except Exception:  # noqa: BLE001 — never break an answer
                logger.warning(
                    "confluence body: could not open a session for %s",
                    self.company_id, exc_info=True,
                )
                self._confluence_session = None
        return self._confluence_session

    def resolve_confluence(self, page_id: str) -> ResolvedBody:
        """One wiki page, fetched live.

        Every failure mode lands on `text=None` with a reason the model can
        repeat truthfully. None of them may be allowed to read as absence: the
        page is in the index because it IS in the wiki, and "we could not
        reach Confluence just now" is a different sentence from "you have no
        such page"."""
        session = self._session()
        if session is None:
            return ResolvedBody(
                None, "Sprntly could not reach Confluence for this workspace"
            )
        try:
            from app.connectors import confluence_fetch

            page = confluence_fetch.get_page(session, page_id)
        except Exception:  # noqa: BLE001 — never break an answer
            logger.warning(
                "confluence body: fetch failed for %s/%s",
                self.company_id, page_id, exc_info=True,
            )
            return ResolvedBody(None, "Confluence did not return this page")
        if page is None:
            return ResolvedBody(
                None,
                "this page could not be read from Confluence — it may have "
                "been moved, deleted, or put out of reach of the connected "
                "account",
            )
        return ResolvedBody(page.get("text") or "")

    def resolve(self, provider: str, external_id: str) -> ResolvedBody:
        """Dispatch on provider. An unknown provider is a stated non-answer,
        not an exception: a new document source can be catalogued long before
        anything here knows how to read it, and that must degrade the same way
        every other unreadable document does."""
        if provider == PROVIDER_CONFLUENCE:
            return self.resolve_confluence(external_id)
        if provider == PROVIDER_GOOGLE_DRIVE:
            return resolve_drive_body(self.company_id, external_id)
        return ResolvedBody(
            None, "Sprntly cannot read documents from this source yet"
        )


__all__ = [
    "BodyResolver", "ResolvedBody", "DRIVE_SOURCE_NS", "DRIVE_SOURCE_TYPE",
    "MD_DATASET_KEY", "MD_FILE_KEY", "backfill_drive_markdown_paths",
    "drive_markdown_path", "drive_source_id", "markdown_location",
    "resolve_drive_body", "stored_markdown_location",
]

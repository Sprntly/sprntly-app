"""Artifact format templates — the library a company keeps its own PRD, ticket
and engineering-spec FORMS in.

  POST   /v1/artifact-templates                -> add a format (file upload OR pasted markdown)
  GET    /v1/artifact-templates?type=prd       -> the library (metadata only) + generation_enabled
  GET    /v1/artifact-templates/{id}           -> one format WITH its source and mapping
  GET    /v1/artifact-templates/{id}/preview   -> the compiled skeleton + how we mapped it
  PATCH  /v1/artifact-templates/{id}           -> rename and/or replace the source
  POST   /v1/artifact-templates/{id}/compile   -> queue a (re)check of this format
  POST   /v1/artifact-templates/{id}/activate  -> make it THE format for its type (admin)
  POST   /v1/artifact-templates/{id}/deactivate-> fall back to Sprntly's built-in (admin)
  DELETE /v1/artifact-templates/{id}           -> remove it (admin when it is the active one)

**Nothing reads this table on any generation path yet.** The library stores,
lists, checks (compiles + validates) and activates; a compiled skeleton is
stored and previewable, but `prd_runner._load_part_a_template()` and its
impl-spec sibling are untouched, so every generated document is still
byte-identical to what it was before this shipped. `generation_enabled` on the
list response says so honestly, per type, so a screen can never imply otherwise
(app/artifact_templates/store.py::GENERATION_ENABLED). Shipping a store ahead of
its reader is the posture routes/company.py:257 already takes with company
documents.

A format is CHECKED in the background — on upload, and on every change to its
markdown — and the client polls the list until nothing is in flight
(app/artifact_templates/compile_prd.py). Checking automatically rather than on
an explicit click is deliberate: a user who has to press "check" first will
instead press "use this format", hit the 409, and file it as a bug.

Templates are COMPANY-SCOPED: all workspaces in a company share one library and
one active format per artifact type, so every read filters by company_id. The
uploading workspace is stamped on the row and never queried. Every id lookup
goes through the company-filtered `db.get_template_by_id`, so a foreign id and a
missing id both raise `HTTPException(404, "Format not found.")` — **404, never
403, on ownership**, because a foreign tenant must not be able to tell "exists
but not yours" from "doesn't exist".

Error ladder (mirrors routes/custom_skills.py's, because the same values end up
in the same columns): missing/over-limit name → 422, missing or unknown
artifact type → 422, a file we can extract no text from → 422, empty source →
400, upload over 25 MB → 413, source over MAX_TEMPLATE_SOURCE_CHARS → 413, activating a format
that has not compiled clean → 409 with the compile notes in `detail`.

Three actions are ADMIN-ONLY, not one: activate, deactivate, and delete OF THE
ROW THAT IS CURRENTLY ACTIVE. All three change the format the whole company
writes in; the third does it through the side door, since falling back to the
built-in is byte-for-byte the effect of deactivating. Those refusals are 403,
not 404 — see `_require_company_admin` for why that does not contradict the
ownership rule above.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db
from app.artifact_templates.store import (
    ARTIFACT_TYPE_LABELS,
    ARTIFACT_TYPES,
    GENERATION_ENABLED,
    MAX_TEMPLATE_SOURCE_CHARS,
    MAX_TEMPLATE_UPLOAD_BYTES,
    PREVIEW_FORMATS,
    TemplateNameRequired,
    TemplateNameTooLong,
    TemplateNotReady,
    TemplateSourceEmpty,
    TemplateSourceNotText,
    TemplateSourceTooLarge,
    TemplateStoreError,
    TemplateTypeUnknown,
    assert_activatable,
    edit_template,
    normalize_compile_notes,
    normalize_section_map,
    store_template,
)
from app.artifact_templates.compile_prd import schedule_compile
from app.artifact_templates.summarize import schedule_missing_summaries
from app.auth import WorkspaceContext, require_workspace
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
# The same ingest path the PRD importer uses, reused rather than re-derived so
# the two never drift. `convert` dispatches by extension (pdf/docx/pptx/xlsx/
# csv/html/rtf/txt/md) and falls back to a textual read for anything else;
# `is_unparsed_stub` detects the placeholder it returns for binary it can't
# read; `_looks_textual` does the NUL sniff and the UTF-8 check as one call.
from app.ingest import _looks_textual, convert, is_unparsed_stub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifact-templates", tags=["artifact-templates"])

# Types whose STRUCTURE survives extraction well enough to be worth recommending
# in the UI. Not a gate — every type is accepted — but the ranking is real and
# it is why the upload modal says what it says. Markdown and .docx keep their
# heading levels (python-docx reads paragraph styles), so a compiled skeleton
# gets the customer's real section hierarchy. A PDF has no heading concept at
# all: pypdf yields a flat run of lines, and a "## 6. What we're building"
# arrives indistinguishable from body text. The PRD and engineering-spec
# compilers are LLM-backed and infer structure from flat text acceptably, but
# the TICKET compiler is a deterministic heading parser — feed it a PDF and it
# finds nothing to key on. Hence the per-type guidance rather than one message.
FIDELITY_BEST = ("md", "markdown", "docx")
FIDELITY_OK = ("txt", "html", "htm", "rtf")
FIDELITY_LOSSY = ("pdf", "pptx", "xlsx", "csv")

# Types whose BYTES are already text and only need decoding — as opposed to the
# binary containers we extract from. The distinction is load-bearing, not
# cosmetic: `ingest.txt_to_md` decodes with `errors="replace"`, so an undecodable
# .md would come back as a page of U+FFFD and get stored as a "format" rather
# than rejected. These are therefore checked as RAW BYTES before extraction,
# preserving the 400 they have always received. A PDF cannot be checked that way
# — its bytes are not text and never will be — so binary types are judged only
# by whether extraction produced anything usable.
TEXT_NATIVE_EXTS = ("md", "markdown", "txt", "csv", "html", "htm", "rtf", "")


# ─── payload shapes ──────────────────────────────────────────────────────────


def _list_item(row: dict) -> dict:
    """One library row, as the list renders it.

    Carries everything the screen needs so it never has to fetch a row's detail
    to fill a list: the badge reads `compile_status`, the reason line reads
    `compile_summary`, and the "See all 3" affordance reads
    `compile_note_count`. Deriving that count client-side from `compile_summary`
    is impossible, and omitting it would either hide that more problems exist or
    force the preview open just to count them.

    Every field is emitted even when empty — a blank `uploader_name` or a null
    `created_at` still renders its labelled line on the row (house rule)."""
    # Normalised on the READ path too, not just where the compiler writes them:
    # a row written before the closed set existed, or edited by hand, must not
    # push a code `web/app/lib/compileNotes.ts` can't translate onto a screen.
    notes = normalize_compile_notes(row.get("compile_notes"))
    first = notes[0] if notes else None
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "artifact_type": row.get("artifact_type") or "",
        "uploader_name": row.get("uploader_name") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "compile_status": row.get("compile_status") or "pending",
        "is_active": bool(row.get("is_active")),
        "source_chars": int(row.get("source_chars") or 0),
        # The FIRST note's message, already plain-language. `code` never reaches
        # the row copy — the client translates it — but the message is the
        # fallback if a code arrives that the client's table doesn't know.
        "compile_summary": (first or {}).get("message") if isinstance(first, dict) else None,
        "compile_note_count": len(notes),
        # What the format CONTAINS — the LLM-written description a successful
        # compile stores (summarize.py). '' until one exists (fresh upload,
        # legacy row the self-heal hasn't reached); the card renders its
        # summary slot either way, per the house every-field rule.
        "summary": row.get("summary") or "",
    }


def _detail(row: dict) -> dict:
    """The list payload PLUS the uploaded source and the full mapping — what the
    edit form and the preview's mapping panel read.

    Split from the list deliberately: `source_md` runs to 50k characters, and
    shipping every format's full text to render a library would be absurd."""
    return {
        **_list_item(row),
        "source_md": row.get("source_md") or "",
        "content_hash": row.get("content_hash") or "",
        "compile_notes": normalize_compile_notes(row.get("compile_notes")),
        "section_map": normalize_section_map(row.get("section_map")),
    }


def _preview(row: dict) -> dict:
    """The compiled skeleton plus how we mapped the customer's format onto it.

    `format` is an EXPLICIT discriminator rather than something the client
    sniffs from a leading `<`: sniffing model output renders a markdown format
    as raw HTML the first time one opens with a `<br>`. PRD skeletons are HTML
    (a v3 PRD is a self-contained HTML page); tickets and engineering specs are
    markdown.

    Available at ANY compile status, including `failed` — the preview is the
    primary diagnostic for a format that didn't map cleanly, so refusing it for
    a non-ready row would take the diagnosis away exactly when it is needed.
    `body` is "" until a compiler has run; the client renders that as "we
    couldn't build a preview from this format yet", not as an error."""
    artifact_type = row.get("artifact_type") or ""
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "artifact_type": artifact_type,
        "compile_status": row.get("compile_status") or "pending",
        "compile_notes": normalize_compile_notes(row.get("compile_notes")),
        "format": PREVIEW_FORMATS.get(artifact_type, "markdown"),
        "body": row.get("compiled") or "",
        "section_map": normalize_section_map(row.get("section_map")),
    }


def _store_error_status(exc: TemplateStoreError) -> int:
    """The status each store refusal answers with, kept in one place because
    both create and edit raise the same ladder."""
    if isinstance(exc, (TemplateSourceTooLarge,)):
        return 413
    if isinstance(exc, (TemplateNameRequired, TemplateNameTooLong, TemplateTypeUnknown)):
        return 422
    if isinstance(exc, TemplateNotReady):
        return 409
    if isinstance(exc, (TemplateSourceEmpty, TemplateSourceNotText)):
        return 400
    return 400


# ─── tenancy + role gates ────────────────────────────────────────────────────


def _owned_or_404(company: WorkspaceContext, template_id: str) -> dict:
    """This company's template by id, or 404.

    The lookup is company-filtered, so a template belonging to another tenant is
    indistinguishable from one that never existed — no 403 anywhere on this
    path, because a 403 would confirm the id names a real row somebody else
    owns."""
    row = db.get_template_by_id(company.company_id, template_id)
    if row is None:
        raise HTTPException(404, "Format not found.")
    return row


def _with_compile_started(company_id: str, row: dict) -> dict:
    """Kick off this template's background check and return the row as the
    caller should now see it.

    `schedule_compile` claims the row (moving it to `compiling`) BEFORE it
    returns, so the response can say `compiling` truthfully rather than handing
    back the `pending` the write returned a microsecond earlier — a client that
    starts polling off a stale `pending` shows "Queued" for a format that is
    already being checked.

    Returns the row unchanged when a compile is already in flight, which is what
    makes a double-click a no-op rather than a second model call."""
    started = schedule_compile(company_id, row["id"])
    if not started:
        return row
    fresh = db.get_template_by_id(company_id, row["id"])
    return fresh if fresh is not None else row


def _require_company_admin(company: WorkspaceContext, message: str) -> None:
    """Gate the three actions that change what the WHOLE COMPANY writes in.

    403, and deliberately not the 404 the ownership rule uses. The two are
    different checks: `_owned_or_404` hides whether a row exists, this one
    refuses an action on a row the caller can ALREADY SEE listed. There is no
    existence left to protect, and answering 404 would tell a member that their
    own company's format had vanished — a worse lie than the honest refusal.

    **Every caller runs `_owned_or_404` FIRST**, which is what makes the
    sentence above true rather than aspirational: a foreign or deleted id 404s
    before this gate is reached, so the 403 only ever answers for a row that
    genuinely exists in the caller's own library. Ordering it the other way
    leaks nothing (the 403 is uniform across ids for a non-admin) but hands the
    client the wrong outcome for a row a teammate just deleted.

    The gate is COMPANY role, not workspace role: the library is company-scoped,
    so a workspace admin who is a plain company member must not be able to
    reformat every workspace's documents from their own."""
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, message)


# ─── create ──────────────────────────────────────────────────────────────────


async def _read_create_payload(request: Request) -> tuple[str, str, str]:
    """(name, artifact_type, source_md) from EITHER a multipart file upload or a
    JSON body.

    One route, two content types, because a format lives in Confluence or a
    Google Doc at least as often as it lives on disk — the upload modal defaults
    to paste and offers a file picker beside it, and forcing the paste path
    through a synthesized File object client-side would be worse. FastAPI can't
    declare both shapes on one signature, so the body is read here and the
    handler stays declarative about everything else. The cost is no OpenAPI body
    schema for this one route; the tests cover both shapes instead.
    """
    ctype = (request.headers.get("content-type") or "").lower()
    if not ctype.startswith("multipart/form-data"):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — any malformed body is one 400
            raise HTTPException(400, "Send a JSON body or a multipart file upload.")
        if not isinstance(body, dict):
            raise HTTPException(400, "Send a JSON body or a multipart file upload.")
        return (
            str(body.get("name") or ""),
            str(body.get("artifact_type") or ""),
            str(body.get("source_md") or ""),
        )

    form = await request.form()
    name = str(form.get("name") or "")
    artifact_type = str(form.get("artifact_type") or "")
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise HTTPException(400, "There's nothing to read yet — attach a file.")

    filename = getattr(upload, "filename", "") or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    # NO extension gate. Every type `ingest.convert` knows is accepted, and
    # anything it doesn't know falls back to a textual read — so a .yaml or a
    # .text lands fine instead of being refused for not being on a list. What
    # decides acceptance is whether we got usable text OUT, checked below; a
    # gate on the extension would reject files we can read and accept files we
    # can't (a scanned .pdf has a valid extension and no text at all).
    #
    # Read ONE BYTE past the cap and no further: an oversize upload is rejected
    # without ever being held whole in memory.
    data = await upload.read(MAX_TEMPLATE_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, "There's nothing to read yet — that file is empty.")
    if len(data) > MAX_TEMPLATE_UPLOAD_BYTES:
        raise HTTPException(
            413,
            "That file is larger than 25 MB. A format is a few pages of a "
            "document — check you picked the right file.",
        )

    # A file that is SUPPOSED to be text is judged on its raw bytes, before
    # extraction. `_looks_textual` catches an undecodable byte sequence and a NUL
    # in one call, and both matter: `txt_to_md` would decode the former to
    # U+FFFD mojibake and store it, and U+0000 is valid UTF-8 so it survives a
    # decode and then 500s on the INSERT (Postgres `text` cannot hold it,
    # SQLSTATE 22P05). Binary types are exempt by necessity and are judged below
    # on what came out instead.
    if ext in TEXT_NATIVE_EXTS and not _looks_textual(data):
        raise HTTPException(
            400,
            "That file isn't readable as text. If you exported it from another "
            "app, try saving it as UTF-8 — or upload the original .docx or PDF "
            "and we'll read it directly.",
        )

    # Extract in a worker thread: pypdf/python-docx/python-pptx all block, and a
    # 20 MB PDF would otherwise stall the event loop for every other request.
    #
    # `convert` is only no-raise for an unknown EXTENSION — it falls back to a
    # textual read there. A known extension whose bytes are malformed goes
    # straight to that type's parser and raises out: a .docx that is not a zip
    # container throws BadZipFile, a truncated PDF throws out of pypdf. Uncaught,
    # every one of those is a 500 on a file the user picked by hand, so they all
    # collapse into the same 422 below.
    try:
        source_md = await asyncio.to_thread(convert, filename or "upload", data)
    except Exception:  # noqa: BLE001 — every parser failure is one 422
        logger.info("artifact template extraction failed for %r", filename, exc_info=True)
        source_md = ""

    if is_unparsed_stub(source_md) or not source_md.strip():
        # The three ways extraction produces nothing usable: a binary type with
        # no converter (.doc, .pages, an image), a PDF with no text layer — a
        # scan, or an export-as-image — and a corrupt container that raised
        # above. All three look like an ordinary document to the user, so the
        # message names the actual fix instead of saying "unsupported".
        raise HTTPException(
            422,
            "We couldn't read any text out of that file. Scanned or image-only "
            "PDFs, legacy .doc/.ppt files, and damaged documents have no text to "
            "extract — export it as PDF, .docx or Markdown and try again.",
        )

    # Second pass, on the EXTRACTED text, for the binary types that skipped the
    # check above: an extractor can still emit a NUL out of a malformed
    # container, and that 500s on the INSERT rather than failing here.
    # store.validate_new_template repeats it over the whole string, since this
    # heuristic only sniffs the first 8 KB.
    if "\x00" in source_md[:8192]:
        raise HTTPException(
            400,
            "We read that file but the text came back malformed. Try exporting "
            "it again, or paste the format instead.",
        )

    if not name:
        # Pre-fill from the filename minus its extension, the way the modal
        # does, so an upload without a typed name still lands with a usable one
        # instead of a 422 the user has to go back and fix.
        stem = filename.rsplit("/", 1)[-1]
        name = stem[: -(len(ext) + 1)] if ext else stem
    return name, artifact_type, source_md


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def create_template(
    request: Request,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Add a format to the company's library — a pasted markdown body or an
    uploaded document of any readable type (≤ 25 MB, and ≤ 50,000 characters of
    EXTRACTED text).

    The check starts immediately and runs in the background: the 201 comes back
    at `compiling`, and the client polls the LIST until nothing is in flight.
    Compiling on upload rather than on an explicit click is deliberate — a user
    who has to press "check" will instead press "use this format", hit the 409,
    and file it as a bug.

    Names are free text and are NOT deconflicted. Two PRD formats called "Acme
    PRD v3" both list, both keep their own id, and neither replaces the other —
    the screen shows a non-blocking notice, and there is no trigger to collide
    the way a custom skill's would."""
    name, artifact_type, source_md = await _read_create_payload(request)
    try:
        row = store_template(
            company_id=company.company_id,
            workspace_id=company.workspace_id,
            uploader_id=company.user_id,
            uploader_name=company.user_name or company.user_email or "",
            name=name,
            artifact_type=artifact_type,
            source_md=source_md,
            # Resolved HERE, from this module's imports, so the cap the suite
            # monkeypatches is the cap the store enforces.
            max_source_chars=MAX_TEMPLATE_SOURCE_CHARS,
        )
    except TemplateStoreError as e:
        raise HTTPException(_store_error_status(e), str(e))
    return _detail(_with_compile_started(company.company_id, row))


# ─── read ────────────────────────────────────────────────────────────────────
#
# Anything added here with a LITERAL first segment (a `/builtin` preview of the
# formats Sprntly ships, say) must be declared ABOVE the `/{template_id}` family
# below: FastAPI matches in declaration order, so a literal under them is
# swallowed by the catch-all and answers "Format not found." for a path that has
# nothing to do with an id. Same trap routes/custom_skills.py's `/github/*`
# routes document.


@router.get("")
def list_templates_route(
    type: str | None = None,
    company: WorkspaceContext = Depends(require_workspace),
):
    """The COMPANY's format library, newest first — shared across all of the
    company's workspaces. `?type=prd|tickets|impl_spec` narrows it.

    `generation_enabled` is TOP-LEVEL and per artifact type, not per row. The
    state most companies are in is zero rows: the screen still renders all three
    group headers, and a type whose generator doesn't honour a custom format yet
    has to say so with no row to hang a flag off. It is served from one backend
    constant so a client can never claim a customer's tickets changed when
    nothing reads a ticket format."""
    if type is not None and type not in ARTIFACT_TYPES:
        raise HTTPException(
            422, "Unknown format type. Use prd, tickets, or impl_spec."
        )
    rows = db.list_templates(company.company_id, type)
    # SELF-HEALING SUMMARIES: a ready row with no summary predates the summary
    # column (20260812200000); seeing one on the library screen is the other
    # natural moment to describe it (the planner's catalog read is the first).
    # Background + single-flighted + never raises, so the list stays a read.
    schedule_missing_summaries(company.company_id, rows)
    return {
        "templates": [_list_item(r) for r in rows],
        "generation_enabled": dict(GENERATION_ENABLED),
    }


@router.get("/{template_id}")
def get_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """One format WITH its uploaded source and its mapping — the edit form's
    source. 404 on a foreign or missing id, made indistinguishable by the
    company-filtered lookup."""
    return _detail(_owned_or_404(company, template_id))


@router.get("/{template_id}/preview")
def preview_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """The compiled skeleton this format produces, plus the section-by-section
    map. Available at every compile status — it is the diagnostic for a format
    that didn't map cleanly. 404 on a foreign or missing id."""
    return _preview(_owned_or_404(company, template_id))


# ─── edit ────────────────────────────────────────────────────────────────────


class TemplateEditIn(BaseModel):
    """PATCH body — rename, replace the source, or both.

    Both fields DEFAULT TO None meaning "not sent", so the rename modal can send
    a name alone without blanking the source it never rendered. That is the
    opposite of SkillEditIn, which requires its whole trio: there the form owns
    every field it shows, here two genuinely separate affordances (Rename, and
    Replace the file) write to one row."""

    name: str | None = None
    source_md: str | None = None


@router.patch(
    "/{template_id}",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def edit_template_route(
    template_id: str,
    body: TemplateEditIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Rename a format and/or replace its markdown, in place on the same row.

    Replacing the source RE-CHECKS it and drops the notes about the old text —
    but NOT the compiled skeleton. If this template is the active one, it stays
    active and keeps serving its last good skeleton while the new source is
    checked; the alternative silently reformats every document the company
    generates for the duration of the recompile, and nobody would connect the
    two events. `is_active = true` with a non-`ready` status is therefore not a
    defensive edge case — it IS the recompile case, and this is the only path
    that reaches it.

    A RENAME does not re-check anything. The check is over the format's content,
    and the content did not change; re-running it would take a `ready` format
    out of use for a minute because somebody fixed a typo in its name.

    The artifact type is not editable: a compiled skeleton is written in the
    vocabulary of one generator, and moving a format between them would strand
    it. 404 on a foreign or missing id."""
    row = _owned_or_404(company, template_id)
    if body.name is None and body.source_md is None:
        raise HTTPException(422, "Nothing to change — send a name or a new format.")
    try:
        updated = edit_template(
            company_id=company.company_id,
            template_id=template_id,
            row=row,
            name=body.name,
            source_md=body.source_md,
            workspace_id=company.workspace_id,
            uploader_id=company.user_id,
            uploader_name=company.user_name or company.user_email or "",
            max_source_chars=MAX_TEMPLATE_SOURCE_CHARS,
        )
    except TemplateStoreError as e:
        raise HTTPException(_store_error_status(e), str(e))
    if updated is None:
        # The row vanished between the read and the write (a concurrent delete).
        raise HTTPException(404, "Format not found.")
    # Compare the STORED text, not `body.source_md is not None`: a client that
    # re-submits the whole form unchanged must not burn a model call.
    if (updated.get("source_md") or "") != (row.get("source_md") or ""):
        updated = _with_compile_started(company.company_id, updated)
    return _detail(updated)


@router.post(
    "/{template_id}/compile",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def compile_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Queue a (re)check of this format against what a Sprntly document needs.

    This is the "Check again" button behind a `needs_review` or `failed` row,
    and the escape hatch when a poll budget runs out. It answers the preview
    shape with the row's new status, so the caller restarts polling from the
    response rather than guessing.

    Already-in-flight is a NO-OP, not an error: `schedule_compile` refuses a row
    already at `compiling`, so an impatient double-click costs nothing and the
    200 still describes the run that is genuinely happening.

    Not admin-gated: checking a format changes nothing about what the company
    writes in — and the row's `compiled` is untouched until a check succeeds, so
    even re-checking the ACTIVE format cannot disturb what is generating right
    now. 404 on a foreign or missing id."""
    row = _owned_or_404(company, template_id)
    return _preview(_with_compile_started(company.company_id, row))


# ─── activation ──────────────────────────────────────────────────────────────


@router.post(
    "/{template_id}/activate",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def activate_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Make this format THE one every document of its type is written in, for
    the whole company and every workspace in it.

    Admin-only (403). Refuses with 409 unless the format has compiled clean:
    activating a `needs_review` format turns downstream features off silently —
    a PRD with no evidence list loses "View more evidence", one with no input
    questions loses every answer button in its chat — and nobody attributes that
    to a format they activated weeks earlier. The refusal carries the compile
    notes in `detail` so the client can translate them into the same sentences
    the row's badge shows, rather than inventing a second vocabulary.

    404 on a foreign or missing id, and OWNERSHIP IS CHECKED FIRST. A non-admin
    acting on a row a teammate deleted a second ago has to get the 404, not the
    403: the client maps the two to different outcomes — 404 drops the row from
    the list, 403 leaves it there with a denial line — so the wrong one strands
    a phantom row nobody can dismiss. Nothing leaks by ordering it this way; the
    403 is uniform across every id for a non-admin either way."""
    row = _owned_or_404(company, template_id)
    _require_company_admin(company, "Only an admin can change your team's format.")
    try:
        assert_activatable(row)
    except TemplateNotReady as e:
        # Same {code, message} vocabulary as compile_notes, so the client
        # translates one set of codes and never prints a raw note.
        raise HTTPException(
            409, {"message": str(e), "code": "not_ready", "notes": e.notes}
        )
    try:
        updated = db.activate_template(
            company.company_id, row["artifact_type"], template_id
        )
    except db.ActiveTemplateConflict:
        # ARTIFACT_TYPE_LABELS, not the raw column value — a user who uploaded
        # an engineering-spec format should never be shown the word
        # "impl_spec". Same table assert_activatable's refusal uses.
        label = ARTIFACT_TYPE_LABELS.get(row.get("artifact_type") or "", "document")
        raise HTTPException(
            409,
            {
                "message": f"Another format just became your team's {label} "
                           "format. Refresh and try again.",
                "code": "activation_raced",
                "notes": [],
            },
        )
    if updated is None:
        raise HTTPException(404, "Format not found.")
    return _detail(updated)


@router.post(
    "/{template_id}/deactivate",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def deactivate_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Go back to Sprntly's built-in format for this artifact type. The format
    stays in the library; only its active flag is cleared.

    Admin-only (403), for the same reason activate is: it changes what the whole
    company writes in. Idempotent — deactivating an already-inactive format
    answers 200 with the row, so a double-click is not an error. 404 on a
    foreign or missing id, checked BEFORE the role gate for the reason
    activate's docstring gives."""
    _owned_or_404(company, template_id)
    _require_company_admin(company, "Only an admin can change your team's format.")
    updated = db.deactivate_template(company.company_id, template_id)
    if updated is None:
        raise HTTPException(404, "Format not found.")
    return _detail(updated)


# ─── delete ──────────────────────────────────────────────────────────────────


@router.delete(
    "/{template_id}",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def delete_template_route(
    template_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Remove a format from the company's library, for everyone. Documents
    already written with it are unchanged.

    Deleting the ACTIVE format is admin-only; deleting any other is open to any
    member. Falling back to the built-in is byte-for-byte the effect of
    deactivating, and that is admin-gated — leaving delete open would let a
    plain member reset company-wide formatting through the side door.

    That refusal is 403, NOT the 404 this repo's ownership rule mandates, and
    the difference is deliberate: `_owned_or_404` has already run, so the caller
    demonstrably can see this row in their own library. There is no existence
    left to protect, and a 404 here would tell a member their own company's
    format had vanished. Ownership mismatches on this route still 404.

    The response says whether the company just fell back to the built-in, so the
    client can name that in its confirmation rather than guessing from a list it
    is about to refetch."""
    row = _owned_or_404(company, template_id)
    was_active = bool(row.get("is_active"))
    if was_active:
        _require_company_admin(
            company, "Only an admin can delete the format your team is using."
        )
    deleted = db.delete_template(company.company_id, template_id)
    if deleted is None:
        raise HTTPException(404, "Format not found.")
    logger.info(
        "artifact_template_deleted company_present=%s type=%s was_active=%s",
        bool(company.company_id), row.get("artifact_type"), was_active,
    )
    return {
        "deleted": True,
        "id": template_id,
        "artifact_type": row.get("artifact_type") or "",
        # True when this delete dropped the company back to the built-in format
        # for that artifact type.
        "fell_back_to_builtin": was_active,
    }

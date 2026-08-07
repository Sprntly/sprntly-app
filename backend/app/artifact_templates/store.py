"""The write half of an artifact-template upload — validated markdown in, a
stored row out.

Split out of routes/artifact_templates.py the way app/skills/store.py is split
out of routes/custom_skills.py, and for the same reason: the route stays HTTP
(status codes, request parsing, tenant context) and this module raises domain
errors the route maps. That split is what lets a future caller — a bulk import,
a compile retry sweep — reuse the rules without going through a request.

Caps are PASSED IN by the caller rather than read here, mirroring
`store_skill(max_content_chars=...)`. The route resolves them from this module's
globals, which is what the suite's monkeypatches actually reach; a cap read
inside this function would be invisible to them.

Template names are FREE TEXT, not slugs. Templates are never invoked by trigger,
so none of the custom-skills collision machinery (`slugify`, `available_slug`,
the `<slug>-2` series) applies: two of a company's templates may share a name,
and the upload path never renames or replaces anything. The screen shows a
non-blocking "you already have a PRD format called X" notice; the API accepts it.
"""
from __future__ import annotations

import hashlib
import logging

from app import db

logger = logging.getLogger(__name__)

# The three generators a customer format can govern. 'impl_spec' is the
# implementation-spec skill's Part B — the markdown the ticket generator
# consumes — not a separate viewer surface.
ARTIFACT_TYPES: tuple[str, ...] = ("prd", "tickets", "impl_spec")

# Plain-language names for the error messages. A user who pasted a ticket format
# should be told "ticket format", not `impl_spec`.
ARTIFACT_TYPE_LABELS: dict[str, str] = {
    "prd": "PRD",
    "tickets": "ticket",
    "impl_spec": "engineering spec",
}

# Which generators actually honour a custom format yet. TOP-LEVEL on the list
# response rather than per-row, because the state most companies are in is zero
# rows: the library screen still renders all three group headers, and the two
# that aren't wired need to say so with no row to hang a flag off.
#
# `prd` is TRUE from the milestone that made `prd_runner.resolve_prd_template`
# replace `_load_part_a_template()` at its call site: an active PRD format with
# a compiled skeleton now genuinely governs every PRD the company generates,
# and the PRD row records which format wrote it (prds.artifact_template_id).
#
# `impl_spec` is TRUE from the milestone that made
# `prd_runner.resolve_impl_spec_template` replace `_load_part_b_template()` at
# its call site: an active engineering-spec format with a compiled skeleton now
# governs the Part B spec, and the B0-B9 ids the ticket generator reads are an
# activation gate rather than a hope.
#
# `tickets` is TRUE from the milestone that made `stories.layout` compile an
# uploaded ticket format into a description layout and `to_description` render
# through it — with `story_editable_text`, `_IMPORT_LABELS` and the web's
# section labels moved in the same change, so the tracker round-trip still
# normalises back to a stable content hash.
#
# All three are now live. Flip a flag HERE, in the milestone that makes it true,
# and nowhere else — a client that hardcodes this tells a user their tickets
# changed when nothing did.
GENERATION_ENABLED: dict[str, bool] = {
    "prd": True,
    "tickets": True,
    "impl_spec": True,
}

# Cap on the uploaded/pasted markdown, in characters. Mirrors
# skills.custom.MAX_SKILL_CONTENT_CHARS and for the same reason: the compiled
# form of this text rides the prompt's cacheable prefix on every generation, so
# it bounds prompt cost. Distinct from the byte cap on the raw file below.
MAX_TEMPLATE_SOURCE_CHARS = 50_000

# Byte cap on an uploaded file. Matches routes/prd.py's _MAX_IMPORT_BYTES rather
# than the old 2 MB, because uploads are no longer markdown-only: a .docx with
# embedded images or a scanned-ish PDF is routinely tens of times larger than the
# few kilobytes of text inside it, and a cap sized for markdown rejected real
# formats before anyone could see whether we could read them. The CHARACTER cap
# above still bounds what actually reaches a prompt, and it is applied to the
# EXTRACTED text — so a 20 MB PDF holding four pages of prose passes both.
MAX_TEMPLATE_UPLOAD_BYTES = 25 * 1024 * 1024

# Matches the upload modal's `maxLength={120}` on the name field.
MAX_TEMPLATE_NAME_CHARS = 120

# The CLOSED SET of `code` values a compile note may carry. Defined here in
# milestone 1 even though the validator that emits them is milestone 2, because
# web/app/lib/compileNotes.ts keys its translation table on exactly these
# strings — an unrecognised code falls back to a generic sentence, and a RAW
# note must never reach a screen ("`ul.ev` is missing" is not user-facing copy).
COMPILE_NOTE_CODES: frozenset[str] = frozenset(
    {
        # Structural hooks a Sprntly document needs a home for.
        "missing_evidence_list",
        "missing_input_questions",
        "missing_hypothesis",
        "missing_requirements",
        "missing_title",
        "missing_style_marker",
        # ENGINEERING SPEC only. Its skeleton is markdown with no class
        # vocabulary, so the six hooks above do not apply to it — the one thing
        # that must survive is the B0-B9 section ids the ticket generator reads.
        # It gets its own code rather than borrowing `missing_requirements`,
        # which was technically honest and actually misleading: a customer who
        # dropped B6 read a sentence about how their requirements are listed.
        "missing_spec_sections",
        # Safety refusals — the uploaded format carries something we will not
        # put inside a document.
        "unsafe_script",
        "unsafe_attribute",
        "unsafe_remote_asset",
        # The compile itself failed (transient or unparseable).
        "compile_error",
    }
)

# The CLOSED SET a section_map entry's `form` may take. A model writing
# "tabular" one run and "table" the next produces two labels for one thing in
# the preview's mapping table, so milestone 2's compiler validates against this.
SECTION_FORMS: frozenset[str] = frozenset({"prose", "bullets", "table", "stories"})

# Which preview renderer a compiled skeleton needs. Sent as an explicit
# discriminator rather than left for the client to sniff from a leading '<':
# sniffing model output renders a markdown format as raw HTML the first time one
# opens with a <br>.
PREVIEW_FORMATS: dict[str, str] = {
    "prd": "html",
    "tickets": "markdown",
    "impl_spec": "markdown",
}


class TemplateStoreError(ValueError):
    """Base for the store's user-facing refusals — the message is verbatim."""


class TemplateNameRequired(TemplateStoreError):
    """No name, or a name of nothing but whitespace (422)."""


class TemplateNameTooLong(TemplateStoreError):
    """Name past MAX_TEMPLATE_NAME_CHARS (422)."""


class TemplateTypeUnknown(TemplateStoreError):
    """artifact_type missing or outside ARTIFACT_TYPES (422)."""


class TemplateSourceEmpty(TemplateStoreError):
    """Nothing to read — an empty paste or an empty file (400)."""


class TemplateSourceNotText(TemplateStoreError):
    """The source carries a NUL byte, so it is not markdown (400).

    Not paranoia about binary uploads — `U+0000` is VALID UTF-8, so a UTF-16LE
    file saved without a BOM decodes without error and every character-level
    check passes. Postgres `text` cannot store a NUL: PostgREST answers SQLSTATE
    22P05 and the caller gets a 500 instead of the readable 400 the route
    already has for the undecodable case. The SQLite fake stores NUL happily, so
    no test in this suite can catch it downstream of here — this check is the
    only thing standing between a mis-saved file and a 500."""


class TemplateSourceTooLarge(TemplateStoreError):
    """Source past the character cap (413)."""


class TemplateNotReady(TemplateStoreError):
    """Activation attempted on a template that has not compiled clean (409).
    Carries the row's compile notes so the refusal can say which gap to fix."""

    def __init__(self, message: str, notes: list[dict] | None = None) -> None:
        super().__init__(message)
        self.notes = notes or []


def content_hash_for(source_md: str) -> str:
    """First 12 hex of sha256 over the uploaded source — the same shape
    skills.custom.content_hash_for and loader._content_hash produce, so the
    exact FORM behind a generated artifact versions the same way the METHOD
    does in prompt_version / agent_decision_log."""
    h = hashlib.sha256()
    h.update((source_md or "").encode("utf-8"))
    return h.hexdigest()[:12]


# Model-emitted `form` values that mean one of SECTION_FORMS. A model writing
# "tabular" one run and "table" the next produces two labels for one thing in
# the preview's "Written as" column, so drift is folded back here rather than
# rendered. Anything still unrecognised lands on "prose" — the neutral default —
# and is logged, because the fix is to add the synonym, not to widen the set.
_FORM_SYNONYMS: dict[str, str] = {
    "table": "table", "tabular": "table", "grid": "table", "matrix": "table",
    "stories": "stories", "story": "stories", "user stories": "stories",
    "user_stories": "stories", "user-stories": "stories",
    "bullets": "bullets", "bullet": "bullets", "bulleted": "bullets",
    "bullet list": "bullets", "list": "bullets", "bulleted list": "bullets",
    "prose": "prose", "narrative": "prose", "paragraph": "prose",
    "paragraphs": "prose", "text": "prose",
}


def normalize_form(raw) -> str:
    """One `form` value coerced into SECTION_FORMS. Never raises — a drifted
    label is a cosmetic problem in one table column, not a reason to throw away
    a compile that is otherwise good."""
    key = str(raw or "").strip().lower()
    form = _FORM_SYNONYMS.get(key)
    if form is not None:
        return form
    if key:
        # The raw value is a short enum-ish token from the model, not customer
        # prose — logging it is what lets somebody add the missing synonym.
        logger.warning(
            "artifact_template_form_drift value=%r normalised_to=prose", key[:32]
        )
    return "prose"


def normalize_section_map(raw) -> dict:
    """A stored section_map → the three-block shape the preview always renders,
    with both closed sets enforced.

    Every block is present even when empty, because the preview renders all
    three including their empty copy: a silently omitted block reads as
    "nothing to report" when it means "we have no data". A row that has never
    compiled therefore previews as three explicit empties rather than a missing
    panel.

    Each section entry is coerced to `{id, house, customer, order, form}`:
    `id` is stable so the preview's mapping table can key its rows (synthesised
    positionally when the model omits it), `order` is an int so sorting can't
    fall over on a string, and `form` is forced into SECTION_FORMS. Applied on
    both the write and the read path, so a row written before this existed —
    or by hand — still renders in the closed vocabulary."""
    src = raw if isinstance(raw, dict) else {}
    raw_sections = src.get("sections")
    sections: list[dict] = []
    for i, entry in enumerate(raw_sections if isinstance(raw_sections, list) else []):
        if not isinstance(entry, dict):
            continue
        try:
            order = int(entry.get("order", i + 1))
        except (TypeError, ValueError):
            order = i + 1
        sections.append({
            "id": str(entry.get("id") or f"s{i + 1}"),
            "house": str(entry.get("house") or ""),
            "customer": str(entry.get("customer") or ""),
            "order": order,
            "form": normalize_form(entry.get("form")),
        })
    sections.sort(key=lambda s: s["order"])
    return {
        "sections": sections,
        "unmapped_house": [
            str(x) for x in (src.get("unmapped_house") or [])
        ] if isinstance(src.get("unmapped_house"), list) else [],
        "extra_sections": [
            str(x) for x in (src.get("extra_sections") or [])
        ] if isinstance(src.get("extra_sections"), list) else [],
    }


def normalize_compile_notes(raw) -> list[dict]:
    """A list of compile notes with COMPILE_NOTE_CODES enforced at the storage
    boundary.

    The validator already refuses to CREATE a note with an unknown code
    (`validate._note`), so anything filtered here is a bug or a hand-written
    row. Dropped rather than passed through: `web/app/lib/compileNotes.ts` keys
    on `code`, an unknown one renders as the generic "one part of your format
    didn't map" line, and a note that says nothing specific while inflating the
    "See all N" count is worse than no note at all."""
    out: list[dict] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        message = str(entry.get("message") or "")
        if code not in COMPILE_NOTE_CODES or not message:
            logger.warning("artifact_template_note_dropped code=%r", code[:40])
            continue
        out.append({"code": code, "message": message})
    return out


def validate_new_template(
    *,
    name: str,
    artifact_type: str,
    source_md: str,
    max_source_chars: int,
) -> tuple[str, str, str]:
    """The shared validation ladder for both create and replace-source.

    Returns the cleaned (name, artifact_type, source_md). Raises the domain
    errors above in the order the route's status ladder expects: metadata first
    (422), then emptiness (400), then size (413) — so a user who pasted the
    wrong thing entirely is told that before being told it is too long.

    `source_md` keeps its interior whitespace verbatim: it is markdown, and
    leading indentation is load-bearing inside a fenced block. Only the
    emptiness check strips."""
    name = (name or "").strip()
    artifact_type = (artifact_type or "").strip()
    source_md = source_md or ""

    if not name:
        raise TemplateNameRequired("Give this format a name so your team can tell it apart.")
    if len(name) > MAX_TEMPLATE_NAME_CHARS:
        raise TemplateNameTooLong(
            f"Format name must be {MAX_TEMPLATE_NAME_CHARS} characters or fewer."
        )
    if artifact_type not in ARTIFACT_TYPES:
        raise TemplateTypeUnknown(
            "Choose whether this format is for a PRD, tickets, or an engineering spec."
        )
    if not source_md.strip():
        raise TemplateSourceEmpty(
            "There's nothing to read yet — paste your format or pick a .md file."
        )
    # Checked HERE rather than only at the upload route, so the JSON paste path
    # and PATCH are covered too — `{"name": "Acme", "source_md": "a\\x00b"}`
    # otherwise reaches the INSERT and 500s against real Postgres.
    if "\x00" in source_md:
        raise TemplateSourceNotText(
            "That format isn't readable as text. Formats must be plain "
            "Markdown — if you exported it from another app, try saving it as "
            "UTF-8 first."
        )
    if len(source_md) > max_source_chars:
        raise TemplateSourceTooLarge(
            f"This format is longer than the {max_source_chars:,} character limit. "
            "Trim it and try again."
        )
    return name, artifact_type, source_md


def store_template(
    *,
    company_id: str,
    workspace_id: str,
    uploader_id: str,
    uploader_name: str,
    name: str,
    artifact_type: str,
    source_md: str,
    max_source_chars: int,
) -> dict:
    """Validate and create one template row; returns the decoded row.

    Lands at `compile_status='pending'`: the format is in the library and
    listed, and governs nothing at all until it compiles and an admin activates
    it. Nothing here deconflicts the name — see the module docstring."""
    name, artifact_type, source_md = validate_new_template(
        name=name,
        artifact_type=artifact_type,
        source_md=source_md,
        max_source_chars=max_source_chars,
    )
    row = db.insert_template(
        company_id=company_id,
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        name=name,
        source_md=source_md,
        content_hash=content_hash_for(source_md),
        uploader_id=uploader_id,
        uploader_name=uploader_name,
    )
    logger.info(
        "artifact_template_created company_present=%s type=%s source_chars=%s",
        bool(company_id), artifact_type, len(source_md),
    )
    return row


def edit_template(
    *,
    company_id: str,
    template_id: str,
    row: dict,
    name: str | None,
    source_md: str | None,
    workspace_id: str,
    uploader_id: str,
    uploader_name: str,
    max_source_chars: int,
) -> dict | None:
    """Rename a template and/or replace its source; returns the decoded row, or
    None when the id vanished between the caller's read and this write.

    `row` is the caller's already-ownership-checked read, so the artifact type
    the validation ladder runs against is the STORED one — the edit form cannot
    move a format from PRD to tickets, which would strand a compiled skeleton
    written in the wrong vocabulary.

    Replacing the source resets `compile_status` to `pending` and clears the
    notes about the old text, but deliberately leaves `compiled` standing so an
    ACTIVE template being re-uploaded keeps serving its last good skeleton while
    the new one is checked (db.update_template's docstring has the full
    reasoning).

    PROVENANCE MOVES ONLY WITH THE CONTENT. `workspace_id`, `uploader_id` and
    `uploader_name` are refreshed when — and only when — the source is actually
    replaced, gated on `source_changed` exactly as `content_hash` is. Sending
    them on every PATCH meant a pure rename rewrote all three: the row's
    "Uploaded by Ada" line became "Uploaded by whoever last fixed a typo", and
    any member of the company could take over the attribution of any format by
    renaming it. `workspace_id` is worse than cosmetic — the migration header
    promises that column makes a future narrowing to workspace scope "a query
    change, not a backfill", and that promise only holds if the column keeps
    saying where a format CAME FROM rather than where it was last renamed."""
    next_name = row.get("name") if name is None else name
    next_source = row.get("source_md") if source_md is None else source_md
    next_name, _type, next_source = validate_new_template(
        name=next_name or "",
        artifact_type=row.get("artifact_type") or "",
        source_md=next_source or "",
        max_source_chars=max_source_chars,
    )
    source_changed = source_md is not None and next_source != (row.get("source_md") or "")
    return db.update_template(
        company_id=company_id,
        template_id=template_id,
        name=next_name,
        source_md=next_source if source_changed else None,
        content_hash=content_hash_for(next_source) if source_changed else None,
        # None means "leave this column alone" in db.update_template — that
        # contract is untouched; this is the caller deciding not to send them.
        workspace_id=workspace_id if source_changed else None,
        uploader_id=uploader_id if source_changed else None,
        uploader_name=uploader_name if source_changed else None,
    )


def assert_activatable(row: dict) -> None:
    """Raise TemplateNotReady unless this template has compiled clean.

    The gate is deliberately absolute: a `needs_review` format can carry a
    missing evidence list or a missing input-questions block, and activating it
    turns those downstream features off silently — no error anywhere, just a
    PRD whose chat has no answer buttons weeks later. Refusing with the notes
    attached is the only outcome anybody can act on.

    NOTE the one state this does NOT govern: a row that is ALREADY active and
    is being recompiled sits at `is_active = true` with a non-`ready` status.
    That is reachable only through the source-replacement path, it is
    intentional, and it is what keeps generation on the last good skeleton
    instead of dropping the company to the built-in mid-recompile."""
    if row.get("compile_status") == "ready":
        return
    label = ARTIFACT_TYPE_LABELS.get(row.get("artifact_type") or "", "artifact")
    notes = row.get("compile_notes") or []
    raise TemplateNotReady(
        f"This {label} format isn't ready to use yet — open its preview to see "
        "what we couldn't place.",
        notes=notes if isinstance(notes, list) else [],
    )

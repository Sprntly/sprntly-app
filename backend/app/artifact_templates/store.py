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
# All three are False here because MILESTONE 1 IS INERT — nothing reads
# artifact_templates on any generation path. `prd` flips true when
# resolve_prd_template replaces _load_part_a_template(); `impl_spec` when
# _load_part_b_template() goes the same way; `tickets` when the description
# layout lands. Flip them HERE and nowhere else — a client that hardcodes this
# tells a user their tickets changed when nothing did.
GENERATION_ENABLED: dict[str, bool] = {
    "prd": False,
    "tickets": False,
    "impl_spec": False,
}

# Cap on the uploaded/pasted markdown, in characters. Mirrors
# skills.custom.MAX_SKILL_CONTENT_CHARS and for the same reason: the compiled
# form of this text rides the prompt's cacheable prefix on every generation, so
# it bounds prompt cost. Distinct from the byte cap on the raw file below.
MAX_TEMPLATE_SOURCE_CHARS = 50_000

# Byte cap on an uploaded .md. Far below the 20 MB skills accept, because a
# format is a few pages of markdown and never an archive — anything larger is a
# mis-picked file, and saying so early is kinder than a character-cap error
# after a 20 MB upload.
MAX_TEMPLATE_UPLOAD_BYTES = 2 * 1024 * 1024

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


def normalize_section_map(raw) -> dict:
    """A stored section_map → the three-block shape the preview always renders.

    Every block is present even when empty, because the preview renders all
    three including their empty copy: a silently omitted block reads as
    "nothing to report" when it means "we have no data". A row that has never
    compiled therefore previews as three explicit empties rather than a missing
    panel."""
    src = raw if isinstance(raw, dict) else {}
    sections = src.get("sections")
    return {
        "sections": list(sections) if isinstance(sections, list) else [],
        "unmapped_house": list(src.get("unmapped_house") or [])
        if isinstance(src.get("unmapped_house"), list) else [],
        "extra_sections": list(src.get("extra_sections") or [])
        if isinstance(src.get("extra_sections"), list) else [],
    }


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
    reasoning). The uploader fields are refreshed: the row describes the version
    it now holds, so it records who last changed it and from where."""
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
        workspace_id=workspace_id,
        uploader_id=uploader_id,
        uploader_name=uploader_name,
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

"""Deterministic validation of a compiled PRD skeleton.

The compiler is a model call; this is not. Everything here is stdlib
(`html.parser`, `re`, `urllib.parse`) and byte-deterministic, because it is the
gate that decides whether a customer's format is allowed to govern every PRD
their company generates. **Do not add `beautifulsoup4` or `lxml`** — a pin
change is its own PR with its own justification, and nothing here needs a real
HTML tree.

Two check families, and they mean different things.

**Structural hooks → `needs_review`.** Four shipped features query this document
by CSS selector. A skeleton missing one of those hooks does not crash anything
— it turns a feature OFF, silently, with no error anywhere, and the person who
activated the format three weeks earlier is the last person who would connect
the two. That is why absence blocks activation rather than warning:

  - exactly one `<style>` element, EMPTY. `html_style.inject_canonical_css`
    (`app/html_style.py:39`) replaces the FIRST `<style>` with `prd.css`. Drop
    the marker and it falls back to a `</head>` insert; keep a SECOND one and
    the customer's own CSS survives to fight the canonical sheet.
  - `.frame > .page[contenteditable]` — the page canvas everything in `prd.css`
    is scoped to, and what the in-app editor writes into.
  - exactly one `<h1>`, plus `.byline`.
  - `ul.ev` — else `applyEvidenceTruncation`
    (`web/app/lib/prdEvidenceTruncate.ts:38`) returns false and "View more
    evidence" simply disappears.
  - `ul.inputs` inside `.appendix` — else `extract_input_questions`
    (`backend/app/prd_questions.py`) finds nothing and the PRD's chat loses
    every answer button.
  - `p.hyp`, but ONLY when the section map claims a hypothesis home — else
    `stripHypothesisSection` (`web/app/lib/htmlBrief.ts:67`) no-ops in the
    combined Evidence+PRD export and the hypothesis is duplicated.
  - a requirements surface: a `<table>` carrying a `.pill` Type column, OR an
    explicit `form` of stories/prose/bullets for the requirements section.
    `implementation-spec` inherits Happy path / Edge case / Failure from
    whatever shape that takes, so it has to exist in SOME shape.

**Safety → hard reject (`failed`).** No `<script>`, no `on*=` handler
attribute, no `src`/`href` pointing at a host outside the Google Fonts pair.
The in-app iframe is sandboxed without `allow-scripts`
(`web/app/components/shared/PrdHtmlView.tsx`) and the PDF renderer runs JS-off
behind the same allowlist (`backend/app/report_pdf.py:57`) — **but
`web/app/lib/prdExport.ts:288-292` hands the raw document to Word with neither
protection.** So this validates rather than trusting the renderers. A rejected
format is recoverable in seconds (remove the tag, re-upload); a document that
runs code when a stakeholder opens it in Word is not.

The off-allowlist `href` rule does reject a format that links out to the team's
own style guide. That is a deliberate, conservative trade: the skeleton is a
blank form, links in it are rare, and the failure mode on the other side is the
Word export path.

Every note this module emits goes through `_note`, which refuses a `code`
outside `store.COMPILE_NOTE_CODES` — the web keys a translation table on
exactly those strings, so a drifted code would render as a generic line and
nobody would notice the check had stopped reporting.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.artifact_templates.store import COMPILE_NOTE_CODES

logger = logging.getLogger(__name__)

#: Hosts a compiled skeleton may load an asset from. Google Fonts only — the
#: house stylesheet's own font source. Anything else is a remote asset in a
#: document that gets exported to Word, where nothing is sandboxed.
ALLOWED_ASSET_HOSTS = frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})

#: Elements that never close, so the tag stack must not push them. Without this
#: a bare `<meta>` or `<br>` desynchronises every ancestor test below it.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

# CSS and HTML comments, stripped before deciding whether the <style> marker is
# empty — the house template's own marker carries an explanatory comment, and a
# comment is not a style rule.
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class ValidationResult:
    """What the validator decided, in the shape the row stores.

    `status` is one of the `compile_status` values, never a bool: the three
    outcomes are genuinely different products — `ready` may be activated,
    `needs_review` may be previewed and fixed, `failed` means we will not put
    this document in front of anyone."""

    status: str
    notes: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ready"


def _note(code: str, message: str) -> dict:
    """One `{code, message}` note, with the closed set enforced at the point of
    creation rather than at the storage boundary.

    A code outside `COMPILE_NOTE_CODES` is a programming error, not user input:
    `web/app/lib/compileNotes.ts` keys its translation table on exactly these
    strings, so a drifted code renders as the generic "one part of your format
    didn't map" line and the specific check silently stops reporting what it
    found. Raising here means a test catches it; storing it means nobody does."""
    if code not in COMPILE_NOTE_CODES:
        raise ValueError(
            f"compile note code {code!r} is not in COMPILE_NOTE_CODES — add it "
            "there AND to web/app/lib/compileNotes.ts, or the note renders as a "
            "generic line."
        )
    return {"code": code, "message": message}


class _SkeletonScanner(HTMLParser):
    """One pass over the skeleton, recording every fact the checks need.

    Deliberately a flat recorder rather than a tree: the questions asked below
    are all "does an element with class X exist" and "is it inside/directly
    inside Y", which a tag stack answers without building a DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, frozenset[str]]] = []

        self.style_blocks: list[str] = []
        self._in_style = False

        self.h1_count = 0
        self.has_page_canvas = False
        self.has_byline = False
        self.has_evidence_list = False
        self.has_inputs_list = False
        self.has_hypothesis = False
        self.has_table = False
        self.has_pill = False

        self.has_script = False
        self.event_attributes: list[str] = []
        self.remote_hosts: list[str] = []

    # ── stack plumbing ───────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs) -> None:
        classes = frozenset(
            (dict(attrs).get("class") or "").split()
        )
        self._record(tag, classes, dict(attrs))
        if tag not in _VOID_ELEMENTS:
            self.stack.append((tag, classes))
        if tag == "style":
            self._in_style = True
            self.style_blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False
        # Pop back THROUGH an unclosed ancestor rather than blindly popping the
        # top: model-emitted HTML is not guaranteed well-formed, and a single
        # unclosed <p> would otherwise shift every ancestor test after it.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_style and self.style_blocks:
            self.style_blocks[-1] += data

    # ── fact recording ───────────────────────────────────────────────────

    def _parent_has(self, cls: str) -> bool:
        return bool(self.stack) and cls in self.stack[-1][1]

    def _ancestor_has(self, cls: str) -> bool:
        return any(cls in classes for _tag, classes in self.stack)

    def _record(self, tag: str, classes: frozenset[str], attrs: dict) -> None:
        if tag == "script":
            self.has_script = True
        if tag == "h1":
            self.h1_count += 1
        if tag == "table":
            self.has_table = True
        if "pill" in classes:
            self.has_pill = True
        if "byline" in classes:
            self.has_byline = True
        # The canvas is `.page[contenteditable]` DIRECTLY inside `.frame` —
        # prd.css scopes to that pair, and the in-app editor writes into it.
        if (
            "page" in classes
            and "contenteditable" in attrs
            and self._parent_has("frame")
        ):
            self.has_page_canvas = True
        if tag == "ul" and "ev" in classes:
            self.has_evidence_list = True
        # `ul.inputs` only counts INSIDE `.appendix` — prd_questions looks for
        # it there, and an inputs list somewhere else is not what it reads.
        if tag == "ul" and "inputs" in classes and self._ancestor_has("appendix"):
            self.has_inputs_list = True
        if tag == "p" and "hyp" in classes:
            self.has_hypothesis = True

        for name, value in attrs.items():
            lowered = name.lower()
            # Any `on*` attribute is an inline handler. Checked by prefix rather
            # than against a list of known events, because the list is long,
            # grows, and a miss here is code running in someone's Word document.
            if lowered.startswith("on"):
                self.event_attributes.append(lowered)
            if lowered in ("src", "href") and value:
                host = _offending_host(value)
                if host:
                    self.remote_hosts.append(host)


def _offending_host(url: str) -> str | None:
    """The host to complain about in `url`, or None when it is acceptable.

    Acceptable: a fragment or a relative path (no scheme, no host — nothing is
    fetched from anywhere else), and the two Google Fonts hosts. Everything
    else, INCLUDING `javascript:` and `data:`, is reported — a `data:` URI can
    carry an SVG with a script in it, and the Word export path has no sandbox
    to stop it."""
    raw = (url or "").strip()
    if not raw or raw.startswith("#"):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return "malformed-url"
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not scheme and not host:
        return None  # relative path — resolves against the document, fetches nothing
    if scheme in ("http", "https") and host in ALLOWED_ASSET_HOSTS:
        return None
    if scheme and not host:
        # javascript:, data:, file: — no host to name, so name the scheme.
        return f"{scheme}:"
    return host or "unknown-host"


def _style_marker_is_empty(blocks: list[str]) -> bool:
    """True when the single `<style>` element carries no CSS rules.

    Comments do not count as content — the house template's own marker is a
    comment telling the model to leave the block alone."""
    if not blocks:
        return False
    body = _CSS_COMMENT_RE.sub("", blocks[0])
    body = _HTML_COMMENT_RE.sub("", body)
    return not body.strip()


def _claims_a_hypothesis(section_map: dict) -> bool:
    """True when the compiled map says the customer's format has a home for the
    hypothesis. Only then is `p.hyp` required — a format with no hypothesis
    section should not be blocked for lacking a hook nothing will look for."""
    for entry in (section_map or {}).get("sections") or []:
        if not isinstance(entry, dict):
            continue
        if "hypothesis" in str(entry.get("house") or "").lower():
            return True
    return False


def _requirements_form(section_map: dict) -> str | None:
    """The declared `form` of the requirements section, or None if the map has
    no requirements entry."""
    for entry in (section_map or {}).get("sections") or []:
        if not isinstance(entry, dict):
            continue
        if "requirement" in str(entry.get("house") or "").lower():
            return str(entry.get("form") or "") or None
    return None


def validate_prd_skeleton(html: str, section_map: dict | None = None) -> ValidationResult:
    """Decide whether a compiled PRD skeleton may govern a company's PRDs.

    Returns `failed` on any safety finding (and stops there — a document we
    will not render is not worth reporting structural gaps in), `needs_review`
    with one note per missing hook, or `ready`.

    Note ordering is stable and deliberate: the list's FIRST entry becomes the
    row's `compile_summary`, the one sentence a user sees without opening the
    preview, so the checks are ordered by how badly the absence hurts."""
    section_map = section_map or {}
    scanner = _SkeletonScanner()
    try:
        scanner.feed(html or "")
        scanner.close()
    except Exception:  # noqa: BLE001 — malformed markup is a compile failure, not a 500
        logger.warning("artifact_template_skeleton_unparseable")
        return ValidationResult(
            status="failed",
            notes=[_note(
                "compile_error",
                "We couldn't read the document we produced from this format. "
                "Nothing about your file has changed — try again.",
            )],
        )

    # ── safety: hard reject, and reported on its own ─────────────────────
    unsafe: list[dict] = []
    if scanner.has_script:
        unsafe.append(_note(
            "unsafe_script",
            "Your file contains a script. Sprntly won't run scripts inside a "
            "document — remove it and upload again.",
        ))
    if scanner.event_attributes:
        unsafe.append(_note(
            "unsafe_attribute",
            "Your file contains code that runs when a document is opened. "
            "Remove it and upload again.",
        ))
    if scanner.remote_hosts:
        unsafe.append(_note(
            "unsafe_remote_asset",
            "Your format loads an image or stylesheet from another site. "
            "Sprntly only allows Google Fonts — remove it and upload again.",
        ))
    if unsafe:
        # Deliberately NOT merged with the structural notes below. A format we
        # refuse to render is one decision; the eight ways it might also be
        # incomplete are noise next to it.
        return ValidationResult(status="failed", notes=unsafe)

    # ── structural hooks: needs_review, one note per concept ─────────────
    notes: list[dict] = []

    if not scanner.has_evidence_list:
        notes.append(_note(
            "missing_evidence_list",
            "We couldn't find where your format lists evidence. Sprntly needs a "
            "bulleted evidence list so readers can open the sources behind each "
            "claim.",
        ))
    if not scanner.has_inputs_list:
        notes.append(_note(
            "missing_input_questions",
            "We couldn't find where your format collects open questions. "
            "Without one, the PRD's chat can't offer answer buttons for the gaps.",
        ))

    form = _requirements_form(section_map)
    # A requirements surface in SOME shape: the house table with its Type pills,
    # or an explicitly declared alternative form. implementation-spec inherits
    # Happy path / Edge case / Failure from whichever it is.
    has_requirements = (scanner.has_table and scanner.has_pill) or form in (
        "stories", "prose", "bullets",
    )
    if not has_requirements:
        notes.append(_note(
            "missing_requirements",
            "We couldn't tell how your format lists requirements. Sprntly needs "
            "each one as a row or a user story so tickets can cite it.",
        ))

    # Only required when the map says the format HAS a hypothesis section.
    if _claims_a_hypothesis(section_map) and not scanner.has_hypothesis:
        notes.append(_note(
            "missing_hypothesis",
            "Your format names a hypothesis section, but we couldn't find a "
            "single hypothesis statement inside it.",
        ))

    # ONE note per code, even though two checks feed each of the last two.
    # Duplicating a code would render the same translated sentence twice and
    # inflate the "See all N" count without telling the user anything more.
    if scanner.h1_count != 1 or not scanner.has_byline:
        notes.append(_note(
            "missing_title",
            "Your format has no single document title. Sprntly needs one "
            "heading at the top to name each document.",
        ))
    if len(scanner.style_blocks) != 1 or not _style_marker_is_empty(
        scanner.style_blocks
    ) or not scanner.has_page_canvas:
        notes.append(_note(
            "missing_style_marker",
            "We couldn't fit Sprntly's styling into your format. Documents "
            "would come out unformatted.",
        ))

    return ValidationResult(
        status="needs_review" if notes else "ready", notes=notes
    )


# ─── engineering spec (Part B) ───────────────────────────────────────────────
#
# A different output vocabulary and a much shorter check. Part B is MARKDOWN,
# has no structured viewer, no class vocabulary and no CSS — so none of the
# HTML machinery above applies. Exactly one thing has to survive a customer's
# format: the B0–B9 section ids.
#
# They are not decoration. `stories/generate.py` builds ticket acceptance
# criteria by inheriting the EARS requirements under B3 and labels the block
# `## Part B (machine-readable Implementation Spec)` in `_build_input`; the
# B0 derivation header is what ties a spec back to the Part A that produced it.
# A spec whose ids are gone still renders fine and still reads fine — and the
# ticket generator quietly stops finding anything to inherit. That is the same
# silent-feature-death failure mode the PRD hooks above exist to prevent, which
# is why a missing id is an activation gate and not a warning.

#: Every id the skeleton must still carry, in order. Sourced from
#: `skills/implementation-spec/templates/implementation-spec-template.md`, whose
#: SKILL.md calls the B0–B9 structure NORMATIVE.
IMPL_SPEC_SECTION_IDS = ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9")

#: The two ids with live downstream READERS, as opposed to readers-in-principle.
#: Kept separate because the note they produce differs — see below.
_IMPL_SPEC_REQUIREMENT_IDS = frozenset({"B3", "B8"})

#: `## B3.` / `**B7**` / `### b0 —` all count. The id has to appear as its own
#: token so a stray "B3" inside a sentence of prose does not satisfy the check
#: for a section that is not there.
_B_ID_RE = re.compile(r"(?<![0-9A-Za-z])B([0-9])(?![0-9A-Za-z])")

#: Markdown permits raw HTML, and Part B is handed to a coding agent and pushed
#: into tracker descriptions. A `<script>` in a spec skeleton has no legitimate
#: purpose, so it is refused for the same reason the PRD path refuses one.
_SCRIPT_RE = re.compile(r"<\s*script\b", re.IGNORECASE)


def missing_impl_spec_ids(markdown: str) -> list[str]:
    """Which of B0–B9 the skeleton no longer carries, in order. Pure."""
    found = {f"B{d}" for d in _B_ID_RE.findall(markdown or "")}
    return [bid for bid in IMPL_SPEC_SECTION_IDS if bid not in found]


def validate_impl_spec_skeleton(markdown: str) -> ValidationResult:
    """Decide whether a compiled ENGINEERING-SPEC skeleton may be activated.

    Two checks, in refusal order.

    SAFETY first, and it is a hard `failed`: a skeleton carrying a `<script>`
    is not stored at all, so it can never be previewed or activated.

    Then the B0–B9 ids. Any missing id is `needs_review` — previewable, fixable,
    but not activatable, because the failure it causes downstream is silent.

    ONE note, `missing_spec_sections`, whichever ids are gone. This code exists
    rather than borrowing the PRD-side ones because borrowing them was
    technically honest and actually misleading: a customer who dropped B6 read a
    sentence explaining how Sprntly wants their requirements listed, which
    describes neither what they did nor what to fix. The six hooks above belong
    to an HTML PRD and have no meaning in a markdown spec.

    The message names the CONSEQUENCE — tickets arriving without acceptance
    criteria — not the ids, because "B6 is missing" is the same class of jargon
    as "`ul.ev` is missing". The specific ids are logged, and the preview shows
    the skeleton, which is where someone fixing it will actually look.
    """
    if _SCRIPT_RE.search(markdown or ""):
        return ValidationResult(
            status="failed",
            notes=[_note(
                "unsafe_script",
                "Your file contains a script. Sprntly won't run scripts inside "
                "a document — remove it and upload again.",
            )],
        )

    missing = missing_impl_spec_ids(markdown)
    if not missing:
        return ValidationResult(status="ready")

    logger.info(
        "impl_spec_skeleton_missing_ids missing=%s gates_tickets=%s",
        ",".join(missing),
        bool(_IMPL_SPEC_REQUIREMENT_IDS.intersection(missing)),
    )
    return ValidationResult(
        status="needs_review",
        notes=[_note(
            "missing_spec_sections",
            "We couldn't find every section a Sprntly engineering spec needs. "
            "Your format is missing the parts the ticket generator reads, so "
            "tickets from it would come back without acceptance criteria.",
        )],
    )

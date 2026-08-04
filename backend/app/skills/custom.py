"""Custom-skill upload parsing — turn an uploaded .md/.zip into the parsed
content the `custom_skills` table stores and the loader's SkillSpec shape.

Built-in skills are directories (SKILL.md + modules/ references/ …) read by
app/skills/loader.py. A custom-skill upload is the same idea in a file: a bare
.md IS the method, and a .zip mirrors the directory layout. The mapping here is
deliberately flexible (PRD 1854 R4/R9 — "parse what we can, no rigid schema"):

  - single top-level folder in a zip is unwrapped first (the claude.ai skill
    convention: my-skill.zip → my-skill/SKILL.md)
  - method = root SKILL.md (case-insensitive) if present, else the only .md,
    else the shallowest-then-alphabetical .md
  - modules/*.md → modules, references/*.md → references, any other loose
    .md → modules (keyed by filename)
  - non-.md members are ignored (scripts/assets are a built-in-only feature
    for now — uploaded code is never executed)

An archive can also hold SEVERAL skills — `parse_multi_upload` handles that
case and `parse_upload` keeps owning the one-skill one. A multi-skill archive
is what you get by zipping a Claude Code skills directory (`skills/<id>/
SKILL.md`, one folder per skill — https://code.claude.com/docs/en/skills), and
the single-skill parser above mangles it: the first SKILL.md wins the method
slot, the second becomes a "module" of it, the third is dropped by the
basename `setdefault`, and a nested `references/` mis-files into modules
because only a ROOT-level one is recognised. Discovery is therefore structural
rather than heuristic — every directory holding a SKILL.md is its own skill,
files under a nested skill belong to that nested skill and not to its
ancestor — and each discovered skill names itself from its own SKILL.md
frontmatter (`name`/`description` are the two required fields of the Agent
Skills format, capped at 64/1024 chars:
https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview),
because one upload form cannot name N skills.

Zip safety guards mirror datasets.ingest_zip: junk filtering, no nested-zip
recursion, per-member and total-uncompressed caps, capped reads (the declared
file_size is untrusted), basename-only handling of hostile paths.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# _parse_frontmatter is borrowed deliberately rather than reimplemented: it
# already handles the YAML block scalars (`description: >`) that five vendored
# skills use, and an uploaded skill written to the same spec will use them too.
# A second, simpler parser here would capture ">" as the whole description —
# exactly the bug loader.py's docstring records.
from app.skills.loader import SkillSpec, _parse_frontmatter

# Mirrors datasets.py's zip guards, sized for skill bundles (a skill is text —
# these are generous). The compressed upload itself is capped by the route at
# skills_storage.MAX_SKILL_UPLOAD_BYTES (20 MB).
_ZIP_MAX_MEMBERS = 200
_ZIP_MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB across the archive
_ZIP_MAX_MEMBER_BYTES = 20 * 1024 * 1024         # per extracted .md

# Agent Skills open-spec field limits (agentskills.io) — enforced at upload so
# Sprntly skills stay interoperable with the format the repo already vendors.
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024

# Cap on the PARSED skill text (method + modules + references, in characters).
# The method block is injected into the prompt on every invocation, so this
# bounds prompt cost; the largest vendored skill is ~32k chars, so 50k leaves
# custom skills headroom above anything we ship. Distinct from the 20 MB byte
# cap on the raw upload (skills_storage.MAX_SKILL_UPLOAD_BYTES).
MAX_SKILL_CONTENT_CHARS = 50_000


class SkillParseError(ValueError):
    """Upload problems that should surface as 4xx with the message verbatim."""


@dataclass(frozen=True)
class ParsedSkill:
    method: str
    modules: dict[str, str] = field(default_factory=dict)
    references: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredSkill:
    """One skill out of a MULTI-skill archive, already named for itself.

    `path` is the archive-relative directory it was rooted at ("" for a skill
    rooted at the archive root), kept for the "which folder was that?" line the
    UI shows and for the skipped-list reasons."""

    path: str
    name: str
    description: str
    parsed: ParsedSkill


@dataclass(frozen=True)
class SkippedSkill:
    """A skill folder that was found but could not be imported, with the
    reason a person can act on. One bad folder never fails the whole archive —
    the rest still import."""

    path: str
    name: str
    reason: str


@dataclass(frozen=True)
class MultiSkillArchive:
    skills: list[DiscoveredSkill]
    skipped: list[SkippedSkill]


def slugify(name: str) -> str:
    """Display name → skill id/trigger: lowercase kebab-case per the Agent
    Skills spec (lowercase alphanumerics + single hyphens, no edge hyphens)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def available_slug(base: str, taken: Iterable[str]) -> str:
    """First free id in the series `base`, `base-2`, `base-3`, … .

    A custom skill NEVER overrides a same-named built-in — both stay invocable,
    so their triggers have to differ. `taken` is every id already spoken for:
    the vendored built-ins plus the company's own custom slugs (a name like
    "PRD Author 2" can itself occupy `prd-author-2`). The display name the user
    typed is untouched — only the trigger is disambiguated."""
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def parse_upload(filename: str, data: bytes) -> ParsedSkill:
    """Parse an uploaded .md or .zip into skill content.

    Raises SkillParseError with a user-readable message on anything that
    should block the upload (empty content, bad archive, no .md inside)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "md":
        method = _decode_md(filename, data)
        if not method.strip():
            raise SkillParseError("The Markdown file is empty — add the skill's method text and try again.")
        return ParsedSkill(method=method)
    if ext == "zip":
        return _parse_zip(filename, data)
    raise SkillParseError("Only .md files and .zip archives are accepted.")


def parse_multi_upload(filename: str, data: bytes) -> MultiSkillArchive | None:
    """Split an archive that holds SEVERAL skills into one entry per skill.

    Returns None when this upload is a single skill — a bare .md, or a zip
    holding at most one SKILL.md — and the caller should use `parse_upload`,
    whose behaviour is deliberately untouched. That split is what keeps the
    one-skill contract (the form's name/description, one row, one 201 body)
    byte-identical: nothing below runs for an archive that has always worked.

    A skill is a directory whose immediate contents include a SKILL.md, plus
    the archive root itself when a root SKILL.md is present. A SKILL.md nested
    inside another skill's folder is its OWN skill and its files are excluded
    from the ancestor's — deepest root wins, so `sales/SKILL.md` never swallows
    `sales/discovery/SKILL.md`. Loose .md files under no skill folder are
    ignored here (with no SKILL.md anywhere, discovery finds nothing and the
    single-skill parser's shallowest-.md promotion still applies).

    Raises SkillParseError only for archive-level problems (bad zip, over the
    member/size caps, non-UTF-8 text) — a single unusable skill folder comes
    back in `skipped` instead, so one bad folder cannot cost the user the
    other nine."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext != "zip":
        return None
    zf = _open_zip(filename, data)
    members, wrapper = _zip_members(zf)
    md_members = [(p, info) for p, info in members if p.suffix.lower() == ".md"]

    roots = _skill_roots(md_members)
    if len(roots) < 2:
        return None

    # The name a root-level skill inherits when its SKILL.md carries no
    # frontmatter: the wrapper folder it was zipped inside, else the zip's own
    # filename — the only two places that archive records what it is called.
    root_label = wrapper or PurePosixPath(filename).stem

    skills: list[DiscoveredSkill] = []
    skipped: list[SkippedSkill] = []
    for root in roots:
        parsed, method_text = _parsed_for_root(zf, md_members, root, roots)
        path = "/".join(root)
        label = root[-1] if root else root_label
        name, description = derive_identity(method_text, label)
        if not method_text.strip():
            skipped.append(SkippedSkill(path, name, "its SKILL.md is empty"))
            continue
        if not name or not slugify(name):
            skipped.append(SkippedSkill(
                path, name,
                "we couldn't work out a name for it — add `name:` to its SKILL.md",
            ))
            continue
        if not description:
            skipped.append(SkippedSkill(
                path, name,
                "we couldn't work out a description for it — add `description:` "
                "to its SKILL.md",
            ))
            continue
        skills.append(DiscoveredSkill(
            path=path, name=name, description=description, parsed=parsed
        ))
    return MultiSkillArchive(skills=skills, skipped=skipped)


def build_skill_archive(parsed: ParsedSkill) -> tuple[bytes, str]:
    """Standalone file bytes + extension for ONE skill lifted out of a
    multi-skill archive.

    Each row owns its own stored original, because everything downstream is
    per-row: GET /v1/skills/{id}/file serves that key, DELETE removes it, and a
    re-upload replaces it. Handing every row the multi-skill zip would make a
    download of one skill hand back nine, and the first delete would strip the
    file out from under the other eight.

    A skill that is only a SKILL.md round-trips as a bare .md (the format it
    would have been uploaded in); anything with supporting files is rebuilt as
    a zip in the layout the parser reads back."""
    if not parsed.modules and not parsed.references:
        return parsed.method.encode("utf-8"), "md"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", parsed.method)
        for name, text in sorted(parsed.modules.items()):
            zf.writestr(f"modules/{name}", text)
        for name, text in sorted(parsed.references.items()):
            zf.writestr(f"references/{name}", text)
    return buf.getvalue(), "zip"


def content_chars(parsed: ParsedSkill) -> int:
    """Total characters of parsed skill text — what MAX_SKILL_CONTENT_CHARS
    measures (every .md the archive contributed, not just the method)."""
    return (
        len(parsed.method)
        + sum(len(t) for t in parsed.modules.values())
        + sum(len(t) for t in parsed.references.values())
    )


def content_hash_for(parsed: ParsedSkill) -> str:
    """First 12 hex of sha256 over the parsed content — same shape as
    loader._content_hash (relative path + NUL + bytes, sorted) so custom and
    built-in skills version identically in prompt_version."""
    h = hashlib.sha256()
    entries: list[tuple[str, str]] = [("SKILL.md", parsed.method)]
    entries += [(f"modules/{n}", t) for n, t in parsed.modules.items()]
    entries += [(f"references/{n}", t) for n, t in parsed.references.items()]
    for path, text in sorted(entries):
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:12]


def build_spec(row: dict) -> SkillSpec:
    """A custom_skills DB row → the loader's SkillSpec shape, so the gateway's
    method-prefix injection treats custom skills exactly like built-ins."""
    return SkillSpec(
        id=row["slug"],
        method=row["method"],
        modules=dict(row.get("modules") or {}),
        references=dict(row.get("references") or {}),
        content_hash=row.get("content_hash", ""),
        description=row.get("description", ""),
        has_scripts=False,
    )


# ─── internals ───────────────────────────────────────────────────────────────


def _decode_md(name: str, data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillParseError(
            f"{PurePosixPath(name).name!r} is not readable as UTF-8 text — "
            "Markdown skill files must be plain text."
        ) from exc


def _is_zip_junk(name: str) -> bool:
    """macOS resource-fork / metadata entries to ignore (mirrors datasets.py)."""
    base = PurePosixPath(name).name
    return (
        name.startswith("__MACOSX/")
        or base.startswith("._")
        or base == ".DS_Store"
        or not base
    )


def _open_zip(filename: str, data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillParseError(
            f"{PurePosixPath(filename).name!r} is not a valid zip archive."
        ) from exc


def _zip_members(
    zf: zipfile.ZipFile,
) -> tuple[list[tuple[PurePosixPath, zipfile.ZipInfo]], str | None]:
    """Safety-checked members with the single top-level wrapper unwrapped.

    Shared by the single- and multi-skill paths so both apply the SAME guards
    and the same unwrap — the wrapper folder's name comes back too, because it
    is the best fallback display name for a skill rooted at the archive root
    (`my-skill.zip → my-skill/SKILL.md` carries its name only there)."""
    members: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
    total_uncompressed = 0
    for info in zf.infolist():
        if info.is_dir() or _is_zip_junk(info.filename):
            continue
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            continue  # hostile path — skip, never extract
        if len(members) >= _ZIP_MAX_MEMBERS:
            raise SkillParseError("The archive contains too many files.")
        total_uncompressed += info.file_size
        if total_uncompressed > _ZIP_MAX_TOTAL_UNCOMPRESSED:
            raise SkillParseError("The archive is too large when uncompressed.")
        members.append((path, info))

    # Unwrap a single top-level folder (my-skill.zip → my-skill/SKILL.md).
    roots = {p.parts[0] for p, _ in members if p.parts}
    wrapper: str | None = None
    if len(roots) == 1 and all(len(p.parts) > 1 for p, _ in members):
        wrapper = next(iter(roots))
        members = [(PurePosixPath(*p.parts[1:]), info) for p, info in members]
    return members, wrapper


def _read_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, path: PurePosixPath
) -> str:
    # Capped read — the declared file_size is untrusted (zip-bomb guard).
    with zf.open(info) as fh:
        raw = fh.read(_ZIP_MAX_MEMBER_BYTES + 1)
    if len(raw) > _ZIP_MAX_MEMBER_BYTES:
        raise SkillParseError(f"{path.name!r} in the archive is too large.")
    return _decode_md(str(path), raw)


def _parse_zip(filename: str, data: bytes) -> ParsedSkill:
    zf = _open_zip(filename, data)
    members, _wrapper = _zip_members(zf)

    md_members = [(p, info) for p, info in members if p.suffix.lower() == ".md"]
    if not md_members:
        raise SkillParseError(
            "The ZIP archive must contain at least one Markdown (.md) file. "
            "Please add a .md file and try again."
        )

    def _read(info: zipfile.ZipInfo, path: PurePosixPath) -> str:
        return _read_member(zf, info, path)

    # Choose the method file: root SKILL.md > the only .md > shallowest-then-
    # alphabetical. Ties on the exact spec name are impossible (zip paths are
    # unique), so the sort is deterministic.
    def _method_rank(item: tuple[PurePosixPath, zipfile.ZipInfo]) -> tuple:
        path, _ = item
        is_root_skill_md = len(path.parts) == 1 and path.name.lower() == "skill.md"
        return (not is_root_skill_md, len(path.parts), str(path).lower())

    md_members.sort(key=_method_rank)
    method_path, method_info = md_members[0]
    method = _read(method_info, method_path)

    modules: dict[str, str] = {}
    references: dict[str, str] = {}
    for path, info in md_members[1:]:
        text = _read(info, path)
        top = path.parts[0].lower() if len(path.parts) > 1 else ""
        target = references if top == "references" else modules
        # Keyed by filename (module names are how the gateway addresses them);
        # first occurrence wins on a duplicate basename from different dirs.
        target.setdefault(path.name, text)

    if not method.strip() and not any(t.strip() for t in {**modules, **references}.values()):
        raise SkillParseError(
            "Every Markdown file in the archive is empty — add the skill's "
            "method text and try again."
        )
    if not method.strip():
        # The chosen method file is empty but another .md has content — promote
        # the first non-empty module (flexible-schema: parse what we can).
        for name in sorted(modules):
            if modules[name].strip():
                method = modules.pop(name)
                break
        else:
            for name in sorted(references):
                if references[name].strip():
                    method = references.pop(name)
                    break
    return ParsedSkill(method=method, modules=modules, references=references)


# ─── skill detection (shared by the zip parser and the GitHub importer) ──────
#
# These four are PUBLIC because a repo is the same problem as an archive: a
# tree of paths, some of which are SKILL.md. app/skills/github_source.py walks
# a GitHub tree through exactly these rules, so an importer and an upload of
# the same repo produce the same skills — a second implementation would drift
# the day one of them fixed a bug.

#: A skill's root directory as a path-parts tuple; `()` is the tree root.
SkillRoot = tuple[str, ...]

_Root = SkillRoot


def skill_roots_for(paths: Iterable[str]) -> list[SkillRoot]:
    """Every directory that DIRECTLY contains a SKILL.md (case-insensitive),
    as a parts tuple — `()` when the SKILL.md sits at the walked root.

    Sorted, so a caller's import order is the tree's reading order rather than
    zip-entry or API-response order."""
    roots: set[SkillRoot] = set()
    for raw in paths:
        parts = tuple(p for p in raw.split("/") if p)
        if parts and parts[-1].lower() == "skill.md":
            roots.add(parts[:-1])
    return sorted(roots)


def owning_skill_root(path: str, roots: Iterable[SkillRoot]) -> SkillRoot | None:
    """The DEEPEST skill root this file lives under, or None when it lives
    under none. Deepest wins, which is the whole rule that keeps a nested
    skill's files out of its ancestor's bundle."""
    parts = tuple(p for p in path.split("/") if p)
    best: SkillRoot | None = None
    for root in roots:
        if parts[: len(root)] == root and (best is None or len(root) > len(best)):
            best = root
    return best


def skill_file_role(path: str, root: SkillRoot) -> str:
    """Where one file lands inside its skill: "method", "references" or
    "modules".

    The built-in layout rules, applied RELATIVE to the skill's own directory:
    its SKILL.md is the method, `references/*.md` are its references, every
    other .md is a module. Relative-to-the-skill is what fixes
    `skill-b/references/x.md`, which the single-skill zip parser filed as a
    module because it only ever looked at the archive's top level."""
    rel = tuple(p for p in path.split("/") if p)[len(root):]
    if len(rel) == 1 and rel[0].lower() == "skill.md":
        return "method"
    if len(rel) > 1 and rel[0].lower() == "references":
        return "references"
    return "modules"


def _owning_root(path: PurePosixPath, roots: list[_Root]) -> _Root | None:
    return owning_skill_root(str(path), roots)


def _skill_roots(
    md_members: list[tuple[PurePosixPath, zipfile.ZipInfo]],
) -> list[_Root]:
    return skill_roots_for(str(p) for p, _ in md_members)


def _parsed_for_root(
    zf: zipfile.ZipFile,
    md_members: list[tuple[PurePosixPath, zipfile.ZipInfo]],
    root: _Root,
    roots: list[_Root],
) -> tuple[ParsedSkill, str]:
    """One skill's ParsedSkill plus its raw SKILL.md text (which the identity
    derivation reads separately from the frontmatter). Layout rules come from
    `skill_file_role`, shared with the GitHub importer."""
    method = ""
    modules: dict[str, str] = {}
    references: dict[str, str] = {}
    for path, info in sorted(md_members, key=lambda item: str(item[0]).lower()):
        if _owning_root(path, roots) != root:
            continue
        role = skill_file_role(str(path), root)
        text = _read_member(zf, info, path)
        if role == "method" and not method:
            method = text
            continue
        target = references if role == "references" else modules
        target.setdefault(path.name, text)
    return ParsedSkill(method=method, modules=modules, references=references), method


def _display_name(raw: str) -> str:
    """A folder id or a spec `name:` → the display name the library shows.

    Agent Skills names are lowercase-hyphen identifiers ("sprint-planner"), and
    so are folder names, but this library lists human names next to a `/trigger`
    — so an all-lowercase identifier is spaced and capitalised. Anything that
    already carries capitals or spaces is the author's own display name and is
    left exactly as typed. slugify() is unaffected either way, so the trigger
    is the same whichever branch runs."""
    text = re.sub(r"\s+", " ", raw.replace("_", " ").replace("-", " ")).strip()
    if not text:
        return ""
    if any(c.isupper() for c in raw) or " " in raw.strip():
        return re.sub(r"\s+", " ", raw.strip())
    return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))


def _strip_frontmatter(text: str) -> str:
    """The markdown body after a leading `---`…`---` block (all of it when
    there is no block)."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    rest = text[end + 4:]
    return rest.split("\n", 1)[1] if "\n" in rest else ""


def _first_paragraph(body: str) -> str:
    """The first block of prose in a SKILL.md body — the description fallback
    when the file carries no frontmatter.

    Headings, code fences and horizontal rules are skipped rather than
    described: "# Sprint Planner" tells a teammate nothing the name did not
    already say. The first run of consecutive content lines is joined into one
    line, because a description is one line in the library and in the router's
    skill list."""
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```") or set(line) <= {"-", "="}:
            if lines:
                break
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def derive_identity(method_text: str, label: str) -> tuple[str, str]:
    """(name, description) for one skill in a multi-skill archive.

    The upload form has ONE name and ONE description field, so an archive of
    nine skills has to name them itself. Frontmatter first — `name` and
    `description` are the two required fields of the Agent Skills format, so a
    skill written to the spec already carries both. Falling back: the folder it
    lives in for the name (that IS its id in a skills/ directory), and the
    first paragraph of its body for the description. Both are truncated to the
    same caps the upload form enforces."""
    front = _parse_frontmatter(method_text)
    name = _display_name(front.get("name", "").strip() or label)
    description = re.sub(r"\s+", " ", front.get("description", "").strip())
    if not description:
        description = _first_paragraph(_strip_frontmatter(method_text))
    return name[:MAX_NAME_CHARS].strip(), description[:MAX_DESCRIPTION_CHARS].strip()

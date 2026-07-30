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

Zip safety guards mirror datasets.ingest_zip: junk filtering, no nested-zip
recursion, per-member and total-uncompressed caps, capped reads (the declared
file_size is untrusted), basename-only handling of hostile paths.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.skills.loader import SkillSpec

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


def slugify(name: str) -> str:
    """Display name → skill id/trigger: lowercase kebab-case per the Agent
    Skills spec (lowercase alphanumerics + single hyphens, no edge hyphens)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


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


def _parse_zip(filename: str, data: bytes) -> ParsedSkill:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillParseError(
            f"{PurePosixPath(filename).name!r} is not a valid zip archive."
        ) from exc

    # Collect candidate members with normalized, safety-checked paths.
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
    if len(roots) == 1 and all(len(p.parts) > 1 for p, _ in members):
        members = [(PurePosixPath(*p.parts[1:]), info) for p, info in members]

    md_members = [(p, info) for p, info in members if p.suffix.lower() == ".md"]
    if not md_members:
        raise SkillParseError(
            "The ZIP archive must contain at least one Markdown (.md) file. "
            "Please add a .md file and try again."
        )

    def _read(info: zipfile.ZipInfo, path: PurePosixPath) -> str:
        # Capped read — the declared file_size is untrusted (zip-bomb guard).
        with zf.open(info) as fh:
            raw = fh.read(_ZIP_MAX_MEMBER_BYTES + 1)
        if len(raw) > _ZIP_MAX_MEMBER_BYTES:
            raise SkillParseError(f"{path.name!r} in the archive is too large.")
        return _decode_md(str(path), raw)

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

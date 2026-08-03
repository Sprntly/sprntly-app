"""Loader for the vendored PM Agent Skills (prompt-layer method specs).

Each skill lives at `backend/skills/<id>/` with a required `SKILL.md` (the
*method* the agent follows) plus optional `modules/`, `templates/`,
`references/`, `assets/`, and `scripts/` directories. `references/` holds the
docs SKILL.md tells the model to read at runtime (schemas, rubrics, examples) —
the gateway folds these into the cacheable METHOD prefix so the skill's full
workflow is actually in-prompt. `get_skill(skill_id)` reads a skill off disk once,
hashes all of its files into a short `content_hash`, and caches the result in
process. The hash is recorded by the gateway in `prompt_version` so every
decision is traceable to the exact method version behind it.

Skills are STATIC bindings — agents name the skill they use directly in code.
There is no dynamic routing here, and as of the bare-chat change there is none
anywhere: the library is NINE skills, each bound by name from the one pipeline
that needs it (prd-author + implementation-spec from the PRD runner,
evidence-brief from evidence_kg, user-stories from the ticket generator,
top-insights from the synthesis agent, and the four connector-extraction
contracts from kg_ingest). A chat turn selects none of them.

`get_skill` still RAISES `UnknownSkillError` on a missing directory, and that is
deliberate — those nine bindings are load-bearing and a silent empty method
would be worse than a stack trace. The tolerance for ids that are no longer
vendored lives one layer up, in `graph.gateway._build_method_prefix`, where it
can be scoped to callers that only pass `skill=` for attribution.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT

# backend/skills/ — sibling of backend/app/. REPO_ROOT is backend/.
SKILLS_ROOT = REPO_ROOT / "skills"


@dataclass(frozen=True)
class SkillSpec:
    """A loaded skill: its method text plus any modules/templates, fingerprinted.

    `content_hash` is the first 12 hex chars of the sha256 over every file in
    the skill directory (path + bytes), so any edit to the method, a module, a
    template, a reference, an asset, or a script changes the hash.

    `references` are the skill's `references/*` docs (schemas, rubrics, golden
    examples) that SKILL.md instructs the model to *read at runtime* ("read
    references/signal-schema.json", "score against references/rubric.md"). The
    gateway injects them into the cacheable METHOD prefix so the skill can run
    its full documented workflow, not just the SKILL.md summary. `assets` are
    the skill's `assets/*` files (e.g. a render template) — loaded for
    completeness/fingerprinting but NOT injected into the prompt: the app renders
    from the structured brief payload, so the template is a downstream view, not
    a prompt input.

    `description` is the one-line summary from the SKILL.md frontmatter — what
    the router classifies against. `has_scripts` is True when the skill bundles
    deterministic `scripts/*.py` (run for math, never estimated).
    """

    id: str
    method: str                                   # SKILL.md text
    modules: dict[str, str] = field(default_factory=dict)    # name -> text
    templates: dict[str, str] = field(default_factory=dict)  # name -> text
    references: dict[str, str] = field(default_factory=dict)  # name -> text
    assets: dict[str, str] = field(default_factory=dict)      # name -> text
    content_hash: str = ""
    description: str = ""
    has_scripts: bool = False


def _join_block(raw: list[str], *, folded: bool) -> str:
    """Join the raw lines of a YAML block scalar into its value.

    The block's own indentation is measured from its least-indented non-empty
    line and stripped, so nesting *inside* a literal block survives. Leading and
    trailing blank lines are dropped.

    Literal (`|`) keeps every newline. Folded (`>`) applies the YAML folding
    rule: consecutive non-empty lines join with a single space, and a blank line
    becomes a paragraph break — which is what makes `description: >` read back as
    one flowing sentence instead of a ragged column of fragments.
    """
    indents = [len(ln) - len(ln.lstrip()) for ln in raw if ln.strip()]
    base = min(indents) if indents else 0
    lines = [ln[base:].rstrip() if ln.strip() else "" for ln in raw]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not folded:
        return "\n".join(lines)
    paragraphs: list[str] = []
    current: list[str] = []
    for ln in lines:
        if ln:
            current.append(ln)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract simple `key: value` pairs from a leading `---`…`---` block.

    Deliberately minimal (still no YAML dep): handles the flat, single-line
    `name:`/`description:` frontmatter the PM skills use, plus YAML BLOCK
    SCALARS — `key: >` (folded) and `key: |` (literal) — whose value lives on the
    following indented lines rather than after the colon.

    Block scalars are not a nicety here. A plain `partition(":")` on the line
    `description: >` captured the single character ">" as the entire
    description. That was found via the (since-deleted) chat router menu, which
    listed `prd-author` as the literal line `- prd-author: >` — zero semantic
    signal. The router menu is gone, but the trap is NOT: five of the nine
    skills we still vendor use a block scalar, including all four
    KG-extraction skills, whose descriptions `app.graph.evals` reads. A skill
    author has no reason to know `>` is unsupported, which is why this is fixed
    in the parser rather than by reflowing the files.

    Fixed here rather than by rewriting the five SKILL.md files on purpose:
    editing a SKILL.md changes its `content_hash`, which is the `prompt_version`
    suffix the decision log uses to pin which method version produced an answer —
    so a cosmetic reflow would invalidate prd-author's recorded method version
    across the whole audit spine. Fixing the parser also disarms the trap for the
    next skill author, who has no reason to know `>` is unsupported.

    Chomping and explicit-indent indicators (`>-`, `|+`, `>2`) are accepted and
    ignored: no vendored skill uses one, and whether a block keeps its trailing
    newline is meaningless for a one-line summary. Returns {} when there is no
    frontmatter block.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    lines = text[3:end].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        # `>` / `|` alone (bar chomping/indent indicators) opens a block scalar:
        # the value is the indented run of lines that follows, up to the next
        # line that starts back at column 0 (the next key).
        if v[:1] in (">", "|") and not v[1:].strip("+-0123456789"):
            block: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and not nxt[:1].isspace():
                    break
                block.append(nxt)
                i += 1
            out[k.strip()] = _join_block(block, folded=v[0] == ">")
        else:
            out[k.strip()] = v
    return out


class UnknownSkillError(KeyError):
    """Raised when a skill id has no vendored directory."""


def _read_subdir(skill_dir: Path, sub: str) -> dict[str, str]:
    """Read every file in `skill_dir/<sub>/` into {filename: text}. Empty if
    the subdir is absent."""
    d = skill_dir / sub
    if not d.is_dir():
        return {}
    out: dict[str, str] = {}
    for f in sorted(d.iterdir()):
        if f.is_file():
            out[f.name] = f.read_text(encoding="utf-8")
    return out


def _content_hash(skill_dir: Path) -> str:
    """sha256 over all files under the skill dir (relative path + bytes),
    truncated to 12 hex chars. Deterministic across machines."""
    h = hashlib.sha256()
    for f in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(skill_dir)).encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


@lru_cache(maxsize=None)
def get_skill(skill_id: str) -> SkillSpec:
    """Load a vendored skill by id (cached in process).

    Raises UnknownSkillError if the id has no directory or no SKILL.md.
    """
    skill_dir = SKILLS_ROOT / skill_id
    method_path = skill_dir / "SKILL.md"
    if not method_path.is_file():
        available = ", ".join(list_skills()) or "(none)"
        raise UnknownSkillError(
            f"unknown skill {skill_id!r}: no SKILL.md under {skill_dir}. "
            f"Vendored skills: {available}"
        )
    method = method_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(method)
    scripts_dir = skill_dir / "scripts"
    has_scripts = scripts_dir.is_dir() and any(
        f.suffix == ".py" for f in scripts_dir.iterdir() if f.is_file()
    )
    return SkillSpec(
        id=skill_id,
        method=method,
        modules=_read_subdir(skill_dir, "modules"),
        templates=_read_subdir(skill_dir, "templates"),
        references=_read_subdir(skill_dir, "references"),
        assets=_read_subdir(skill_dir, "assets"),
        content_hash=_content_hash(skill_dir),
        description=fm.get("description", ""),
        has_scripts=has_scripts,
    )


def list_skills() -> list[str]:
    """Sorted ids of every vendored skill (a directory holding a SKILL.md)."""
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(
        d.name for d in SKILLS_ROOT.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )

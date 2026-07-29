"""Custom-skill upload parsing — slug rules, .md/.zip mapping, zip guards,
content hashing, and the DB-row → SkillSpec bridge (app/skills/custom.py).

The parser is deliberately flexible (PRD 1854 R4/R9): it maps a zip onto the
built-in skill-directory layout (SKILL.md + modules/ + references/), unwraps
the claude.ai single-folder convention, and rejects only what would create a
skill that silently fails at invocation (empty/unparseable content).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.skills.custom import (
    ParsedSkill,
    SkillParseError,
    build_spec,
    content_hash_for,
    parse_upload,
    slugify,
)
from app.skills.loader import SkillSpec


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ─── slugify ─────────────────────────────────────────────────────────────────


def test_slugify_kebab_cases_display_names():
    assert slugify("My Estimation Template") == "my-estimation-template"
    assert slugify("  PRD -- Writer!  ") == "prd-writer"
    assert slugify("Sprint—Review 2") == "sprint-review-2"


def test_slugify_empty_when_no_alphanumerics():
    assert slugify("!!! ???") == ""


# ─── .md uploads ─────────────────────────────────────────────────────────────


def test_md_upload_is_the_method():
    parsed = parse_upload("estimate.md", b"# Estimation\nDo the thing.")
    assert parsed.method.startswith("# Estimation")
    assert parsed.modules == {} and parsed.references == {}


def test_md_upload_empty_rejected():
    with pytest.raises(SkillParseError):
        parse_upload("empty.md", b"   \n\t ")


def test_md_upload_non_utf8_rejected():
    with pytest.raises(SkillParseError):
        parse_upload("binary.md", b"\xff\xfe\x00\x01MZ")


def test_unsupported_extension_rejected():
    with pytest.raises(SkillParseError):
        parse_upload("skill.pdf", b"%PDF-1.4")


# ─── .zip uploads: layout mapping ────────────────────────────────────────────


def test_zip_maps_skill_dir_layout():
    data = _zip_bytes({
        "SKILL.md": b"# Method",
        "modules/extra.md": b"module text",
        "references/guide.md": b"reference text",
        "loose.md": b"loose text",
        "assets/logo.png": b"\x89PNG",  # non-.md ignored
    })
    parsed = parse_upload("skill.zip", data)
    assert parsed.method == "# Method"
    assert parsed.modules == {"extra.md": "module text", "loose.md": "loose text"}
    assert parsed.references == {"guide.md": "reference text"}


def test_zip_unwraps_single_top_level_folder():
    """claude.ai convention: my-skill.zip → my-skill/SKILL.md."""
    data = _zip_bytes({
        "my-skill/SKILL.md": b"# Wrapped",
        "my-skill/modules/m.md": b"mod",
    })
    parsed = parse_upload("my-skill.zip", data)
    assert parsed.method == "# Wrapped"
    assert parsed.modules == {"m.md": "mod"}


def test_zip_single_md_becomes_method_regardless_of_name():
    parsed = parse_upload("s.zip", _zip_bytes({"notes.md": b"the method"}))
    assert parsed.method == "the method"


def test_zip_case_insensitive_md_detection():
    parsed = parse_upload("s.zip", _zip_bytes({"NOTES.MD": b"upper"}))
    assert parsed.method == "upper"


def test_zip_without_md_rejected():
    with pytest.raises(SkillParseError, match=r"at least one Markdown"):
        parse_upload("s.zip", _zip_bytes({"readme.txt": b"nope", "img.png": b"x"}))


def test_zip_invalid_archive_rejected():
    with pytest.raises(SkillParseError, match=r"not a valid zip"):
        parse_upload("s.zip", b"this is not a zip")


def test_zip_macos_junk_ignored():
    data = _zip_bytes({
        "__MACOSX/._SKILL.md": b"resource fork",
        ".DS_Store": b"junk",
        "SKILL.md": b"# Real",
    })
    parsed = parse_upload("s.zip", data)
    assert parsed.method == "# Real"
    assert parsed.modules == {}


def test_zip_nested_zip_never_recursed():
    inner = _zip_bytes({"inner.md": b"hidden"})
    parsed = parse_upload("s.zip", _zip_bytes({"a.md": b"outer", "deep.zip": inner}))
    assert parsed.method == "outer"
    assert parsed.modules == {}


def test_zip_empty_method_promotes_first_nonempty_module():
    """Flexible schema: an empty SKILL.md with real content elsewhere still
    yields a usable method rather than a zombie skill."""
    data = _zip_bytes({"SKILL.md": b"  ", "modules/real.md": b"actual method"})
    parsed = parse_upload("s.zip", data)
    assert parsed.method == "actual method"
    assert parsed.modules == {}


def test_zip_all_empty_mds_rejected():
    with pytest.raises(SkillParseError, match=r"empty"):
        parse_upload("s.zip", _zip_bytes({"SKILL.md": b" ", "modules/a.md": b"\n"}))


def test_zip_hostile_paths_skipped():
    data = _zip_bytes({"../escape.md": b"evil", "SKILL.md": b"# Safe"})
    parsed = parse_upload("s.zip", data)
    assert parsed.method == "# Safe"
    assert "escape.md" not in parsed.modules


# ─── content hash + spec bridge ──────────────────────────────────────────────


def test_content_hash_is_stable_and_content_sensitive():
    a = ParsedSkill(method="m", modules={"x.md": "1"})
    b = ParsedSkill(method="m", modules={"x.md": "1"})
    c = ParsedSkill(method="m", modules={"x.md": "2"})
    assert content_hash_for(a) == content_hash_for(b)
    assert content_hash_for(a) != content_hash_for(c)
    assert len(content_hash_for(a)) == 12  # same width as loader._content_hash


def test_build_spec_matches_loader_shape():
    row = {
        "slug": "my-skill",
        "method": "# M",
        "modules": {"a.md": "mod"},
        "references": {"r.md": "ref"},
        "content_hash": "abc123def456",
        "description": "does things",
    }
    spec = build_spec(row)
    assert isinstance(spec, SkillSpec)
    # The gateway's _build_method_prefix contract: id, content_hash, method,
    # modules, references — identical to a vendored skill.
    assert spec.id == "my-skill"
    assert spec.content_hash == "abc123def456"
    assert spec.method == "# M"
    assert spec.modules == {"a.md": "mod"}
    assert spec.references == {"r.md": "ref"}
    assert spec.has_scripts is False

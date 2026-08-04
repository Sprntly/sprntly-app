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
    available_slug,
    build_skill_archive,
    build_spec,
    content_hash_for,
    parse_multi_upload,
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


# ─── available_slug ──────────────────────────────────────────────────────────


def test_available_slug_keeps_the_base_when_free():
    assert available_slug("my-estimator", ["prd-author", "roadmap"]) == "my-estimator"
    assert available_slug("my-estimator", []) == "my-estimator"


def test_available_slug_numbers_past_every_taken_id():
    # The suffix series starts at -2 and skips whatever is already spoken for,
    # so a colliding upload never lands on a trigger someone else answers.
    assert available_slug("prd-author", ["prd-author"]) == "prd-author-2"
    assert available_slug("prd-author", ["prd-author", "prd-author-2"]) == "prd-author-3"
    assert (
        available_slug("prd-author", ["prd-author", "prd-author-2", "prd-author-4"])
        == "prd-author-3"
    )


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


# ─── .zip uploads: MULTI-skill archives ──────────────────────────────────────
#
# A zipped skills/ directory (one folder per skill, the Claude Code layout) is
# N skills, not one. parse_multi_upload owns that case and returns None for
# everything the single-skill parser above has always handled — the tests in
# this section pin both halves of that split, because the None half is what
# keeps every test above true.


def _fm(name: str, description: str, body: str = "Do the thing.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# Heading\n\n{body}\n".encode()


def test_multi_zip_splits_into_one_skill_per_folder():
    data = _zip_bytes({
        "skills/sprint-planner/SKILL.md": _fm("sprint-planner", "Plans a sprint."),
        "skills/sprint-planner/modules/deep.md": b"module text",
        "skills/sprint-planner/references/source.md": b"reference text",
        "skills/pricing-review/SKILL.md": _fm("pricing-review", "Reviews pricing."),
    })
    archive = parse_multi_upload("skills.zip", data)
    assert archive is not None
    assert archive.skipped == []
    by_path = {s.path: s for s in archive.skills}
    assert sorted(by_path) == ["pricing-review", "sprint-planner"]

    planner = by_path["sprint-planner"]
    assert planner.name == "Sprint Planner"
    assert planner.description == "Plans a sprint."
    assert planner.parsed.method.startswith("---\nname: sprint-planner")
    assert planner.parsed.modules == {"deep.md": "module text"}
    # The old single-skill parser only recognised a ROOT-level references/, so
    # this file used to land in modules — per-skill roots are what fix it.
    assert planner.parsed.references == {"source.md": "reference text"}

    # No cross-contamination: the second skill got its own SKILL.md and none of
    # the first one's supporting files.
    pricing = by_path["pricing-review"]
    assert pricing.parsed.modules == {} and pricing.parsed.references == {}
    assert "Plans a sprint" not in pricing.parsed.method


def test_multi_zip_nested_skill_is_its_own_and_leaves_its_ancestor():
    data = _zip_bytes({
        "sales/SKILL.md": _fm("sales", "Sells."),
        "sales/modules/pitch.md": b"pitch",
        "sales/discovery/SKILL.md": _fm("discovery", "Discovers."),
        "sales/discovery/modules/questions.md": b"questions",
    })
    archive = parse_multi_upload("sales.zip", data)
    assert archive is not None
    # `sales/` is the single top-level folder, so it is unwrapped exactly as a
    # one-skill archive's is — the outer skill ends up at the archive root and
    # keeps its name from the folder it was unwrapped out of.
    by_path = {s.path: s for s in archive.skills}
    assert sorted(by_path) == ["", "discovery"]
    assert by_path[""].name == "Sales"
    # Deepest root wins: the nested skill's files belong to IT, and the
    # ancestor keeps only its own.
    assert by_path[""].parsed.modules == {"pitch.md": "pitch"}
    assert "Discovers" not in by_path[""].parsed.method
    assert by_path["discovery"].parsed.modules == {"questions.md": "questions"}


def test_multi_zip_root_skill_is_named_from_the_wrapper_folder():
    # A wrapper folder + a nested skill: the root skill has no folder of its
    # own, so its fallback name is the wrapper the archive was zipped from.
    data = _zip_bytes({
        "team-methods/SKILL.md": b"# Team method\n\nHow we run reviews.\n",
        "team-methods/extras/SKILL.md": b"# Extras\n\nThe extras method.\n",
    })
    archive = parse_multi_upload("download.zip", data)
    assert archive is not None
    names = {s.path: s.name for s in archive.skills}
    assert names == {"": "Team Methods", "extras": "Extras"}


def test_multi_zip_root_skill_falls_back_to_the_zip_filename():
    data = _zip_bytes({
        "SKILL.md": b"# Root\n\nThe root method.\n",
        "other/SKILL.md": b"# Other\n\nThe other method.\n",
    })
    archive = parse_multi_upload("our-methods.zip", data)
    assert archive is not None
    assert {s.path: s.name for s in archive.skills}[""] == "Our Methods"


def test_multi_zip_description_falls_back_to_the_first_paragraph():
    data = _zip_bytes({
        "a/SKILL.md": b"# Alpha\n\nScores features by reach and confidence.\nSecond line.\n\nLater paragraph.\n",
        "b/SKILL.md": _fm("b", "Bee."),
    })
    archive = parse_multi_upload("s.zip", data)
    assert archive is not None
    alpha = next(s for s in archive.skills if s.path == "a")
    # The heading is skipped (it repeats the name) and the first block of prose
    # is folded onto one line — a description is one line everywhere it shows.
    assert alpha.description == "Scores features by reach and confidence. Second line."
    assert alpha.name == "A"


def test_multi_zip_frontmatter_block_scalar_description_survives():
    # `description: >` is the vendored skills' own convention; a naive parser
    # captures ">" as the whole description (the loader bug this reuses the fix
    # for).
    folded = (
        b"---\nname: Folded Skill\ndescription: >\n  Plans the quarter,\n"
        b"  end to end.\n---\n\nBody.\n"
    )
    archive = parse_multi_upload("s.zip", _zip_bytes({
        "folded/SKILL.md": folded,
        "plain/SKILL.md": _fm("plain", "Plain."),
    }))
    assert archive is not None
    folded_skill = next(s for s in archive.skills if s.path == "folded")
    assert folded_skill.description == "Plans the quarter, end to end."
    # A frontmatter name that already reads as a display name is left alone.
    assert folded_skill.name == "Folded Skill"


def test_multi_zip_frontmatter_survives_bom_crlf_and_a_leading_blank_line():
    # Frontmatter detection demands `---` at character zero — GitHub's renderer
    # applies the same rule — but real files open with a Notepad BOM, CRLF
    # endings, or a stray blank line above the block. One such file cost a
    # repo's ticket-breakdown skill its own name: the parser saw no
    # frontmatter and labelled it after the repo instead. Identity derivation
    # tolerates all three; the stored method still keeps the author's bytes.
    messy = (
        "\ufeff\r\n---\r\nname: ticket-breakdown\r\n"
        "description: Breaks work into tickets.\r\n---\r\n\r\nBody.\r\n"
    ).encode("utf-8")
    archive = parse_multi_upload("s.zip", _zip_bytes({
        "messy/SKILL.md": messy,
        "plain/SKILL.md": _fm("plain", "Plain."),
    }))
    assert archive is not None
    skill = next(s for s in archive.skills if s.path == "messy")
    assert skill.name == "Ticket Breakdown"
    assert skill.description == "Breaks work into tickets."


def test_multi_zip_skips_the_unusable_folder_and_keeps_the_rest():
    data = _zip_bytes({
        "good/SKILL.md": _fm("good", "Good one."),
        "empty/SKILL.md": b"   \n",
        # A folder name with no letters or digits slugifies to nothing, so it
        # cannot become a trigger — the only way a skill ends up nameless,
        # since the folder normally supplies the name.
        "!!!/SKILL.md": b"Has prose but no name anywhere.\n",
        "undescribed/SKILL.md": b"# Undescribed\n\n## Only headings\n",
    })
    archive = parse_multi_upload("s.zip", data)
    assert archive is not None
    assert [s.path for s in archive.skills] == ["good"]
    reasons = {s.path: s.reason for s in archive.skipped}
    assert set(reasons) == {"empty", "!!!", "undescribed"}
    assert "empty" in reasons["empty"]
    # Each reason names the fix, so a person can act on it without guessing.
    assert "name:" in reasons["!!!"]
    assert "description:" in reasons["undescribed"]


def test_multi_zip_caps_derived_name_and_description():
    long_name = "n" * 200
    long_desc = "d" * 2000
    archive = parse_multi_upload("s.zip", _zip_bytes({
        "a/SKILL.md": _fm(long_name, long_desc),
        "b/SKILL.md": _fm("b", "Bee."),
    }))
    assert archive is not None
    a = next(s for s in archive.skills if s.path == "a")
    assert len(a.name) == 64 and len(a.description) == 1024


def test_single_skill_zips_are_not_multi():
    """The None half of the split — every layout the single-skill parser has
    always owned must still reach it, or the contract above changes."""
    assert parse_multi_upload("s.md", b"# method") is None
    assert parse_multi_upload("s.zip", _zip_bytes({
        "SKILL.md": b"# Method",
        "modules/extra.md": b"module text",
        "references/guide.md": b"reference text",
        "loose.md": b"loose text",
    })) is None
    # The claude.ai single-folder convention.
    assert parse_multi_upload("my-skill.zip", _zip_bytes({
        "my-skill/SKILL.md": b"# Wrapped",
        "my-skill/modules/m.md": b"mod",
    })) is None
    # No SKILL.md at all: nothing to discover, so the shallowest-.md promotion
    # in parse_upload still decides.
    assert parse_multi_upload("s.zip", _zip_bytes({
        "notes.md": b"the method",
        "deep/more.md": b"more",
    })) is None


def test_multi_zip_hostile_and_junk_members_never_become_skills():
    data = _zip_bytes({
        "__MACOSX/nested/SKILL.md": b"resource fork",
        "../escape/SKILL.md": b"evil",
        "a/SKILL.md": _fm("a", "Ay."),
        "b/SKILL.md": _fm("b", "Bee."),
    })
    archive = parse_multi_upload("s.zip", data)
    assert archive is not None
    assert sorted(s.path for s in archive.skills) == ["a", "b"]


# ─── per-skill archive synthesis ─────────────────────────────────────────────


def test_build_skill_archive_keeps_a_method_only_skill_as_markdown():
    data, ext = build_skill_archive(ParsedSkill(method="# Only the method\n"))
    assert ext == "md"
    assert data == b"# Only the method\n"


def test_build_skill_archive_round_trips_through_the_parser():
    parsed = ParsedSkill(
        method="# Method\n",
        modules={"extra.md": "module text"},
        references={"guide.md": "reference text"},
    )
    data, ext = build_skill_archive(parsed)
    assert ext == "zip"
    # What we stage as one skill's original must read back as that same skill —
    # otherwise a re-upload of a downloaded skill would parse differently.
    again = parse_upload("skill.zip", data)
    assert again == parsed


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

"""Phase 1 — the vendored skill catalog loads and the manifest is coherent.

Guards the "install a skill = drop a folder" property: every folder under
backend/skills/ loads, carries a frontmatter description, is categorized, and
appears in the manifest with the right routability.
"""
from __future__ import annotations

from app.skills.catalog import (
    NON_ROUTABLE,
    SKILL_CATEGORY,
    build_manifest,
    humanize_label,
    routable_manifest,
)
from app.skills.loader import get_skill, list_skills

EXPECTED_MIN_SKILLS = 56  # PM-Agent-Skills pack + repo-only public-feedback-report


def test_all_installed_skills_load():
    ids = list_skills()
    assert len(ids) >= EXPECTED_MIN_SKILLS
    for sid in ids:
        spec = get_skill(sid)  # raises UnknownSkillError if SKILL.md missing
        assert spec.method.strip(), f"{sid} has empty SKILL.md"
        assert spec.description, f"{sid} has no frontmatter description"


MIN_ROUTER_DESCRIPTION_CHARS = 60


def test_every_routable_skill_has_a_usable_router_description():
    """A routable skill the router cannot read is invisible to it.

    `_router_menu()` renders one `- <id>: <description>` line per routable skill
    and that menu is the ONLY semantic signal the LLM router gets. prd-author
    shipped with `description: >` in its frontmatter, which the no-YAML-dep
    parser reduced to the single character ">", so its menu line was
    `- prd-author: >` — unpickable on meaning, and reachable only because
    `skill_router`'s regex fast-path caught PRD phrasings first.

    `test_all_installed_skills_load` already asserts the description is truthy,
    which is exactly why this went unnoticed: ">" is truthy. A length floor is
    what actually catches it, so the next author who writes a block scalar the
    parser mishandles gets a red build instead of a silent hole in the menu.
    60 chars is comfortably below every real description (the shortest genuine
    one is well clear of it) and far above any stray marker.
    """
    too_short = [
        (s["id"], s["description"])
        for s in routable_manifest()
        if len(s["description"].strip()) < MIN_ROUTER_DESCRIPTION_CHARS
    ]
    assert too_short == [], (
        "routable skills whose router menu line carries no usable description "
        f"(likely an unparsed YAML block scalar in SKILL.md frontmatter): {too_short}"
    )


def test_router_menu_lines_carry_each_skill_description():
    """The menu the router actually sees — proving the fix at the surface that
    matters, not just at the loader."""
    from app.qa_agent import _router_menu

    menu = _router_menu()
    assert "- prd-author: >" not in menu, "prd-author menu line lost its description"
    assert "Product Requirements Document" in menu


def test_manifest_covers_every_installed_skill():
    manifest_ids = {s["id"] for s in build_manifest()}
    assert manifest_ids == set(list_skills())


def test_every_skill_is_categorized():
    missing = [s["id"] for s in build_manifest() if s["category"] == "Uncategorized"]
    assert missing == [], f"uncategorized skills: {missing}"
    # the category map shouldn't reference skills that aren't installed
    stale = set(SKILL_CATEGORY) - set(list_skills())
    assert stale == set(), f"category map references uninstalled skills: {stale}"


def test_non_routable_excluded_from_routable_manifest():
    routable_ids = {s["id"] for s in routable_manifest()}
    for sid in NON_ROUTABLE:
        assert sid in list_skills(), f"{sid} should still be installed"
        assert sid not in routable_ids, f"{sid} must not be router-pickable"


def test_has_scripts_flag_matches_disk():
    by_id = {s["id"]: s for s in build_manifest()}
    # The four script-bearing skills per the PM pack.
    for sid in ("prioritize", "experiment-design", "saas-metrics-diagnosis", "prd-critique"):
        assert by_id[sid]["has_scripts"] is True, f"{sid} should report has_scripts"
    # A purely-markdown skill should not.
    assert by_id["roadmap"]["has_scripts"] is False


def test_humanize_label_uppercases_acronyms():
    assert humanize_label("prd-author") == "PRD author"
    assert humanize_label("okr-nct") == "OKR NCT"
    assert humanize_label("roadmap") == "Roadmap"

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

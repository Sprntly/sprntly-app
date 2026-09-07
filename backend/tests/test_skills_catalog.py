"""Phase 1 — the vendored skill library loads and is coherent.

`app/skills/catalog.py` is gone with the built-in chat-routing layer: category,
routability and the router manifest were policy over a ~78-skill library, and
the library is now a NINE-skill keep-list bound by name from the pipelines that
need it. `humanize_label` outlived that module (it labels captured reports and
the public share page) and lives in `app/labels.py`.

What is still worth guarding here is the property that never depended on
routing: every vendored skill loads, and every one of them carries a
frontmatter description a reader — human or model — can actually use.
"""
from __future__ import annotations

from app.labels import humanize_label
from app.skills.loader import get_skill, list_skills

# The keep-list, stated exactly. This is a CLOSED set, not a floor: the whole
# point of the trim is that adding a skill back is a decision, not a drop-a-
# folder side effect. Five artifact methods bound at their own call sites
# (prd_runner, evidence_kg / evidence_runner, stories/generate, synthesis/agent)
# plus the four connector-extraction contracts kg_ingest binds by provider.
KEEPERS = {
    "prd-author",
    "implementation-spec",
    "evidence-brief",
    "user-stories",
    "top-insights",
    "jira-extraction",
    "hubspot-extraction",
    "clickup-extraction",
    "roadmap-extraction",
    # The two scheduled report methods, vendored 2026-08-20. The engines have
    # named these since the monthly reports shipped; until now the id matched
    # no directory and both ran METHOD-LESS (`+bare` in prompt_version).
    "competitive-intelligence-review",
    "public-feedback-report",
}


def test_every_keeper_is_still_vendored():
    """The MACHINERY half of the library, still a closed set.

    Equality against the whole tree was the right guard while the library WAS
    the keep-list. The method library is back (~69 docs, reachable only by an
    explicit `/<slug>`), so equality would fail on every method and guard
    nothing. What still matters is that no keeper goes missing: each is bound
    by name from its own runner, and losing one degrades that runner to a
    method-less `+bare` call rather than raising, so the damage would be
    invisible."""
    missing = sorted(KEEPERS - set(list_skills()))
    assert not missing, f"pipeline-bound skills missing from the library: {missing}"


def test_the_library_partitions_into_machinery_and_user_invocable_methods():
    """Every vendored skill is exactly one of two things, and nothing falls
    between them.

    That partition IS the product rule: a keeper runs when its pipeline calls
    it and can never be typed; a method runs when — and only when — a person
    types `/<slug>`. A skill that answered "no" to both would be dead weight
    nothing could ever reach; one that answered "yes" to both would be a
    machinery prompt a user could summon onto an ordinary chat turn."""
    from app.qa_agent import is_user_invocable_builtin

    for sid in sorted(list_skills()):
        machinery = sid in KEEPERS
        method = is_user_invocable_builtin(sid)
        assert machinery != method, (
            f"{sid} is {'both machinery and user-invocable' if machinery else 'neither'}"
        )


def test_all_installed_skills_load():
    for sid in sorted(KEEPERS):
        spec = get_skill(sid)  # raises UnknownSkillError if SKILL.md missing
        assert spec.method.strip(), f"{sid} has empty SKILL.md"
        assert spec.description, f"{sid} has no frontmatter description"


MIN_DESCRIPTION_CHARS = 60


def test_every_vendored_skill_has_a_usable_description():
    """A description the frontmatter parser mangled is a silent hole.

    RE-POINTED, not deleted, when the router menu went away. The trap this
    guards is in `loader._parse_frontmatter`, not in the router: `prd-author`
    shipped with `description: >` (a YAML block scalar), which the no-YAML-dep
    parser once reduced to the single character ">". `test_all_installed_skills
    _load` above asserts the description is truthy, which is exactly why that
    went unnoticed — ">" is truthy. A LENGTH FLOOR is what actually catches it.

    Still load-bearing with the router gone, because the descriptions it guards
    still have readers. Five of the nine keepers use a block scalar, including
    all four KG-extraction skills, whose descriptions
    `app.graph.evals.SKILL_EXPECTED_VOCAB` is maintained against — a mangled
    one there degrades the extraction contract Babajide made unconditional.

    60 chars is comfortably below every real description and far above any
    stray marker.
    """
    too_short = [
        (sid, get_skill(sid).description)
        for sid in sorted(KEEPERS)
        if len(get_skill(sid).description.strip()) < MIN_DESCRIPTION_CHARS
    ]
    assert too_short == [], (
        "vendored skills whose frontmatter description carries no usable text "
        f"(likely an unparsed YAML block scalar in SKILL.md): {too_short}"
    )


def test_prd_author_description_survived_the_block_scalar():
    """The original bug, at the surface that matters."""
    description = get_skill("prd-author").description
    assert description.strip() != ">"
    assert "Product Requirements Document" in description


def test_humanize_label_uppercases_acronyms():
    assert humanize_label("prd-author") == "PRD author"
    assert humanize_label("okr-nct") == "OKR NCT"
    assert humanize_label("roadmap") == "Roadmap"

"""The restored built-in method library is SUMMONED, never SELECTED.

The ~78-skill library was cut (#1024) because chat picked a method for every
turn and applied it to questions that merely resembled one — "did the prototype
ship last week?" came back as a 40 KB Voice-of-Customer document. Bringing the
library back keeps that from recurring by construction rather than by prompt:
a built-in method has exactly ONE door, `/<slug>` typed by a person, and every
path that would choose a skill on the user's behalf refuses vendored ids.

So this file is mostly negative space. The valuable assertions are the ones
proving a built-in CANNOT be reached — one per selecting path, because each is a
separate mechanism and a regression in any one of them re-opens the failure the
trim was for.

Custom skills are the deliberate asymmetry: a company's own uploads stay
auto-routable, which is what the owner asked for and what
`_custom_skill_block` / the planner's `company_skill_id` still serve.
"""
from __future__ import annotations

import pytest

from app.qa_agent import (
    _PIPELINE_BOUND_SKILLS,
    _invocable,
    _routable,
    is_user_invocable_builtin,
)
from app.skill_router import PIPELINE_SKILLS
from app.skills.loader import list_skills


def _methods() -> list[str]:
    return sorted(s for s in list_skills() if is_user_invocable_builtin(s))


# ── The library came back ────────────────────────────────────────────────────

def test_the_method_library_is_actually_restored():
    """A floor, not equality: the point of the restore is that the library
    grows again. Sixty-nine came back; asserting a count would turn every
    future addition into a failing test for no reason."""
    assert len(_methods()) >= 60


@pytest.mark.parametrize("sid", ["working-backwards", "pre-mortem", "jobs-to-be-done",
                                 "lean-canvas", "prioritize", "retrospective"])
def test_named_methods_are_summonable(sid):
    """A spot-check in the user's own vocabulary — these are the slugs someone
    would actually type."""
    assert is_user_invocable_builtin(sid)


# ── …and cannot be reached by anything that chooses FOR the user ─────────────

def test_no_builtin_method_is_auto_routable():
    """`_routable` is the single gate every selecting path funnels through: the
    LLM classifier's company pick, the regex tier's custom pick, the planner's
    `company_skill_id`, and the interception contest. One refusal covers all
    four, which is why this is the assertion that matters most in the file."""
    for sid in _methods():
        assert not _routable(sid, "ent-1"), f"{sid} is auto-selectable"


def test_the_planner_cannot_name_a_builtin_method_either():
    """The planner reaches skills through the same gate — `ask_planner`'s
    `company_skill_id` is validated with `_routable` before it is honoured — so
    a hallucinated `company_skill_id: "pre-mortem"` is dropped rather than run.
    Pinned separately from the router because the two are different call sites
    and only the shared gate makes them agree."""
    assert not _routable("pre-mortem", "ent-1")
    assert not _routable("working-backwards", "ent-1")


def test_a_builtin_method_is_not_a_pipeline():
    """Pipelines ARE auto-picked, deliberately — they do live fetches and paid
    web sweeps, and the owner's instruction was that what already worked keeps
    working. The two sets must stay disjoint, or a method would inherit that
    auto-pick."""
    assert not (set(_methods()) & set(PIPELINE_SKILLS))


# ── The nine bound by their own runners are untouched ────────────────────────

@pytest.mark.parametrize("sid", sorted(_PIPELINE_BOUND_SKILLS))
def test_pipeline_bound_skills_are_not_user_summonable(sid):
    """`/prd-author` still resolves to nothing, exactly as before the restore.
    Typing it is not a request for a method prompt — it names an engine that
    runs from its own route with its own inputs, and answering the trigger
    would hand a user a prompt fragment instead of the document they meant."""
    assert not is_user_invocable_builtin(sid)
    assert not _routable(sid, "ent-1")


def test_every_pipeline_bound_skill_is_present_on_disk():
    """Drift guard for `_PIPELINE_BOUND_SKILLS`. If one of these were deleted,
    the set would still exclude it from the slash path while nothing ran it
    either — a skill in limbo. If one were RENAMED, the stale entry would stop
    excluding the new name and it would quietly become user-summonable."""
    missing = sorted(_PIPELINE_BOUND_SKILLS - set(list_skills()))
    assert not missing, f"listed as pipeline-bound but not vendored: {missing}"


# ── Explicit invocation is what `_invocable` is for ──────────────────────────

def test_invocable_must_NOT_admit_a_builtin_method():
    """The regression that nearly shipped, pinned so it cannot come back.

    Folding methods into `_invocable` looks like the obvious way to let
    `pinned_skill` honour one — and it is wrong, because `_invocable` is also
    the gate on two paths that choose FOR the user: `ask_planner._gate_pipeline`
    and the LLM router's pipeline pick. Widening it let the planner return
    `pipeline_id: "market-structure"` and have it honoured, which is exactly the
    failure this change exists to prevent.

    So explicit invocation is widened at its own two call sites instead, and
    this predicate stays narrow."""
    for sid in _methods():
        assert not _invocable(sid, "ent-1"), (
            f"{sid} is reachable through _invocable, which the planner's "
            f"pipeline gate also consults"
        )


def test_invocable_still_accepts_pipelines_and_still_refuses_junk():
    assert _invocable("competitive-intelligence-review", "ent-1")
    assert not _invocable("not-a-real-skill", "ent-1")
    assert not _invocable("", "ent-1")


def test_the_builtin_check_needs_no_tenant_and_no_db():
    """A vendored method is a file on disk, not a row, so the explicit-door
    check answers without a company and without a round-trip. (A custom skill
    genuinely does need one; that asymmetry is the point, and it is why the two
    predicates stay separate.)"""
    assert is_user_invocable_builtin("pre-mortem")
    assert not is_user_invocable_builtin("not-a-real-skill")


# ── The honesty rule the restore had to satisfy (#1018) ──────────────────────

def test_no_restored_method_tells_the_model_to_run_a_file_it_never_gets():
    """`_build_method_prefix` folds SKILL.md, `modules/` and `references/*` into
    the prompt — never `scripts/`, and `skills/scripts.py` was deleted outright
    with the library. Four restored methods still said "run `scripts/score.py`"
    and the like; left alone the model cannot comply, so it invents what it
    thinks the script returned. Exactly the defect #1018 fixed for the five
    surviving skills, which restoring 69 files could quietly re-open."""
    import re

    from app.skills.loader import get_skill

    offenders = [
        sid for sid in _methods()
        if re.search(r"scripts/[A-Za-z0-9_]+\.py", get_skill(sid).method)
    ]
    assert not offenders, f"methods naming a deleted script: {offenders}"

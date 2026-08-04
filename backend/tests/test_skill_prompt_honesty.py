"""A skill may only tell the model to read what the model is actually given.

THE BUG THIS GUARDS. `gateway._build_method_prefix` folds a bound skill's
`SKILL.md`, its `modules/` and its `references/*` into the prompt — and
deliberately NOT `templates/`, `assets/` or `examples/` ("the app renders from
the structured payload, so the template is a downstream view, not a prompt
input"). Nothing checked that the SKILL.md text agreed with that, and four of
the five vendored PM skills had drifted:

  * `implementation-spec` called `templates/implementation-spec-template.md`
    NORMATIVE while never sending it, so the model reconstructed the B0-B9
    skeleton from prose.
  * `evidence-brief` said "the five `examples/` are the authoritative rendering
    reference: match their markup" — of files it never received.
  * `user-stories` named an HTML file "the source of truth for layout" in a
    forced-tool JSON call that cannot emit markup at all.
  * `top-insights` said "**Every run produces a rendered HTML file** … a run
    that emits only the structured object has not finished" — an instruction
    the schema makes UNSATISFIABLE, since structured-only is the sole possible
    output.

A dangling path is bad; an unsatisfiable instruction is worse, because the model
cannot comply and will improvise something to fill the gap. These tests pin the
contract in both directions: what a skill claims is injected must be injected,
and a skill whose call is structured must not claim to render.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.skills.loader import get_skill

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

# The subdirectories `_build_method_prefix` actually folds into the prompt.
INJECTED_SUBDIRS = ("references",)
# Present on disk, never sent to the model. `templates/` and `assets/` reach a
# model only when a runner explicitly loads one onto a cacheable prefix (Part A
# and Part B of the PRD do; nothing else does).
NOT_INJECTED_SUBDIRS = ("examples", "assets", "templates")

# The five PM skills whose output the keep-list decision was made to protect.
PM_SKILLS = (
    "prd-author", "implementation-spec", "user-stories", "evidence-brief",
    "top-insights",
)

# Skills bound to a call that passes `json_schema=` — a forced-tool JSON call.
# The model emits a structured object and literally cannot return markup, so a
# rendering instruction is not merely unhelpful, it is impossible to satisfy.
STRUCTURED_OUTPUT_SKILLS = frozenset({"user-stories", "top-insights"})

# Runners that explicitly load a template onto the cacheable prefix, which is
# what makes a "skeleton in templates/..." claim legitimate for these two.
TEMPLATE_INJECTED_BY_RUNNER = {
    "prd-author": "prd-template.html",
    "implementation-spec": "implementation-spec-template.md",
}

# Stylesheets the SERVER applies to the finished document (`html_style.
# inject_canonical_css`), after generation. Naming one is honest precisely
# because the skill tells the model NOT to produce it — "leave the `<style>`
# block EMPTY, Sprntly injects the canonical stylesheet". That is a statement
# about what happens downstream, not an instruction to go read a file, so it is
# exempt from the read-instruction rule rather than a hole in it.
SERVER_INJECTED_ASSETS = frozenset({"prd.css", "evidence.css"})

_PATH_REF = re.compile(r"`(examples|assets|templates|references|modules)/([^`]+)`")


def _skill_md(skill_id: str) -> str:
    return (SKILLS_ROOT / skill_id / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill_id", PM_SKILLS)
def test_every_path_a_skill_names_actually_exists(skill_id):
    """A path that does not resolve is a typo the model cannot report."""
    for sub, name in _PATH_REF.findall(_skill_md(skill_id)):
        assert (SKILLS_ROOT / skill_id / sub / name).exists(), (
            f"{skill_id}/SKILL.md names {sub}/{name}, which is not in the skill"
        )


@pytest.mark.parametrize("skill_id", PM_SKILLS)
def test_a_read_instruction_only_ever_points_at_injected_material(skill_id):
    """"read X", "score against X", "match X" — X must be in the prompt.

    Only `references/` is folded in by the gateway. A read-verb aimed at
    `examples/` or `assets/` is an instruction the model cannot follow, so it
    fills the gap by inventing what it thinks the file said.
    """
    offenders = []
    for line in _skill_md(skill_id).splitlines():
        if not re.search(
            r"\b(read|consult|open|match|align|score against|copying|copy)\b",
            line, re.I,
        ):
            continue
        for sub, name in _PATH_REF.findall(line):
            if sub in NOT_INJECTED_SUBDIRS:
                # Legitimate iff a runner explicitly injects that template…
                if TEMPLATE_INJECTED_BY_RUNNER.get(skill_id) == name:
                    continue
                # …or the skill is describing a stylesheet the SERVER applies
                # after generation, which the model is told not to write.
                if sub == "assets" and name in SERVER_INJECTED_ASSETS:
                    continue
                offenders.append(f"{sub}/{name} :: {line.strip()[:110]}")
    assert not offenders, (
        f"{skill_id}/SKILL.md tells the model to read material that is never "
        f"sent to it:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("skill_id", sorted(STRUCTURED_OUTPUT_SKILLS))
def test_a_structured_skill_does_not_claim_to_render(skill_id):
    """These run as forced-tool JSON calls. Telling them to produce an HTML file
    is unsatisfiable — and `top-insights` went further and called a
    structured-only run unfinished, which is the only output it can produce."""
    text = _skill_md(skill_id)
    for pattern in (
        r"produces? a rendered HTML file",
        r"has not finished",
        r"populating `assets/[^`]+` is part of what it means",
    ):
        assert not re.search(pattern, text, re.I), (
            f"{skill_id} emits structured JSON; it cannot satisfy {pattern!r}"
        )


@pytest.mark.parametrize(
    "skill_id,filename", sorted(TEMPLATE_INJECTED_BY_RUNNER.items())
)
def test_the_runner_injected_templates_are_loadable(skill_id, filename):
    """Both PRD legs load their skeleton through `get_skill(...).templates`.
    A rename would otherwise surface as a KeyError mid-generation."""
    assert filename in get_skill(skill_id).templates


def test_evidence_examples_are_injected_not_merely_shipped():
    """`evidence-brief` is the one PM skill whose model output IS raw HTML, so
    "match their markup" is a real instruction — which means the briefs have to
    live under `references/` where the gateway folds them in. Shipping them in
    `examples/` looked identical on disk and sent nothing."""
    refs = get_skill("evidence-brief").references
    briefs = [n for n in refs if n.endswith(".html")]
    assert len(briefs) == 5, f"expected the 5 calibration briefs, got {briefs}"
    assert not (SKILLS_ROOT / "evidence-brief" / "examples").exists(), (
        "the briefs moved to references/; an examples/ dir here means a split "
        "set, half of which the model never sees"
    )

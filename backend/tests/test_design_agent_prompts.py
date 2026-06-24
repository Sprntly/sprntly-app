"""Tests for app.design_agent.prompts — the scaffold-side prompts (AD8).

Covers shape (template version, prompt length), the 9-section skeleton
(agent-build-research.md §2.2), the AD4 anchor-id prohibition, the AD17
action-vs-exit-sentinel framing, the AD3 stack hard-constraints, the
shadcn component inventory, the user-template placeholders + render
helper, and cache-prefix readiness.

Assertions are substring-based by design (AC13 — no string-equality
checks against the full prompt, which would brittle-break on every word
edit). The module is pure constants + one pure helper; no network, no
DB, no env reads (AC12).
"""
from __future__ import annotations

import re
from pathlib import Path

from app.design_agent import prompts as p

SYS = p.DESIGN_AGENT_SCAFFOLD_SYSTEM

_SECTION_MARKERS = [
    "[1] ROLE",
    "[2] STACK",
    "[3] WORKFLOW",
    "[4] TOOLS",
    "[5] DESIGN SYSTEM",
    "[6] GOTCHAS",
    "[7] OUTPUT FORMAT",
    "[8] WHEN TO ASK",
    "[9] STABLE JSX IDs",
]


def _section(n: int) -> str:
    """Return the text of section [n] (1-indexed) from the system prompt."""
    start = SYS.index(_SECTION_MARKERS[n - 1])
    end = SYS.index(_SECTION_MARKERS[n]) if n < len(_SECTION_MARKERS) else len(SYS)
    return SYS[start:end]


# ---- creation ---------------------------------------------------------------

def test_template_version_is_current():
    # v2 = scaffold-completeness chore #65 (inventory synced to the real vendored
    # prototype-runtime/src/components/ui/* set); v3 = iterate spine (the
    # iterate-aware template family lands); v4 = manual-edit commit-back (the
    # DESIGN_AGENT_MANUAL_EDIT_SYSTEM commit-only family lands); v5 =
    # recreate-discipline append (codebase-context wave); v6 = scoped-interactivity
    # axis appended to the recreate discipline (changes which handlers the agent
    # emits → template-invalidating). Each invalidates cached prototypes so they
    # regenerate.
    assert p.DESIGN_AGENT_TEMPLATE_VERSION == 7
    assert isinstance(p.DESIGN_AGENT_TEMPLATE_VERSION, int)


def test_scaffold_system_present_and_nonempty():
    assert isinstance(SYS, str)
    assert len(SYS) > 2000


def test_scaffold_user_template_present():
    assert isinstance(p.DESIGN_AGENT_SCAFFOLD_USER_TEMPLATE, str)
    assert p.DESIGN_AGENT_SCAFFOLD_USER_TEMPLATE.strip()


# ---- required sections (agent-build-research.md §2.2 skeleton) --------------

def test_scaffold_system_has_all_9_sections():
    for marker in _SECTION_MARKERS:
        assert marker in SYS, f"missing section marker: {marker}"


# ---- AD4 (anchor-id prohibition) --------------------------------------------

def test_scaffold_system_forbids_manual_anchor_id_emission():
    sec9 = _section(9)
    assert "data-anchor-id" in sec9
    assert "do not emit" in sec9.lower()


def test_scaffold_system_explains_vite_plugin_auto_apply():
    sec9 = _section(9)
    assert "Vite plugin" in sec9
    # The prompt explains the attribute is applied AUTOMATICALLY at build time.
    assert "AUTOMATICALLY" in sec9 or "auto-applied" in sec9.lower()


# ---- AD17 (action vs exit-sentinel framing) ---------------------------------

def test_scaffold_system_lists_all_6_action_tools():
    sec4 = _section(4)
    for tool in ("view", "write", "line_replace", "search", "fetch_figma", "read_console"):
        assert tool in sec4, f"action tool not named in section [4]: {tool}"


def test_scaffold_system_distinguishes_action_vs_sentinel():
    sec4 = _section(4)
    assert "Action tools" in sec4
    assert "exit-sentinel" in sec4.lower()


def test_scaffold_system_does_not_register_a_clarifying_question_tool():
    # Section [8] may reference a FUTURE clarifying_question tool ...
    sec8 = _section(8)
    assert "future version" in sec8.lower()
    # ... but it must NOT be registered as one of the P1 action tools.
    assert "clarifying_question" not in _section(4)


# ---- stack hard constraints (AD3) -------------------------------------------

def test_scaffold_system_forbids_nextjs_vue_svelte():
    sec2 = _section(2)
    # AC6: all eight forbidden technologies named explicitly in the
    # "Do NOT use" framing.
    assert "Do NOT use:" in sec2
    for forbidden in (
        "Next.js",
        "Vue",
        "Svelte",
        "styled-components",
        "emotion",
        "material-ui",
        "ant-design",
        "framer-motion",
    ):
        assert forbidden in sec2, f"forbidden tech not named in section [2]: {forbidden}"


def test_scaffold_system_pins_react_vite_typescript_tailwind_shadcn():
    sec2 = _section(2)
    assert "React" in sec2
    assert "Vite" in sec2
    assert "TypeScript" in sec2
    assert "Tailwind" in sec2
    assert "shadcn/ui" in sec2


# ---- component inventory ----------------------------------------------------

def test_shadcn_inventory_has_at_least_20_components():
    before_icons = p.SHADCN_COMPONENT_INVENTORY.split("Icons:")[0]
    names = [
        n
        for n in re.findall(r"\b[A-Z][a-zA-Z]+\b", before_icons)
        if n != "Available"
    ]
    assert len(names) >= 20


def test_shadcn_inventory_present_in_system_prompt():
    assert "Accordion, Alert, AlertDialog" in SYS


# ---- user template ----------------------------------------------------------

def test_user_template_has_five_placeholders():
    template = p.DESIGN_AGENT_SCAFFOLD_USER_TEMPLATE
    found = set(re.findall(r"\{(\w+)\}", template))
    # `codebase_repo` was added as an additive context block (existing
    # placeholders unchanged) — the optional connected-repo "match this
    # codebase" line, rendered by render_scaffold_user.
    assert found == {
        "prd_md", "target_platform", "instructions", "figma_frames", "codebase_repo",
    }
    # .format(...) with exactly those five kwargs must not raise.
    template.format(
        prd_md="a", target_platform="b", instructions="c",
        figma_frames="d", codebase_repo="e",
    )


def test_render_scaffold_user_substitutes_values():
    out = p.render_scaffold_user("x", "mobile", "y", "z")
    assert "x" in out
    assert "mobile" in out
    assert "y" in out
    assert "z" in out


def test_render_scaffold_user_empty_prd_falls_back_to_placeholder():
    out = p.render_scaffold_user("", "mobile", "y", "z")
    assert "(PRD is empty)" in out


def test_render_scaffold_user_empty_instructions_falls_back_to_none():
    out = p.render_scaffold_user("x", "mobile", "", "z")
    assert "(none)" in out


def test_render_scaffold_user_empty_figma_falls_back_to_no_source_detected():
    out = p.render_scaffold_user("x", "mobile", "y", "")
    assert "(no Figma source detected)" in out


# ---- cache readiness --------------------------------------------------------

def test_system_prompt_meets_sonnet_min_cacheable_length():
    # >2000 chars is a conservative proxy for ≥1,024 tokens, Sonnet 4.6's
    # minimum cacheable prefix (agent-build-research.md §1.5).
    assert len(SYS) > 2000


# ---- observability (AC12 — pure constants, no logs, no env reads) -----------

def test_module_emits_no_logs_and_reads_no_env():
    source = Path(p.__file__).read_text(encoding="utf-8")
    for needle in ("logger", "getenv", "environ"):
        assert needle not in source, f"module unexpectedly references {needle!r}"


# ---- recreate discipline: scoped-interactivity axis -------------------------

DISCIPLINE = p.DESIGN_AGENT_RECREATE_DISCIPLINE


def test_discipline_mentions_both_axes():
    # The discipline must state BOTH faithful RENDERING of the shell AND scoped
    # interactivity (only PRD interactions live, everything else inert).
    low = DISCIPLINE.lower()
    assert "rendering axis" in low or ("render" in low and "faithful" in low)
    assert "interactivity axis" in low or "scope the interactivity" in low
    assert "only the interactions the prd" in low
    assert "live" in low
    assert "inert" in low


def test_discipline_mentions_entangled_case():
    # The discipline must address a feature ENTANGLED with existing interactions,
    # not only an isolated handler dropped onto a static screen.
    low = DISCIPLINE.lower()
    assert "entangled" in low
    assert "existing" in low
    assert "isolated" in low


def test_inert_affordance_default_documented_as_pending():
    # The discipline ships the visibly-disabled default AND flags it as a
    # pending product decision (a default, not a settled rule).
    low = DISCIPLINE.lower()
    assert "disabled" in low
    assert "cursor-not-allowed" in low
    assert "pending" in low
    assert "default" in low
    assert "not settled" in low or "not a final decision" in low


def test_recreate_discipline_append_only_and_version_line():
    # The change is template-invalidating → version bumped to 6, owned here.
    assert p.DESIGN_AGENT_TEMPLATE_VERSION == 7
    assert isinstance(p.DESIGN_AGENT_TEMPLATE_VERSION, int)
    # Append-only: the pre-existing discipline halves are all still present.
    assert "RE-EXPRESS, DON'T PARAPHRASE." in DISCIPLINE
    assert "ON-THEME TOKENS ONLY" in DISCIPLINE
    assert "PRD-SCOPED FIDELITY" in DISCIPLINE
    # No importer breaks: the module compiles.
    import py_compile

    py_compile.compile(p.__file__, doraise=True)


def test_no_prohibited_tokens_in_source():
    # No internal engagement coordinates in the appended discipline (the prose
    # this ticket adds to the prompt). Scoped to the changed region — pre-existing
    # legacy refs elsewhere in the prompts module / this test file are out of
    # scope (the no-historical-scrub rule).
    parts = [
        r"C[0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
    ]
    pattern = "|".join(parts)
    matches = re.findall(pattern, DISCIPLINE)
    assert not matches, f"Prohibited token(s) {matches} found in the discipline"

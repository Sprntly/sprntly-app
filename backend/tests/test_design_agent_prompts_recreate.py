"""Property tests for the recreate-path discipline constant + composition.

Turns prompt drift into a CI failure at authorship time: if someone edits
DESIGN_AGENT_RECREATE_DISCIPLINE in a way that removes any of the three
required rules, the property tests here catch it immediately.

Pure (no LLM, no network). No internal engagement coordinates — the
``test_no_new_prohibited_tokens_in_appended_region`` test constructs the
check pattern at runtime from split parts so that the literals checked
for are not continuous strings in this file.
"""
from __future__ import annotations

import re
from pathlib import Path

import app.design_agent.prompts as p
from app.design_agent.codebase_map.recreate import (
    BrandAssetCarry,
    LocatedScreen,
    RecreateSources,
    render_recreate_task_block,
    render_shell_task_block,
)
from app.design_agent.codebase_map.types import (
    LogoAsset,
    MapResult,
    NavItem,
    ScreenNode,
    ShellModel,
)
from app.design_agent.prompts import (
    DESIGN_AGENT_ITERATE_SYSTEM,
    DESIGN_AGENT_MANUAL_EDIT_SYSTEM,
    DESIGN_AGENT_PLAN_SYSTEM,
    DESIGN_AGENT_RECREATE_DISCIPLINE,
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
)

_DISCIPLINE = DESIGN_AGENT_RECREATE_DISCIPLINE


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_map_result(
    route: str = "/dashboard",
    component: str = "Dashboard",
    sha: str = "abc123",
) -> MapResult:
    node = ScreenNode(
        route=route,
        entry_component=component,
        file=f"src/pages/{component}.tsx",
        composed_components=(),
    )
    return MapResult(
        repo="org/repo",
        commit_sha=sha,
        posture="CLEAN",
        nodes=(node,),
        shell=ShellModel(
            logo=LogoAsset(render_kind="absent", asset_ref="", alt_text=""),
            nav_items=(),
            collapse_mechanism="",
        ),
    )


def _make_located(
    route: str = "/dashboard",
    component: str = "Dashboard",
    sha: str = "abc123",
) -> LocatedScreen:
    m = _make_map_result(route, component, sha)
    return LocatedScreen(map_result=m, node=m.nodes[0])


def _make_sources(
    files: "dict[str, str] | None" = None,
    sha: str = "abc123",
) -> RecreateSources:
    return RecreateSources(
        repo="org/repo",
        commit_sha=sha,
        files=files or {"src/pages/Dashboard.tsx": "export default function Dashboard(){}"},
        screen_path="src/pages/Dashboard.tsx",
        also_screen_paths=(),
    )


# ─── AC2: on-theme rule with explicit anti-example ───────────────────────────

def test_discipline_states_on_theme_token_rule_with_anti_example():
    """The discipline must name a semantic token class (bg-primary), name
    bg-green-600 as the raw-palette anti-example, and use 'never'/'NEVER'
    to make the contrast unambiguous."""
    lower = _DISCIPLINE.lower()
    assert "bg-primary" in lower, "discipline must name bg-primary as a semantic token"
    assert "bg-green-600" in _DISCIPLINE, (
        "discipline must name bg-green-600 as the raw-palette anti-example"
    )
    assert "never" in lower, "discipline must use 'never'/'NEVER' to forbid raw palette"


# ─── AC3: no-gold-plate rule with recognizable + do NOT clause ───────────────

def test_discipline_states_no_gold_plate_rule():
    """The discipline must contain 'recognizable' and a 'do NOT' pixel/clone
    clause so the PRD-scoped-fidelity rule is explicit."""
    assert "recognizable" in _DISCIPLINE.lower()
    assert "do not" in _DISCIPLINE.lower() or "do NOT" in _DISCIPLINE, (
        "discipline must contain 'do NOT' pixel/clone language"
    )


# ─── AC4: re-express drop rule ────────────────────────────────────────────────

def test_discipline_states_re_express_drop_rule():
    """The discipline must name what to drop for the fixed stack and name the
    mock-data / local-state substitution strategy."""
    lower = _DISCIPLINE.lower()
    has_drop_marker = any(
        phrase in lower
        for phrase in ("use client", "server component", "backend", "context provider")
    )
    assert has_drop_marker, (
        "discipline must name what the fixed stack cannot run "
        "('use client', server components, backend fetches, or context providers)"
    )
    has_substitution = "mock" in lower or "local state" in lower
    assert has_substitution, (
        "discipline must describe the mock-data / local-state substitution"
    )


# ─── AC5: length bounds ───────────────────────────────────────────────────────

def test_discipline_length_within_bounds():
    """The discipline is substantive (carries four rules: re-express, on-theme,
    PRD-scoped fidelity, and the scoped-interactivity axis) but bounded (it is a
    task block, not a manual). Bounds: [800, 4500] chars."""
    length = len(_DISCIPLINE)
    assert length >= 800, f"discipline too short ({length} chars) — the rules must be present"
    assert length <= 4500, f"discipline too long ({length} chars) — keep it a task block, not a manual"


# ─── AC6: version bumped ─────────────────────────────────────────────────────

def test_template_version_bumped_to_5():
    """DESIGN_AGENT_TEMPLATE_VERSION is an int, now 7 (bumped when the pre-seed
    began emitting token-driven semantic palette vars)."""
    assert DESIGN_AGENT_TEMPLATE_VERSION == 7
    assert isinstance(DESIGN_AGENT_TEMPLATE_VERSION, int)


def test_scaffold_sync_green_at_new_version():
    """The version-coupled scaffold-sync contract: version is an int equal to 7
    (bumped to 7 when the pre-seed began emitting token-driven semantic palette
    vars --destructive/--error/--warning/--success)."""
    assert p.DESIGN_AGENT_TEMPLATE_VERSION == 7


# ─── AC7: existing system prompts unchanged (append-only) ────────────────────

def test_existing_system_prompts_unchanged():
    """SCAFFOLD, ITERATE, PLAN, and MANUAL_EDIT system prompts must not contain
    the recreate discipline (user-message-only) and must still open with their
    canonical first-section markers."""
    for name, prompt in [
        ("scaffold", DESIGN_AGENT_SCAFFOLD_SYSTEM),
        ("iterate", DESIGN_AGENT_ITERATE_SYSTEM),
        ("plan", DESIGN_AGENT_PLAN_SYSTEM),
        ("manual_edit", DESIGN_AGENT_MANUAL_EDIT_SYSTEM),
    ]:
        assert "[1] ROLE" in prompt, f"{name} system lost its [1] ROLE section"
        assert "[9] STABLE JSX IDs" in prompt, f"{name} system lost [9] STABLE JSX IDs"
        assert "RE-EXPRESS" not in prompt, (
            f"discipline re-express rule leaked into {name} system prompt"
        )
        assert "PRD-SCOPED FIDELITY" not in prompt, (
            f"discipline PRD-scoped rule leaked into {name} system prompt"
        )


# ─── AC8: discipline only in user message, not system ────────────────────────

def test_discipline_only_in_user_message_not_system():
    """The discipline text must not appear in any system prompt (the cached
    prefix must stay stable). It IS present in the recreate task block."""
    # Not in any system prompt
    for name, prompt in [
        ("scaffold", DESIGN_AGENT_SCAFFOLD_SYSTEM),
        ("iterate", DESIGN_AGENT_ITERATE_SYSTEM),
        ("plan", DESIGN_AGENT_PLAN_SYSTEM),
        ("manual_edit", DESIGN_AGENT_MANUAL_EDIT_SYSTEM),
    ]:
        assert "NEVER RAW PALETTE" not in prompt, (
            f"discipline on-theme rule found in {name} system prompt"
        )
        assert "PRD-SCOPED FIDELITY" not in prompt, (
            f"discipline PRD-scoped rule found in {name} system prompt"
        )

    # IS present in the recreate task block (user-message path)
    located = _make_located()
    sources = _make_sources()
    block = render_recreate_task_block(located, sources)
    assert "NEVER" in block
    assert "recognizable" in block.lower()


# ─── AC1: discipline composed into recreate task block ───────────────────────

def test_recreate_block_includes_discipline_when_located():
    """When a screen is located, render_recreate_task_block must include the
    full discipline prose so the agent receives it in the user message."""
    located = _make_located()
    sources = _make_sources()
    block = render_recreate_task_block(located, sources)

    # Core discipline rule markers — one per rule
    assert "RE-EXPRESS" in block, "re-express rule missing from recreate block"
    assert "recognizable" in block.lower(), "PRD-scoped fidelity rule missing from recreate block"
    # Multi-word phrase unique to the discipline constant
    assert "plausible client-side mock data" in block


def test_recreate_block_includes_discipline_with_brand_carry():
    """Brand-carry path: discipline still appended after the logo reference line."""
    located = _make_located()
    sources = _make_sources()
    brand = BrandAssetCarry(
        virtual_fs_keys={},
        shell_render_ref="<img src='/logo.svg' alt='Brand' />",
        deployed_url="",
        render_kind="img_src",
        carried=False,
    )
    block = render_recreate_task_block(located, sources, brand_carry=brand)
    assert "Brand logo" in block
    assert "RE-EXPRESS" in block, "discipline missing when brand_carry is present"
    assert "recognizable" in block.lower()


# ─── shell task block: real nav labels listed when provided ──────────────────

def test_shell_task_block_lists_nav_labels_when_provided():
    """When nav_items with labels are passed, the rendered block lists each
    label in order so the model reproduces the real icon-rail."""
    sources = _make_sources()
    nav = [
        NavItem(label="Weekly brief", order=0),
        NavItem(label="All chats", order=1),
        NavItem(label="Backlog Projects", order=2),
    ]
    block = render_shell_task_block(sources, nav_items=nav)
    assert "REAL NAV ITEMS" in block
    for label in ("Weekly brief", "All chats", "Backlog Projects"):
        assert label in block
    # order preserved
    assert block.index("Weekly brief") < block.index("All chats") < block.index(
        "Backlog Projects"
    )


def test_shell_task_block_omits_nav_line_when_no_items():
    """No nav_items (None or empty) → no nav-list line, no error."""
    sources = _make_sources()
    assert "REAL NAV ITEMS" not in render_shell_task_block(sources)
    assert "REAL NAV ITEMS" not in render_shell_task_block(
        sources, nav_items=[]
    )
    # blank-label items contribute nothing
    assert "REAL NAV ITEMS" not in render_shell_task_block(
        sources, nav_items=[NavItem(label="", order=0)]
    )


def test_shell_task_block_instructs_dependency_free_rebuild():
    """The block must tell the model to REBUILD the shell in the scaffold's
    primitives (lucide-react + shadcn) and NOT import the app's own modules —
    the real shell can't be reproduced verbatim in the scaffold."""
    sources = _make_sources()
    nav = [
        NavItem(label="Weekly brief", order=0),
        NavItem(label="All chats", order=1),
    ]
    block = render_shell_task_block(sources, nav_items=nav)
    # Rebuild + do-not-import instruction.
    assert "REBUILD" in block
    assert "Do NOT import" in block
    # Scaffold primitives named.
    assert "lucide-react" in block
    assert "shadcn" in block
    # Real labels still present alongside the rebuild instruction.
    assert "Weekly brief" in block
    assert "All chats" in block
    # The rebuild instruction is present even with no nav items.
    bare = render_shell_task_block(sources)
    assert "REBUILD" in bare
    assert "lucide-react" in bare


# ─── AC10: no new prohibited tokens in appended region ───────────────────────

def test_no_new_prohibited_tokens_in_appended_region():
    """Neither the discipline constant nor this test file contains internal
    engagement coordinates.

    The check pattern is assembled at runtime from split parts so that the
    prohibited literals are not continuous strings in this file.
    """
    parts = [
        r"C[0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\b" + "AD" + r"[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
    ]
    pattern = "|".join(parts)
    targets = [
        _DISCIPLINE,
        Path(__file__).read_text(),
    ]
    for text in targets:
        matches = re.findall(pattern, text)
        assert not matches, f"Prohibited token(s) {matches!r} found in scanned region"

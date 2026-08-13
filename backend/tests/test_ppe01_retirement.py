"""Static guards for the PPE-01 retirement (spec §"PPE-01 retire / keep").

RETIRE: `PROPOSE_PROJECT_PRD_PATCH_TOOL` + `handle_propose_prd_patch` (the
project-chat propose/review write tool + its handler) — in-place editing via
the shared `apply_chat_edit_scoped` (PCU-01 private, this ticket's group
wiring) supersedes it on both project-chat surfaces.

KEEP (untouched): `project_prd_gate.py` (`assert_prd_on_project`), the
surviving `_resolve_prd_id`/`project_prd_edit_enabled` in
`project_prd_patch_tool.py`, the `prd_patches` table + its DB helpers, and
Design-Agent's own main-chat `prd_patches` propose/accept/reject flow
(`routes/design_agent.py`'s `/prd-patches*` endpoints + the frontend
`designAgentApi.listPendingPatches/acceptPatch/rejectPatch`) — the SAME
`prd_patches` table disambiguated by `prototype_id IS NULL` (project, now
unused for NEW writes) vs not-null (Design-Agent), `db/prd_patches.py:51`.

These are cheap, deterministic, no-DB checks — source-scan + import-time —
appropriate for a "did we retire exactly the right surface and nothing else"
guard.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
WEB = REPO_ROOT / "web"


def _grep(pattern: str, root: Path) -> list[str]:
    """A plain-text occurrence scan (no regex surprises) over PRODUCTION
    source only — `tests/`/`__tests__/` directories are deliberately
    excluded, since a test suite legitimately asserts the retired symbols'
    ABSENCE by name (this file, `test_project_prd_patch_tool.py`,
    `test_group_chat_prd_edit.py`) and would otherwise self-trigger. A
    reintroduction anywhere the product actually imports from is what this
    guards against."""
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    for path in root.rglob("*.ts*"):
        if "__tests__" in path.parts or "node_modules" in path.parts or ".next" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    return hits


# ── AC10 — the propose tool + handler no longer exist in production code ─────
def test_no_propose_symbols_remain():
    for pattern in ("PROPOSE_PROJECT_PRD_PATCH_TOOL", "handle_propose_prd_patch"):
        hits = _grep(pattern, BACKEND / "app") + _grep(pattern, WEB / "app")
        assert hits == [], f"{pattern!r} still referenced in: {hits}"


def test_importing_retired_symbols_raises():
    import app.project_prd_patch_tool as tool_mod

    assert not hasattr(tool_mod, "PROPOSE_PROJECT_PRD_PATCH_TOOL")
    assert not hasattr(tool_mod, "handle_propose_prd_patch")
    with pytest.raises(AttributeError):
        tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL
    with pytest.raises(AttributeError):
        tool_mod.handle_propose_prd_patch


def test_group_route_no_longer_imports_propose_symbols():
    src = (BACKEND / "app" / "routes" / "projects.py").read_text(encoding="utf-8")
    assert "PROPOSE_PROJECT_PRD_PATCH_TOOL" not in src
    assert "handle_propose_prd_patch" not in src
    # The survivors ARE still imported (both surfaces need them).
    assert "_resolve_prd_id" in src
    assert "project_prd_edit_enabled" in src


# ── AC11 — the banner is gone, and no longer wired into the individual chat ──
def test_banner_file_and_test_deleted():
    banner = WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectPrdPatchBanner.tsx"
    banner_test = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "__tests__"
        / "ProjectPrdPatchBanner.dom.test.tsx"
    )
    assert not banner.exists()
    assert not banner_test.exists()


def test_individual_chat_no_longer_imports_or_renders_banner():
    src = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectIndividualChat.tsx"
    ).read_text(encoding="utf-8")
    assert "ProjectPrdPatchBanner" not in src


def test_group_chat_unchanged_never_referenced_banner():
    src = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectGroupChat.tsx"
    ).read_text(encoding="utf-8")
    assert "ProjectPrdPatchBanner" not in src


# ── AC12 — the KEEP set is intact ─────────────────────────────────────────────
def test_keep_set_intact():
    # Backend: the project-scope gate, the surviving resolver + flag.
    from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project, prd_on_project
    from app.project_prd_patch_tool import _resolve_prd_id, project_prd_edit_enabled

    assert callable(assert_prd_on_project)
    assert callable(prd_on_project)
    assert issubclass(ProjectPrdWriteDenied, Exception)
    assert callable(_resolve_prd_id)
    assert callable(project_prd_edit_enabled)

    # Backend: the shared scoped-edit callable (PCU-01/this ticket's shared writer).
    from app.project_chat_edit import apply_chat_edit_scoped

    assert callable(apply_chat_edit_scoped)

    # Backend: `prd_patches` DB helpers + Design-Agent's own main-chat routes.
    from app.db.prd_patches import (
        insert_patch, list_pending_patches, mark_patch_applied, mark_patch_rejected,
    )

    assert callable(insert_patch)
    assert callable(list_pending_patches)
    assert callable(mark_patch_applied)
    assert callable(mark_patch_rejected)

    da_src = (BACKEND / "app" / "routes" / "design_agent.py").read_text(encoding="utf-8")
    assert '@router.get("/prd-patches"' in da_src
    assert '"/prd-patches/{patch_id}/accept"' in da_src
    assert '"/prd-patches/{patch_id}/reject"' in da_src

    # Frontend: designAgentApi's prd-patches methods + the main-chat banner
    # (never touched by this ticket).
    api_src = (WEB / "app" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "listPendingPatches" in api_src
    assert "acceptPatch" in api_src
    assert "rejectPatch" in api_src

    main_chat_banner = WEB / "app" / "components" / "design-agent" / "PrdPatchBanner.tsx"
    assert main_chat_banner.exists()


# ── AC13 — the CI-lane registry has both old keys gone, both new keys present ─
def test_ci_registry_no_stale_or_missing():
    import tests.test_ci_lane_coverage as ci_mod

    keys = set(ci_mod._KNOWN_UNRUNNABLE)
    assert ("test_project_individual_prd_edit_live.py", "RUN_PROJECT_PRD_EDIT_LIVE") not in keys
    assert ("test_project_individual_prd_edit_live.py", "ANTHROPIC_API_KEY") not in keys
    assert ("test_group_chat_prd_edit_live.py", "RUN_GROUP_CHAT_PRD_EDIT_LIVE") in keys
    assert ("test_group_chat_prd_edit_live.py", "ANTHROPIC_API_KEY") in keys

    live_file = BACKEND / "tests" / "test_project_individual_prd_edit_live.py"
    assert not live_file.exists()
    new_live_file = BACKEND / "tests" / "test_group_chat_prd_edit_live.py"
    assert new_live_file.exists()

    # The registry's own ratchet test still passes (re-run here so THIS
    # ticket's registry surgery is proven, not just asserted statically).
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            str(BACKEND / "tests" / "test_ci_lane_coverage.py::test_no_stale_unrunnable_entries"),
            "-q",
        ],
        cwd=str(BACKEND), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

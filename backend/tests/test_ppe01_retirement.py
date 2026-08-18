"""Static guards for the PPE-01 retirement (spec §"PPE-01 retire / keep").

RETIRE: `PROPOSE_PROJECT_PRD_PATCH_TOOL` + `handle_propose_prd_patch` (the
project-chat propose/review write tool + its handler) — in-place editing via
the shared `apply_chat_edit_scoped` (PCU-01 private, this ticket's group
wiring) supersedes it on both project-chat surfaces.

ALSO RETIRED, later, by e05577dc: `_resolve_prd_id` and the rest of the
server-side edit-target resolution that enumerated a project's own PRDs. Both
chat surfaces now bind the open-drawer PRD explicitly and apply through
`apply_chat_edit_scoped`, so there is nothing left to infer across a project's
PRDs. That commit removed the code and deleted the two live-edit test files
but did not update these guards, which is why they asserted a KEEP set that no
longer existed and held main red.

KEEP (untouched): `project_prd_gate.py` (`assert_prd_on_project`), the
surviving `project_prd_edit_enabled` in
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
    # Retired later by e05577dc, together with the propose flow it served.
    assert not hasattr(tool_mod, "_resolve_prd_id")
    with pytest.raises(AttributeError):
        tool_mod.PROPOSE_PROJECT_PRD_PATCH_TOOL
    with pytest.raises(AttributeError):
        tool_mod.handle_propose_prd_patch
    with pytest.raises(AttributeError):
        tool_mod._resolve_prd_id


def test_group_route_no_longer_imports_propose_symbols():
    src = (BACKEND / "app" / "routes" / "projects.py").read_text(encoding="utf-8")
    assert "PROPOSE_PROJECT_PRD_PATCH_TOOL" not in src
    assert "handle_propose_prd_patch" not in src
    # `_resolve_prd_id` went with them: the route binds the open-drawer PRD id
    # explicitly now rather than inferring a target across the project's PRDs.
    assert "_resolve_prd_id" not in src
    # The one survivor IS still imported (both surfaces need the flag).
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
    # `ProjectIndividualChat.tsx` was deleted by the chat-shell refactor —
    # its individual-chat surface now renders as `ProjectPrivateChat.tsx`
    # through the shared chat shell. The retirement intent (no banner import)
    # holds vacuously for a file that no longer exists, and holds concretely
    # for its successor.
    individual_chat = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectIndividualChat.tsx"
    )
    assert not individual_chat.exists()

    private_chat_src = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectPrivateChat.tsx"
    ).read_text(encoding="utf-8")
    assert "ProjectPrdPatchBanner" not in private_chat_src


def test_group_chat_unchanged_never_referenced_banner():
    src = (
        WEB / "app" / "components" / "screens" / "app" / "projects" / "ProjectGroupChat.tsx"
    ).read_text(encoding="utf-8")
    assert "ProjectPrdPatchBanner" not in src


# ── AC12 — the KEEP set is intact ─────────────────────────────────────────────
def test_keep_set_intact():
    # Backend: the project-scope gate, the surviving resolver + flag.
    from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project, prd_on_project
    from app.project_prd_patch_tool import project_prd_edit_enabled

    assert callable(assert_prd_on_project)
    assert callable(prd_on_project)
    assert issubclass(ProjectPrdWriteDenied, Exception)
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

    # e05577dc deleted BOTH live-edit files — the propose/confirm flow they
    # exercised no longer exists, and `test_project_prd_edit_parity.py` covers
    # the shared editor both surfaces now use. So the registry must carry
    # neither, and a stale entry for a deleted file is exactly what
    # `test_ci_lane_coverage.test_no_stale_unrunnable_entries` exists to catch.
    keys = set(ci_mod._KNOWN_UNRUNNABLE)
    for stale in (
        "test_project_individual_prd_edit_live.py",
        "test_group_chat_prd_edit_live.py",
    ):
        assert not any(f == stale for f, _ in keys), f"stale registry entry for {stale}"
        assert not (BACKEND / "tests" / stale).exists()

    assert (BACKEND / "tests" / "test_project_prd_edit_parity.py").exists()

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

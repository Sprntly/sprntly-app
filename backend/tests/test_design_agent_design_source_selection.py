"""Unit tests for the explicit design-source selection wiring.

Covers the new ``design_source`` kwarg on ``_design_source_for_generation``:
explicit selection behaviour, graceful degrade for unsatisfiable selections,
back-compat when ``design_source`` is None, scenario-label independence, and
key invariants (no new LLM import, signature back-compat).

All tests are pure unit tests — no network, no LLM calls.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from app.design_agent.runner import _design_source_for_generation
from app.db.prototypes import infer_scenario_from_inputs


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _call(
    *,
    figma_file_key: str | None = None,
    figma_access_token: str | None = None,
    website_url: str | None = None,
    website_sample: dict | None = None,
    github_repo: str | None = None,
    github_installation_id: int | None = None,
    design_source: str | None = None,
):
    """Thin wrapper so tests only supply the kwargs they care about."""
    return _design_source_for_generation(
        figma_file_key=figma_file_key,
        figma_access_token=figma_access_token,
        website_url=website_url,
        website_sample=website_sample,
        github_repo=github_repo,
        github_installation_id=github_installation_id,
        design_source=design_source,
    )


# ─── Explicit Figma selection ─────────────────────────────────────────────────


def test_figma_selection_returns_figma_and_skips_website():
    """design_source='figma' with a key + token returns the figma arm even
    when a website_url is also present — the website does not win."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="figma",
        figma_file_key="FILE_KEY_ABC",
        figma_access_token="tok_figma",
        website_url="https://example.com",
    )
    assert provider == "figma"
    assert source_ref == "FILE_KEY_ABC"
    # Factories are bound callables, not None.
    assert callable(raw_factory)
    assert callable(version_factory)


# ─── Explicit GitHub selection ────────────────────────────────────────────────


def test_github_selection_wins_over_website():
    """design_source='github' with repo + installation_id returns the github arm
    even when a website_url is present."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="github",
        github_repo="org/my-repo",
        github_installation_id=42,
        website_url="https://example.com",
    )
    assert provider == "github"
    assert source_ref == "org/my-repo"
    assert callable(raw_factory)
    assert callable(version_factory)


# ─── Explicit Website selection ───────────────────────────────────────────────


def test_website_selection_returns_web():
    """design_source='website' with a website_url returns the web arm even when
    a Figma key + token are both present."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="website",
        website_url="https://brand.example.com",
        figma_file_key="FILE_KEY_IGNORED",
        figma_access_token="tok_ignored",
    )
    assert provider == "web"
    assert source_ref == "https://brand.example.com"
    assert callable(raw_factory)
    assert callable(version_factory)


# ─── None selection: back-compat implicit precedence ─────────────────────────


def test_none_selection_preserves_implicit_precedence():
    """design_source=None falls through to the implicit Figma → website → github
    chain, exactly as before this change."""
    # Sub-case 1: Figma inputs present → figma arm.
    p1, r1, _, _ = _call(
        design_source=None,
        figma_file_key="FK",
        figma_access_token="tok",
        website_url="https://example.com",
        github_repo="org/repo",
        github_installation_id=7,
    )
    assert p1 == "figma"
    assert r1 == "FK"

    # Sub-case 2: no Figma, only website → web arm.
    p2, r2, _, _ = _call(
        design_source=None,
        figma_file_key=None,
        figma_access_token=None,
        website_url="https://example.com",
        github_repo="org/repo",
        github_installation_id=7,
    )
    assert p2 == "web"
    assert r2 == "https://example.com"

    # Sub-case 3: no Figma, no website, only github → github arm.
    p3, r3, _, _ = _call(
        design_source=None,
        figma_file_key=None,
        figma_access_token=None,
        website_url=None,
        github_repo="org/repo",
        github_installation_id=7,
    )
    assert p3 == "github"
    assert r3 == "org/repo"

    # Sub-case 4: nothing available → (None, None, None, None).
    p4, r4, raw4, ver4 = _call(design_source=None)
    assert p4 is None
    assert r4 is None
    assert raw4 is None
    assert ver4 is None


# ─── Graceful degrade: unsatisfiable selections ───────────────────────────────


def test_unsatisfiable_github_selection_degrades_to_website():
    """design_source='github' but github_installation_id is None degrades to
    the website arm when a website_url is present — no exception raised."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="github",
        github_repo="org/repo",
        github_installation_id=None,
        website_url="https://brand.example.com",
    )
    assert provider == "web"
    assert source_ref == "https://brand.example.com"
    assert callable(raw_factory)
    assert callable(version_factory)


def test_unsatisfiable_figma_selection_degrades_to_website():
    """design_source='figma' but no figma_file_key/token degrades to the
    website arm when a website_url is present."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="figma",
        figma_file_key=None,
        figma_access_token=None,
        website_url="https://brand.example.com",
    )
    assert provider == "web"
    assert source_ref == "https://brand.example.com"
    assert callable(raw_factory)
    assert callable(version_factory)


def test_unsatisfiable_selection_with_no_website_returns_none():
    """design_source='github' but neither the installation nor a website_url is
    available — returns (None, None, None, None) without raising."""
    provider, source_ref, raw_factory, version_factory = _call(
        design_source="github",
        github_repo="org/repo",
        github_installation_id=None,
        website_url=None,
    )
    assert provider is None
    assert source_ref is None
    assert raw_factory is None
    assert version_factory is None


# ─── Scenario label unchanged by this ticket ─────────────────────────────────


def test_scenario_label_unchanged_design_source_only():
    """The infer_scenario_from_inputs function is NOT modified by this ticket;
    its output for a fixed input set must remain identical regardless of what
    design_source value was passed to _design_source_for_generation."""
    inputs = dict(
        figma_file_key="FK",
        website_url="https://example.com",
        github_installation_id=1,
        prd_references_codebase=False,
    )
    # Verify the function's return is stable — it doesn't accept design_source.
    result = infer_scenario_from_inputs(**inputs)
    # Figma present → Scenario A (website does not add B when figma is present).
    assert result == frozenset({"A"})

    # Without Figma: website → B.
    result2 = infer_scenario_from_inputs(
        figma_file_key=None,
        website_url="https://example.com",
        github_installation_id=1,
        prd_references_codebase=False,
    )
    assert result2 == frozenset({"B"})

    # Verify design_source is NOT a parameter of infer_scenario_from_inputs
    # (the scenario-label function is decoupled from the design-source selector).
    sig = inspect.signature(infer_scenario_from_inputs)
    assert "design_source" not in sig.parameters, (
        "infer_scenario_from_inputs must not accept design_source — "
        "scenario labelling is decoupled from design-source selection"
    )


# ─── Invariants ──────────────────────────────────────────────────────────────


def test_no_anthropic_import_added():
    """The design-source selection wiring must not introduce any new import of
    the anthropic SDK — this is pure routing/factory binding with no LLM calls."""
    runner_path = (
        Path(__file__).parent.parent / "app" / "design_agent" / "runner.py"
    )
    routes_path = (
        Path(__file__).parent.parent / "app" / "routes" / "design_agent.py"
    )

    # Check at module level for runner.py (anthropic may be imported for other
    # reasons elsewhere in the file; we assert the _design_source_for_generation
    # function itself — confirm it is present and has the new kwarg).
    runner_source = runner_path.read_text()
    routes_source = routes_path.read_text()

    # Neither new field name should be absent — confirm the wiring was applied.
    assert "design_source" in runner_source, "design_source kwarg missing from runner.py"
    assert "design_source" in routes_source, "design_source field missing from routes/design_agent.py"

    # The wiring functions must not introduce a new top-level anthropic import.
    # If anthropic is already imported in runner for other reasons, we still
    # assert the new _design_source_for_generation does not call it.
    # Simplest check: confirm "import anthropic" count is zero or the same as
    # before (the design_source code path contains no anthropic reference).
    dsf_start = runner_source.index("def _design_source_for_generation(")
    # Find the next top-level def after _design_source_for_generation.
    next_def = runner_source.find("\ndef ", dsf_start + 1)
    dsf_body = runner_source[dsf_start:next_def] if next_def > 0 else runner_source[dsf_start:]
    assert "import anthropic" not in dsf_body, (
        "_design_source_for_generation must not import anthropic"
    )
    assert "anthropic" not in dsf_body, (
        "_design_source_for_generation must not reference anthropic"
    )


def test_design_source_signature_back_compatible():
    """The new design_source kwarg has a default of None so all existing callers
    that omit it are unaffected — confirming back-compat without changing them."""
    sig = inspect.signature(_design_source_for_generation)
    param = sig.parameters.get("design_source")
    assert param is not None, "design_source param not found in _design_source_for_generation"
    assert param.default is None, (
        f"design_source default should be None for back-compat; got {param.default!r}"
    )
    # Calling without design_source must not raise.
    result = _call(figma_file_key=None, figma_access_token=None, website_url=None)
    assert result == (None, None, None, None)


def test_github_installation_remains_workspace_scoped():
    """The github_installation_id used in _design_source_for_generation is the
    caller-supplied value — no new cross-workspace query is introduced.

    This test confirms the function signature still accepts github_installation_id
    as a direct parameter (not fetched internally) so workspace isolation holds.
    """
    sig = inspect.signature(_design_source_for_generation)
    assert "github_installation_id" in sig.parameters, (
        "github_installation_id must remain a direct parameter "
        "(workspace-scoped by the caller, not fetched inside the function)"
    )
    assert "github_repo" in sig.parameters, "github_repo must remain a direct parameter"

    # Providing a mismatched installation (wrong workspace) would be caught by
    # the caller's _resolve_github_installation_id_for_repo; this function
    # trusts what it receives.
    p, r, _, _ = _call(
        design_source="github",
        github_repo="ws1/repo",
        github_installation_id=101,
    )
    assert p == "github"
    assert r == "ws1/repo"

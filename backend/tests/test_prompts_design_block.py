"""Tests for the F1 :::design PRD block — version bump + system-prompt spec.

P1-01 bumps PRD_TEMPLATE_VERSION (2 -> 3) so the startup invalidation loop
(`invalidate_stale_prds` in app.main.lifespan) demotes cached v2 PRDs and
re-renders them under the new template, and teaches the PRD LLM to emit a
:::design block (the Design section + prototype entry point). These tests
pin the version value and assert the spec is present in PRD_SYSTEM and
documents both hint keys as optional.
"""
from app import prompts


def test_prd_template_version_bumped_to_3():
    assert prompts.PRD_TEMPLATE_VERSION == 3


def test_prd_system_contains_design_block_spec():
    assert ":::design" in prompts.PRD_SYSTEM


def test_design_block_spec_documents_optional_keys():
    system = prompts.PRD_SYSTEM.lower()
    assert "platform_hint" in system
    assert "notes" in system
    # Both hint keys must be taught as optional, not required.
    assert "both are optional" in system

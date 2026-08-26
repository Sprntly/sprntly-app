"""The answer prompt's WORKSPACE CONFIGURATION block carries what onboarding
captured — not just the company's name.

The reported failure: asked "what is their north star goal", chat answered that
no connected source explicitly states one and told the team to connect Google
Drive, Confluence or Notion. That workspace had picked a north star on the
onboarding metrics page; it is on screen in Settings > Metrics. Nothing was
retrieved and lost — `company_facts_block` rendered the company name, the
product name and the website, and every other answer the wizard collected was
simply absent from the prompt, so a question the workspace had already answered
looked like one no source had answered.

These tests pin the block's CONTENTS. The prompt half of the fix (which
questions this block answers, and where to send someone when it genuinely does
not) lives in `test_prompts.py`.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def facts(isolated_settings):
    """`company_facts_block` bound to this test's fresh fake Supabase."""
    from app.ask_runner import company_facts_block

    return company_facts_block


def _seed(db, company_id: str, **columns):
    db.table("companies").insert(
        {
            "id": company_id,
            "slug": f"slug-{company_id}",
            "display_name": "Aurora Notes",
            **columns,
        }
    ).execute()


def _seed_product(db, company_id: str, **columns):
    db.table("products").insert(
        {
            "id": f"prod-{company_id}",
            "company_id": company_id,
            "name": "Aurora",
            "website": "https://aurora.example",
            "is_primary": 1,
            **columns,
        }
    ).execute()


def test_north_star_the_team_picked_is_in_the_block(isolated_settings, facts):
    """The exact regression. The metrics page writes `companies.kpi_tree`; the
    answer prompt has to be able to read the north star out of it."""
    _seed(
        isolated_settings["supabase"], "co-ns",
        kpi_tree={
            "north_star": {
                "metric": "Weekly active teams",
                "description": "teams with 3+ notes shared in a week",
            },
            "primary_metrics": [{"metric": "Net revenue retention", "description": ""}],
            "secondary_signals": [],
            "version": 1,
        },
    )

    block = facts("co-ns")

    assert "North star: Weekly active teams" in block
    assert "teams with 3+ notes shared in a week" in block
    # The supporting picks ride along — "what are our metrics" is the same
    # question one rung down.
    assert "Net revenue retention" in block


def test_business_context_north_star_is_rendered(isolated_settings, facts):
    """`goals_strategy.north_star` and `current_priorities` are collected and
    shown in Settings > Business Context, but `render_for_prompt` used to emit
    only `stated_goal` and `known_constraints` — so the lens answered the
    question everywhere except in a prompt."""
    _seed(
        isolated_settings["supabase"], "co-lens",
        business_context={
            "identity": {"legal_name": {"value": "Aurora Notes", "src": "given"}},
            "goals_strategy": {
                "north_star": {"value": "Weekly active teams", "src": "user"},
                "current_priorities": {"value": ["ship SSO", "cut churn"],
                                       "src": "user"},
            },
            "version": 1,
        },
    )

    block = facts("co-lens")

    assert "North star: Weekly active teams" in block
    assert "Current priorities: ship SSO, cut churn" in block


def test_onboarding_answers_ride_the_block(isolated_settings, facts):
    """Everything else the wizard collected, across all three rows it writes:
    companies, the primary product, and the default workspace."""
    db = isolated_settings["supabase"]
    _seed(
        db, "co-full",
        mission="Make meeting notes decide something.",
        strategy="Land teams via bottom-up adoption, expand to the org.",
        industry="B2B SaaS",
        planning_cycle="quarterly",
        prioritization_framework="rice",
        decision_process="The PM decides; the founder breaks ties.",
        business_context_summary="Aurora Notes sells to product teams.",
    )
    _seed_product(
        db, "co-full",
        positioning="The notes tool that closes the loop on decisions.",
        users_description="Product managers at 20-200 person software teams.",
    )
    db.table("workspaces").insert(
        {
            "id": "ws-full", "company_id": "co-full", "name": "Product",
            "slug": "product", "is_default": True,
            "team_scope": "Discovery and delivery for the core app.",
            "team_strategy": "Fewer, bigger bets this half.",
            "team_roadmap": "SSO, then search.",
        }
    ).execute()

    block = facts("co-full")

    for needle in (
        "Mission & vision: Make meeting notes decide something.",
        "Strategy / OKRs: Land teams via bottom-up adoption",
        "Industry: B2B SaaS",
        "Positioning: The notes tool that closes the loop on decisions.",
        "Users / customers: Product managers at 20-200 person software teams.",
        "Planning cycle: quarterly",
        "Prioritization framework: rice",
        "How the team decides: The PM decides; the founder breaks ties.",
        "Team scope of work: Discovery and delivery for the core app.",
        "Team strategy: Fewer, bigger bets this half.",
        "Team roadmap: SSO, then search.",
        "Business context (accepted by this team): Aurora Notes sells to",
    ):
        assert needle in block, f"missing from the configuration block: {needle!r}"


def test_fields_the_team_never_filled_are_absent(isolated_settings, facts):
    """An empty field contributes no line at all. Rendering it as "unknown"
    would hand the model a finding to repeat back, which is the failure this
    change exists to stop — in the opposite direction."""
    _seed(isolated_settings["supabase"], "co-bare")

    block = facts("co-bare")

    assert "Company name: Aurora Notes" in block
    for absent in ("Mission", "Strategy", "North star", "Planning cycle",
                   "unknown", "Positioning"):
        assert absent not in block


def test_block_still_empty_without_a_tenant(facts):
    """Unchanged degradation: no tenant, no block — chat behaves exactly as it
    did for a workspace that never onboarded."""
    assert facts(None) == ""
    assert facts("") == ""


def test_a_verbose_field_cannot_crowd_out_the_rest(isolated_settings, facts):
    """One pasted essay is truncated, and the fields after it still render —
    the block is a cached per-tenant prefix, not an unbounded dump."""
    from app.ask_runner import _CONFIG_VALUE_MAX_CHARS

    _seed(
        isolated_settings["supabase"], "co-long",
        strategy="x" * (_CONFIG_VALUE_MAX_CHARS * 3),
        planning_cycle="half",
    )

    block = facts("co-long")

    assert "…" in block
    assert len(block) < _CONFIG_VALUE_MAX_CHARS * 2
    assert "Planning cycle: half" in block

"""Tests for the DURABLE promotion-exclusion fix at the memory promoter
(`app/project_memory.py::maybe_promote_turn` / `_is_self_capability` /
`_PROMOTE_SYSTEM`).

THE PROBLEM this closes: the group-chat memory promoter can promote
Sprntly's OWN capability/meta self-descriptions ("I'm your project agent",
"I can edit the PRD", "here's what I can do") as if they were durable
project knowledge. Once promoted, they get re-fed to the agent as project
memory and reinforced — a self-reinforcing contamination loop. The fix
mirrors the existing "update" path's belt-and-braces shape: a prompt rule
(`_PROMOTE_SYSTEM`) PLUS a deterministic backstop (`_is_self_capability`)
applied AFTER the classifier returns but BEFORE any write, so a
mis-classification can't poison memory either.

Fast lane only, same fake-classifier technique as
`test_project_memory_promotion.py` — the real-LLM behaviour for this
promoter is covered by that file's existing live tier (unchanged); this
ticket adds no second promotion live file.
"""
from __future__ import annotations

import logging

import pytest

from app import project_memory
from app.db import project_memory_entries as memory_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Self-capability guard project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


_SELF_CAPABILITY_TRANSCRIPT = (
    "Ada (PM): @Sprntly what can you actually do here?\n"
    "Sprntly: I'm your project agent — I can edit the PRD in place, read the "
    "shared memory and artifacts, and delegate tasks to teammates."
)

_GENUINE_FACT_TRANSCRIPT = (
    "Ada (PM): @Sprntly can you record that we're locking the API rate "
    "limit at 100 requests/min per tenant, with no exception for "
    "enterprise customers?\n"
    "Sprntly: Got it — 100 req/min per tenant, applied uniformly including "
    "enterprise accounts. Noted."
)


@pytest.fixture
def fake_promote_llm(isolated_settings, monkeypatch):
    """Same seam `test_project_memory_promotion.py` patches
    (`app.project_memory.call_json`), controllable per-test."""
    state: dict = {
        "calls": [],
        "action": "none",
        "body": "",
        "target_entry_id": None,
    }

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user, "model": model})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 30, "output_tokens": 12,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return {
            "action": state["action"],
            "target_entry_id": state["target_entry_id"],
            "body": state["body"],
        }

    monkeypatch.setattr(project_memory, "call_json", _fake_call_json)
    return state


# ── The durable fix (AC-8) ──────────────────────────────────────────────


def test_self_capability_excerpt_not_promoted_classifier_none(
    isolated_settings, monkeypatch, fake_promote_llm
):
    """(a) When the classifier ITSELF correctly says "none" for a
    self-capability excerpt, nothing is promoted — the baseline the prompt
    rule alone should already achieve."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "none"
    fake_promote_llm["body"] = ""
    result = project_memory.maybe_promote_turn(
        project["id"], conv["id"], _SELF_CAPABILITY_TRANSCRIPT
    )
    assert result is None
    assert memory_db.list_entries(project["id"]) == []


def test_self_capability_body_blocked_despite_classifier_new(
    isolated_settings, monkeypatch, fake_promote_llm, caplog
):
    """(b) THE load-bearing case: even when the classifier is FORCED to
    return "new" (or "update") with a self-capability body, the
    deterministic backstop coerces it to a no-op — zero writes, zero regen,
    and the skip is logged. This is what makes the fix durable rather than
    a prompt-only band-aid: it fires regardless of the classifier's own
    (possibly wrong) decision."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = (
        "I'm your project agent — I can edit the PRD and read your memory "
        "and artifacts whenever you need."
    )
    with caplog.at_level(logging.INFO):
        result = project_memory.maybe_promote_turn(
            project["id"], conv["id"], _SELF_CAPABILITY_TRANSCRIPT
        )
    assert result is None
    assert memory_db.list_entries(project["id"]) == []

    skip_lines = [
        r.getMessage() for r in caplog.records
        if "memory_promotion_self_capability_skipped" in r.getMessage()
    ]
    assert len(skip_lines) == 1
    assert f"project_id={project['id']}" in skip_lines[0]
    assert f"conversation_id={conv['id']}" in skip_lines[0]

    # Same backstop on the "update" branch — forced to target a real
    # existing agent entry with a self-capability body must still no-op.
    existing = memory_db.add_agent_promoted_entry(
        project["id"], body="A genuine prior fact.", source_conversation_id=conv["id"],
    )
    fake_promote_llm["action"] = "update"
    fake_promote_llm["target_entry_id"] = existing["id"]
    fake_promote_llm["body"] = "My role is to help you edit the PRD — I have tools for that."
    result2 = project_memory.maybe_promote_turn(
        project["id"], conv["id"], _SELF_CAPABILITY_TRANSCRIPT
    )
    assert result2 is None
    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1
    assert rows[0]["body"] == "A genuine prior fact.", (
        "the existing entry must be untouched by a blocked self-capability update"
    )


def test_genuine_fact_still_promotes(isolated_settings, monkeypatch, fake_promote_llm):
    """(c) Non-regression: the guard must NOT swallow a genuine project
    fact — a real decision/guardrail excerpt still promotes exactly as
    before the fix."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)

    fake_promote_llm["action"] = "new"
    fake_promote_llm["body"] = "The team locked the API rate limit at 100 req/min per tenant."
    result = project_memory.maybe_promote_turn(
        project["id"], conv["id"], _GENUINE_FACT_TRANSCRIPT
    )
    assert result is not None
    assert result["body"] == fake_promote_llm["body"]
    rows = memory_db.list_entries(project["id"])
    assert len(rows) == 1
    assert rows[0]["id"] == result["id"]


# ── Prompt property (AC-9) ───────────────────────────────────────────────


def test_promote_system_has_self_capability_exclusion():
    system = project_memory._PROMOTE_SYSTEM.lower()
    assert "capabilit" in system or "role" in system
    assert "not durable project facts" in system or "not durable" in system
    assert "i can edit the prd" in system or "i'm your project" in system

    # Negative-space: the pre-fix prompt (small-talk rule only, no
    # self-capability clause) must NOT satisfy this check.
    weak_prompt = project_memory._PROMOTE_SYSTEM.split(
        "Sprntly's OWN descriptions"
    )[0].lower()
    assert "not durable project facts" not in weak_prompt


# ── Deterministic guard unit-level (property/edge) ──────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "I'm your project agent — happy to help.",
        "I can edit the PRD directly for you.",
        "As your project assistant, I have tools to read memory and artifacts.",
        "Sprntly can read the ledger and ask for clarification.",
        "My role is to help track this project's decisions.",
        "I can't make that change without more detail.",
    ],
)
def test_is_self_capability_matches_agent_meta_statements(body):
    assert project_memory._is_self_capability(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "The team locked the API rate limit at 100 req/min per tenant.",
        "We never enable telemetry by default for enterprise customers.",
        "The rate limit is 250 req/min per tenant, revised after load testing.",
        "Ship dark mode behind a feature flag.",
        "",
    ],
)
def test_is_self_capability_does_not_match_genuine_facts(body):
    assert project_memory._is_self_capability(body) is False

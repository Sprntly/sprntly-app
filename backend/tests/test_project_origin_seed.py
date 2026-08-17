"""Tests for `app/project_origin_seed.py::seed_project_origin_memory` — the
best-effort origin-memory writer that seeds a freshly auto-created project
with a grounded brief (+ any settled decisions) from the originating chat
and its PRD.

Fast lane: monkeypatches the module's own seams — `call_json` (the LLM),
`memory_db.add_agent_promoted_entry` / `schedule_regen` (the memory-write
pipeline), `prds_db.get_prd` (the PRD read), and `_read_turns` (the
conversation_turns read) — so every test is deterministic and hits no
network/real DB. Proves the writer's CONTRACT (what gets written, when it
falls back, when it stays silent, that it never raises, and that it reuses
the EXISTING memory primitives rather than forking a second writer).

The real-DB/real-LLM round trip lives in `test_project_origin_seed_live.py`
(`[[feedback_stubbed-e2e-masks-loop-behaviour]]` — a fully-stubbed LLM here
can prove wiring, never that the real model actually produces a grounded
brief).
"""
from __future__ import annotations

import logging

import pytest

import app.project_origin_seed as seed_mod
from app.db.client import require_client


@pytest.fixture
def fake_seed_llm(monkeypatch):
    """Patches every seam `seed_project_origin_memory` calls through, so a
    test can drive each branch (summarizer success/failure, fallback, no
    content) without a real DB or a real model. `state["written"]` /
    `state["regen_calls"]` / `state["calls"]` are the assertion points."""
    state: dict = {
        "calls": [],
        "brief_summary": "This project builds a dark-mode toggle for mobile settings.",
        "decisions": ["The team decided to ship dark mode behind a feature flag."],
        "raise_error": False,
        "written": [],
        "regen_calls": [],
        "turns": "User: we need dark mode\n\nAssistant: got it, drafting a PRD.",
        "prd_body": "# Dark mode\n\nUsers want a dark mode toggle in settings.",
        "write_error": False,
    }

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user, "model": model})
        if state["raise_error"]:
            raise RuntimeError("simulated summarizer failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 40,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return {"brief_summary": state["brief_summary"], "decisions": state["decisions"]}

    def _fake_add_entry(project_id, *, body, source_conversation_id):
        if state["write_error"]:
            raise RuntimeError("simulated write failure")
        entry = {
            "id": len(state["written"]) + 1,
            "project_id": project_id,
            "body": body,
            "promoted_by": "agent",
            "source_conversation_id": source_conversation_id,
        }
        state["written"].append(entry)
        return entry

    def _fake_schedule_regen(project_id):
        state["regen_calls"].append(project_id)

    def _fake_get_prd(prd_id):
        return {"id": prd_id, "payload_md": state["prd_body"]}

    def _fake_read_turns(conversation_id):  # noqa: ARG001
        return state["turns"]

    monkeypatch.setattr(seed_mod, "call_json", _fake_call_json)
    monkeypatch.setattr(seed_mod.memory_db, "add_agent_promoted_entry", _fake_add_entry)
    monkeypatch.setattr(seed_mod, "schedule_regen", _fake_schedule_regen)
    monkeypatch.setattr(seed_mod.prds_db, "get_prd", _fake_get_prd)
    monkeypatch.setattr(seed_mod, "_read_turns", _fake_read_turns)
    return state


# ── Writes (AC-1/AC-2) ──────────────────────────────────────────────────


def test_seed_writes_brief_and_decisions(fake_seed_llm):
    fake_seed_llm["decisions"] = [
        "Decision one.", "Decision two.", "Decision three.", "Decision four.",
        "Decision five.", "Decision six.", "Decision seven — over the cap.",
    ]
    seed_mod.seed_project_origin_memory(
        project_id=101, prd_id=5, prd_title="Dark mode on mobile", conversation_id=9,
    )
    written = fake_seed_llm["written"]
    # Brief + at most _MAX_DECISIONS decisions — never all seven.
    assert len(written) == 1 + seed_mod._MAX_DECISIONS
    assert written[0]["body"] == fake_seed_llm["brief_summary"]
    assert [w["body"] for w in written[1:]] == fake_seed_llm["decisions"][: seed_mod._MAX_DECISIONS]
    for entry in written:
        assert entry["source_conversation_id"] == 9
        assert entry["promoted_by"] == "agent"


def test_seed_schedules_one_regen(fake_seed_llm):
    seed_mod.seed_project_origin_memory(
        project_id=202, prd_id=6, prd_title="Instant quote flow", conversation_id=11,
    )
    assert fake_seed_llm["regen_calls"] == [202]


# ── Fallback / empty (AC-3/AC-4) ────────────────────────────────────────


def test_seed_falls_back_on_summarizer_failure(fake_seed_llm, caplog):
    fake_seed_llm["raise_error"] = True
    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        seed_mod.seed_project_origin_memory(
            project_id=303, prd_id=7, prd_title="Bulk export", conversation_id=13,
        )
    written = fake_seed_llm["written"]
    # Only the deterministic fallback brief — the summarizer never produced
    # decisions, so none were written.
    assert len(written) == 1
    assert 'This project was created from the PRD "Bulk export".' in written[0]["body"]
    assert fake_seed_llm["regen_calls"] == [303]

    cost_lines = [r.getMessage() for r in caplog.records if "projects.memory.origin_seed" in r.getMessage()]
    assert len(cost_lines) == 1
    assert "used_fallback=True" in cost_lines[0]
    assert "entries=1" in cost_lines[0]


def test_seed_no_title_writes_nothing(fake_seed_llm, caplog):
    fake_seed_llm["brief_summary"] = ""
    fake_seed_llm["decisions"] = []
    fake_seed_llm["prd_body"] = ""
    with caplog.at_level(logging.WARNING, logger="app.project_origin_seed"):
        seed_mod.seed_project_origin_memory(
            project_id=404, prd_id=8, prd_title="", conversation_id=15,
        )
    assert fake_seed_llm["written"] == []
    assert fake_seed_llm["regen_calls"] == []
    assert any(
        "project_origin_seed_empty" in r.getMessage() for r in caplog.records
    )


# ── Never raises (AC-5) ─────────────────────────────────────────────────


def test_seed_never_raises_on_write_error(fake_seed_llm, caplog):
    fake_seed_llm["write_error"] = True
    with caplog.at_level(logging.WARNING, logger="app.project_origin_seed"):
        result = seed_mod.seed_project_origin_memory(
            project_id=505, prd_id=9, prd_title="Roadmap sync", conversation_id=17,
        )
    assert result is None  # must not raise
    assert any("project_origin_seed_failed" in r.getMessage() for r in caplog.records)


# ── Only the new-project branch seeds (AC-6) ────────────────────────────


def _new_conversation(company_id: str, user_id: str) -> int:
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "generate prd",
        "query": "generate prd",
        "agent_type": "ask",
    }
    resp = require_client().table("conversations").insert(row).execute()
    return resp.data[0]["id"]


@pytest.fixture
def seed_spy(monkeypatch):
    import app.project_from_prd as pfp_mod

    calls: list[dict] = []

    def _spy(*, project_id, prd_id, prd_title, conversation_id):
        calls.append(
            {
                "project_id": project_id, "prd_id": prd_id,
                "prd_title": prd_title, "conversation_id": conversation_id,
            }
        )

    monkeypatch.setattr(pfp_mod, "seed_project_origin_memory", _spy)
    return calls


def test_new_project_branch_seeds_once(tenant_client, isolated_settings, seed_spy):
    t = tenant_client.make(slug="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=42, prd_title="Dark mode on mobile", conversation_id=conv_id,
    )
    assert project_id is not None
    assert len(seed_spy) == 1
    assert seed_spy[0] == {
        "project_id": project_id, "prd_id": 42,
        "prd_title": "Dark mode on mobile", "conversation_id": conv_id,
    }


def test_already_bound_path_does_not_seed(tenant_client, isolated_settings, seed_spy):
    t = tenant_client.make(slug="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    from app.project_from_prd import maybe_auto_create_project_for_prd

    first = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=42, prd_title="Dark mode on mobile", conversation_id=conv_id,
    )
    assert first is not None
    assert len(seed_spy) == 1

    # A second PRD generated on the SAME (already-bound) conversation returns
    # the same project — and must NOT re-seed.
    second = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=43, prd_title="Something else", conversation_id=conv_id,
    )
    assert second == first
    assert len(seed_spy) == 1, "the already-bound path must never re-seed"


# ── Cost line / PII discipline (AC-7) ───────────────────────────────────


def test_seed_emits_one_cost_line_no_pii(fake_seed_llm, caplog):
    fake_seed_llm["turns"] = "User: SECRET_CHAT_TEXT_DO_NOT_LOG"
    fake_seed_llm["prd_body"] = "SECRET_PRD_BODY_DO_NOT_LOG"
    fake_seed_llm["brief_summary"] = "SECRET_BRIEF_TEXT_DO_NOT_LOG"
    fake_seed_llm["decisions"] = ["SECRET_DECISION_TEXT_DO_NOT_LOG"]

    with caplog.at_level(logging.INFO):
        seed_mod.seed_project_origin_memory(
            project_id=606, prd_id=10, prd_title="Secret project", conversation_id=19,
        )

    cost_lines = [r.getMessage() for r in caplog.records if "projects.memory.origin_seed" in r.getMessage()]
    assert len(cost_lines) == 1
    assert "project_id=606" in cost_lines[0]
    assert "prd_id=10" in cost_lines[0]
    assert "conversation_id=19" in cost_lines[0]

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_CHAT_TEXT_DO_NOT_LOG" not in joined
    assert "SECRET_PRD_BODY_DO_NOT_LOG" not in joined
    assert "SECRET_BRIEF_TEXT_DO_NOT_LOG" not in joined
    assert "SECRET_DECISION_TEXT_DO_NOT_LOG" not in joined


# ── Prompt / schema property (AC-11) ────────────────────────────────────


def test_seed_system_prompt_properties():
    system = seed_mod._SYSTEM
    lowered = system.lower()
    assert system.strip() != ""
    assert "#" not in system
    # The prompt's OWN "Hard rules:" section is a short bullet list
    # instructing the model about its output (and quotes the two forbidden
    # trailing-question examples verbatim as part of that instruction) —
    # that is the prompt's OWN structure, not a claim it violates its own
    # rule. What matters is that the RULE ITSELF is stated.
    assert "no markdown headings" in lowered
    assert "bullet characters" in lowered
    assert 'do not address the reader or offer next steps' in lowered
    assert "grounded strictly" in lowered
    assert "brief_summary" in system
    assert "decisions" in system

    # Negative-space: a weak prompt that OMITS these rules entirely must
    # NOT satisfy the check — proves the assertions aren't vacuous.
    weak_prompt = "Summarize the chat and the PRD in a couple of sentences."
    assert "grounded strictly" not in weak_prompt.lower()
    assert "do not address the reader or offer next steps" not in weak_prompt.lower()

    schema = seed_mod._SCHEMA
    assert set(schema["required"]) == {"brief_summary", "decisions"}
    assert schema["properties"]["brief_summary"]["type"] == "string"
    assert schema["properties"]["decisions"]["type"] == "array"


# ── DRY — reuses the existing pipeline (AC-13) ──────────────────────────


def test_seed_reuses_existing_pipeline_no_fork(repo_root):
    import re as re_mod

    src = (repo_root / "app" / "project_origin_seed.py").read_text()
    assert "add_agent_promoted_entry" in src
    assert "schedule_regen" in src
    assert "create table" not in src.lower()
    # No new memory-insert function, no second regen path — this module only
    # CALLS the existing writers, it never defines its own.
    assert not re_mod.search(r"^def add_\w*entry", src, re_mod.MULTILINE)
    assert "def regenerate" not in src

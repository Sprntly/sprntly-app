"""Tests for the KG write-event hooks fired from PRD generation.

The hooks themselves are no-op stubs today — these tests pin the call
sites so the wire-up PR (FalkorDB + Graphiti) just has to swap bodies.
"""
from __future__ import annotations

import pytest

from app import prd_runner
from app.synthesis import kg_hooks


def _seed_corpus(data_dir, dataset="asurion", body="corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _seed_brief(db_mod, dataset="asurion", insights=None):
    if insights is None:
        insights = [{"title": "Insight A", "subtitle": "behaviour"}]
    payload = {
        "summary_headline": "stub",
        "insights": insights,
        "_schema_version": 1,
    }
    db_mod.insert_dataset(slug=dataset, display_name=dataset.title())
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


_VALID_PRD_MD = (
    "# Stub PRD\n\n"
    ':::problem\n{"user_story": "A user tries x", "impact": []}\n:::\n\n'
    ':::requirements\n[{"behavior": "x"}]\n:::\n\n'
    ':::acceptance-criteria\n[{"id": "AC1"}]\n:::\n'
)


# ---- kg_hooks stubs ---------------------------------------------------------

def test_write_prd_generated_stub_does_not_raise():
    """Today's stub just logs — exercising it must not throw so the
    runner stays exception-free when KG infra isn't wired up yet."""
    kg_hooks.write_prd_generated(
        "decision-123",
        {"prd_id": 1, "title": "t", "payload_md": "# md"},
        workspace_id="ws-1",
    )


def test_write_artifact_edit_stub_does_not_raise():
    kg_hooks.write_artifact_edit(
        "artifact-1",
        original="a",
        edited="b",
        workspace_id="ws-1",
        user_id="u-1",
    )


# ---- prd_runner call site ---------------------------------------------------

def test_run_sync_fires_write_prd_generated(
    isolated_settings, fake_llm, monkeypatch
):
    """A successful PRD generation must invoke write_prd_generated with
    the snapshot + workspace scope. The body is a no-op today, but the
    call site is what the wire-up PR will hang real graph writes off."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: _VALID_PRD_MD)

    calls: list[dict] = []

    def _spy(decision_id, prd_json, *, workspace_id):
        calls.append(
            {
                "decision_id": decision_id,
                "prd_json": prd_json,
                "workspace_id": workspace_id,
            }
        )

    monkeypatch.setattr(prd_runner.kg_hooks, "write_prd_generated", _spy)

    prd_runner._run_sync(prd_id, brief_id, 0)

    assert len(calls) == 1
    call = calls[0]
    # Decision id is synthesised today — the placeholder prefix is the
    # contract the wire-up PR replaces with a real Decision node id.
    assert call["decision_id"].startswith("placeholder-decision-")
    # Workspace falls back to dataset slug until tenancy lands.
    assert call["workspace_id"] == "asurion"
    # Snapshot must include the payload markdown so the graph artifact
    # carries the full agent_output (§5.6 agent_output_snapshot).
    assert call["prd_json"]["prd_id"] == prd_id
    assert call["prd_json"]["payload_md"] == _VALID_PRD_MD
    assert call["prd_json"]["variant"] == "v2"


def test_run_sync_passes_explicit_decision_id_through(
    isolated_settings, fake_llm, monkeypatch
):
    """When the caller has a real decision_id (post-KG-wireup), it must
    flow through to the hook unchanged — no placeholder."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: _VALID_PRD_MD)

    captured: dict = {}
    monkeypatch.setattr(
        prd_runner.kg_hooks,
        "write_prd_generated",
        lambda d, p, *, workspace_id: captured.update(
            decision_id=d, workspace_id=workspace_id
        ),
    )

    prd_runner._run_sync(
        prd_id,
        brief_id,
        0,
        decision_id="decision-real-42",
        workspace_id="ws-acme",
    )

    assert captured["decision_id"] == "decision-real-42"
    assert captured["workspace_id"] == "ws-acme"


# ---- template validation ----------------------------------------------------

def test_run_sync_rejects_prd_missing_required_blocks(
    isolated_settings, fake_llm, monkeypatch
):
    """If the LLM forgets a required block, the runner must raise a
    clear error rather than silently storing a broken PRD."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    # Missing :::requirements and :::acceptance-criteria.
    bad_md = (
        "# half-baked\n\n"
        ':::problem\n{"user_story": "x"}\n:::\n'
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: bad_md)

    with pytest.raises(RuntimeError) as exc:
        prd_runner._run_sync(prd_id, brief_id, 0)
    assert "missing required template fields" in str(exc.value)
    assert "requirements" in str(exc.value)
    assert "acceptance-criteria" in str(exc.value)


def test_run_sync_rejects_prd_with_empty_block(
    isolated_settings, fake_llm, monkeypatch
):
    """A block opener that's immediately followed by its closer counts as
    missing — empty blocks are not acceptable."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    empty_blocks_md = (
        "# empty\n\n"
        ":::problem\n:::\n\n"
        ":::requirements\n:::\n\n"
        ":::acceptance-criteria\n:::\n"
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: empty_blocks_md)

    with pytest.raises(RuntimeError) as exc:
        prd_runner._run_sync(prd_id, brief_id, 0)
    assert "missing required template fields" in str(exc.value)


def test_run_sync_rejects_problem_without_user_story(
    isolated_settings, fake_llm, monkeypatch
):
    """The :::problem block must reference a user_story field — the
    smoke check flags this as a missing `user_stories` template field."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    no_user_story_md = (
        "# no user story\n\n"
        ':::problem\n{"impact": []}\n:::\n\n'
        ':::requirements\n[{"behavior": "x"}]\n:::\n\n'
        ':::acceptance-criteria\n[{"id": "AC1"}]\n:::\n'
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: no_user_story_md)

    with pytest.raises(RuntimeError) as exc:
        prd_runner._run_sync(prd_id, brief_id, 0)
    assert "user_stories" in str(exc.value)


def test_generate_prd_records_validation_failure_in_db(
    isolated_settings, monkeypatch
):
    """When the smoke check fires inside the runner, the async wrapper
    must surface it as status='failed' with the validator's message —
    the UI relies on this to show a clear error rather than a spinner."""
    import asyncio

    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: "# nothing here")

    asyncio.run(prd_runner.generate_prd(prd_id, brief_id, 0))

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "failed"
    assert "missing required template fields" in (row["error"] or "")

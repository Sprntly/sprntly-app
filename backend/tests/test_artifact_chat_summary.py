"""POST /v1/artifacts/chat-summary + app.artifact_summary.

When a PRD / evidence report / prototype finishes generating in the chat's
right panel, the frontend calls this endpoint once and posts the returned text
as an assistant turn — the thread's one-glance record of what got built.

Contract under test:
  - the module is best-effort: empty content → None with NO llm call; gateway
    error → None (never raises); blank/malformed output → None; long content
    clipped to the cap before the call
  - the route reads content server-side behind each kind's own ownership gate
    (foreign ids 404, cross-tenant existence never disclosed)
  - a summarizer failure returns {"summary": null} with HTTP 200 — the chat
    simply goes without
  - prototype rows (no document of their own) summarize the parent PRD plus
    the build instructions, attributed to the design_agent usage bucket

LLM work is mocked at the gateway seam (app.artifact_summary.llm_call).
"""
from __future__ import annotations

import pytest

from app import artifact_summary
from app.graph.gateway import LLMResult

# The `prototypes` table is deliberately NOT in conftest's shared base schema
# (the Design Agent suites each create their own richer copy — see the note at
# conftest._FAKE_SCHEMA). The two prototype tests here add the minimal columns
# start_prototype/get_prototype touch, suite-locally, like those suites do.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL DEFAULT 1,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    created_by_user_id     TEXT,
    current_checkpoint_id  INTEGER,
    share_mode             TEXT NOT NULL DEFAULT 'private',
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
);
"""


@pytest.fixture
def with_prototypes_table(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    return isolated_settings


def _llm_result(output) -> LLMResult:
    return LLMResult(
        output=output, model="m", prompt_version="artifact-chat-summary-v1",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1, stop_reason="end_turn",
    )


def _seed_prd(db_mod, dataset="acme", md="## Goal\nDark mode for mobile users."):
    payload = {"summary_headline": "s", "insights": [{"title": "A"}], "_schema_version": 1}
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="w", payload=payload, schema_version=1
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="Dark mode", md=md)
    return brief_id, prd_id


# ── module ───────────────────────────────────────────────────────────────────


def test_summarize_returns_trimmed_summary(monkeypatch):
    monkeypatch.setattr(
        artifact_summary, "llm_call",
        lambda **kw: _llm_result({"summary": "  Targets mobile users; anchored on system-follow default.  "}),
    )
    out = artifact_summary.summarize_artifact("ent-1", kind="prd", title="t", content="body")
    assert out == "Targets mobile users; anchored on system-follow default."


def test_summarize_empty_content_skips_the_call(monkeypatch):
    called = []
    monkeypatch.setattr(artifact_summary, "llm_call", lambda **kw: called.append(kw))
    assert artifact_summary.summarize_artifact("ent-1", kind="prd", title="t", content="   ") is None
    assert called == []  # no spend on nothing


def test_summarize_fails_open_on_gateway_error(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(artifact_summary, "llm_call", _boom)
    assert artifact_summary.summarize_artifact("ent-1", kind="prd", title="t", content="body") is None


def test_summarize_rejects_blank_or_malformed_output(monkeypatch):
    monkeypatch.setattr(artifact_summary, "llm_call", lambda **kw: _llm_result({"summary": "   "}))
    assert artifact_summary.summarize_artifact("e", kind="prd", title="t", content="b") is None
    monkeypatch.setattr(artifact_summary, "llm_call", lambda **kw: _llm_result("not a dict"))
    assert artifact_summary.summarize_artifact("e", kind="prd", title="t", content="b") is None


def test_summarize_clips_content_and_attributes_the_call(monkeypatch):
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"summary": "ok"})

    monkeypatch.setattr(artifact_summary, "llm_call", _capture)
    artifact_summary.summarize_artifact(
        "ent-42", kind="evidence", title="t", content="x" * 30_000, agent="evidence",
    )
    assert seen["enterprise_id"] == "ent-42"
    assert seen["agent"] == "evidence"
    assert seen["purpose"] == "artifact_chat_summary"
    assert seen["prompt_version"] == artifact_summary.SUMMARY_PROMPT_VERSION
    # The 30k body was clipped to the cap before it reached the model.
    assert len(seen["input"]) < 25_000


def test_summary_prompt_forbids_trailing_offers():
    # The chat-intent resolver adopts the assistant's most recent offer on a
    # bare "yes" — the summary becomes that most recent turn, so the prompt
    # must pin this. Guard the load-bearing instruction.
    assert "Do NOT end with a question or an offer" in artifact_summary._SYSTEM


# ── route ────────────────────────────────────────────────────────────────────


def test_prd_summary_round_trip(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(isolated_settings["db"])
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"summary": "Targets mobile users."})

    monkeypatch.setattr(artifact_summary, "llm_call", _capture)
    resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": "prd", "id": prd_id})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"summary": "Targets mobile users."}
    # Content came from the server-side row, attributed to the caller's company.
    assert "Dark mode for mobile users" in seen["input"]
    assert seen["enterprise_id"] == t.company_id
    assert seen["agent"] == "prd"


def test_evidence_summary_round_trip(tenant_client, isolated_settings, monkeypatch):
    from app.db import evidences as ev

    t = tenant_client.make(slug="acme")
    brief_id, _ = _seed_prd(isolated_settings["db"])
    evidence_id = ev.start_evidence(brief_id, 0, "Evidence: dark mode")
    ev.complete_evidence(evidence_id, "Evidence: dark mode", "<html>42% of sessions at night</html>")
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"summary": "Night usage dominates."})

    monkeypatch.setattr(artifact_summary, "llm_call", _capture)
    resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": "evidence", "id": evidence_id})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"summary": "Night usage dominates."}
    assert "42% of sessions at night" in seen["input"]
    assert seen["agent"] == "evidence"


def test_prototype_summary_uses_parent_prd_and_instructions(
    tenant_client, with_prototypes_table, monkeypatch
):
    from app.db import prototypes as proto_db

    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(with_prototypes_table["db"])
    proto_id = proto_db.start_prototype(
        prd_id=prd_id, workspace_id=t.company_id, template_version=1,
        instructions="Focus on the toggle flow", target_platform="mobile",
    )
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"summary": "A mobile toggle-flow prototype."})

    monkeypatch.setattr(artifact_summary, "llm_call", _capture)
    resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": "prototype", "id": proto_id})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"summary": "A mobile toggle-flow prototype."}
    # The prototype has no document — the substance is instructions + parent PRD.
    assert "Focus on the toggle flow" in seen["input"]
    assert "Dark mode for mobile users" in seen["input"]
    assert seen["agent"] == "design_agent"


def test_foreign_ids_404_without_llm_spend(tenant_client, with_prototypes_table, monkeypatch):
    from app.db import prototypes as proto_db

    t = tenant_client.make(slug="acme")
    rival = tenant_client.make(slug="rival")
    _, foreign_prd = _seed_prd(with_prototypes_table["db"], dataset="rival")
    foreign_proto = proto_db.start_prototype(
        prd_id=foreign_prd, workspace_id=rival.company_id, template_version=1,
    )
    called = []
    monkeypatch.setattr(artifact_summary, "llm_call", lambda **kw: called.append(kw))

    for kind, art_id in (("prd", foreign_prd), ("prototype", foreign_proto)):
        resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": kind, "id": art_id})
        assert resp.status_code == 404, f"{kind} leaked"
    assert called == []  # ownership gates fire before any model spend


def test_summarizer_failure_is_a_null_summary_not_an_error(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(isolated_settings["db"])

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(artifact_summary, "llm_call", _boom)
    resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": "prd", "id": prd_id})
    assert resp.status_code == 200
    assert resp.json() == {"summary": None}


def test_unknown_kind_422s(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = t.client.post("/v1/artifacts/chat-summary", json={"kind": "ticket", "id": 1})
    assert resp.status_code == 422

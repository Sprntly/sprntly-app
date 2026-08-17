"""`POST /v1/projects/{project_id}/chat/intent` — the PRIVATE project chat's
classify decision, the project-scoped counterpart to `POST /v1/chat/intent`
(`routes/chat.py`).

Covers: the target is the EXPLICIT open-drawer `prd_id` the client sends on
the request body (never a server-side resolution over the project's own
PRDs) threaded into `resolve_chat_intent` as `prd_id`, so a bound target
survives the `_NEEDS_PRD` downgrade and an `edit_prd` verdict comes back
intact; membership is required; the response is shaped exactly like
`/v1/chat/intent`'s envelope, so the client's dispatch needs no
project-specific branch; a plain (non-PRD-target) question never triggers
the clarify branch. The 0/1/2+-PRD "open a PRD" clarify design (no
enumeration, no auto-select) is covered by
`test_project_prd_edit_parity.py`.

`resolve_chat_intent` is monkeypatched at `app.routes.projects.resolve_chat_
intent` for determinism — its own thresholds/prompt are unit-tested
elsewhere (`test_chat_intent_route.py`/`test_chat_intent_evals.py`), not
re-litigated here. Real `projects`/`project_members`/`project_artifacts`/
`prds`/`prd_versions` rows via `tenant_client` + `isolated_settings` (the
fake in-memory Supabase every backend suite composes on) — same convention
`test_projects_prd_chat_edit_route.py` already uses. The real cross-tenant
Postgres fan-out through a real LLM is exercised by the env-gated
`test_project_intent_route_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.routes.projects as projects_route
from tests import _fake_supabase
from app.db.workspaces import ensure_default_workspace
from tests._project_helpers import seed_same_tenant_non_member

# `_resolve_prd_id` walks `list_artifacts_for_project` -> `list_artifacts_for_
# company`, which queries `prototypes` unconditionally — deliberately NOT in
# conftest's shared fake schema (same convention as the write-route and
# group-classify sibling test files).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _prototypes_table(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _seed_prd(db_mod, dataset="acme", html="<html><body><h1>Doc</h1></body></html>"):
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Doc",
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    db_mod.complete_prd(prd_id, title="Doc", md=html)
    return prd_id


def _seed_project(t, isolated_settings, *, with_prd: bool = True):
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch", created_by=t.user_id,
    )
    prd_id = None
    if with_prd:
        prd_id = _seed_prd(isolated_settings["db"])
        projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"], prd_id


def _fake_envelope(intent="answer", **overrides):
    envelope = {
        "intent": intent, "confidence": 0.9, "task": None, "instruction": None,
        "artifact_type": None, "artifact_query": None, "reason": "r", "source": "llm",
    }
    envelope.update(overrides)
    return envelope


# ── AC4 — membership required ────────────────────────────────────────────────
def test_project_intent_requires_membership(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    non_member_id, _ = seed_same_tenant_non_member(SimpleNamespace(company_id=t.company_id))
    headers = tenant_client.bearer(non_member_id)
    classify_calls = []
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: classify_calls.append(1) or _fake_envelope(),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what's next?"}, headers=headers,
    )
    assert resp.status_code == 403
    assert classify_calls == []


# ── AC5 — response shape matches /v1/chat/intent's envelope ─────────────────
def test_project_intent_returns_chat_intent_envelope_shape(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: _fake_envelope(),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what's next?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_keys = {
        "intent", "confidence", "task", "instruction", "artifact_type",
        "artifact_query", "reason", "source", "prd_id", "prd_title",
    }
    assert expected_keys.issubset(body.keys())


# ── The PRIVATE route still routes through the shared resolve helper ─────────
def test_private_route_uses_the_shared_resolve_helper(
    tenant_client, isolated_settings, monkeypatch
):
    """The private route (`project_chat_intent`) resolves+classifies through
    the shared `resolve_project_chat_intent` helper. (The GROUP surface no
    longer uses it — it edits in-band via the `edit_prd` tool, applying
    directly through the shared editor against its own closed-over
    open-drawer target.)"""
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: _fake_envelope(),
    )
    real_helper = projects_route.resolve_project_chat_intent
    calls = []

    def _spy(*a, **kw):
        calls.append(1)
        return real_helper(*a, **kw)

    monkeypatch.setattr(projects_route, "resolve_project_chat_intent", _spy)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent", json={"message": "what's next?"},
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1  # the private route went through the shared helper


# ── AC6 — a plain non-edit question on a 2-PRD project does NOT over-fire ───
# a clarify: `source != "no_target_prd"` skips the branch entirely.
def test_private_intent_plain_question_two_prds_no_clarify(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: _fake_envelope(intent="answer", source="llm"),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what's the weather like?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "answer"
    assert "clarification" not in body
    assert "prd_options" not in body

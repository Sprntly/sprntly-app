"""`POST /v1/projects/{project_id}/chat/intent` — the PRIVATE project chat's
classify decision, the project-scoped counterpart to `POST /v1/chat/intent`
(`routes/chat.py`).

Covers: the target is resolved SERVER-side over the project's own PRDs
(never a client-supplied id) and threaded into `resolve_chat_intent` as
`prd_id`, so a project-attached PRD survives the `_NEEDS_PRD` downgrade and
an `edit_prd` verdict comes back intact (AC2/AC5); membership is required
(AC4); the response is shaped exactly like `/v1/chat/intent`'s envelope, so
the client's dispatch needs no project-specific branch (AC5); and the
shared group-classify reference this route mirrors is functionally
unperturbed (AC8's behavioural half — the byte-identity half is a
ship-gate `git diff` check, not a unit test, per TICKET_STANDARD_ADDENDUM).

`resolve_chat_intent` is monkeypatched at `app.routes.projects.resolve_chat_
intent` for determinism — its own thresholds/prompt are unit-tested
elsewhere (`test_chat_intent_route.py`/`test_chat_intent_evals.py`), not
re-litigated here. Real `projects`/`project_members`/`project_artifacts`/
`prds`/`prd_versions` rows via `tenant_client` + `isolated_settings` (the
fake in-memory Supabase every backend suite composes on) — same convention
`test_projects_prd_chat_edit_route.py` and `test_group_chat_prd_edit.py`
already use. The real cross-tenant Postgres fan-out through a real LLM is
exercised by the env-gated `test_project_intent_route_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.prd_questions as prd_questions
import app.routes.projects as projects_route
from tests import _fake_supabase
from app.db.client import require_client
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


def _versions(prd_id):
    return (
        require_client().table("prd_versions").select("*")
        .eq("prd_id", prd_id).execute().data or []
    )


def _payload(prd_id):
    return require_client().table("prds").select("payload_md").eq(
        "id", prd_id
    ).execute().data[0]["payload_md"]


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


# ── AC2/AC5 — server-resolved target survives into resolve_chat_intent ──────
def test_project_intent_resolves_target_and_keeps_edit_prd(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)

    captured_prd_ids = []

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        captured_prd_ids.append(prd_id)
        return _fake_envelope(intent="edit_prd", instruction="tighten it")

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The route passed a NON-None, server-resolved prd_id in — proving the
    # `_NEEDS_PRD` downgrade (chat_intent.py) never had a reason to fire.
    assert captured_prd_ids == [prd_id]
    assert body["intent"] == "edit_prd"
    assert body["prd_id"] == prd_id


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


# ── AC5 — target is server-resolved, never a client id; 0/ambiguous → None ──
def test_project_intent_target_is_server_resolved_not_client(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)  # zero PRDs
    captured_prd_ids = []

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        captured_prd_ids.append(prd_id)
        return _fake_envelope()

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        # Nothing in the request body can even NAME a prd_id (the route only
        # ever reads `{message, conversation_id}`) — this proves the zero-PRD
        # refusal path, not a client-id bypass.
        json={"message": "edit prd 999999 please"},
    )
    assert resp.status_code == 200, resp.text
    assert captured_prd_ids == [None]
    assert resp.json()["prd_id"] is None

    # Ambiguous: two PRDs on the project → also refused to a None target.
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    resp2 = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp2.status_code == 200, resp2.text
    assert captured_prd_ids[-1] is None
    assert resp2.json()["prd_id"] is None


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


# ── AC8 (revised) — the group path is BEHAVIOR-identical, not byte-identical ─
# The single-source extraction (`resolve_project_chat_intent`) makes
# `_classify_and_maybe_edit_group_prd` call a shared helper instead of its
# own inline resolve+classify pair — a deliberate, planner-ratified diff.
# AC8's new contract is behavioural: this test (still green) + the ship-
# gate's live group-edit re-verify, NOT a `git diff` byte-identity check.
def test_group_classify_unchanged(tenant_client, isolated_settings, monkeypatch):
    """Regression: `_classify_and_maybe_edit_group_prd` still resolves+
    classifies (now via the shared `resolve_project_chat_intent` helper) and
    a group edit still applies in place — the extraction must be provably
    behavior-preserving."""
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    before_versions = len(_versions(prd_id))
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten requirements"},
    )
    monkeypatch.setattr(
        prd_questions, "apply_chat_edit",
        lambda *a, **kw: {
            "html": "<html><body><h1>Doc v2</h1></body></html>",
            "sections_changed": ["Requirements"],
            "summary": "Tightened requirements.",
        },
    )
    loop_calls = []
    monkeypatch.setattr(
        "app.llm.run_tool_loop", lambda **kw: loop_calls.append(1) or "unused"
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly tighten the requirements section"},
    )
    assert resp.status_code == 200, resp.text
    assert loop_calls == []  # classified straight to edit_prd, never fell through
    assert "Doc v2" in _payload(prd_id)
    assert len(_versions(prd_id)) == before_versions + 1


# ── Single-source proof: both surfaces resolve through the SAME helper ──────
def test_group_and_private_share_the_resolve_helper(
    tenant_client, isolated_settings, monkeypatch
):
    """The private route (`project_chat_intent`) and the group classifier
    (`_classify_and_maybe_edit_group_prd`) both reach `resolve_project_chat_
    intent` — not their own inline resolve+classify pair — proving the
    extraction is genuinely single-sourced, not just parallel duplicate
    call sites that happen to agree today."""
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

    def _fake_loop(*, system, user, tools, dispatch, model, meta_out=None, **kw):
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 1, "output_tokens": 1,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return "unused"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent", json={"message": "what's next?"},
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1  # the private route went through the shared helper

    resp2 = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly what's next?"},
    )
    assert resp2.status_code == 200, resp2.text
    assert len(calls) == 2  # the group classifier ALSO went through it


# ── AC1 — the private route surfaces the >1-PRD disambiguation as a real ────
# clarify instead of silently discarding it (`refusal`) and returning
# `intent:"answer", prd_id:null`.
def test_private_intent_two_prds_returns_clarify_not_silent_answer(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        # Mirrors the REAL `_NEEDS_PRD` downgrade (chat_intent.py): an
        # edit-phrased message whose target failed to resolve comes back
        # rewritten to `answer`, `source="no_target_prd"` — exactly what
        # `resolve_chat_intent` itself would produce for `prd_id=None`.
        return _fake_envelope(intent="answer", source="no_target_prd")

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # NOT the silent no-op the unfixed route returns.
    assert body["intent"] == "clarify"
    assert body["prd_id"] is None
    assert "more than one PRD" in body["clarification"]
    assert {o["id"] for o in body["prd_options"]} == {prd_a, prd_b}


# ── AC5 — the clarify's `prd_options` is exactly `_project_prd_ids(...)` ────
# for the project — tenant-scoped, never a client-supplied listing.
def test_private_intent_clarify_options_are_project_prds(
    tenant_client, isolated_settings, monkeypatch
):
    from app.project_prd_patch_tool import _project_prd_ids

    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: _fake_envelope(intent="answer", source="no_target_prd"),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    expected = _project_prd_ids(project_id, "acme", t.company_id)
    assert resp.json()["prd_options"] == expected


# ── AC3 — exactly ONE PRD: unchanged edit_prd verdict, no clarify ───────────
def test_private_intent_one_prd_unchanged_edit_prd(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        # A single project PRD resolves — the target survives, no downgrade.
        return _fake_envelope(intent="edit_prd", instruction="tighten it", source="llm")

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "edit_prd"
    assert body["prd_id"] == prd_id
    assert "clarification" not in body
    assert "prd_options" not in body


# ── AC4 — zero PRDs: unchanged honest answer, no clarify ────────────────────
def test_private_intent_zero_prd_unchanged_answer(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: _fake_envelope(intent="answer", source="no_target_prd"),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "answer"
    assert body["prd_id"] is None
    assert "clarification" not in body
    assert "prd_options" not in body


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

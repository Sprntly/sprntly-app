"""§A/★ — `apply_chat_edit_scoped`: the SINGLE writer of `prds.payload_md` in a
project chat context, and its ★ cross-project IDOR gate — exercised here on
the private surface (a future group-chat surface reuses the SAME callable).

Fixtures mirror `test_routes_prd_chat_edit.py` (real `prds`/`prd_versions` rows
via `tenant_client` + `isolated_settings`) combined with
`test_project_prd_gate.py`'s technique of substituting the gate call itself for
a deterministic cross-project/cross-tenant posture. The real Postgres
cross-project/cross-tenant fan-out is exercised by the env-gated
`test_projects_prd_chat_edit_route_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.prd_edit as prd_edit
import app.project_chat_edit as pce
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from app.project_prd_gate import ProjectPrdWriteDenied


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


def _company(company_id: str, workspace_id: str | None):
    return SimpleNamespace(
        company_id=company_id, workspace_id=workspace_id,
        user_id="u1", user_email=None,
    )


def _workspace_id(company_id: str) -> str:
    return ensure_default_workspace(company_id)["id"]


# ── AC4 — keyword-only gate call, BEFORE any read/write ──────────────────────
def test_scoped_edit_calls_gate_kwargs_before_read(monkeypatch):
    calls = []

    def _fake_gate(**kw):
        calls.append(kw)
        raise ProjectPrdWriteDenied("denied")

    def _fail_if_called(*a, **kw):  # noqa: ARG001
        raise AssertionError("require_owned_prd must not run before a denied gate")

    monkeypatch.setattr(pce, "assert_prd_on_project", _fake_gate)
    monkeypatch.setattr(pce, "require_owned_prd", _fail_if_called)

    with pytest.raises(ProjectPrdWriteDenied):
        pce.apply_chat_edit_scoped(
            5, "shorten it", _company("c1", "w1"),
            project_id=1, dataset="acme",
        )
    # Keyword-only, exactly these four keys — a positional call is a
    # guaranteed TypeError against the real gate (project_prd_gate.py:56).
    assert calls == [{"prd_id": 5, "project_id": 1, "dataset": "acme", "company_id": "c1"}]


# ── AC5 — cross-project (in-tenant) refused, zero write ──────────────────────
def test_edit_prd_cross_project_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    before = _payload(prd_id)
    before_versions = len(_versions(prd_id))

    monkeypatch.setattr(
        pce, "assert_prd_on_project",
        lambda **kw: (_ for _ in ()).throw(ProjectPrdWriteDenied("cross-project")),
    )
    editor_called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(ProjectPrdWriteDenied):
        pce.apply_chat_edit_scoped(
            prd_id, "tighten the problem statement",
            _company(t.company_id, _workspace_id(t.company_id)),
            project_id=999, dataset="acme",
        )
    assert editor_called == []
    assert _payload(prd_id) == before
    assert len(_versions(prd_id)) == before_versions


# ── AC6 — cross-tenant refused, zero write ────────────────────────────────────
def test_edit_prd_cross_tenant_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    before = _payload(prd_id)
    before_versions = len(_versions(prd_id))

    # The project gate PASSES (this PRD really is on caller b's named
    # project) — the refusal must come from the cross-TENANT check, proving
    # the two gates are independent, not one masking the other.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    editor_called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(HTTPException) as exc:
        pce.apply_chat_edit_scoped(
            prd_id, "shorten it", _company(b.company_id, _workspace_id(b.company_id)),
            project_id=1, dataset="globex",
        )
    assert exc.value.status_code == 404
    assert editor_called == []
    assert _payload(prd_id) == before
    assert len(_versions(prd_id)) == before_versions
    assert a.company_id != b.company_id


# ── AC7 — own-project edit applies in place + exactly one version ────────────
def test_edit_prd_own_project_in_place_versioned(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    before_versions = len(_versions(prd_id))

    gate_calls = []
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: gate_calls.append(kw))
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2</h1></body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Tightened requirements.",
    })

    result = pce.apply_chat_edit_scoped(
        prd_id, "tighten requirements", _company(t.company_id, _workspace_id(t.company_id)),
        project_id=7, dataset="acme",
    )
    assert gate_calls == [
        {"prd_id": prd_id, "project_id": 7, "dataset": "acme", "company_id": t.company_id}
    ]
    assert result["sections_changed"] == ["Requirements"]
    assert "Doc v2" in _payload(prd_id)

    versions = _versions(prd_id)
    assert len(versions) == before_versions + 1
    assert "Doc v2" not in versions[-1]["payload_md"]  # snapshot is the PRE-edit content

    # In-place — no `prd_patches` row (the retired propose/review shape).
    patches = require_client().table("prd_patches").select("id").eq("prd_id", prd_id).execute().data
    assert patches == []

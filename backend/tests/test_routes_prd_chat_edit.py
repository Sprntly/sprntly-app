"""POST /v1/prd/{prd_id}/chat-edit — free-form chat edits to the PRD.

The chat-driven counterpart of the input-question scoped editor (issue b of
the chat→PRD bug set): an edit-phrased chat message on a PRD tab used to get a
text-only answer while the document never changed. Contract under test:

  - a real edit persists the updated HTML through update_prd_content AND
    snapshots the pre-edit content as an undoable version
  - a no-op verdict (sections_changed == []) leaves the stored document and
    version history untouched
  - editor failure (no usable HTML) → 502, document untouched
  - a PRD with no content yet → 409
  - tenant isolation: a teammate/other company's PRD → 404
  - validation: instruction too short → 422, no editor call

The editor is mocked at the module seam (app.prd_edit.apply_chat_edit —
the route lazy-imports it per call, so patching the source module works).

Also covers the §A extraction's byte-identical guard: the route delegates to
the shared `apply_chat_edit_scoped(project_id=None)` (AC1), and that guard-off
path never touches the ★ cross-project gate (AC3) — `test_project_chat_edit.py`
covers the gate ON (project_id set) side of the same shared callable.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.prd_edit as prd_edit
import app.project_chat_edit as pce
from app.db.client import require_client


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


def test_chat_edit_persists_and_snapshots(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    seen = {}

    def _edit(prd_html, instruction, enterprise_id):
        seen.update(html=prd_html, instruction=instruction, enterprise=enterprise_id)
        return {
            "html": "<html><body><h1>Doc v2</h1></body></html>",
            "sections_changed": ["Requirements", "Goal"],
            "summary": "Tightened both sections.",
        }

    monkeypatch.setattr(prd_edit, "apply_chat_edit", _edit)
    resp = t.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "make this PRD shorter"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sections_changed"] == ["Requirements", "Goal"]
    assert body["summary"] == "Tightened both sections."
    assert "Doc v2" in body["prd"]["payload_md"]

    # The editor saw the stored document + the user's instruction, bound to the
    # acting company.
    assert "Doc" in seen["html"]
    assert seen["instruction"] == "make this PRD shorter"
    assert seen["enterprise"] == t.company_id

    # Persisted: new content stored, pre-edit content snapshotted (undoable).
    assert "Doc v2" in _payload(prd_id)
    vers = _versions(prd_id)
    assert len(vers) == 1 and "Doc v2" not in vers[0]["payload_md"]


def test_chat_edit_noop_leaves_document_untouched(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    before = _payload(prd_id)

    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: {
        "html": before, "sections_changed": [], "summary": "No change requested.",
    })
    resp = t.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "looks good to me"}
    )
    assert resp.status_code == 200
    assert resp.json()["sections_changed"] == []
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []  # no snapshot for a no-op


def test_chat_edit_editor_failure_is_502_and_untouched(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    before = _payload(prd_id)

    def _boom(*a, **kw):
        raise RuntimeError("scoped PRD edit returned no HTML")

    monkeypatch.setattr(prd_edit, "apply_chat_edit", _boom)
    resp = t.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "shorten the prd"}
    )
    assert resp.status_code == 502
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []


def test_chat_edit_conflicts_on_empty_prd(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = db_mod.save_brief(
        dataset="acme", week_label="w",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Generating",
        template_version=1, variant="v3",
    )  # never completed → no payload_md

    called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: called.append(1))
    resp = t.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "shorten the prd"}
    )
    assert resp.status_code == 409
    assert called == []


def test_chat_edit_is_tenant_scoped(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")

    called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a_, **kw: called.append(1))
    resp = b.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "shorten the prd"}
    )
    assert resp.status_code == 404
    assert called == []
    assert a.company_id != b.company_id


def test_chat_edit_validates_instruction(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: called.append(1))

    assert t.client.post(
        f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "ab"}
    ).status_code == 422
    assert called == []


# ── AC1 — the route delegates to the shared callable, guard off ──────────────
def test_chat_edit_route_delegates_guard_off(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    seen = {}

    def _spy(prd_id_, instruction, company, *, project_id=None, dataset=None):
        seen.update(
            prd_id=prd_id_, instruction=instruction,
            company_id=company.company_id, project_id=project_id, dataset=dataset,
        )
        return {"prd": {"payload_md": "ok"}, "sections_changed": [], "summary": ""}

    monkeypatch.setattr(pce, "apply_chat_edit_scoped", _spy)
    resp = t.client.post(f"/v1/prd/{prd_id}/chat-edit", json={"instruction": "shorten it"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"prd": {"payload_md": "ok"}, "sections_changed": [], "summary": ""}
    assert seen == {
        "prd_id": prd_id, "instruction": "shorten it",
        "company_id": t.company_id, "project_id": None, "dataset": None,
    }


# ── AC3 — guard-off never calls the ★ cross-project gate ─────────────────────
def test_scoped_edit_guard_off_never_calls_project_gate(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    gate_called = []
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: gate_called.append(kw))
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2</h1></body></html>",
        "sections_changed": [], "summary": "no-op",
    })
    company = SimpleNamespace(company_id=t.company_id, workspace_id=None, user_id="u1", user_email=None)

    result = pce.apply_chat_edit_scoped(prd_id, "looks fine", company, project_id=None)
    assert gate_called == []
    assert result["sections_changed"] == []

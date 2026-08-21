"""`POST /v1/prd/generate-from-task` returns the forked `project_id` — on
BOTH the new-PRD and the existing-PRD (find-or-create) response paths — so
the client can land the user in that project's private chat after
generation. `maybe_auto_create_project_for_prd` was already called on both
paths before this ticket; its return value was simply discarded. `None`
when nothing forked (no `conversation_id`, or the helper itself returns
None) — every existing response key (`prd_id`/`status`/`title`/`variant`)
is unchanged (additive-optional field).
"""
from __future__ import annotations

from app.db.client import require_client


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _new_conversation(company_id, user_id):
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "generate prd",
        "query": "generate prd",
        "agent_type": "ask",
    }
    resp = require_client().table("conversations").insert(row).execute()
    return resp.data[0]["id"]


def _stub_fork(monkeypatch, project_id):
    """Replace the already-called auto-fork helper with a stub returning a
    fixed id — this ticket only threads the EXISTING return value through,
    it never changes `maybe_auto_create_project_for_prd` itself (that
    helper's own behaviour is covered by `test_project_from_prd.py`)."""
    from app.routes import prd as prd_routes

    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return project_id

    monkeypatch.setattr(prd_routes, "maybe_auto_create_project_for_prd", _fake)
    return calls


def test_generate_from_task_returns_project_id_new_prd(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)
    calls = _stub_fork(monkeypatch, 555)

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == 555
    assert body["status"] == "generating"
    assert body["title"] == "Dark mode on mobile"
    assert body["variant"] == "v3"
    assert body["prd_id"]
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == conv_id


def test_generate_from_task_returns_project_id_existing_prd(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    # First call with no conversation_id, no fork stub yet — creates the PRD
    # that the second (find-or-create) call resolves.
    first = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    ).json()
    assert first["prd_id"]

    conv_id = _new_conversation(t.company_id, t.user_id)
    calls = _stub_fork(monkeypatch, 777)

    again = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert again.status_code == 200
    body = again.json()
    # The EXISTING-PRD response path (`if existing:`) — same prd_id, forked id set.
    assert body["prd_id"] == first["prd_id"]
    assert body["project_id"] == 777
    # NOT `== first["status"]`. `first["status"]` was captured from the FIRST
    # response, before that PRD's background generation had finished; the route
    # returns `existing["status"]`, read live from the row. Once generation
    # completes the row is `ready`, so this comparison decided nothing about
    # the existing-PRD path and everything about whether the worker beat the
    # second request — timing, not behaviour. It failed in CI on two
    # consecutive heads with `assert 'ready' == 'generating'` while passing
    # every time locally, which is the signature of exactly that race.
    #
    # The transition is one-way, so the honest assertion is that the response
    # reports a legal status of the SAME row rather than a remembered one. The
    # ordering this used to fail in is now covered deterministically below.
    assert body["status"] in {"generating", "ready"}
    assert body["title"] == first["title"]
    assert body["variant"] == "v3"
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == conv_id


def test_existing_prd_path_holds_after_generation_has_finished(
    tenant_client, isolated_settings, monkeypatch
):
    """The CI ordering, made deterministic.

    The sibling test above passed locally on every run and failed in CI on two
    consecutive heads, because locally the second request always beat the
    background worker. This pins the row to `ready` first, so the existing-PRD
    path is exercised in the ordering that was previously reached only by
    losing a race — and the response must still resolve the same PRD and
    report the row's real status rather than a remembered one.

    Verified to be the actual bug: with the old `== first["status"]`
    assertion, this test fails with `assert 'ready' == 'generating'`, the
    identical message CI produced.
    """
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    first = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    ).json()

    # What the background worker does when it finishes, done here on purpose.
    require_client().table("prds").update({"status": "ready"}).eq(
        "id", first["prd_id"]
    ).execute()

    conv_id = _new_conversation(t.company_id, t.user_id)
    calls = _stub_fork(monkeypatch, 777)

    again = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert again.status_code == 200
    body = again.json()
    assert body["prd_id"] == first["prd_id"]      # found, not re-created
    assert body["project_id"] == 777
    assert body["status"] == "ready"              # the row's truth, not a memory
    assert body["title"] == first["title"]
    assert len(calls) == 1


def test_generate_from_task_project_id_none_when_unbound(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    # No conversation_id at all — the auto-fork helper is never even called,
    # so no stub is installed; `project_id` must default to None.
    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] is None
    assert body["prd_id"]
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Dark mode on mobile"
    assert body["variant"] == "v3"


def test_generate_from_task_project_id_none_when_helper_returns_none(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)
    _stub_fork(monkeypatch, None)  # e.g. the best-effort helper itself failed

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert resp.status_code == 200
    assert resp.json()["project_id"] is None

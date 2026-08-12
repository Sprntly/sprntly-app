"""Change a PRD's format in place — POST /v1/prd/{id}/change-template.

The missing half of the format-override feature (test_prd_format_override.py):
an override chooses a format at GENERATION time; this path re-writes a document
that already exists. The contracts worth breaking a build over:

  * EVERY GATE ANSWERS BEFORE ANYTHING IS WRITTEN. Ownership 404, not-ready
    409, unusable-target 404/409, and the no-op all return with the row
    untouched — no snapshot, no status flip, no task.
  * THE SWITCH IS ALWAYS UNDOABLE. The current raw payload_md is snapshotted
    to version history before the row goes back to `generating`.
  * FAILURE IS HONEST. A failed regeneration leaves the previous document
    standing and restores `ready` over it; the unchanged
    `artifact_template_id` stamp is what tells the caller the switch did not
    happen. (An in-place regeneration never touches payload_md until
    complete_prd — that fact is what makes the restore truthful.)
  * NULL MEANS THE BUILT-IN, EXPLICITLY. On this route a null target is "back
    to Sprntly's own format" — resolve_prd_template's BUILTIN_FORMAT sentinel,
    which must outrank the company's active format, and whose success clears
    the stamp (set_prd_artifact_template treats None as no-op by design).
"""
from __future__ import annotations

import asyncio

import pytest

from app import prd_runner

_URL = "/v1/artifact-templates"


def _save_brief_with_insights(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Insight A"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload,
        schema_version=1,
    )


def _ready_prd(db_mod, dataset, *, md="<h1>The document</h1>", template_id=None):
    from app.db.prds import set_prd_artifact_template

    brief_id = _save_brief_with_insights(db_mod, dataset)
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=md)
    if template_id:
        set_prd_artifact_template(prd_id, template_id)
    return prd_id


def _add_format(company_id, *, artifact_type="prd", ready=True):
    """One uploaded format row, compiled `ready` unless told otherwise."""
    from app.db.artifact_templates import insert_template, set_compile_result

    row = insert_template(
        company_id=company_id,
        workspace_id="ws-1",
        artifact_type=artifact_type,
        name="Acme PRD v3",
        source_md="# Acme\n",
        content_hash="abc",
        uploader_id="u-1",
        uploader_name="Ada",
    )
    if ready:
        set_compile_result(
            company_id=company_id, template_id=row["id"],
            compile_status="ready", compiled="<h1>{{title}}</h1>",
        )
    return row["id"]


@pytest.fixture
def no_regen(monkeypatch):
    """Capture the background regeneration instead of running it.

    Patched on `app.routes.prd`, whose `from app.prd_runner import …` binding
    is fixed at import — patching the source module would not reach the route.
    """
    calls: list[dict] = []

    # A PLAIN function returning an inert coroutine, not an async def: the
    # route evaluates `regenerate_prd_into_template(...)` eagerly inside
    # `asyncio.create_task(...)`, so recording in the call itself happens
    # synchronously during the request — asserting on `calls` right after the
    # POST is deterministic, where an async capture would record only whenever
    # the test loop next ran the task.
    def _capture(prd_id, **kw):
        calls.append({"prd_id": prd_id, **kw})

        async def _noop():
            return None

        return _noop()

    import app.routes.prd as prd_routes

    monkeypatch.setattr(prd_routes, "regenerate_prd_into_template", _capture)
    return calls


# ─── the gates ───────────────────────────────────────────────────────────────


def test_a_foreign_prd_is_a_404(tenant_client, isolated_settings, no_regen):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    prd_id = _ready_prd(isolated_settings["db"], "acme")

    resp = b.client.post(
        f"/v1/prd/{prd_id}/change-template", json={"artifact_template_id": None}
    )

    assert resp.status_code == 404
    assert no_regen == []
    assert a.client.get(f"/v1/prd/{prd_id}").json()["status"] == "ready"


def test_a_row_still_generating_is_a_409(tenant_client, isolated_settings, no_regen):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, "acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )  # status='generating'

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template", json={"artifact_template_id": None}
    )

    assert resp.status_code == 409
    assert no_regen == []


def test_an_unusable_target_is_refused_before_anything_moves(
    tenant_client, isolated_settings, no_regen
):
    """404 for an id this company does not own; 409 for one that exists but
    cannot write a PRD — and in both cases the row is untouched: no snapshot,
    still `ready`."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    prd_id = _ready_prd(db_mod, "acme")
    tickets_id = _add_format(t.company_id, artifact_type="tickets")

    missing = t.client.post(
        f"/v1/prd/{prd_id}/change-template",
        json={"artifact_template_id": "tpl-does-not-exist"},
    )
    wrong_kind = t.client.post(
        f"/v1/prd/{prd_id}/change-template",
        json={"artifact_template_id": tickets_id},
    )

    assert missing.status_code == 404
    assert wrong_kind.status_code == 409
    assert no_regen == []
    assert t.client.get(f"/v1/prd/{prd_id}").json()["status"] == "ready"
    assert t.client.get(f"/v1/prd/{prd_id}/versions").json() == []


def test_already_in_that_format_is_a_stated_noop(
    tenant_client, isolated_settings, no_regen
):
    """"Make it X" when it is X is a satisfied request: 200, `unchanged`, and
    nothing written — no snapshot, no status flip, no task."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    template_id = _add_format(t.company_id)
    prd_id = _ready_prd(db_mod, "acme", template_id=template_id)

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template",
        json={"artifact_template_id": template_id},
    )

    assert resp.status_code == 200
    assert resp.json()["unchanged"] is True
    assert resp.json()["status"] == "ready"
    assert no_regen == []
    assert t.client.get(f"/v1/prd/{prd_id}/versions").json() == []


def test_builtin_to_builtin_is_the_same_noop(
    tenant_client, isolated_settings, no_regen
):
    t = tenant_client.make(slug="acme")
    prd_id = _ready_prd(isolated_settings["db"], "acme")  # unstamped = built-in

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template", json={"artifact_template_id": None}
    )

    assert resp.status_code == 200
    assert resp.json()["unchanged"] is True
    assert no_regen == []


def test_a_prd_with_no_content_is_a_409(tenant_client, isolated_settings, no_regen):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    template_id = _add_format(t.company_id)
    prd_id = _ready_prd(db_mod, "acme", md="")

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template",
        json={"artifact_template_id": template_id},
    )

    assert resp.status_code == 409
    assert no_regen == []


# ─── the switch ──────────────────────────────────────────────────────────────


def test_the_switch_snapshots_then_regenerates_in_place(
    tenant_client, isolated_settings, no_regen
):
    """The happy path, end to end at the route: undo point captured from the
    raw payload, row back in flight, and the regeneration handed the current
    document as its only source material."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    template_id = _add_format(t.company_id)
    prd_id = _ready_prd(db_mod, "acme", md="<h1>The document</h1>")

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template",
        json={"artifact_template_id": template_id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "generating"
    assert body["artifact_template_id"] == template_id

    versions = t.client.get(f"/v1/prd/{prd_id}/versions").json()
    assert len(versions) == 1

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "generating"
    # The document itself is untouched until the regeneration completes.
    assert row["payload_md"] == "<h1>The document</h1>"

    assert len(no_regen) == 1
    call = no_regen[0]
    assert call["prd_id"] == prd_id
    assert call["source_md"] == "<h1>The document</h1>"
    assert call["artifact_template_id"] == template_id
    assert call["company_id"] == t.company_id


def test_switching_back_to_the_builtin_is_allowed(
    tenant_client, isolated_settings, no_regen
):
    """null on THIS route is a real choice ("back to Sprntly's format"), not
    the "no preference" it means on the generate routes."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    template_id = _add_format(t.company_id)
    prd_id = _ready_prd(db_mod, "acme", template_id=template_id)

    resp = t.client.post(
        f"/v1/prd/{prd_id}/change-template", json={"artifact_template_id": None}
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "generating"
    assert resp.json()["artifact_template_id"] is None
    assert len(no_regen) == 1
    assert no_regen[0]["artifact_template_id"] is None


# ─── the regeneration runner ─────────────────────────────────────────────────


def _run(coro):
    asyncio.run(coro)


def test_the_runner_rides_the_import_paths_faithful_relayout(monkeypatch):
    """The current document IS the source material (`import_source_md`), and a
    null target travels as the BUILTIN_FORMAT sentinel — a bare None would
    resolve to the company's ACTIVE format, the exact wrong answer for
    "switch back to the built-in"."""
    seen: dict = {}

    async def _fake_generate(prd_id, brief_id, insight_index, **kw):
        seen.update({"prd_id": prd_id, "brief_id": brief_id, **kw})

    monkeypatch.setattr(prd_runner, "generate_prd_and_warm", _fake_generate)
    monkeypatch.setattr(
        prd_runner, "get_prd", lambda pid: {"status": "ready"}
    )
    cleared: list = []
    monkeypatch.setattr(
        prd_runner, "clear_prd_artifact_template", lambda pid: cleared.append(pid)
    )

    _run(prd_runner.regenerate_prd_into_template(
        7, source_md="<h1>Doc</h1>", brief_id=3, insight_index=0,
        title="Doc", artifact_template_id=None, company_id="co-1",
    ))

    assert seen["import_source_md"] == "<h1>Doc</h1>"
    assert seen["artifact_template_id"] == prd_runner.BUILTIN_FORMAT
    assert seen["insight_override"]["title"] == "Doc"
    # Success + built-in target = the stamp is explicitly cleared.
    assert cleared == [7]


def test_a_successful_switch_to_a_real_format_keeps_the_new_stamp(monkeypatch):
    """`_finalize_part_a` stamped the resolver's answer during generation; the
    runner must not second-guess it — no clear, no ready-restore."""
    async def _fake_generate(*a, **kw):
        return None

    monkeypatch.setattr(prd_runner, "generate_prd_and_warm", _fake_generate)
    monkeypatch.setattr(
        prd_runner, "get_prd",
        lambda pid: {"status": "ready", "artifact_template_id": "tpl-b"},
    )
    touched: list = []
    monkeypatch.setattr(
        prd_runner, "clear_prd_artifact_template",
        lambda pid: touched.append(("clear", pid)),
    )
    monkeypatch.setattr(
        prd_runner, "mark_prd_ready", lambda pid: touched.append(("ready", pid))
    )

    _run(prd_runner.regenerate_prd_into_template(
        7, source_md="<h1>Doc</h1>", brief_id=3, insight_index=0,
        title="Doc", artifact_template_id="tpl-b", company_id="co-1",
    ))

    assert touched == []


def test_a_failed_regeneration_restores_the_previous_document(monkeypatch):
    """fail_prd wrote status='failed' but never touched payload_md — the
    previous document is intact, so `ready` is its truthful status. The stamp
    keeps its OLD value, which is how the client learns the switch failed."""
    async def _fake_generate(*a, **kw):
        return None  # generate_prd swallowed its own failure and fail_prd'd

    monkeypatch.setattr(prd_runner, "generate_prd_and_warm", _fake_generate)
    monkeypatch.setattr(
        prd_runner, "get_prd",
        lambda pid: {"status": "failed", "artifact_template_id": "tpl-old"},
    )
    restored: list = []
    monkeypatch.setattr(
        prd_runner, "mark_prd_ready", lambda pid: restored.append(pid)
    )
    cleared: list = []
    monkeypatch.setattr(
        prd_runner, "clear_prd_artifact_template", lambda pid: cleared.append(pid)
    )

    _run(prd_runner.regenerate_prd_into_template(
        7, source_md="<h1>Doc</h1>", brief_id=3, insight_index=0,
        title="Doc", artifact_template_id="tpl-b", company_id="co-1",
    ))

    assert restored == [7]
    assert cleared == []


def test_the_restore_runs_even_when_generation_raises(monkeypatch):
    """generate_prd_and_warm can propagate (the stream-close finally re-raises)
    — the restore is in a finally so the row can still never be left buried
    behind a failed status with a good document inside."""
    async def _boom(*a, **kw):
        raise RuntimeError("part A exploded")

    monkeypatch.setattr(prd_runner, "generate_prd_and_warm", _boom)
    monkeypatch.setattr(
        prd_runner, "get_prd", lambda pid: {"status": "failed"}
    )
    restored: list = []
    monkeypatch.setattr(
        prd_runner, "mark_prd_ready", lambda pid: restored.append(pid)
    )

    with pytest.raises(RuntimeError):
        _run(prd_runner.regenerate_prd_into_template(
            7, source_md="<h1>Doc</h1>", brief_id=3, insight_index=0,
            title="Doc", artifact_template_id="tpl-b", company_id="co-1",
        ))

    assert restored == [7]


# ─── the resolver sentinel ───────────────────────────────────────────────────


def test_the_builtin_sentinel_outranks_the_active_format(monkeypatch):
    """The whole reason the sentinel exists: None has always meant "whatever is
    active", so "back to the built-in" needs a spelling that beats it."""
    monkeypatch.setattr(
        prd_runner, "get_active_template",
        lambda cid, kind: {
            "id": "tpl-active", "artifact_type": "prd",
            "compile_status": "ready", "compiled": "<h1>ACTIVE</h1>",
        },
    )
    monkeypatch.setattr(
        prd_runner, "_load_part_a_template", lambda: "<h1>BUILT-IN</h1>"
    )

    template, template_id = prd_runner.resolve_prd_template(
        "co-1", prd_runner.BUILTIN_FORMAT
    )

    assert template == "<h1>BUILT-IN</h1>"
    assert template_id is None

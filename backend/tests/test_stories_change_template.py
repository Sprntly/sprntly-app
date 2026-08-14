"""In-place ticket format switch — POST /v1/stories/change-template.

The tickets counterpart of test_prd_change_template.py, with one structural
difference that most of these tests exist to pin: the switch is a re-LAYOUT,
not a regeneration. The contracts worth breaking a build over:

  * EVERY GATE ANSWERS BEFORE ANYTHING IS WRITTEN. Ownership 404, not-ready
    409, unusable-target 404/409, exactly-one-target 400 — all return with the
    stored stories untouched.
  * IDENTITY SURVIVES THE SWITCH. Every story keeps its stable id (pinned
    through Story.from_dict), because tracker mappings, per-ticket edits and
    comments are keyed on it — a switch that re-hashed ids would orphan every
    synced issue in a customer's live tracker.
  * THE HASH IS NOT TOUCHED. `content_hash` records which PRD content the
    tickets describe; layout is orthogonal, so a switch must not fake
    freshness (or break it).
  * NULL MEANS THE BUILT-IN, EXPLICITLY. A stamped set switching back gets a
    bare None layout — never the company's ACTIVE format, which is what a
    None would mean on the generate path (the resolve_ticket_layout trap
    layout_for_template exists to avoid).
  * THE FILL FAILS OPEN. Custom sections the target format asks for are
    filled by one gateway call; that call failing leaves them empty and the
    switch still lands (empty sections are skipped at render).
"""
from __future__ import annotations

import json

import pytest

from app.stories.generate import Story

_URL = "/v1/stories/change-template"

# A ticket format whose compiled artifact is a stored layout: two renamed
# canonical sections plus one custom one.
_ACME_LAYOUT = [
    {"label": "Summary", "source": "what"},
    {"label": "Story", "source": "user_story"},
    {"label": "QA owner", "source": "custom:qa_owner"},
]


def _add_ticket_format(company_id, *, name="Acme Tickets", layout=_ACME_LAYOUT,
                       ready=True):
    from app.db.artifact_templates import insert_template, set_compile_result

    row = insert_template(
        company_id=company_id,
        workspace_id="ws-1",
        artifact_type="tickets",
        name=name,
        source_md="# Acme tickets\n",
        content_hash=f"hash-{name}",
        uploader_id="u-1",
        uploader_name="Ada",
    )
    if ready:
        set_compile_result(
            company_id=company_id, template_id=row["id"],
            compile_status="ready", compiled=json.dumps(layout),
        )
    return row["id"]


def _stories() -> list[dict]:
    return [
        Story(title="Login retry", body="As a user I retry login",
              what="Retry flow", why_now="Support load",
              user_story="As a user I retry login",
              scope=["backend", "web"], out_of_scope="SSO",
              acceptance_criteria=["Given/When/Then"]).to_dict(),
        Story(title="Audit log", body="As an admin I see logins",
              what="Audit trail").to_dict(),
    ]


def _ready_prd(db_mod, dataset: str) -> int:
    """A real, ready prds row — prd_tickets.prd_id carries an enforced FK in
    the fake schema (seeded from the live migrations), so the cache row must
    hang off an actual PRD, exactly as production rows do."""
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "stub", "insights": [{"title": "Insight A"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="<h1>The document</h1>")
    return prd_id


def _seed_prd_tickets(db_mod, company_id: str, *, dataset: str = "acme",
                      status: str = "ready", stories=None,
                      template_id: str | None = None,
                      content_hash: str = "hash-original") -> int:
    from app.db.client import require_client

    prd_id = _ready_prd(db_mod, dataset)
    require_client().table("prd_tickets").insert({
        "company_id": company_id,
        "prd_id": prd_id,
        "content_hash": content_hash,
        "stories": stories if stories is not None else _stories(),
        "status": status,
        "artifact_template_id": template_id,
    }).execute()
    return prd_id


def _seed_set(company_id: str, *, status: str = "ready", stories=None,
              template_id: str | None = None) -> int:
    from app.db.client import require_client

    return require_client().table("ticket_sets").insert({
        "company_id": company_id,
        "title": "T",
        "stories": stories if stories is not None else _stories(),
        "status": status,
        "source_text": "make tickets",
        "artifact_template_id": template_id,
    }).execute().data[0]["id"]


def _get_prd_tickets_row(company_id: str, prd_id: int) -> dict:
    from app.db.prd_tickets import get_tickets

    return get_tickets(company_id, prd_id)


# ─── the gates ───────────────────────────────────────────────────────────────


def test_both_or_neither_target_is_a_400(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    assert t.client.post(_URL, json={"artifact_template_id": None}).status_code == 400
    assert t.client.post(
        _URL, json={"prd_id": 1, "ticket_set_id": 1, "artifact_template_id": None}
    ).status_code == 400


def test_missing_and_foreign_rows_are_404(tenant_client, isolated_settings):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    prd_id = _seed_prd_tickets(isolated_settings["db"], a.company_id)
    set_id = _seed_set(a.company_id)

    assert a.client.post(
        _URL, json={"prd_id": 999, "artifact_template_id": None}
    ).status_code == 404
    assert b.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": None}
    ).status_code == 404
    assert b.client.post(
        _URL, json={"ticket_set_id": set_id, "artifact_template_id": None}
    ).status_code == 404
    # The foreign attempts wrote nothing.
    row = _get_prd_tickets_row(a.company_id, prd_id)
    assert [s["title"] for s in row["stories"]] == ["Login retry", "Audit log"]


def test_an_unusable_target_format_is_refused(tenant_client, isolated_settings):
    """404 for an id this company does not own; 409 for one that exists but is
    the wrong kind (a PRD format cannot lay out tickets)."""
    from app.db.artifact_templates import insert_template, set_compile_result

    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id)
    prd_format = insert_template(
        company_id=t.company_id, workspace_id="ws-1", artifact_type="prd",
        name="Acme PRD", source_md="# p", content_hash="h", uploader_id="u",
        uploader_name="Ada",
    )["id"]
    set_compile_result(
        company_id=t.company_id, template_id=prd_format,
        compile_status="ready", compiled="<h1>{{title}}</h1>",
    )

    assert t.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": "tpl-nope"}
    ).status_code == 404
    assert t.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": prd_format}
    ).status_code == 409


def test_generating_and_empty_sets_are_409(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    generating = _seed_set(t.company_id, status="generating")
    empty = _seed_set(t.company_id, stories=[])

    assert t.client.post(
        _URL, json={"ticket_set_id": generating, "artifact_template_id": template_id}
    ).status_code == 409
    assert t.client.post(
        _URL, json={"ticket_set_id": empty, "artifact_template_id": template_id}
    ).status_code == 409


def test_already_in_that_format_is_a_stated_noop(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id, template_id=template_id)

    resp = t.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": template_id}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is True
    assert body["artifact_template_id"] == template_id
    assert body["artifact_template_name"] == "Acme Tickets"
    assert "stories" not in body


def test_builtin_to_builtin_is_the_same_noop(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id)  # unstamped = built-in

    resp = t.client.post(_URL, json={"prd_id": prd_id, "artifact_template_id": None})
    assert resp.status_code == 200
    assert resp.json()["unchanged"] is True


# ─── the switch ──────────────────────────────────────────────────────────────


def test_the_switch_relays_stamps_and_keeps_identity(
    tenant_client, isolated_settings
):
    """The happy path on a PRD's tickets: every story re-stamped with the new
    layout, ids untouched, content untouched, hash untouched, stamp updated —
    and the response carries the re-laid stories so the client re-renders
    without another read."""
    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    before = _stories()
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id, stories=before)

    resp = t.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": template_id}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["artifact_template_id"] == template_id
    assert body["artifact_template_name"] == "Acme Tickets"

    # Identity + content preserved; layout swapped.
    assert [s["id"] for s in body["stories"]] == [s["id"] for s in before]
    assert [s["title"] for s in body["stories"]] == ["Login retry", "Audit log"]
    for s in body["stories"]:
        assert s["description_layout"] == _ACME_LAYOUT

    # Persisted the same way, with the hash NOT touched.
    row = _get_prd_tickets_row(t.company_id, prd_id)
    assert row["artifact_template_id"] == template_id
    assert row["content_hash"] == "hash-original"
    assert all(s["description_layout"] == _ACME_LAYOUT for s in row["stories"])


def test_the_switch_works_on_a_standalone_set(tenant_client, isolated_settings):
    from app.db.ticket_sets import get_set

    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    set_id = _seed_set(t.company_id)

    resp = t.client.post(
        _URL, json={"ticket_set_id": set_id, "artifact_template_id": template_id}
    )

    assert resp.status_code == 200
    assert resp.json()["artifact_template_id"] == template_id
    row = get_set(t.company_id, set_id)
    assert row["artifact_template_id"] == template_id
    assert all(s["description_layout"] == _ACME_LAYOUT for s in row["stories"])


def test_switching_back_to_the_builtin_clears_the_layout(
    tenant_client, isolated_settings
):
    """null on THIS route is a real choice ("back to Sprntly's layout") and
    must NOT resolve to the company's active format — the resolve_ticket_layout
    trap layout_for_template exists to avoid. An ACTIVE format is seeded
    precisely so this test fails if that trap is ever reintroduced."""
    from app.db.artifact_templates import activate_template

    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    activate_template(t.company_id, "tickets", template_id)
    stamped = [
        {**s, "description_layout": _ACME_LAYOUT} for s in _stories()
    ]
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id, stories=stamped,
                               template_id=template_id)

    resp = t.client.post(_URL, json={"prd_id": prd_id, "artifact_template_id": None})

    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_template_id"] is None
    assert all("description_layout" not in s for s in body["stories"])
    row = _get_prd_tickets_row(t.company_id, prd_id)
    assert row["artifact_template_id"] is None


def test_the_fill_failing_still_lands_the_switch(
    tenant_client, isolated_settings, monkeypatch
):
    """The custom-section fill is one gateway call and it FAILS OPEN: an
    exploding call leaves the sections empty (skipped at render) and the
    switch still persists."""
    import app.stories.relayout as relayout_mod

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(relayout_mod, "llm_call", _boom)

    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id)

    resp = t.client.post(
        _URL, json={"prd_id": prd_id, "artifact_template_id": template_id}
    )

    assert resp.status_code == 200
    row = _get_prd_tickets_row(t.company_id, prd_id)
    assert row["artifact_template_id"] == template_id
    for s in row["stories"]:
        assert s["description_layout"] == _ACME_LAYOUT
        assert not s.get("custom_sections")


def test_the_fill_grounds_only_missing_keys_and_clamps(monkeypatch):
    """Unit-level: the fill only writes keys a ticket was missing, ignores
    invented keys, and never overwrites existing custom content."""
    import app.stories.relayout as relayout_mod
    from types import SimpleNamespace

    calls: list[dict] = []

    def _fake_llm(**kw):
        calls.append(kw)
        return SimpleNamespace(output={"tickets": [
            {"id": kw["_id_a"], "sections": {
                "qa_owner": "QA lead reviews",
                "invented_key": "should be dropped",
            }},
        ]})

    a = Story(title="A", body="story a")
    b = Story(title="B", body="story b",
              custom_sections={"qa_owner": "Already set"})

    def _llm(**kw):
        return _fake_llm(_id_a=a.stable_id(), **kw)

    monkeypatch.setattr(relayout_mod, "llm_call", _llm)

    relayout_mod._fill_custom_sections(
        "ent-1", [a, b],
        [{"label": "QA owner", "source": "custom:qa_owner"}],
    )

    assert a.custom_sections == {"qa_owner": "QA lead reviews"}
    # B already had content — it was never asked for and never overwritten.
    assert b.custom_sections == {"qa_owner": "Already set"}
    assert len(calls) == 1
    assert a.stable_id() in calls[0]["input"]
    assert b.stable_id() not in calls[0]["input"]


def test_no_custom_keys_means_no_llm_call(monkeypatch):
    """A layout of purely canonical sections is a pure metadata swap — the
    switch must be instant, with no gateway call at all."""
    import app.stories.relayout as relayout_mod

    def _boom(**kw):
        raise AssertionError("must not be called")

    monkeypatch.setattr(relayout_mod, "llm_call", _boom)

    out = relayout_mod.relayout_stories(
        "ent-1", _stories(),
        [{"label": "Summary", "source": "what"},
         {"label": "Story", "source": "user_story"}],
    )
    assert all(
        s["description_layout"] == [
            {"label": "Summary", "source": "what"},
            {"label": "Story", "source": "user_story"},
        ]
        for s in out
    )


# ─── the read routes carry the stamp ─────────────────────────────────────────


def test_for_prd_carries_the_stamp_and_its_name(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    # Hash freshness is irrelevant here — only the stamp fields are under test.
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id, template_id=template_id)

    body = t.client.get(f"/v1/stories/for-prd/{prd_id}").json()
    assert body["artifact_template_id"] == template_id
    assert body["artifact_template_name"] == "Acme Tickets"


def test_ticket_set_get_carries_the_stamp_and_its_name(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    template_id = _add_ticket_format(t.company_id)
    set_id = _seed_set(t.company_id, template_id=template_id)

    body = t.client.get(f"/v1/ticket-sets/{set_id}").json()
    assert body["artifact_template_id"] == template_id
    assert body["artifact_template_name"] == "Acme Tickets"


def test_unstamped_rows_read_as_the_builtin(tenant_client, isolated_settings):
    """Legacy rows (stored before the stamp column existed) must read as the
    built-in — id and name both null, never an error."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd_tickets(isolated_settings["db"], t.company_id)
    set_id = _seed_set(t.company_id)

    prd_body = t.client.get(f"/v1/stories/for-prd/{prd_id}").json()
    set_body = t.client.get(f"/v1/ticket-sets/{set_id}").json()
    assert prd_body["artifact_template_id"] is None
    assert prd_body["artifact_template_name"] is None
    assert set_body["artifact_template_id"] is None
    assert set_body["artifact_template_name"] is None

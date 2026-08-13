"""Tests for the project artifacts fan-out: `db/artifacts.py::list_artifacts_for_project`,
`db/projects.py::add_artifact`/`list_project_artifact_refs`, and the
`GET`/`POST /v1/projects/{id}/artifacts` routes.

Covers:
  - the reader reuses `list_artifacts_for_company` verbatim, filtered to the
    project's `(type, id)` refs (AD-P1/AD-P12) — zero new per-table scoping
  - shape parity with `GET /v1/artifacts`
  - write-time access validation (AD-P12, the IDOR guard/R3): prd/evidence
    via their dedicated ownership deps, prototype/report/ticket_set via
    presence in the caller's own company fan-out — a foreign artifact 404s
    and writes no ref
  - the membership gate (AD-P11): a same-tenant non-member is 403'd on both
    GET and POST, distinct from the cross-tenant 404
  - tolerated-stale reads (a deleted/foreign artifact's ref silently drops)
  - dedupe on repeat add + `updated_at` touch
  - the observability log line carries only identifiers

Mirrors the fixture style of test_routes_artifacts.py: `company_client`
gives a JWT-authed TestClient with a seeded company + membership; the
`prototypes` table is added on top of conftest's base fake schema (which
already has briefs/prds/evidences/reports/ticket_sets/projects/
project_artifacts — see the "NOTE: the `prototypes` table is intentionally
NOT in this shared base schema" comment in conftest.py).
"""
from __future__ import annotations

import logging

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client, seed_company
from tests._project_helpers import seed_same_tenant_non_member

# SQLite translation of supabase/migrations/20260528000000_design_agent_prototypes.sql
# (the columns the fan-out reads + the workspace_id scope) — same DDL as
# test_routes_artifacts.py's local copy (each artifact-fanout test file owns
# its own, per that file's own docstring note on why `prototypes` isn't in
# the shared base schema).
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
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    preview_image_url      TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT
);
"""


@pytest.fixture
def artifacts_env(isolated_settings):
    """Add the prototypes table to conftest's already-reset fake DB."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


# ── Seed helpers (write directly through the fake Supabase client) ─────────


def _seed_brief(*, dataset: str, week_label: str) -> int:
    import json

    from app.db.client import require_client

    resp = require_client().table("briefs").insert(
        {"dataset": dataset, "week_label": week_label, "payload": json.dumps({}), "is_current": True}
    ).execute()
    return resp.data[0]["id"]


def _seed_prd(
    *, brief_id: int, title: str, insight_index: int = 0, status: str = "ready",
    generated_at: str | None = None,
) -> int:
    from app.db.client import require_client

    payload = {"brief_id": brief_id, "insight_index": insight_index, "title": title, "status": status}
    # Only set when the caller cares about ORDER within a family (regenerate
    # scenarios below) — sqlite's `datetime('now')` default is second-precision,
    # so two same-second inserts would otherwise tie on "newest".
    if generated_at is not None:
        payload["generated_at"] = generated_at
    resp = require_client().table("prds").insert(payload).execute()
    return resp.data[0]["id"]


def _seed_evidence(*, brief_id: int, title: str, insight_index: int = 0) -> int:
    from app.db.client import require_client

    resp = require_client().table("evidences").insert(
        {"brief_id": brief_id, "insight_index": insight_index, "title": title, "status": "ready"}
    ).execute()
    return resp.data[0]["id"]


def _seed_prototype(*, prd_id: int, workspace_id: str, status: str = "ready") -> int:
    from app.db.client import require_client

    resp = require_client().table("prototypes").insert(
        {"prd_id": prd_id, "workspace_id": workspace_id, "status": status, "template_version": 1}
    ).execute()
    return resp.data[0]["id"]


def _seed_report(*, company_id: str, skill: str, title: str) -> int:
    from app.db.client import require_client

    resp = require_client().table("reports").insert(
        {"company_id": company_id, "skill": skill, "title": title}
    ).execute()
    return resp.data[0]["id"]


def _seed_ticket_set(*, company_id: str, title: str, stories: list | None = None, status: str = "ready") -> int:
    from app.db.client import require_client

    resp = require_client().table("ticket_sets").insert(
        {"company_id": company_id, "title": title, "stories": stories or [], "status": status}
    ).execute()
    return resp.data[0]["id"]


def _create_project(ctx, *, name: str = "Fan-out project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_ref(project_id: int, artifact_type: str, artifact_id: int) -> None:
    from app.db.client import require_client

    require_client().table("project_artifacts").insert(
        {"project_id": project_id, "artifact_type": artifact_type, "artifact_id": artifact_id}
    ).execute()


# ── Retrieval (round-trip) ──────────────────────────────────────────────────


def test_list_artifacts_for_project_filters_to_refs(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    in_scope = _seed_prd(brief_id=brief_id, title="In this project")
    out_of_scope = _seed_prd(brief_id=brief_id, title="Not in this project", insight_index=1)
    _seed_ref(project["id"], "prd", in_scope)

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert ids == [in_scope]
    assert out_of_scope not in ids


def test_list_reuses_company_fanout(artifacts_env, monkeypatch):
    """AC2 — `list_artifacts_for_project` calls the EXISTING
    `list_artifacts_for_company`, not a re-implemented per-table query."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Spied PRD")
    _seed_ref(project["id"], "prd", prd_id)

    calls = []
    from app.db import artifacts as artifacts_db

    real_fanout = artifacts_db.list_artifacts_for_company

    def _spy(**kwargs):
        calls.append(kwargs)
        return real_fanout(**kwargs)

    monkeypatch.setattr(artifacts_db, "list_artifacts_for_company", _spy)

    items = artifacts_db.list_artifacts_for_project(
        project_id=project["id"], dataset="acme", company_id=ctx.company_id
    )
    assert len(calls) == 1
    assert calls[0] == {"dataset": "acme", "company_id": ctx.company_id}
    assert [i["id"] for i in items] == [prd_id]


def test_shape_matches_artifacts_endpoint(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Shape check")
    _seed_ref(project["id"], "prd", prd_id)

    r_project = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    r_company = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r_project.status_code == 200 and r_company.status_code == 200

    project_item = next(a for a in r_project.json()["artifacts"] if a["type"] == "prd")
    company_item = next(a for a in r_company.json()["artifacts"] if a["type"] == "prd")
    assert set(project_item.keys()) == set(company_item.keys())
    assert {"type", "id", "title", "status", "created_at", "source", "open"} <= set(project_item.keys())


def test_empty_project_artifacts(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    assert r.json()["artifacts"] == []


def test_deleted_artifact_ref_omitted(artifacts_env, monkeypatch):
    """A ref pointing at an artifact that no longer resolves (deleted PRD
    row) silently drops — no error (AD-P1/§4.3, AC3)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _seed_ref(project["id"], "prd", 999999)  # no such prd row

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    assert r.json()["artifacts"] == []


# ── Regenerate-stays-attached (resolve-forward-on-read) ─────────────────────


def test_regenerated_prd_stays_in_project(artifacts_env, monkeypatch):
    """A PRD pinned to a project, then regenerated (`force=True` mints a new
    `prds.id` in the same family; the old row stays `ready` — the project's
    `project_artifacts` ref is NEVER re-pointed, mirroring
    `maybe_auto_create_project_for_prd`'s already-bound early-return). The
    RETURNED artifact list must still contain exactly one PRD entry for the
    family — asserted on the list itself, never on a raw ref/row count (AC1)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_a = _seed_prd(
        brief_id=brief_id, title="Onboarding flow", generated_at="2026-08-01T00:00:00Z"
    )
    _seed_ref(project["id"], "prd", prd_a)

    # The regenerate: a NEW prds row in the SAME family (same brief_id +
    # insight_index), newer generated_at. The project's ref still points at
    # prd_a — nothing re-pins it.
    _seed_prd(
        brief_id=brief_id, title="Onboarding flow (v2)", generated_at="2026-08-02T00:00:00Z"
    )

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    prd_items = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prd_items) == 1


def test_project_surfaces_current_generation(artifacts_env, monkeypatch):
    """The surfaced PRD entry is the family's CURRENT (newest) generation —
    a member opening it from the project reaches the regenerated content,
    not the stale pre-regenerate row (AC2)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_a = _seed_prd(
        brief_id=brief_id, title="Onboarding flow", generated_at="2026-08-01T00:00:00Z"
    )
    _seed_ref(project["id"], "prd", prd_a)
    prd_b = _seed_prd(
        brief_id=brief_id, title="Onboarding flow (v2)", generated_at="2026-08-02T00:00:00Z"
    )

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    prd_items = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prd_items) == 1
    assert prd_items[0]["id"] == prd_b
    assert prd_items[0]["title"] == "Onboarding flow (v2)"


def test_superseded_pin_resolves_to_one_prd(artifacts_env, monkeypatch):
    """A family regenerated TWICE (three generations total) still resolves
    the pin — planted on the ORIGINAL generation — to exactly one surfaced
    PRD, never zero/two (AC1, AC5)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_a = _seed_prd(
        brief_id=brief_id, title="Gen 1", generated_at="2026-08-01T00:00:00Z"
    )
    _seed_ref(project["id"], "prd", prd_a)
    _seed_prd(brief_id=brief_id, title="Gen 2", generated_at="2026-08-02T00:00:00Z")
    prd_c = _seed_prd(brief_id=brief_id, title="Gen 3", generated_at="2026-08-03T00:00:00Z")

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    prd_items = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prd_items) == 1
    assert prd_items[0]["id"] == prd_c


def test_resolve_forward_stays_within_family_and_dedupes(artifacts_env, monkeypatch):
    """The wave's must-be-airtight guard (AC5): resolve-forward

      1. stays within `list_prd_generations(pinned_id)`'s OWN family — a
         second, independently-superseded family on the SAME brief resolves
         to ITS OWN current generation, never to the other family's;
      2. dedupes by surfaced id — a family pinned TWICE (once at its stale
         id, once at its own current id) still surfaces exactly once.

    Loosening either property would change WHICH PRD a private-chat edit
    lands on, via `_resolve_prd_id`/`prd_on_project` reading this same
    function."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")

    # Family A (insight_index=0): a stale pin AND a direct pin on its own
    # current generation — both refs point into family A.
    a_old = _seed_prd(
        brief_id=brief_id, title="Family A gen 1", insight_index=0,
        generated_at="2026-08-01T00:00:00Z",
    )
    a_new = _seed_prd(
        brief_id=brief_id, title="Family A gen 2", insight_index=0,
        generated_at="2026-08-02T00:00:00Z",
    )
    _seed_ref(project["id"], "prd", a_old)  # stale — superseded
    _seed_ref(project["id"], "prd", a_new)  # also pinned directly at current

    # Family B (insight_index=1, same brief): independently superseded. Must
    # resolve to ITS OWN current generation, never family A's.
    b_old = _seed_prd(
        brief_id=brief_id, title="Family B gen 1", insight_index=1,
        generated_at="2026-08-01T00:00:00Z",
    )
    b_new = _seed_prd(
        brief_id=brief_id, title="Family B gen 2", insight_index=1,
        generated_at="2026-08-02T00:00:00Z",
    )
    _seed_ref(project["id"], "prd", b_old)  # stale — superseded

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    prd_items = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    ids = sorted(item["id"] for item in prd_items)
    # Exactly one PRD per family, each its OWN current generation, no
    # cross-family leak (a_old and b_old never re-point at each other's
    # family) and no double-surfacing of family A (pinned twice).
    assert ids == sorted([a_new, b_new])


# ── Isolation (mutation-proofed for R3) ─────────────────────────────────────


def test_list_foreign_project_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db import projects as projects_db
    from app.db.workspaces import ensure_default_workspace

    foreign = projects_db.create_project(
        company_id="foreign-co",
        workspace_id=ensure_default_workspace("foreign-co")["id"],
        name="Not mine",
        created_by="someone-else",
    )

    r = ctx.client.get(f"/v1/projects/{foreign['id']}/artifacts")
    assert r.status_code == 404


def test_list_artifacts_same_tenant_non_member_403(artifacts_env, monkeypatch):
    """AD-P11 membership gate — a SAME-TENANT caller who is never added to
    this project must be blocked (403), distinct from the cross-tenant 404
    above. Uses the same `seed_same_tenant_non_member` pattern the other
    project routes' membership-gate tests already rely on."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Members only")
    _seed_ref(project["id"], "prd", prd_id)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts", headers=non_member_headers)
    assert r.status_code == 403

    # The real member still reaches it fine — proves this is a membership
    # gate, not an accidental blanket lockout.
    r_owner = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r_owner.status_code == 200
    assert [a["id"] for a in r_owner.json()["artifacts"]] == [prd_id]


def test_cross_tenant_artifact_not_listed(artifacts_env, monkeypatch):
    """R3 — even if a ref for a FOREIGN company's artifact somehow exists on
    this project (bypassing write-time validation, e.g. a raw DB write),
    the read path never surfaces it: the filter only keeps rows that are
    ALSO in the caller's own tenant-scoped fan-out."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    seed_company(user_id="intruder", slug="rival")
    rival_brief = _seed_brief(dataset="rival", week_label="Rival wk")
    rival_prd = _seed_prd(brief_id=rival_brief, title="Rival PRD")
    _seed_ref(project["id"], "prd", rival_prd)

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    assert r.json()["artifacts"] == []


def test_add_foreign_prd_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    seed_company(user_id="intruder", slug="rival")
    rival_brief = _seed_brief(dataset="rival", week_label="Rival wk")
    rival_prd = _seed_prd(brief_id=rival_brief, title="Rival PRD")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": rival_prd},
    )
    assert r.status_code == 404

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_add_foreign_evidence_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    seed_company(user_id="intruder", slug="rival")
    rival_brief = _seed_brief(dataset="rival", week_label="Rival wk")
    rival_evidence = _seed_evidence(brief_id=rival_brief, title="Rival evidence")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "evidence", "artifact_id": rival_evidence},
    )
    assert r.status_code == 404

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_add_foreign_prototype_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    rival_company_id = seed_company(user_id="intruder", slug="rival")
    rival_brief = _seed_brief(dataset="rival", week_label="Rival wk")
    rival_prd = _seed_prd(brief_id=rival_brief, title="Rival PRD")
    rival_prototype = _seed_prototype(prd_id=rival_prd, workspace_id=rival_company_id)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prototype", "artifact_id": rival_prototype},
    )
    assert r.status_code == 404

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_add_foreign_report_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    rival_company_id = seed_company(user_id="intruder", slug="rival")
    rival_report = _seed_report(company_id=rival_company_id, skill="voice-of-customer-report", title="Rival report")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "report", "artifact_id": rival_report},
    )
    assert r.status_code == 404

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_add_foreign_ticket_set_404(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    rival_company_id = seed_company(user_id="intruder", slug="rival")
    rival_set = _seed_ticket_set(company_id=rival_company_id, title="Rival tickets")

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "ticket_set", "artifact_id": rival_set},
    )
    assert r.status_code == 404

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


def test_add_artifact_same_tenant_non_member_403(artifacts_env, monkeypatch):
    """POST also membership-gates before validating ownership."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Owner's PRD")
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": prd_id},
        headers=non_member_headers,
    )
    assert r.status_code == 403

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert refs == []


# ── Creation / edge ──────────────────────────────────────────────────────


def test_add_owned_artifact_writes_ref(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="My PRD")

    from app.db.client import require_client

    before = require_client().table("projects").select("updated_at").eq("id", project["id"]).execute().data[0]

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": prd_id},
    )
    assert r.status_code == 200

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert len(refs) == 1
    assert refs[0]["artifact_type"] == "prd"
    assert refs[0]["artifact_id"] == prd_id

    after = require_client().table("projects").select("updated_at").eq("id", project["id"]).execute().data[0]
    assert after["updated_at"] >= before["updated_at"]

    # The route response echoes the ref.
    body = r.json()
    assert body["project_id"] == project["id"]
    assert body["artifact_type"] == "prd"
    assert body["artifact_id"] == prd_id


def test_add_owned_prototype_writes_ref(artifacts_env, monkeypatch):
    """The no-dedicated-dep branch (AD-P12) also succeeds for an artifact
    the caller genuinely owns."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Parent PRD")
    prototype_id = _seed_prototype(prd_id=prd_id, workspace_id=ctx.company_id)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prototype", "artifact_id": prototype_id},
    )
    assert r.status_code == 200

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert [(r_["artifact_type"], r_["artifact_id"]) for r_ in refs] == [("prototype", prototype_id)]


def test_add_ref_dedupe(artifacts_env, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Repeat add")

    r1 = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": prd_id},
    )
    r2 = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts",
        json={"artifact_type": "prd", "artifact_id": prd_id},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    from app.db.client import require_client

    refs = require_client().table("project_artifacts").select("*").eq("project_id", project["id"]).execute().data
    assert len(refs) == 1


def test_add_artifact_log_carries_only_identifiers(artifacts_env, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Logged add")

    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/artifacts",
            json={"artifact_type": "prd", "artifact_id": prd_id},
        )
    assert r.status_code == 200
    lines = [rec.getMessage() for rec in caplog.records]
    expected = f"project_artifact_added project_id={project['id']} type=prd artifact_id={prd_id}"
    assert any(line == expected for line in lines)

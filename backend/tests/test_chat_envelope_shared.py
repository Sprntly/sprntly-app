"""The shared envelope enrichment (`app.chat_envelope`) — one enrichment,
every chat surface.

`enrich_chat_envelope` is the extraction of `/v1/chat/intent`'s inline
render-data legs (the open-artifact lookup + its conversation stamps, the
artifact rows, the full-library counts). Proven here:

  * main's `/v1/chat/intent` envelope is BYTE-identical after the
    extraction — the pre-extraction inline sequence is replayed step for
    step against the same seeds and compared as serialized JSON, so keys,
    ORDER and values all have to match;
  * the PRIVATE project classify envelope now carries `artifact_list`
    produced by the SAME shared enrichment;
  * the GROUP classify envelope now carries the open lookup as the NESTED
    `open["candidates"]` (stamped in place by the enrichment — never a
    top-level `open_candidates` key);
  * a source-scan guard: attaching the enrichment added NO new
    `resolve_project_chat_intent` call — the resolver stays def + 2 call
    sites (the count `test_group_trigger_and_no_fabrication` also pins),
    and BOTH project call sites run the shared enrichment.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.routes.chat as chat_route
import app.routes.projects as projects_route
from app.db.workspaces import ensure_default_workspace
from app.stories.generate import Story

REPO_ROOT = Path(__file__).resolve().parents[2]

# `list_artifacts_for_company` fans out over `prototypes`, which is
# deliberately NOT in conftest's shared base schema — same suite-local
# pattern as test_chat_list_artifacts / test_project_intent_route.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id            INTEGER,
    workspace_id      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'generating',
    preview_image_url TEXT,
    is_complete       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _with_prototypes_table(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    return isolated_settings


class _Ctx:
    """The tiny slice of CompanyContext the enrichment reads."""

    def __init__(self, company_id: str, workspace_id=None):
        self.company_id = company_id
        self.workspace_id = workspace_id
        self.workspace_is_default = True


def _seed_set(company_id: str, *, title: str, created_at: str) -> int:
    from app.db.client import require_client

    return require_client().table("ticket_sets").insert({
        "company_id": company_id,
        "title": title,
        "stories": [Story(title="A", body="b").to_dict()],
        "status": "ready",
        "created_at": created_at,
    }).execute().data[0]["id"]


def _seed_prd(db_mod, dataset: str = "acme", *, title: str = "Checkout PRD") -> int:
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title=title, md="<h1>Doc</h1>")
    return prd_id


def _seed_project(t, *, with_prd_id: int | None = None) -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch",
        created_by=t.user_id,
    )
    if with_prd_id is not None:
        projects_db.add_artifact(project["id"], "prd", with_prd_id)
    return project["id"]


def _ctx(t) -> SimpleNamespace:
    return SimpleNamespace(
        company_id=t.company_id,
        workspace_id=ensure_default_workspace(t.company_id)["id"],
        user_id=t.user_id, user_email=None,
    )


# ─── main: byte-identical after the extraction ───────────────────────────────


def test_main_envelope_byte_identical_after_extraction(
    tenant_client, isolated_settings, monkeypatch
):
    """`/v1/chat/intent`'s envelope, for BOTH enrichment legs, serializes to
    exactly the bytes the pre-extraction inline sequence produces: copy the
    classifier's dict, stamp `prd_id`/`prd_title`, then attach the legs in
    the old inline order against the same seeds. Key ORDER rides along in
    the JSON comparison, so a reordered or renamed key fails, not just a
    changed value."""
    from app.chat_envelope import (
        _attach_open_conversations,
        _chat_artifact_counts,
        _chat_artifact_list,
        _dataset_for,
    )
    from app.artifact_open import resolve_open_artifact

    t = tenant_client.make(slug="acme")
    # Fixed timestamps: the comparison replays the helpers against the same
    # rows, so nothing here may move between the two assemblies.
    _seed_set(t.company_id, title="Webhook tickets", created_at="2026-08-10T01:00:00Z")
    _seed_set(t.company_id, title="Billing tickets", created_at="2026-08-11T01:00:00Z")

    def _classified(intent: str, **extra) -> dict:
        base = {
            "intent": intent, "confidence": 0.9, "task": None,
            "instruction": None, "artifact_type": None, "artifact_query": None,
            "reason": "r", "source": "llm",
        }
        base.update(extra)
        return base

    # Leg 1 — list_artifacts in count mode (rows AND tallies attach).
    monkeypatch.setattr(
        chat_route, "resolve_chat_intent",
        lambda *a, **kw: _classified(
            "list_artifacts", list_kind="ticket_set", list_mode="count",
        ),
    )
    resp = t.client.post("/v1/chat/intent", json={"message": "how many ticket sets?"})
    assert resp.status_code == 200, resp.text

    expected = _classified("list_artifacts", list_kind="ticket_set", list_mode="count")
    expected["prd_id"] = None
    expected["prd_title"] = None
    expected["artifact_list"] = _chat_artifact_list(_Ctx(t.company_id), "ticket_set", None)
    expected["artifact_counts"] = _chat_artifact_counts(_Ctx(t.company_id), "ticket_set")
    assert json.dumps(resp.json()) == json.dumps(expected)
    assert expected["artifact_list"], "seeded rows must actually attach"
    assert expected["artifact_counts"]["total"] == 2

    # Leg 2 — open_artifact (lookup + conversation stamps attach).
    monkeypatch.setattr(
        chat_route, "resolve_chat_intent",
        lambda *a, **kw: _classified(
            "open_artifact", artifact_type="ticket_set", artifact_query="webhook",
        ),
    )
    resp2 = t.client.post("/v1/chat/intent", json={"message": "open the webhook tickets"})
    assert resp2.status_code == 200, resp2.text

    expected2 = _classified(
        "open_artifact", artifact_type="ticket_set", artifact_query="webhook",
    )
    expected2["prd_id"] = None
    expected2["prd_title"] = None
    expected2["open"] = resolve_open_artifact(
        artifact_type="ticket_set", query="webhook",
        dataset=_dataset_for(_Ctx(t.company_id)),
    )
    _attach_open_conversations(expected2["open"], t.company_id)
    assert json.dumps(resp2.json()) == json.dumps(expected2)


# ─── private project classify: same cards, same data ─────────────────────────


def test_project_envelope_carries_artifact_list(
    tenant_client, isolated_settings, monkeypatch
):
    """A private-project list ask now carries `artifact_list` rows produced
    by the SHARED enrichment — the same clickable rows main chat renders,
    not an empty card."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    project_id = _seed_project(t, with_prd_id=prd_id)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {
            "intent": "list_artifacts", "confidence": 0.9, "task": None,
            "instruction": None, "artifact_type": None, "artifact_query": None,
            "reason": "listing", "source": "llm",
            "list_kind": "prd", "list_mode": "items",
        },
    )
    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what are my PRDs?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "list_artifacts"
    rows = body["artifact_list"]
    assert rows, "the shared enrichment must attach real rows"
    assert {r["type"] for r in rows} == {"prd"}
    assert prd_id in {r["id"] for r in rows}


# ─── group classify: open lookup stamped in place, nested ────────────────────


def test_group_envelope_carries_open_candidates(
    tenant_client, isolated_settings, monkeypatch
):
    """The group classify path runs the SAME enrichment: an open-artifact
    envelope gains the NESTED `open["candidates"]` (stamped in place on the
    dict `resolve_project_chat_intent` returned) — never a top-level
    `open_candidates` key."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"], title="Checkout PRD")
    project_id = _seed_project(t, with_prd_id=prd_id)

    held = {
        "intent": "open_artifact", "confidence": 0.9, "task": None,
        "instruction": None, "artifact_type": "prd",
        "artifact_query": "checkout", "reason": "open", "source": "llm",
    }
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: (held, prd_id, None),
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, 0, _ctx(t), "open the checkout PRD", [], "acme",
    )
    # Not an edit: nothing applied, the reply falls through as usual…
    assert outcome.applied_turn is None
    assert outcome.was_edit_request is False
    # …but the classify envelope was enriched IN PLACE with the lookup.
    assert held["open"]["status"] in {"resolved", "ambiguous"}
    candidates = held["open"]["candidates"]
    assert candidates and candidates[0]["prd_id"] == prd_id
    # The stamp the enrichment adds to PRD candidates (null-safe when the
    # PRD has no surviving thread — the client's fallback signal).
    assert "conversation_id" in candidates[0]
    # Nested is the contract; a top-level key would be a different (wrong) API.
    assert "open_candidates" not in held


# ─── project scoping: cards and counts match the project, not the workspace ──


def _list_envelope(list_kind: str = "prd", list_mode: str = "count") -> dict:
    """A classifier list-envelope stub — count mode so BOTH listing legs
    (`artifact_list` and `artifact_counts`) attach in one pass."""
    return {
        "intent": "list_artifacts", "confidence": 0.9, "task": None,
        "instruction": None, "artifact_type": None, "artifact_query": None,
        "reason": "listing", "source": "llm",
        "list_kind": list_kind, "list_mode": list_mode,
    }


def test_project_envelope_artifact_list_is_project_scoped(
    tenant_client, isolated_settings, monkeypatch
):
    """The regression: a workspace with N PRDs, a project holding M<N — the
    PROJECT chat envelope's cards must be the project's M rows (and its
    count M), never the workspace-wide N the main surface lists. Before the
    fix the project surface showed workspace-wide cards next to
    project-scoped prose, and the two disagreed."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    in_project = {_seed_prd(db, title="Checkout PRD"), _seed_prd(db, title="Search PRD")}
    outside = {_seed_prd(db, title="Billing PRD"), _seed_prd(db, title="Onboarding PRD")}
    project_id = _seed_project(t)
    from app.db import projects as projects_db

    for pid in in_project:
        projects_db.add_artifact(project_id, "prd", pid)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: _list_envelope()
    )
    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what PRDs do we have?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["id"] for r in body["artifact_list"]} == in_project
    assert not outside & {r["id"] for r in body["artifact_list"]}
    # The HOW-MANY leg is scoped the same way — prose-count == card-count
    # needs both, and the workspace holds 4.
    assert body["artifact_counts"]["total"] == len(in_project) == 2


def test_project_zero_artifacts_empty_no_leak(
    tenant_client, isolated_settings, monkeypatch
):
    """A project holding NOTHING renders nothing: empty cards, zero count —
    never the workspace's artifacts leaking in from other projects."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    _seed_prd(db, title="Checkout PRD")
    _seed_prd(db, title="Billing PRD")
    project_id = _seed_project(t)  # no artifacts pinned

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: _list_envelope()
    )
    resp = t.client.post(
        f"/v1/projects/{project_id}/chat/intent",
        json={"message": "what PRDs do we have?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifact_list"] == []
    assert body["artifact_counts"]["total"] == 0


def test_project_envelope_idor_no_foreign_project_or_tenant(
    tenant_client, isolated_settings, monkeypatch
):
    """Project A's envelope never carries project B's artifact (same tenant)
    nor a foreign-tenant artifact — even one whose ref was written onto
    project A directly, bypassing the route's write-time gate: the listing
    keeps only rows that are ALSO in the caller's own tenant fan-out."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    prd_a = _seed_prd(db, title="Checkout PRD")
    prd_b = _seed_prd(db, title="Billing PRD")
    foreign_prd = _seed_prd(db, dataset="other", title="Foreign PRD")

    project_a = _seed_project(t, with_prd_id=prd_a)
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project_b = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Other project",
        created_by=t.user_id,
    )["id"]
    projects_db.add_artifact(project_b, "prd", prd_b)
    # A ref a write-time check should have rejected: a foreign-tenant PRD
    # pinned straight onto project A at the db layer.
    projects_db.add_artifact(project_a, "prd", foreign_prd)

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: _list_envelope()
    )
    resp = t.client.post(
        f"/v1/projects/{project_a}/chat/intent",
        json={"message": "what PRDs do we have?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {r["id"] for r in body["artifact_list"]}
    assert ids == {prd_a}
    assert prd_b not in ids and foreign_prd not in ids
    assert body["artifact_counts"]["total"] == 1


def test_main_envelope_artifact_list_unchanged(
    tenant_client, isolated_settings, monkeypatch
):
    """MAIN chat's listing legs are byte-identical after the project-scoping
    param landed: `routes/chat.py` passes no `project_id`, so the envelope's
    rows/counts still come from the workspace-wide
    `list_artifacts_for_company` path — proven by JSON equality against the
    unchanged-path helpers AND a workspace-wide id set (a project pinning a
    subset must not narrow main's cards)."""
    from app.chat_envelope import _chat_artifact_counts, _chat_artifact_list

    t = tenant_client.make(slug="acme")
    set_a = _seed_set(t.company_id, title="Webhook tickets",
                      created_at="2026-08-10T01:00:00Z")
    set_b = _seed_set(t.company_id, title="Billing tickets",
                      created_at="2026-08-11T01:00:00Z")
    # A project pinning ONE of the two — main must still list BOTH.
    from app.db import projects as projects_db

    project_id = _seed_project(t)
    projects_db.add_artifact(project_id, "ticket_set", set_a)

    monkeypatch.setattr(
        chat_route, "resolve_chat_intent",
        lambda *a, **kw: _list_envelope(list_kind="ticket_set"),
    )
    resp = t.client.post("/v1/chat/intent", json={"message": "how many ticket sets?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["id"] for r in body["artifact_list"]} == {set_a, set_b}
    assert body["artifact_counts"]["total"] == 2
    # Equality against the default (no-project_id) helper path — the exact
    # bytes the pre-change workspace-wide path produces.
    assert json.dumps(body["artifact_list"]) == json.dumps(
        _chat_artifact_list(_Ctx(t.company_id), "ticket_set", None)
    )
    assert json.dumps(body["artifact_counts"]) == json.dumps(
        _chat_artifact_counts(_Ctx(t.company_id), "ticket_set")
    )


def test_enrich_default_project_id_is_none_workspace_wide(
    tenant_client, isolated_settings
):
    """Calling the enrichment WITHOUT `project_id` (every pre-existing
    caller) stays workspace-wide: the additive default cannot narrow an
    existing surface."""
    from app.chat_envelope import enrich_chat_envelope

    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    pinned = _seed_prd(db, title="Checkout PRD")
    unpinned = _seed_prd(db, title="Billing PRD")
    _seed_project(t, with_prd_id=pinned)

    envelope = enrich_chat_envelope(_list_envelope(), _Ctx(t.company_id))
    assert {r["id"] for r in envelope["artifact_list"]} == {pinned, unpinned}
    assert envelope["artifact_counts"]["total"] == 2


# ─── source-scan guard: enrichment attached WITHOUT a new resolver call ──────


def test_enrichment_adds_no_resolve_intent_call():
    """The enrichment attaches to the envelopes the EXISTING
    `resolve_project_chat_intent` calls return — def + 2 call sites, same
    count the trigger suite pins — and BOTH project call sites (private
    route + group classify) run the shared enrichment."""
    src = (REPO_ROOT / "backend" / "app" / "routes" / "projects.py").read_text()
    assert src.count("resolve_project_chat_intent(") == 3  # def + 2 call sites
    assert src.count("enrich_chat_envelope(") == 2  # private route + group path

    chat_src = (REPO_ROOT / "backend" / "app" / "routes" / "chat.py").read_text()
    assert chat_src.count("enrich_chat_envelope(") == 1  # main consumes it too

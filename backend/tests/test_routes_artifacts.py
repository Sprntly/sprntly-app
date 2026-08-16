"""Tests for GET /v1/artifacts — the All-Chats "Artifacts" tab listing.

Covers:
  - tenant scoping (caller sees only their own dataset; 404 on an unowned slug)
  - unified shape across all five artifact types (prd / prototype / evidence /
    report / ticket_set), including a report's chat/PRD attachment and the
    omission of its (potentially huge) html body from the listing, and a ticket
    set's chat attachment + count without its ticket payloads
  - per-source PRD dedupe: only the newest row per regeneration FAMILY lists,
    where a family is theme_id (chat/ideation), the row itself (upload), or
    insight_index (brief) — see db/artifacts._prd_family_key
  - recency sort (newest first)
  - prototype title derived from the parent PRD
  - empty result for a company with no artifacts

Mirrors the fixture style of test_routes_connectors.py: `company_client`
gives a JWT-authed TestClient with a seeded company + membership; we add the
`prototypes` table on top of conftest's base fake schema (which already has
briefs/prds/evidences) and seed rows directly through the fake Supabase client.
"""
from __future__ import annotations

import json

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client, seed_company, supabase_bearer

# SQLite translation of supabase/migrations/20260528000000_design_agent_prototypes.sql
# (the columns this route reads + the workspace_id scope). Only `prototypes` is
# needed here; the route never touches prototype_checkpoints.
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


# SQLite translation of supabase/migrations/20260813120000_custom_artifacts.sql
# (the columns this route reads). Team documents of any kind — the "Others"
# section of the library.
_CUSTOM_ARTIFACT_DDL = """
CREATE TABLE IF NOT EXISTS custom_artifacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    conversation_id INTEGER,
    kind            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    body_html       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ready',
    error           TEXT,
    error_code      TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def artifacts_env(isolated_settings):
    """Add the prototypes table to conftest's already-reset fake DB. briefs /
    prds / evidences are present in the base schema, so no extra DDL for them."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    _fake_supabase.get_fake_db().executescript(_CUSTOM_ARTIFACT_DDL)
    yield


def _client(monkeypatch, *, slug: str = "acme"):
    """A company-scoped TestClient for `slug`. Returns the ctx namespace."""
    return company_client(monkeypatch)


# ─── Seed helpers (write directly through the fake Supabase client) ──────────


def _seed_brief(*, dataset: str, week_label: str) -> int:
    from app.db.client import require_client
    resp = require_client().table("briefs").insert({
        "dataset": dataset,
        "week_label": week_label,
        "payload": json.dumps({}),
        "is_current": True,
    }).execute()
    return resp.data[0]["id"]


def _seed_prd(*, brief_id: int, title: str, insight_index: int = 0,
              status: str = "ready", generated_at: str | None = None,
              theme_id: str | None = None, source: str = "brief") -> int:
    """Seed a prds row. `theme_id`/`source` mirror the real generation paths:
    brief PRDs are (source='brief', theme_id=None) with a real insight_index;
    chat/ideation PRDs carry a theme_id at the sentinel insight_index 0; uploads
    are (source='upload', theme_id=None) on the uploads-anchor brief."""
    from app.db.client import require_client
    row = {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "status": status,
        "source": source,
        "theme_id": theme_id,
    }
    if generated_at is not None:
        row["generated_at"] = generated_at
    resp = require_client().table("prds").insert(row).execute()
    return resp.data[0]["id"]


def _seed_evidence(*, brief_id: int, title: str, insight_index: int = 0,
                   status: str = "ready", generated_at: str | None = None) -> int:
    from app.db.client import require_client
    row = {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "status": status,
    }
    if generated_at is not None:
        row["generated_at"] = generated_at
    resp = require_client().table("evidences").insert(row).execute()
    return resp.data[0]["id"]


def _seed_prototype(*, prd_id: int, workspace_id: str, status: str = "ready",
                    created_at: str | None = None,
                    preview_image_url: str | None = None,
                    is_complete: bool = False) -> int:
    from app.db.client import require_client
    row = {
        "prd_id": prd_id,
        "workspace_id": workspace_id,
        "status": status,
        "template_version": 1,
        "is_complete": is_complete,
    }
    if created_at is not None:
        row["created_at"] = created_at
    if preview_image_url is not None:
        row["preview_image_url"] = preview_image_url
    resp = require_client().table("prototypes").insert(row).execute()
    return resp.data[0]["id"]


def _seed_conversation(*, company_id: str, title: str) -> int:
    from app.db.client import require_client
    resp = require_client().table("conversations").insert({
        "company_id": company_id,
        "title": title,
    }).execute()
    return resp.data[0]["id"]


def _seed_report(*, company_id: str, skill: str, title: str,
                 html: str = "<!DOCTYPE html><html></html>",
                 question: str = "", created_at: str | None = None,
                 conversation_id: int | None = None,
                 prd_id: int | None = None) -> int:
    """Seed a captured report (what app/report_capture.py writes).
    `conversation_id` / `prd_id` are its attachment; both optional."""
    from app.db.client import require_client
    row = {
        "company_id": company_id,
        "skill": skill,
        "title": title,
        "html": html,
        "question": question,
        "conversation_id": conversation_id,
        "prd_id": prd_id,
    }
    if created_at is not None:
        row["created_at"] = created_at
    resp = require_client().table("reports").insert(row).execute()
    return resp.data[0]["id"]


def _seed_ticket_set(*, company_id: str, title: str = "", stories: list | None = None,
                     status: str = "ready", source_text: str = "",
                     conversation_id: int | None = None,
                     created_at: str | None = None) -> int:
    """Seed a standalone ticket set (tickets born in a chat, no PRD behind
    them). `conversation_id` is its attachment and may be absent/dangling."""
    from app.db.client import require_client
    row = {
        "company_id": company_id,
        "title": title,
        "stories": stories if stories is not None else [],
        "status": status,
        "source_text": source_text,
        "conversation_id": conversation_id,
    }
    if created_at is not None:
        row["created_at"] = created_at
    resp = require_client().table("ticket_sets").insert(row).execute()
    return resp.data[0]["id"]


def _seed_custom_artifact(*, company_id: str, title: str = "", kind: str = "",
                          body_html: str = "", status: str = "ready",
                          conversation_id: int | None = None,
                          created_at: str | None = None,
                          updated_at: str | None = None) -> int:
    """Seed a custom artifact (a team document — the "Others" section)."""
    from app.db.client import require_client
    row = {
        "company_id": company_id,
        "title": title,
        "kind": kind,
        "body_html": body_html,
        "status": status,
        "conversation_id": conversation_id,
    }
    if created_at is not None:
        row["created_at"] = created_at
    if updated_at is not None:
        row["updated_at"] = updated_at
    resp = require_client().table("custom_artifacts").insert(row).execute()
    return resp.data[0]["id"]


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_requires_auth(unauth_client, artifacts_env):
    r = unauth_client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 401


def test_empty_for_company_with_no_artifacts(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    assert r.json() == {"artifacts": []}


def test_404_on_unowned_dataset(artifacts_env, monkeypatch):
    # The caller owns "acme" (seeded by company_client). A different slug that
    # maps to no company (or another company) must 404 — never leak.
    ctx = _client(monkeypatch)
    r = ctx.client.get("/v1/artifacts", params={"dataset": "someone-else"})
    assert r.status_code == 404


def test_tenant_scoping_only_own_artifacts(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)  # owns "acme", workspace_id == ctx.company_id

    # A second, foreign company with its own brief + PRD + prototype.
    other_company_id = seed_company(user_id="intruder", slug="rival")
    other_brief = _seed_brief(dataset="rival", week_label="Wk Rival")
    _seed_prd(brief_id=other_brief, title="Rival PRD")
    _seed_prototype(prd_id=999, workspace_id=other_company_id)

    # The caller's own artifacts under "acme".
    my_brief = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=my_brief, title="My PRD")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    titles = [a["title"] for a in r.json()["artifacts"]]
    assert "My PRD" in titles
    assert "Rival PRD" not in titles
    # The rival prototype (scoped by the rival's company UUID) is excluded.
    assert all(a["type"] != "prototype" for a in r.json()["artifacts"])

    # And the rival cannot read acme's artifacts.
    rival_headers = supabase_bearer("intruder")
    rr = ctx.client.get(
        "/v1/artifacts", params={"dataset": "acme"}, headers=rival_headers,
    )
    assert rr.status_code == 404


def test_prd_list_dedups_to_latest_generation(artifacts_env, monkeypatch):
    # Each PRD regeneration is a new prds row sharing (brief_id, insight_index).
    # The artifacts list must show only the LATEST generation per logical PRD.
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
              generated_at="2026-06-15T01:00:00+00:00")
    _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
              generated_at="2026-06-15T02:00:00+00:00")
    latest = _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
                       generated_at="2026-06-15T03:00:00+00:00")
    # A different insight is a different logical PRD → its own entry.
    _seed_prd(brief_id=brief_id, title="Pricing PRD", insight_index=1,
              generated_at="2026-06-14T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    prds = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prds) == 2  # latest KG Timeout + Pricing — not all 4 rows
    kg = [a for a in prds if a["title"] == "KG Timeout PRD"]
    assert len(kg) == 1
    assert kg[0]["id"] == latest  # the newest generation wins


def test_chat_prds_sharing_the_sentinel_insight_all_list(artifacts_env, monkeypatch):
    # Chat/ideation PRDs have no brief insight: they anchor to the company's
    # brief at insight_index 0 as a STORAGE SENTINEL and are identified by
    # theme_id. Keying the dedupe on insight_index alone collapsed them all into
    # one row, so every chat PRD but the newest silently vanished from the tab
    # (and it shadowed the brief's own insight-0 PRD too).
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 30")
    brief_prd = _seed_prd(brief_id=brief_id, title="Bulk onboarding is failing",
                          insight_index=0, generated_at="2026-07-28T15:56:55+00:00")
    chat_a = _seed_prd(brief_id=brief_id, title="Custom Skills", insight_index=0,
                       theme_id="chat:c532ebe2365bc3c3", source="chat",
                       generated_at="2026-07-28T16:18:18+00:00")
    chat_b = _seed_prd(brief_id=brief_id, title="Invitation link expiry",
                       insight_index=0, theme_id="chat:9c02be013e9e027a",
                       source="chat", generated_at="2026-07-28T16:27:59+00:00")
    chat_c = _seed_prd(brief_id=brief_id, title="Navbar update", insight_index=0,
                       theme_id="chat:d781353f58d9bbfd", source="chat",
                       generated_at="2026-07-28T16:35:42+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    prds = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    # Four distinct logical PRDs — three themes plus the brief insight.
    assert {a["id"] for a in prds} == {brief_prd, chat_a, chat_b, chat_c}
    # Newest first, and each opens by its OWN prd_id.
    assert [a["id"] for a in prds] == [chat_c, chat_b, chat_a, brief_prd]
    assert next(a for a in prds if a["id"] == chat_a)["open"]["prd_id"] == chat_a


def test_chat_prd_regenerations_collapse_to_newest(artifacts_env, monkeypatch):
    # Re-issuing the same chat ask reuses its theme_id (find-or-create), so those
    # rows ARE one family and must still collapse to the newest generation.
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 30")
    theme = "chat:c532ebe2365bc3c3"
    _seed_prd(brief_id=brief_id, title="Custom Skills", insight_index=0,
              theme_id=theme, source="chat", generated_at="2026-07-28T16:18:18+00:00")
    _seed_prd(brief_id=brief_id, title="Custom Skills", insight_index=0,
              theme_id=theme, source="chat", generated_at="2026-07-28T17:02:00+00:00")
    latest = _seed_prd(brief_id=brief_id, title="Custom Skills", insight_index=0,
                       theme_id=theme, source="chat",
                       generated_at="2026-07-28T18:40:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    prds = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prds) == 1
    assert prds[0]["id"] == latest


def test_uploaded_prds_each_list_on_the_shared_anchor_brief(artifacts_env, monkeypatch):
    # Uploads have no insight AND no theme: every import for a company lands on
    # the SAME uploads-anchor brief at the sentinel index, so each row has to be
    # its own family or only the newest upload would ever be listed.
    ctx = _client(monkeypatch)
    anchor = _seed_brief(dataset="acme", week_label="Uploaded PRDs")
    first = _seed_prd(brief_id=anchor, title="Q3 Roadmap.pdf", insight_index=0,
                      source="upload", generated_at="2026-07-20T09:00:00+00:00")
    second = _seed_prd(brief_id=anchor, title="Payments Spec.docx", insight_index=0,
                       source="upload", generated_at="2026-07-21T09:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    prds = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert {a["id"] for a in prds} == {first, second}


def test_unified_shape_all_three_types(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Week of May 20")
    prd_id = _seed_prd(brief_id=brief_id, title="Handoff PRD", insight_index=2)
    _seed_evidence(brief_id=brief_id, title="Retention Evidence", insight_index=1)
    _seed_prototype(prd_id=prd_id, workspace_id=ctx.company_id)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    items = r.json()["artifacts"]
    by_type = {it["type"]: it for it in items}
    assert set(by_type) == {"prd", "prototype", "evidence"}

    prd = by_type["prd"]
    assert prd["id"] == prd_id
    assert prd["title"] == "Handoff PRD"
    assert prd["status"] == "ready"
    assert prd["created_at"]
    assert prd["source"] == {
        "brief_id": brief_id, "week_label": "Week of May 20", "insight_index": 2,
    }
    assert prd["open"] == {
        "brief_id": brief_id, "insight_index": 2, "prd_id": prd_id,
    }

    ev = by_type["evidence"]
    assert ev["title"] == "Retention Evidence"
    assert ev["source"]["week_label"] == "Week of May 20"
    assert ev["open"]["evidence_id"] == ev["id"]
    assert ev["open"]["brief_id"] == brief_id

    proto = by_type["prototype"]
    assert proto["open"]["prd_id"] == prd_id
    assert proto["open"]["prototype_id"] == proto["id"]
    assert proto["source"]["prd_id"] == prd_id


def test_prototype_title_derived_from_parent_prd(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Parent PRD Title")
    _seed_prototype(prd_id=prd_id, workspace_id=ctx.company_id)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    proto = next(a for a in r.json()["artifacts"] if a["type"] == "prototype")
    assert proto["title"] == "Parent PRD Title"
    assert proto["source"]["prd_title"] == "Parent PRD Title"


def test_recency_sort_newest_first(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    # Three artifacts with explicit, distinct timestamps. The two PRDs use
    # distinct insight_index so they're separate logical PRDs (not collapsed by
    # the latest-generation dedup) — this test is about recency sort, not dedup.
    _seed_prd(brief_id=brief_id, title="Oldest", insight_index=0,
              generated_at="2026-05-01T00:00:00+00:00")
    _seed_evidence(brief_id=brief_id, title="Middle",
                   generated_at="2026-05-15T00:00:00+00:00")
    prd_newest = _seed_prd(brief_id=brief_id, title="Newest", insight_index=1,
                           generated_at="2026-06-01T00:00:00+00:00")
    _seed_prototype(prd_id=prd_newest, workspace_id=ctx.company_id,
                    created_at="2026-06-10T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    titles = [a["title"] for a in r.json()["artifacts"]]
    # Prototype (Jun 10) → Newest PRD (Jun 1) → Middle (May 15) → Oldest (May 1).
    assert titles == ["Newest", "Newest", "Middle", "Oldest"]
    # First item is the prototype (newest created_at).
    assert r.json()["artifacts"][0]["type"] == "prototype"


def test_prototype_status_filter_includes_generating_and_ready(artifacts_env, monkeypatch):
    # generating + ready are surfaced; failed + invalidated are excluded.
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_gen = _seed_prd(brief_id=brief_id, title="Building PRD")
    prd_ready = _seed_prd(brief_id=brief_id, title="Built PRD")
    prd_failed = _seed_prd(brief_id=brief_id, title="Failed PRD")
    prd_inval = _seed_prd(brief_id=brief_id, title="Invalidated PRD")

    _seed_prototype(prd_id=prd_gen, workspace_id=ctx.company_id, status="generating")
    _seed_prototype(prd_id=prd_ready, workspace_id=ctx.company_id, status="ready")
    _seed_prototype(prd_id=prd_failed, workspace_id=ctx.company_id, status="failed")
    _seed_prototype(prd_id=prd_inval, workspace_id=ctx.company_id, status="invalidated")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    proto_statuses = sorted(
        a["status"] for a in r.json()["artifacts"] if a["type"] == "prototype"
    )
    assert proto_statuses == ["generating", "ready"]


def test_prototype_emits_preview_and_completion_fields(artifacts_env, monkeypatch):
    # preview_image_url, is_complete, status present on emitted prototype items,
    # including the null-preview / not-yet-complete case.
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_done = _seed_prd(brief_id=brief_id, title="Completed PRD")
    prd_building = _seed_prd(brief_id=brief_id, title="Building PRD")

    _seed_prototype(
        prd_id=prd_done, workspace_id=ctx.company_id, status="ready",
        preview_image_url="prototypes/1/acme/_preview/preview.png",
        is_complete=True,
        created_at="2026-06-10T00:00:00+00:00",
    )
    _seed_prototype(
        prd_id=prd_building, workspace_id=ctx.company_id, status="generating",
        created_at="2026-06-09T00:00:00+00:00",
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    protos = {
        a["title"]: a for a in r.json()["artifacts"] if a["type"] == "prototype"
    }

    done = protos["Completed PRD"]
    assert done["status"] == "ready"
    assert done["is_complete"] is True
    assert done["preview_image_url"] == "prototypes/1/acme/_preview/preview.png"

    building = protos["Building PRD"]
    assert building["status"] == "generating"
    assert building["is_complete"] is False
    # NULL preview surfaces as JSON null (the shimmer case).
    assert building["preview_image_url"] is None


# ─── Reports ─────────────────────────────────────────────────────────────────


def test_report_lists_with_its_kind_and_no_html_body(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    _seed_report(
        company_id=ctx.company_id,
        skill="voice-of-customer-report",
        title="Voice of Customer Report · Q2",
        html="<!DOCTYPE html><html><body>" + ("x" * 5000) + "</body></html>",
        question="what are customers saying?",
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    reports = [a for a in r.json()["artifacts"] if a["type"] == "report"]
    assert len(reports) == 1
    rep = reports[0]
    assert rep["title"] == "Voice of Customer Report · Q2"
    # The report KIND drives the badge sub-label / per-kind filtering.
    assert rep["skill"] == "voice-of-customer-report"
    assert rep["source"]["skill"] == "voice-of-customer-report"
    assert rep["source"]["question"] == "what are customers saying?"
    assert rep["open"] == {"report_id": rep["id"]}
    # A report is complete the moment it is captured — no lifecycle to render.
    assert rep["status"] == ""
    # The listing must never carry document bodies.
    assert "html" not in rep


def test_report_attachment_names_its_chat_and_prd(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Checkout revamp")
    convo_id = _seed_conversation(company_id=ctx.company_id, title="Q2 customer themes")

    _seed_report(
        company_id=ctx.company_id, skill="voice-of-customer-report",
        title="VoC", conversation_id=convo_id, prd_id=prd_id,
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    rep = next(a for a in r.json()["artifacts"] if a["type"] == "report")
    assert rep["source"]["conversation_id"] == convo_id
    assert rep["source"]["conversation_title"] == "Q2 customer themes"
    assert rep["source"]["prd_id"] == prd_id
    assert rep["source"]["prd_title"] == "Checkout revamp"


def test_unattached_report_reports_null_attachment(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    _seed_report(
        company_id=ctx.company_id, skill="competitive-intelligence-review",
        title="Competitive Review",
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    rep = next(a for a in r.json()["artifacts"] if a["type"] == "report")
    # Standing alone is a normal state, not an error — the row simply shows no
    # "from" line.
    assert rep["source"]["conversation_id"] is None
    assert rep["source"]["conversation_title"] is None
    assert rep["source"]["prd_id"] is None
    assert rep["source"]["prd_title"] is None


def test_report_from_a_deleted_chat_still_lists(artifacts_env, monkeypatch):
    """`on delete set null` fires in prod, but a report whose conversation row is
    gone must still list — with no invented label — rather than vanish."""
    ctx = _client(monkeypatch)
    _seed_report(
        company_id=ctx.company_id, skill="voice-of-customer-report",
        title="VoC", conversation_id=4242,  # no such conversation
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    rep = next(a for a in r.json()["artifacts"] if a["type"] == "report")
    assert rep["source"]["conversation_id"] == 4242
    assert rep["source"]["conversation_title"] is None


def test_reports_are_tenant_scoped(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    other_company_id = seed_company(user_id="intruder", slug="rival")
    _seed_report(company_id=other_company_id, skill="voice-of-customer-report",
                 title="Rival VoC")
    _seed_report(company_id=ctx.company_id, skill="voice-of-customer-report",
                 title="My VoC")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    titles = [a["title"] for a in r.json()["artifacts"] if a["type"] == "report"]
    assert titles == ["My VoC"]


def test_reports_sort_into_the_unified_recency_order(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=brief_id, title="Older PRD",
              generated_at="2026-06-01T00:00:00+00:00")
    _seed_report(company_id=ctx.company_id, skill="voice-of-customer-report",
                 title="Newer report", created_at="2026-06-05T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    order = [(a["type"], a["title"]) for a in r.json()["artifacts"]]
    assert order == [("report", "Newer report"), ("prd", "Older PRD")]


# ─── Standalone ticket sets (the fifth artifact type) ────────────────────────


def _story(title: str) -> dict:
    from app.stories.generate import Story
    return Story(title=title, body="b").to_dict()


def test_ticket_set_lists_with_its_count_and_chat_attachment(artifacts_env, monkeypatch):
    """The row carries what it takes to render "6 tickets · from <chat>" without
    a second request — and NOT the ticket bodies."""
    ctx = _client(monkeypatch)
    conv = _seed_conversation(company_id=ctx.company_id, title="Checkout drop-off")
    _seed_ticket_set(
        company_id=ctx.company_id, title="Checkout Retry Fixes",
        stories=[_story("A"), _story("B"), _story("C")],
        source_text="turn this into tickets", conversation_id=conv,
    )

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    item = next(a for a in r.json()["artifacts"] if a["type"] == "ticket_set")
    assert item["title"] == "Checkout Retry Fixes"
    assert item["ticket_count"] == 3
    assert item["source"]["conversation_title"] == "Checkout drop-off"
    assert item["source"]["question"] == "turn this into tickets"
    assert item["open"] == {"ticket_set_id": item["id"]}
    # The listing must never ship the ticket payloads themselves.
    assert "stories" not in item


def test_ticket_set_with_a_deleted_chat_omits_the_label(artifacts_env, monkeypatch):
    """`on delete set null` leaves a dangling id; the row must not fabricate a
    thread name for a conversation that no longer exists."""
    ctx = _client(monkeypatch)
    _seed_ticket_set(company_id=ctx.company_id, title="Orphaned",
                     stories=[_story("A")], conversation_id=9999)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    item = next(a for a in r.json()["artifacts"] if a["type"] == "ticket_set")
    assert item["source"]["conversation_id"] == 9999
    assert item["source"]["conversation_title"] is None


def test_generating_ticket_set_lists_but_failed_one_does_not(artifacts_env, monkeypatch):
    """A set the user just asked for appears immediately (marked building, like
    an in-progress prototype); a failed run is not an artifact."""
    ctx = _client(monkeypatch)
    _seed_ticket_set(company_id=ctx.company_id, title="Building", status="generating")
    _seed_ticket_set(company_id=ctx.company_id, title="Broken", status="failed")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    sets = {a["title"]: a for a in r.json()["artifacts"] if a["type"] == "ticket_set"}
    assert set(sets) == {"Building"}
    assert sets["Building"]["status"] == "generating"
    assert sets["Building"]["ticket_count"] == 0


def test_ticket_set_with_no_title_keeps_the_empty_string(artifacts_env, monkeypatch):
    """Empty until the naming leg lands. The API returns it blank rather than
    inventing a label — the panel owns that copy."""
    ctx = _client(monkeypatch)
    _seed_ticket_set(company_id=ctx.company_id, title="", stories=[_story("A")])

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    item = next(a for a in r.json()["artifacts"] if a["type"] == "ticket_set")
    assert item["title"] == ""


def test_ticket_sets_are_tenant_scoped(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    other_company_id = seed_company(user_id="intruder", slug="rival")
    _seed_ticket_set(company_id=other_company_id, title="Rival tickets",
                     stories=[_story("A")])
    _seed_ticket_set(company_id=ctx.company_id, title="My tickets",
                     stories=[_story("A")])

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    titles = [a["title"] for a in r.json()["artifacts"] if a["type"] == "ticket_set"]
    assert titles == ["My tickets"]


def test_ticket_sets_sort_into_the_unified_recency_order(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=brief_id, title="Older PRD",
              generated_at="2026-06-01T00:00:00+00:00")
    _seed_ticket_set(company_id=ctx.company_id, title="Newer tickets",
                     stories=[_story("A")],
                     created_at="2026-06-05T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    order = [(a["type"], a["title"]) for a in r.json()["artifacts"]]
    assert order == [("ticket_set", "Newer tickets"), ("prd", "Older PRD")]


def test_one_conversation_lookup_serves_reports_and_ticket_sets(artifacts_env, monkeypatch):
    """Both types name the chat they were born in; the block hoists the lookup
    so a listing with both does not run two conversation queries."""
    ctx = _client(monkeypatch)
    conv = _seed_conversation(company_id=ctx.company_id, title="Shared thread")
    _seed_report(company_id=ctx.company_id, skill="voice-of-customer-report",
                 title="VoC", conversation_id=conv)
    _seed_ticket_set(company_id=ctx.company_id, title="Tickets",
                     stories=[_story("A")], conversation_id=conv)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    by_type = {a["type"]: a for a in r.json()["artifacts"]}
    assert by_type["report"]["source"]["conversation_title"] == "Shared thread"
    assert by_type["ticket_set"]["source"]["conversation_title"] == "Shared thread"


# ─── Custom artifacts: the "Others" section of the library ──────────────────


def test_custom_artifact_lists_with_its_kind_and_no_body(artifacts_env, monkeypatch):
    """A team document appears in the unified list, carrying the free-text
    `kind` its row shows and NOT its body — the listing must not ship N full
    documents (the same rule that keeps report `html` out)."""
    ctx = _client(monkeypatch)
    _seed_custom_artifact(
        company_id=ctx.company_id,
        title="Q3 leadership update",
        kind="leadership update",
        body_html="<p>" + "x" * 5000 + "</p>",
    )
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert len(items) == 1
    it = items[0]
    assert it["type"] == "custom_artifact"
    assert it["title"] == "Q3 leadership update"
    assert it["kind"] == "leadership update"
    assert "body_html" not in it
    assert it["open"] == {"custom_artifact_id": it["id"]}


def test_custom_artifact_is_company_scoped_not_dataset_scoped(artifacts_env, monkeypatch):
    """It hangs off the company, like reports and ticket sets — so it lists
    even for a company whose briefs (the PRD/evidence scope) are empty."""
    ctx = _client(monkeypatch)
    _seed_custom_artifact(company_id=ctx.company_id, title="standalone")
    # No briefs seeded at all: a dataset-scoped read would return nothing.
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert [i["title"] for i in items] == ["standalone"]


def test_another_companys_document_never_lists(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    _seed_custom_artifact(company_id="some-other-company-uuid", title="theirs")
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert items == []


def test_a_document_lists_in_every_state_including_failed(artifacts_env, monkeypatch):
    """A document the user just asked for should appear immediately (marked as
    writing), the treatment building prototypes and ticket sets already get.

    AND SO SHOULD A FAILED ONE. This test previously asserted the opposite —
    "a run that produced nothing is not an artifact" — which is true of the row
    and false of the product. Someone asked for that document and came to the
    library to find it; answering with nothing at all gave them no document, no
    failure, and no reason to look anywhere else. That is precisely how a failed
    generation stayed invisible on staging. The row is listed, carries its
    status, and opens onto its own reason.
    """
    ctx = _client(monkeypatch)
    _seed_custom_artifact(company_id=ctx.company_id, title="being written",
                          status="generating")
    _seed_custom_artifact(company_id=ctx.company_id, title="died", status="failed")
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    by_title = {i["title"]: i for i in items}
    assert set(by_title) == {"being written", "died"}
    assert by_title["being written"]["status"] == "generating"
    assert by_title["died"]["status"] == "failed"


def test_custom_artifact_sorts_by_last_edit_not_birth(artifacts_env, monkeypatch):
    """A library of LIVING documents orders by last touch: a doc created last
    week and edited today belongs above one created today and untouched since.
    Sorting on `created_at` would bury it."""
    ctx = _client(monkeypatch)
    _seed_custom_artifact(company_id=ctx.company_id, title="old but just edited",
                          created_at="2026-08-01T00:00:00Z",
                          updated_at="2026-08-13T12:00:00Z")
    _seed_custom_artifact(company_id=ctx.company_id, title="newer but untouched",
                          created_at="2026-08-12T00:00:00Z",
                          updated_at="2026-08-12T00:00:00Z")
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert [i["title"] for i in items] == ["old but just edited", "newer but untouched"]
    # The BIRTH date survives under its own name rather than being overwritten
    # by the sort key. Collapsing the two made a document edited today read as
    # created today, wherever a surface labels a row "Created <date>".
    assert items[0]["born_at"].startswith("2026-08-01")
    assert items[0]["updated_at"].startswith("2026-08-13")


def test_custom_artifact_names_the_chat_it_was_born_in(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    cid = _seed_conversation(company_id=ctx.company_id, title="Board prep")
    _seed_custom_artifact(company_id=ctx.company_id, title="Memo", conversation_id=cid)
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert items[0]["source"]["conversation_title"] == "Board prep"


def test_custom_artifact_with_a_deleted_chat_omits_the_label(artifacts_env, monkeypatch):
    """`on delete set null` leaves the id but no row. The listing must degrade
    to no label rather than inventing one for a thread that is gone."""
    ctx = _client(monkeypatch)
    _seed_custom_artifact(company_id=ctx.company_id, title="Orphan", conversation_id=99999)
    items = ctx.client.get("/v1/artifacts", params={"dataset": "acme"}).json()["artifacts"]
    assert items[0]["source"]["conversation_title"] is None

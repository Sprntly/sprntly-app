"""POST /v1/chat/intent — SERVER-SIDE project scoping from the conversation
binding.

A project chat (private `kind='individual'` or the shared `kind='group'`) must
resolve its `open_artifact` / `list_artifacts` legs — and its `edit_prd` target —
against THAT project's own artifacts, never the whole workspace's. The scope is
derived server-side from the conversation's `project_id`, so it holds even when
the client sends NO `context_source` (the class of bug where a project chat
silently answers workspace-wide).

Mutation proof throughout: the workspace carries a SECOND identically-titled PRD
that is NOT on the project. Without the project scope the open goes AMBIGUOUS
across both; with it, RESOLVED to the project's one. A main-chat conversation
(no `project_id`) stays workspace-wide, unchanged.
"""
from __future__ import annotations

import pytest

from app.db import conversations as conversations_db
from app.db import projects as projects_db
from app.db.workspaces import ensure_default_workspace

# `list_artifacts_for_project` fans out over `prototypes`, which is not in
# conftest's shared base schema — same suite-local pattern as
# test_chat_envelope_shared / test_project_intent_route.
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


def _seed_prd(db_mod, *, dataset: str, title: str, theme_id: str) -> int:
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat", theme_id=theme_id,
    )
    db_mod.complete_prd(prd_id, title=title, md="<html><body>Doc</body></html>")
    return prd_id


def _seed_project_with_prd(t, prd_id: int) -> int:
    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch",
        created_by=t.user_id,
    )
    projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"]


def _insert_legacy_group_conversation(t, project_id: int) -> dict:
    """A `kind='group'` conversation row inserted directly (the group-chat
    WRITE path — `create_group_chat` — was removed with the group-chat
    backend; pre-existing group rows are explicitly NOT deleted from the
    database, so `get_conversation_project_id` must keep resolving them)."""
    from app.db.client import require_client

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": t.company_id,
                "workspace_id": ws_id,
                "user_id": t.user_id,
                "project_id": project_id,
                "kind": "group",
            }
        )
        .execute()
        .data[0]
    )


def _open_envelope(monkeypatch, query: str):
    """Force an `open_artifact` verdict so the route's LOOKUP is what's tested."""
    import app.routes.chat as chat_route

    def _resolve(enterprise_id, message, history=None, *, prd_id=None,
                 prd_title=None, has_attachments=False, open_artifact=None,
                 thread_artifact=None):
        return {
            "intent": "open_artifact", "confidence": 0.95, "task": None,
            "instruction": None, "artifact_type": "prd",
            "artifact_query": query, "reason": "open request", "source": "llm",
        }

    monkeypatch.setattr(chat_route, "resolve_chat_intent", _resolve)


def _edit_envelope(monkeypatch):
    """Force an `edit_prd` verdict + capture the `prd_id` the route resolved and
    fed to the resolver (the act-on-PRD target)."""
    import app.routes.chat as chat_route

    seen: dict = {}

    def _resolve(enterprise_id, message, history=None, *, prd_id=None,
                 prd_title=None, has_attachments=False, open_artifact=None,
                 thread_artifact=None):
        seen["prd_id"] = prd_id
        return {
            "intent": "edit_prd", "confidence": 0.92, "task": None,
            "instruction": "Shorten the intro", "reason": "edit on PRD",
            "source": "llm",
        }

    monkeypatch.setattr(chat_route, "resolve_chat_intent", _resolve)
    return seen


# ─── open_artifact: project-scoped from the conversation binding ─────────────


def test_individual_project_chat_open_resolves_to_project_prd(
    tenant_client, isolated_settings, monkeypatch
):
    """A `kind='individual'` project-bound conversation, NO context_source: the
    open resolves to the PROJECT's PRD even though an identically-titled PRD also
    lives in the workspace (mutation proof — workspace-wide would be ambiguous)."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:in")
    _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:ws")  # workspace twin, NOT on project
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open the PRD for compliance reporting",
              "conversation_id": conv["id"]},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == project_prd


def test_group_project_chat_open_resolves_to_project_prd(
    tenant_client, isolated_settings, monkeypatch
):
    """Same for the shared `kind='group'` conversation — the scope is derived
    from its `project_id`, not from any per-user ownership."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:g")
    _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:gws")
    project_id = _seed_project_with_prd(t, project_prd)
    group = _insert_legacy_group_conversation(t, project_id)
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open the PRD for compliance reporting",
              "conversation_id": group["id"]},
    ).json()

    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == project_prd


def test_main_chat_conversation_stays_workspace_wide(
    tenant_client, isolated_settings, monkeypatch
):
    """Mutation proof, other direction: a main-chat conversation carries no
    `project_id`, so the SAME two-PRD workspace resolves AMBIGUOUS (the pre-fix
    behaviour, preserved). This is what proves the project scope is what flips
    the two cases above to `resolved`, not the seed."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    a = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:m1")
    b = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="chat:m2")
    # A plain conversation (no project binding) via the same insert the main
    # chat uses.
    from app.db.client import require_client

    conv = require_client().table("conversations").insert(
        {"company_id": t.company_id, "user_id": t.user_id, "title": "chat",
         "query": "chat", "agent_type": "ask"}
    ).execute().data[0]
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open the PRD for compliance reporting",
              "conversation_id": conv["id"]},
    ).json()

    assert body["open"]["status"] == "ambiguous"
    assert {c["prd_id"] for c in body["open"]["candidates"]} == {a, b}


# ─── edit_prd: the project's PRD becomes the target ──────────────────────────


def test_individual_project_chat_edit_targets_project_prd(
    tenant_client, isolated_settings, monkeypatch
):
    """`edit_prd` in a project chat with no open tab still gets the project's PRD
    as its target (server-derived), so the classify keeps `edit_prd` instead of
    downgrading to `answer` for lack of a target — the reported summary defect."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Onboarding Flow", theme_id="chat:e")
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)
    seen = _edit_envelope(monkeypatch)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "make the PRD's intro shorter",
              "conversation_id": conv["id"]},
    ).json()

    # The route resolved the project's PRD as the target and both fed it to the
    # resolver AND echoed it on the envelope, so the client dispatches edit_prd
    # against it rather than showing a summary.
    assert seen["prd_id"] == project_prd
    assert body["intent"] == "edit_prd"
    assert body["prd_id"] == project_prd


def test_main_chat_edit_without_target_still_has_none(
    tenant_client, isolated_settings, monkeypatch
):
    """Mutation proof: the same edit with no project binding and no open PRD
    reaches the resolver with `prd_id=None` (the pre-fix behaviour) — the
    project derivation is what supplies the target above, nothing else."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    _seed_prd(db, dataset="acme", title="Onboarding Flow", theme_id="chat:me")
    from app.db.client import require_client

    conv = require_client().table("conversations").insert(
        {"company_id": t.company_id, "user_id": t.user_id, "title": "chat",
         "query": "chat", "agent_type": "ask"}
    ).execute().data[0]
    seen = _edit_envelope(monkeypatch)

    t.client.post(
        "/v1/chat/intent",
        json={"message": "make the PRD's intro shorter",
              "conversation_id": conv["id"]},
    )

    assert seen["prd_id"] is None


# ─── get_conversation_project_id unit contract ───────────────────────────────


def test_get_conversation_project_id_reads_binding(
    tenant_client, isolated_settings
):
    """Individual + group bound rows return their `project_id`; a main-chat row
    (no project) and a foreign-company read return None."""
    t = tenant_client.make(slug="acme")
    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch",
        created_by=t.user_id,
    )
    individual = conversations_db.create_individual_project_chat(project["id"], t.user_id)
    group = _insert_legacy_group_conversation(t, project["id"])

    assert conversations_db.get_conversation_project_id(
        individual["id"], t.company_id
    ) == project["id"]
    assert conversations_db.get_conversation_project_id(
        group["id"], t.company_id
    ) == project["id"]

    # A main-chat row (no project binding) → None.
    from app.db.client import require_client

    plain = require_client().table("conversations").insert(
        {"company_id": t.company_id, "user_id": t.user_id, "title": "chat"}
    ).execute().data[0]
    assert conversations_db.get_conversation_project_id(
        plain["id"], t.company_id
    ) is None

    # A foreign company can't read this project's binding.
    assert conversations_db.get_conversation_project_id(
        individual["id"], "some-other-company"
    ) is None


# ─── open_artifact: a REPORT is scoped to the thread that produced it ────────
#
# REPORTED live: a chat holding two reports was asked to choose between reports
# from all over the workspace, and a chat holding NO ticket set was handed
# another conversation's — opened inside this one. Reports, ticket sets and team
# documents are each BORN in a conversation, so in a chat "the report" can only
# mean this chat's. These drive the real route, so they cover the whole path:
# route → conversation binding → enrich → resolve_open_artifact.


def _report_envelope(monkeypatch, query: str = "", kind: str = "report"):
    """Force a bare `open_artifact` verdict for a thread-born kind — the shape
    `detect_open_intent` produces for "show me the report" (no title at all)."""
    import app.routes.chat as chat_route

    def _resolve(enterprise_id, message, history=None, *, prd_id=None,
                 prd_title=None, has_attachments=False, open_artifact=None,
                 thread_artifact=None):
        return {
            "intent": "open_artifact", "confidence": 1.0, "task": None,
            "instruction": None, "artifact_type": kind,
            "artifact_query": query or None,
            "reason": "deterministic open request", "source": "open_intent",
        }

    monkeypatch.setattr(chat_route, "resolve_chat_intent", _resolve)


def _plain_conversation(t, title: str = "chat"):
    from app.db.client import require_client

    return require_client().table("conversations").insert(
        {"company_id": t.company_id, "user_id": t.user_id, "title": title,
         "query": title, "agent_type": "ask"}
    ).execute().data[0]


def _seed_report(t, *, title: str, conversation_id: int | None) -> int:
    from app.db.reports import save_report

    return save_report(
        t.company_id, skill="voice-of-customer-report", title=title,
        html="<p>body</p>", question="q", conversation_id=conversation_id,
    )


def test_a_chat_with_no_report_is_not_handed_another_chats(
    tenant_client, isolated_settings, monkeypatch
):
    """The reported failure at its sharpest: the workspace's ONLY report belongs
    to a different conversation, so without thread scoping it resolves and opens
    right here. Nothing of this chat's exists, and that is the answer."""
    t = tenant_client.make(slug="acme")
    mine = _plain_conversation(t, "my chat")
    theirs = _plain_conversation(t, "another chat")
    _seed_report(t, title="Churn review", conversation_id=theirs["id"])
    _report_envelope(monkeypatch)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "show me the report", "conversation_id": mine["id"]},
    ).json()

    assert body["open"]["status"] == "not_found"
    assert body["open"]["artifact"] is None
    assert body["open"]["candidates"] == []


def test_a_chat_with_two_reports_is_asked_about_ITS_OWN_two(
    tenant_client, isolated_settings, monkeypatch
):
    """The other half of the report: "this thread has just two reports, but it
    returned everything". Two of its own is still a question — asked about the
    two, not about the workspace."""
    t = tenant_client.make(slug="acme")
    mine = _plain_conversation(t, "my chat")
    theirs = _plain_conversation(t, "another chat")
    a = _seed_report(t, title="Onboarding drop-off", conversation_id=mine["id"])
    b = _seed_report(t, title="Churn review", conversation_id=mine["id"])
    _seed_report(t, title="Not mine", conversation_id=theirs["id"])
    _seed_report(t, title="Standalone", conversation_id=None)
    _report_envelope(monkeypatch)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "show me the report", "conversation_id": mine["id"]},
    ).json()

    assert body["open"]["status"] == "ambiguous"
    assert {c["report_id"] for c in body["open"]["candidates"]} == {a, b}


def test_a_chats_own_single_report_still_opens(
    tenant_client, isolated_settings, monkeypatch
):
    """Mutation proof, other direction: the scoping is what makes the two cases
    above land where they do — with exactly one of its own, the chat still
    opens it, amid reports it does not own."""
    t = tenant_client.make(slug="acme")
    mine = _plain_conversation(t, "my chat")
    theirs = _plain_conversation(t, "another chat")
    a = _seed_report(t, title="Onboarding drop-off", conversation_id=mine["id"])
    _seed_report(t, title="Not mine", conversation_id=theirs["id"])
    _report_envelope(monkeypatch)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "show me the report", "conversation_id": mine["id"]},
    ).json()

    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["report_id"] == a


def test_a_project_chat_opens_the_PROJECTS_report_not_the_workspaces(
    tenant_client, isolated_settings, monkeypatch
):
    """The project half of the same question. A project chat's container is the
    PROJECT: its own report opens, and an identically-titled workspace report
    that is NOT on the project cannot make it ambiguous."""
    t = tenant_client.make(slug="acme")
    project_prd = _seed_prd(
        isolated_settings["db"], dataset="acme", title="Anchor", theme_id="chat:rp",
    )
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)
    # The project's own report, plus a workspace twin that is NOT on it.
    on_project = _seed_report(t, title="Churn review", conversation_id=conv["id"])
    projects_db.add_artifact(project_id, "report", on_project)
    _seed_report(t, title="Churn review", conversation_id=None)
    _report_envelope(monkeypatch, "churn review")

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "show me the churn review report",
              "conversation_id": conv["id"]},
    ).json()

    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["report_id"] == on_project


def test_a_project_report_no_chat_produced_is_still_openable(
    tenant_client, isolated_settings, monkeypatch
):
    """Why the thread filter must NOT apply under a project scope: a report
    attached to a project carries no conversation of its own, and narrowing to
    whichever chat is open would hide an artifact that genuinely belongs to the
    project the reader is standing in."""
    t = tenant_client.make(slug="acme")
    project_prd = _seed_prd(
        isolated_settings["db"], dataset="acme", title="Anchor", theme_id="chat:rp2",
    )
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)
    orphan = _seed_report(t, title="Quarterly review", conversation_id=None)
    projects_db.add_artifact(project_id, "report", orphan)
    _report_envelope(monkeypatch)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "show me the report", "conversation_id": conv["id"]},
    ).json()

    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["report_id"] == orphan

"""Fast-lane tests for the mention/add/accept LIVENESS signals + the invite-
reminder project-name drip.

The load-bearing gate (AD-TNM2/AD-P30): a `mention.received`/`member.added`
signal is PRIVATE to the recipient — it publishes to the target's OWN per-user
channel `project:{id}:user:{uid}` ONLY, NEVER the group channel `project:{id}`,
and carries a hard-whitelisted DTO ({project_id, project_name, actor_name,
kind}) with NO message, brief, artifact, or member-list content. Publishing is
best-effort (AD-P22): a `publish_broadcast` failure never raises, never changes
the tag route's response, and never rolls back the membership write.

Spies `publish_broadcast` (patched on `project_delegation`, the module the
publishers call through) so no real Realtime traffic is made — same shape as
`test_delegation_event_publish.py`/`test_realtime_publish.py`. The tag-route
tier is pinned via `resolve_candidate` monkeypatch exactly like
`test_tag_candidate_api.py`, so these exercise the ROUTE's per-branch publish
wiring, not the resolver's classification.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import uuid

from app import project_delegation
from app.db import projects as projects_db
from app.db import team as team_db
from app.routes import projects as projects_routes
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


# ── helpers ──────────────────────────────────────────────────────────────


def _new_project(ctx, name: str = "Launch") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _project_member_rows(project_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("project_members")
        .select("*")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )


def _seed_workspace_member(ctx, workspace_id: str, *, role: str = "member") -> str:
    from app.db.workspaces import upsert_workspace_member

    uid = "ws-user-" + uuid.uuid4().hex[:8]
    upsert_workspace_member(workspace_id, uid, role)
    return uid


def _pin_resolver(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(projects_db, "resolve_candidate", lambda pid, needle: result)


def _capture_invite_email(monkeypatch, status: str = "sent") -> list[dict]:
    calls: list[dict] = []

    def _fake(email, **kwargs):
        calls.append({"email": email, **kwargs})
        return status

    monkeypatch.setattr(projects_routes, "send_invite_email", _fake)
    return calls


def _spy_publish(monkeypatch, *, raises: bool = False) -> list[dict]:
    """Record every `publish_broadcast(topic, event, payload)` the publishers
    make; optionally force it to raise to prove the best-effort swallow (AC-5).
    Patched on `project_delegation` — the module the publishers read
    `publish_broadcast` from."""
    calls: list[dict] = []

    def _fake(topic, event, payload):
        calls.append({"topic": topic, "event": event, "payload": payload})
        if raises:
            raise RuntimeError("realtime down")

    monkeypatch.setattr(project_delegation, "publish_broadcast", _fake)
    return calls


# ── AC-1: mention.received on the per-user channel ONLY ───────────────────


def test_mention_signal_per_user_channel_only(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    mentioned = "mentioned-" + uuid.uuid4().hex[:8]
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": mentioned, "name": "Mia"}},
    )
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Mia"})
    assert r.status_code == 200, r.text

    assert len(calls) == 1
    assert calls[0]["event"] == "mention.received"
    assert calls[0]["topic"] == f"project:{project['id']}:user:{mentioned}"
    # NEVER the group channel — a private nudge on `project:{id}` would leak it.
    assert calls[0]["topic"] != f"project:{project['id']}"


# ── AC-2: member.added on the added user's per-user channel ────────────────


def test_member_added_signal_channel(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Fortune"})
    assert r.status_code == 200, r.text

    assert len(calls) == 1
    assert calls[0]["event"] == "member.added"
    assert calls[0]["topic"] == f"project:{project['id']}:user:{uid}"
    assert calls[0]["topic"] != f"project:{project['id']}"


# ── AC-4: whitelisted DTO, no content leak ────────────────────────────────


def test_signal_dto_whitelist_no_content(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx, name="Pricing revamp")
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})
    calls = _spy_publish(monkeypatch)

    ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "x"})

    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert set(payload.keys()) == {"project_id", "project_name", "actor_name", "kind"}
    assert payload["project_id"] == project["id"]
    assert payload["project_name"] == "Pricing revamp"
    assert payload["kind"] == "added"
    # No message / brief / roster / artifact content ever rides along (AD-TNM2).
    for leaked in ("content", "message", "brief", "members", "member_list",
                   "artifacts", "roster", "task_summary"):
        assert leaked not in payload, leaked


# ── AC-5: best-effort — publish failure never raises / rolls back ─────────


def test_publish_failure_swallowed_no_raise_no_rollback(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})
    _spy_publish(monkeypatch, raises=True)  # forced-raise publish

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "x"})
    # The tag route still returns success over a write that committed.
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "t_workspace"
    # The membership write is intact — not rolled back by the realtime hiccup.
    assert uid in {m["user_id"] for m in _project_member_rows(project["id"])}


# ── AC-6: invite tiers publish NO per-user signal ─────────────────────────


def test_invite_tier_publishes_no_signal(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _capture_invite_email(monkeypatch)
    calls = _spy_publish(monkeypatch)

    for tier in (projects_db.TIER_COMPANY, projects_db.TIER_NEWUSER):
        email = f"{tier}@acme.example"  # distinct — UNIQUE (company_id, email)
        _pin_resolver(monkeypatch, {"tier": tier, "email": email})
        r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": email})
        assert r.status_code == 200, r.text

    # An invitee has no per-user channel pre-accept — zero signals published.
    assert calls == []


# ── AC-1/AC-2: right branch invokes the right publisher ───────────────────


def test_tag_route_invokes_publisher_on_right_branch(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    calls = _spy_publish(monkeypatch)

    # t_member → mention.received
    mentioned = "mentioned-" + uuid.uuid4().hex[:8]
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": mentioned, "name": "Mia"}},
    )
    ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Mia"})

    # t_workspace → member.added
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})
    ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "y"})

    events = [(c["event"], c["topic"]) for c in calls]
    assert ("mention.received", f"project:{project['id']}:user:{mentioned}") in events
    assert ("member.added", f"project:{project['id']}:user:{uid}") in events
    assert len(calls) == 2


# ── AC-3: accept-hook publishes member.added ──────────────────────────────


def test_accept_hook_publishes_member_added(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx, name="Onboarding")
    calls = _spy_publish(monkeypatch)

    accepter = "accepter-" + uuid.uuid4().hex[:8]
    # The Extension-B project hook: an accepted project-carrying invite lands
    # the accepter in project_members AND fires a live member.added signal.
    team_db._add_invite_project_member({"project_id": project["id"]}, accepter)

    assert accepter in {m["user_id"] for m in _project_member_rows(project["id"])}
    assert len(calls) == 1
    assert calls[0]["event"] == "member.added"
    assert calls[0]["topic"] == f"project:{project['id']}:user:{accepter}"
    assert calls[0]["payload"]["project_name"] == "Onboarding"
    assert calls[0]["payload"]["kind"] == "added"


def test_accept_hook_no_project_id_is_noop(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)  # noqa: F841 — sets up the fake client
    calls = _spy_publish(monkeypatch)
    # A plain WJ/team invite (no project_id) publishes nothing.
    team_db._add_invite_project_member({}, "some-uid")
    assert calls == []


# ── AC-7: the reminder select-widening is source-grounded ─────────────────


def test_reminder_select_carries_project_id(isolated_settings):
    """Source-grounded proof (NOT a fake-DB round-trip): FakeSupabaseClient
    ignores the `.select(...)` column list entirely (`_cols` is stored but
    never applied), so a returned-value assertion would pass even with the OLD
    narrow select — it cannot prove the widening. The truth is the source: the
    column-enumerated select must NAME `project_id` (AC-7)."""
    from app.db import invite_reminders as inv_db

    src = inspect.getsource(inv_db.list_pending_invites_all_companies)
    select_arg = src.split(".select(", 1)[1].split(".execute()", 1)[0]
    assert "project_id" in select_arg, (
        "list_pending_invites_all_companies().select(...) must include "
        "project_id or the reminder copy silently no-ops"
    )


# ── AC-7: project-carrying invite names the project; project-less unchanged ─


def test_render_names_project_when_present_else_unchanged(isolated_settings):
    ir = importlib.import_module("app.invite_reminders")
    importlib.reload(ir)

    base_subject, base_text, base_html = ir.render_reminder(
        ir.STEP_DAY_1,
        first_name="Zoe",
        inviter_first_name="Dana",
        workspace_name="Acme",
    )
    proj_subject, proj_text, proj_html = ir.render_reminder(
        ir.STEP_DAY_1,
        first_name="Zoe",
        inviter_first_name="Dana",
        workspace_name="Acme",
        project_name="Pricing Revamp",
    )

    # The project-carrying copy names the project, in both parts.
    assert "Pricing Revamp" in proj_text
    assert "Pricing Revamp" in proj_html
    # The project-less copy is byte-identical to the prior render.
    assert "Pricing Revamp" not in base_text
    assert "Pricing Revamp" not in base_html
    assert base_subject == proj_subject  # subject line is unchanged either way


def test_reminder_includes_project_invite_with_name(isolated_settings, monkeypatch):
    """The cycle resolves the project name via `get_project` for a project-
    carrying invite and threads it into `send_reminder_email`; a project-less
    invite passes no name (its copy stays unchanged). Uses a REAL project row
    (created via the route, valid `created_by`) so `get_project` returns it."""
    from datetime import datetime, timedelta, timezone

    ctx = company_client(monkeypatch)
    project = _new_project(ctx, name="Pricing Revamp")

    import app.db.invite_reminders as inv_db
    importlib.reload(inv_db)
    ir = importlib.import_module("app.invite_reminders")
    importlib.reload(ir)

    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "invite_expiry_days", 30, raising=False)

    from app.db.client import require_client
    client = require_client()

    def _iso_days_ago(days: int) -> str:
        return (
            (datetime.now(timezone.utc) - timedelta(days=days))
            .replace(microsecond=0)
            .isoformat()
        )

    # A project-carrying invite (Extension B) + a plain project-less invite,
    # both a due day-1 age, under the client's real company.
    client.table("workspace_invites").insert(
        {
            "id": "inv-proj", "company_id": ctx.company_id, "email": "proj@x.com",
            "role": "member", "invited_by": ctx.user_id,
            "created_at": _iso_days_ago(10), "workspace_ids": [],
            "project_id": project["id"],
        }
    ).execute()
    client.table("workspace_invites").insert(
        {
            "id": "inv-plain", "company_id": ctx.company_id, "email": "plain@x.com",
            "role": "member", "invited_by": ctx.user_id,
            "created_at": _iso_days_ago(10), "workspace_ids": [], "project_id": None,
        }
    ).execute()

    captured: list[dict] = []

    def _spy_send(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(ir, "send_reminder_email", _spy_send)

    summary = ir.run_invite_reminder_cycle()
    assert summary["sent"] == 2

    by_email = {c["to_email"]: c for c in captured}
    assert by_email["proj@x.com"]["project_name"] == "Pricing Revamp"
    # A project-less invite carries no project name → the copy is unchanged.
    assert not (by_email["plain@x.com"].get("project_name") or "")


# ── AC-9: no LLM cost line, ids-only logs (no actor/target names) ─────────


def test_no_llm_cost_line_no_pii_in_logs(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    from app.db.client import require_client

    # A distinctive actor first name — it may ride the private DTO, but must
    # NEVER appear in a log line (ids only, AD-P: no PII in logs).
    require_client().table("profiles").insert(
        {"id": ctx.user_id, "first_name": "Zephyrine", "full_name": "Zephyrine Q"}
    ).execute()
    _spy_publish(monkeypatch)  # no real network, but the INFO log still fires

    # An opaque id (never a name substring) so the ids-only log line can't
    # accidentally satisfy the "no name" assertion via the id itself.
    mentioned = "mentioned-" + uuid.uuid4().hex[:8]
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": mentioned, "name": "Xanthe Doe"}},
    )
    with caplog.at_level(logging.INFO):
        r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Xanthe"})
    assert r.status_code == 200

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "mention_signal_published" in text  # the ids-only observability line fired
    assert "cost" not in text.lower()  # no LLM cost-summary line (pure CRUD)
    assert "Zephyrine" not in text  # actor name never logged
    assert "Xanthe" not in text  # target name never logged


# ── AC-10: non-breakage — ledger publishers + signatures unchanged ────────


def test_ledger_publishers_and_signatures_unchanged(isolated_settings):
    # The new publishers are net-new siblings; the ledger's own publishers and
    # handler are untouched.
    assert hasattr(project_delegation, "_publish_delegation_event")
    assert hasattr(project_delegation, "_publish_brief_delivered")
    assert hasattr(project_delegation, "handle_delegate_task")
    sig = inspect.signature(project_delegation.handle_delegate_task)
    assert list(sig.parameters) == [
        "project_id", "assigner_user_id", "source_conversation_id",
        "source_turn_id", "roster", "dataset", "company_id", "tool_input",
        # Added by 9513cc26 so a delegated task carries the originating
        # message's own text instead of the handler inventing a summary.
        # Pinned here because this guard watches for UNINTENDED drift, and a
        # real-but-unlisted parameter just makes it fail on every run.
        "source_content",
    ]
    # The new publishers exist with the documented shapes.
    assert list(inspect.signature(project_delegation._publish_member_added).parameters) == [
        "project_id", "target_user_id", "project_name",
    ]
    assert list(inspect.signature(project_delegation._publish_mention_signal).parameters) == [
        "project_id", "target_user_id", "actor_name", "project_name",
    ]

"""Tests for the invite reminder drip (Day-1 / Day-3 follow-ups).

Covers:
  - weekend shifting + due-time math (next_workday / due_at / _due_step)
  - copy rendering (fills names, escapes user data, branded shell, fallbacks)
  - send_reminder_email best-effort contract (no key / ok / non-2xx / raise)
  - run_invite_reminder_cycle end-to-end over the fake DB: eligibility by age,
    Day-3 anchored to the Day-1 send, de-dup, and the stop conditions
    (already-member, expired, accepted→row-deleted).
  - scheduler wiring (job registered only when enabled).

Uses the in-memory fake Supabase from conftest (isolated_settings).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from app.db.client import require_client


def _iso_days_ago(days: float) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
    )


def _mod(isolated_settings):
    ir = importlib.import_module("app.invite_reminders")
    importlib.reload(ir)
    return ir


# ── seeding ─────────────────────────────────────────────────────────────


def _seed_company(client, company_id="co-1", name="Acme"):
    client.table("companies").insert(
        {"id": company_id, "slug": name.lower(), "display_name": name}
    ).execute()


def _seed_inviter(client, user_id="inviter-1", first_name="Dana"):
    client.table("profiles").insert(
        {"id": user_id, "email": f"{user_id}@acme.com", "first_name": first_name}
    ).execute()


def _seed_invite(
    client, *, invite_id="inv-1", company_id="co-1", email="new@bob.com",
    invited_by="inviter-1", created_days_ago=10,
):
    client.table("workspace_invites").insert(
        {
            "id": invite_id,
            "company_id": company_id,
            "email": email,
            "role": "member",
            "invited_by": invited_by,
            "created_at": _iso_days_ago(created_days_ago),
            "workspace_ids": [],
        }
    ).execute()


# ── time math ───────────────────────────────────────────────────────────


def test_next_workday_shifts_weekends(isolated_settings):
    ir = _mod(isolated_settings)
    sat = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)  # Saturday
    sun = datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc)  # Sunday
    mon = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)  # Monday
    wed = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)  # Wednesday
    assert ir.next_workday(sat) == mon           # +2 days, time preserved
    assert ir.next_workday(sun) == mon           # +1 day
    assert ir.next_workday(mon) == mon           # unchanged
    assert ir.next_workday(wed) == wed


def test_due_at_shifts_target_off_weekend(isolated_settings):
    ir = _mod(isolated_settings)
    # Invite created Friday → +1 day = Saturday → shifts to Monday.
    fri = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)
    assert ir.due_at(fri, 1) == datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
    # Created Monday → +1 = Tuesday (no shift).
    mon = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
    assert ir.due_at(mon, 1) == datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def test_due_step_selects_day1_then_day3(isolated_settings):
    ir = _mod(isolated_settings)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    created = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)  # Monday, 10d ago

    # Nothing sent, past day_1 target → day_1 due.
    step, _t = ir._due_step(created_at=created, sends={}, now=now)
    assert step.key == "day_1"

    # day_1 sent 4 days ago → day_3 (day1_sent + 3) is due.
    d1_at = (now - timedelta(days=4)).isoformat()
    step, _t = ir._due_step(created_at=created, sends={"day_1": d1_at}, now=now)
    assert step.key == "day_3"

    # day_1 sent just now → day_3 not due yet.
    d1_now = now.isoformat()
    assert ir._due_step(created_at=created, sends={"day_1": d1_now}, now=now) is None

    # day_1 + day_3 sent, day_3 sent 8 days ago → day_7 (day_3 + 7) is due.
    d3_old = (now - timedelta(days=8)).isoformat()
    step, _t = ir._due_step(
        created_at=created, sends={"day_1": d1_at, "day_3": d3_old}, now=now
    )
    assert step.key == "day_7"

    # day_3 sent recently → day_7 not due yet.
    d3_now = now.isoformat()
    assert ir._due_step(
        created_at=created, sends={"day_1": d1_at, "day_3": d3_now}, now=now
    ) is None

    # all three sent → nothing.
    assert ir._due_step(
        created_at=created,
        sends={"day_1": d1_at, "day_3": d1_at, "day_7": d1_at},
        now=now,
    ) is None


def test_due_step_day1_not_due_when_fresh(isolated_settings):
    ir = _mod(isolated_settings)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    created = now  # just invited → day_1 target is in the future
    assert ir._due_step(created_at=created, sends={}, now=now) is None


# ── rendering ───────────────────────────────────────────────────────────


def test_render_day1_fills_and_links(isolated_settings):
    ir = _mod(isolated_settings)
    subject, text, html = ir.render_reminder(
        ir.STEP_DAY_1,
        first_name="Bob",
        inviter_first_name="Dana",
        workspace_name="Acme",
        accept_link="https://app.sprntly.ai/sign-in",
    )
    assert subject == "Your invitation from Dana is waiting"
    # Verbatim spec copy.
    assert (
        "Hi Bob, You were invited by Dana to join Sprntly where you can "
        "collaborate on PRD, tickets, prototypes. We realized you haven't "
        "joined yet." in text
    )
    assert "Here's the link again: https://app.sprntly.ai/sign-in" in text
    assert "It takes under 60 seconds." in text
    assert "https://app.sprntly.ai/sign-in" in html
    assert "Sprntly<span" in html and "#1a8a52" in html  # branded shell


def test_render_day7_team_joined(isolated_settings):
    ir = _mod(isolated_settings)
    subject, text, _html = ir.render_reminder(
        ir.STEP_DAY_7,
        first_name="Bob",
        inviter_first_name="Dana",
        workspace_name="Acme",
        accept_link="https://x/sign-in",
    )
    assert subject == "Dana is waiting on you to join Acme"
    assert "Most of your team has set up their accounts on Acme. Yours is " \
        "still open." in text
    assert "Set up your account: https://x/sign-in" in text


def test_render_day3_expiring(isolated_settings):
    ir = _mod(isolated_settings)
    subject, text, _html = ir.render_reminder(
        ir.STEP_DAY_3,
        first_name="Bob",
        inviter_first_name="Dana",
        workspace_name="Acme",
        accept_link="https://x/sign-in",
    )
    assert subject == "Your invitation from Dana is expiring"
    assert "expiring soon" in text
    assert "Acme" in text


def test_render_fallbacks_and_escaping(isolated_settings):
    ir = _mod(isolated_settings)
    subject, text, html = ir.render_reminder(
        ir.STEP_DAY_1,
        first_name="",
        inviter_first_name="",
        workspace_name="",
        accept_link="https://x/sign-in",
    )
    assert "a teammate" in subject          # inviter fallback
    assert "Hi there," in text              # first-name fallback
    # user data is escaped in HTML
    _s, _t, html2 = ir.render_reminder(
        ir.STEP_DAY_3, first_name="A<b>", inviter_first_name="C&D",
        workspace_name="W<x>", accept_link="https://x/sign-in",
    )
    assert "A&lt;b&gt;" in html2 and "C&amp;D" in html2 and "W&lt;x&gt;" in html2


# ── send best-effort ────────────────────────────────────────────────────


def test_send_no_key_returns_false(isolated_settings, monkeypatch):
    ir = _mod(isolated_settings)
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "resend_api_key", "", raising=False)
    assert ir.send_reminder_email(
        to_email="a@b.com", step=ir.STEP_DAY_1,
        first_name="B", inviter_first_name="D", workspace_name="W",
    ) is False


def test_send_success_posts_both_parts(isolated_settings, monkeypatch):
    ir = _mod(isolated_settings)
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "resend_api_key", "re_test", raising=False)

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def _fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(ir.httpx, "post", _fake_post)
    ok = ir.send_reminder_email(
        to_email="a@b.com", step=ir.STEP_DAY_1,
        first_name="Bob", inviter_first_name="Dana", workspace_name="Acme",
    )
    assert ok is True
    assert captured["json"]["to"] == ["a@b.com"]
    assert "Dana" in captured["json"]["subject"]
    assert "Bob" in captured["json"]["text"]
    assert "1a8a52" in captured["json"]["html"]


def test_send_non_2xx_and_raise_return_false(isolated_settings, monkeypatch):
    ir = _mod(isolated_settings)
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "resend_api_key", "re_test", raising=False)

    class _Resp:
        status_code = 500
        text = "err"

    monkeypatch.setattr(ir.httpx, "post", lambda url, **kw: _Resp())
    assert ir.send_reminder_email(
        to_email="a@b.com", step=ir.STEP_DAY_1,
        first_name="B", inviter_first_name="D", workspace_name="W",
    ) is False

    def _boom(url, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(ir.httpx, "post", _boom)
    assert ir.send_reminder_email(
        to_email="a@b.com", step=ir.STEP_DAY_1,
        first_name="B", inviter_first_name="D", workspace_name="W",
    ) is False


# ── run_invite_reminder_cycle end-to-end ─────────────────────────────────


@pytest.fixture
def ir_cycle(isolated_settings, monkeypatch):
    """The module wired for cycle tests: an always-succeed sender so we exercise
    eligibility + tracking, not the transport."""
    import app.db.invite_reminders as inv_db
    importlib.reload(inv_db)
    ir = importlib.import_module("app.invite_reminders")
    importlib.reload(ir)
    monkeypatch.setattr(ir, "send_reminder_email", lambda **kw: True)
    return ir


def test_cycle_sends_day1_when_eligible(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=10)

    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 1
    rows = client.table("invite_reminder_sends").select(
        "step_key, status"
    ).eq("invite_id", "inv-1").execute().data
    assert len(rows) == 1
    assert rows[0]["step_key"] == "day_1"
    assert rows[0]["status"] == "sent"


def test_cycle_not_due_for_fresh_invite(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=0)  # target is in the future
    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 0
    assert summary["steps_considered"] == 0


def test_cycle_does_not_double_send(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=10)

    first = ir_cycle.run_invite_reminder_cycle()
    assert first["sent"] == 1
    # Second pass: day_1 already recorded, day_3 not yet due (day_1 just sent).
    second = ir_cycle.run_invite_reminder_cycle()
    assert second["sent"] == 0
    rows = client.table("invite_reminder_sends").select("id").eq(
        "invite_id", "inv-1"
    ).execute().data
    assert len(rows) == 1


def test_cycle_sends_day3_after_day1(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=10)
    # Pre-record day_1 as sent 4 days ago → day_3 (day1+3) is now due.
    client.table("invite_reminder_sends").insert(
        {
            "id": "rs-1", "invite_id": "inv-1", "company_id": "co-1",
            "email": "new@bob.com", "step_key": "day_1", "status": "sent",
            "sent_at": _iso_days_ago(4),
        }
    ).execute()

    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 1
    keys = {
        r["step_key"]
        for r in client.table("invite_reminder_sends").select("step_key")
        .eq("invite_id", "inv-1").execute().data
    }
    assert keys == {"day_1", "day_3"}


def test_cycle_sends_day7_after_day3(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=20)
    # day_1 + day_3 already sent; day_3 sent 8 days ago → day_7 (day_3 + 7) due.
    for step, rs_id, days in (("day_1", "rs-1", 11), ("day_3", "rs-2", 8)):
        client.table("invite_reminder_sends").insert(
            {
                "id": rs_id, "invite_id": "inv-1", "company_id": "co-1",
                "email": "new@bob.com", "step_key": step, "status": "sent",
                "sent_at": _iso_days_ago(days),
            }
        ).execute()

    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 1
    keys = {
        r["step_key"]
        for r in client.table("invite_reminder_sends").select("step_key")
        .eq("invite_id", "inv-1").execute().data
    }
    assert keys == {"day_1", "day_3", "day_7"}


def test_cycle_stops_when_already_member(ir_cycle):
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, email="member@bob.com", created_days_ago=10)
    # The invitee already has a profile + membership in this company.
    client.table("profiles").insert(
        {"id": "u-member", "email": "member@bob.com", "first_name": "Mo"}
    ).execute()
    client.table("company_members").insert(
        {"id": "cm-1", "company_id": "co-1", "user_id": "u-member", "role": "member"}
    ).execute()

    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 0
    assert client.table("invite_reminder_sends").select("id").execute().data == []


def test_cycle_stops_when_expired(ir_cycle, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "invite_expiry_days", 14, raising=False)
    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=20)  # older than expiry

    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary["sent"] == 0
    assert client.table("invite_reminder_sends").select("id").execute().data == []


def test_cycle_records_skipped_when_send_fails(isolated_settings, monkeypatch):
    import app.db.invite_reminders as inv_db
    importlib.reload(inv_db)
    ir = importlib.import_module("app.invite_reminders")
    importlib.reload(ir)
    monkeypatch.setattr(ir, "send_reminder_email", lambda **kw: False)

    client = require_client()
    _seed_company(client)
    _seed_inviter(client)
    _seed_invite(client, created_days_ago=10)

    summary = ir.run_invite_reminder_cycle()
    assert summary["skipped"] == 1
    assert summary["sent"] == 0
    rows = client.table("invite_reminder_sends").select("status").eq(
        "invite_id", "inv-1"
    ).execute().data
    assert rows[0]["status"] == "skipped"
    # A later cycle does not retry the skipped step.
    assert ir.run_invite_reminder_cycle()["steps_considered"] == 0


def test_cycle_no_invites_is_noop(ir_cycle):
    summary = ir_cycle.run_invite_reminder_cycle()
    assert summary == {
        "invites": 0, "sent": 0, "skipped": 0, "steps_considered": 0
    }


# ── scheduler wiring ─────────────────────────────────────────────────────


class _FakeScheduler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, *, trigger=None, id=None, name=None,
                replace_existing=False):
        self.jobs.append({"id": id, "name": name})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        pass


def _run_start_scheduler(monkeypatch, *, invite_enabled):
    from app import scheduler as sched_mod
    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", True)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "invite_reminders_enabled", invite_enabled)
    monkeypatch.setattr(sched_mod.settings, "invite_reminder_interval_hours", 6)
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_scheduler_registers_invite_job_when_enabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, invite_enabled=True)
    assert "invite_reminders" in {j["id"] for j in fake.jobs}


def test_scheduler_omits_invite_job_when_disabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, invite_enabled=False)
    assert "invite_reminders" not in {j["id"] for j in fake.jobs}

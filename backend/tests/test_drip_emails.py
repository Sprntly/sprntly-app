"""Tests for onboarding drip / nudge emails (v0 checklist 2.1).

Covers:
  - cadence resolution (default, global day override, per-company override,
    per-company disable)
  - copy rendering (uses "company", never "dataset")
  - send_drip_email best-effort contract (no key → False; Resend ok/err)
  - the scheduler-driven run_drip_cycle: eligibility by age, de-dup so steps
    never double-send, "skipped" recording when sending isn't configured.

Uses the in-memory fake Supabase from conftest (isolated_settings fixture).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from app.db.client import require_client


def _iso_days_ago(days: int) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
    )


def _seed_company(client, company_id="co-1", slug="acme", name="Acme",
                  notification_settings=None):
    row = {"id": company_id, "slug": slug, "display_name": name}
    if notification_settings is not None:
        row["notification_settings"] = notification_settings
    client.table("companies").insert(row).execute()
    return company_id


def _seed_member(client, company_id, user_id, *, joined_days_ago,
                 email=None, full_name="Pat"):
    client.table("company_members").insert(
        {
            "id": f"cm-{user_id}",
            "company_id": company_id,
            "user_id": user_id,
            "role": "owner",
            "created_at": _iso_days_ago(joined_days_ago),
        }
    ).execute()
    client.table("profiles").insert(
        {
            "id": user_id,
            "email": email or f"{user_id}@example.com",
            "full_name": full_name,
        }
    ).execute()


# ── cadence resolution ────────────────────────────────────────────────


def test_resolve_cadence_default(isolated_settings):
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    steps = drip.resolve_cadence(None)
    assert [s.day_offset for s in steps] == [1, 3, 7]
    assert [s.key for s in steps] == ["day_1", "day_3", "day_7"]


def test_resolve_cadence_global_override(isolated_settings, monkeypatch):
    monkeypatch.setenv("DRIP_CADENCE_DAYS", "2,5")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    steps = drip.resolve_cadence(None)
    assert [s.day_offset for s in steps] == [2, 5]
    assert [s.key for s in steps] == ["day_2", "day_5"]


def test_resolve_cadence_per_company_override_wins(isolated_settings, monkeypatch):
    monkeypatch.setenv("DRIP_CADENCE_DAYS", "2,5")  # should be overridden
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    ns = {"drip": {"cadence": [
        {"key": "day_1", "day_offset": 1, "subject": "Custom for {company}"},
    ]}}
    steps = drip.resolve_cadence(ns)
    assert len(steps) == 1
    assert steps[0].day_offset == 1
    assert steps[0].subject == "Custom for {company}"
    # missing body_text falls back to the default day_1 copy
    assert "{company}" in steps[0].body_text


def test_resolve_cadence_disabled(isolated_settings):
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.resolve_cadence({"drip": {"enabled": False}}) == []


# ── copy rendering ─────────────────────────────────────────────────────


def test_render_step_uses_company_not_dataset(isolated_settings):
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    for step in drip.DEFAULT_CADENCE:
        subject, body = drip.render_step(step, company="Acme", name="Pat")
        assert "Acme" in subject or "Acme" in body
        combined = (subject + body).lower()
        assert "dataset" not in combined
    # placeholder gaps degrade gracefully
    subject, body = drip.render_step(drip.DEFAULT_CADENCE[0], company="", name="")
    assert "your company" in body
    assert "there" in body


def test_render_drip_html_branded_shell(isolated_settings):
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    subject, body = drip.render_step(
        drip.DEFAULT_CADENCE[0], company="Acme <Co>", name="Pat"
    )
    html = drip.render_drip_html(subject=subject, body_text=body)
    # Branded shell: wordmark, card, green CTA.
    assert "Sprntly<span" in html
    assert "#1a8a52" in html
    assert "Open Sprntly" in html
    # Body paragraphs render escaped (no raw angle brackets from user data).
    assert "Acme &lt;Co&gt;" in html
    assert "Acme <Co>" not in html
    # Sign-off renders as the muted footer paragraph.
    assert "— The Sprntly team" in html


# ── send_drip_email best-effort contract ──────────────────────────────


def test_send_drip_email_no_key_returns_false(isolated_settings, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.send_drip_email(
        to_email="a@b.com", subject="s", body_text="b"
    ) is False


def test_send_drip_email_success(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return _Resp()

    monkeypatch.setattr(drip.httpx, "post", _fake_post)
    assert drip.send_drip_email(
        to_email="a@b.com", subject="Hello", body_text="Body"
    ) is True
    assert captured["url"] == drip.RESEND_API_URL
    assert captured["json"]["to"] == ["a@b.com"]
    assert captured["json"]["subject"] == "Hello"
    # Both parts ship: branded HTML + the plain-text fallback.
    assert captured["json"]["text"] == "Body"
    assert "Open Sprntly" in captured["json"]["html"]
    assert "Bearer re_test" in captured["headers"]["Authorization"]


def test_send_drip_email_non_2xx_returns_false(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)

    class _Resp:
        status_code = 422
        text = "bad"

    monkeypatch.setattr(drip.httpx, "post", lambda url, **kw: _Resp())
    assert drip.send_drip_email(
        to_email="a@b.com", subject="s", body_text="b"
    ) is False


def test_send_drip_email_swallows_exceptions(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)

    def _boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(drip.httpx, "post", _boom)
    assert drip.send_drip_email(
        to_email="a@b.com", subject="s", body_text="b"
    ) is False


# ── run_drip_cycle end-to-end (over the fake DB) ──────────────────────


@pytest.fixture
def drip_mod(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    import app.config as config_mod
    importlib.reload(config_mod)
    import app.db.drip as drip_db
    importlib.reload(drip_db)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    # Always-succeed sender so we exercise the eligibility + tracking logic.
    monkeypatch.setattr(drip, "send_drip_email", lambda **kw: True)
    return drip


def test_run_drip_cycle_sends_eligible_steps(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client)
    # Member joined 4 days ago → day_1 and day_3 are eligible, day_7 not yet.
    _seed_member(client, "co-1", "u1", joined_days_ago=4)

    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 2

    rows = client.table("drip_email_sends").select("step_key, status").eq(
        "company_id", "co-1"
    ).execute().data
    sent_keys = {r["step_key"] for r in rows}
    assert sent_keys == {"day_1", "day_3"}
    assert all(r["status"] == "sent" for r in rows)


def test_run_drip_cycle_does_not_double_send(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=10)

    first = drip_mod.run_drip_cycle()
    assert first["sent"] == 3  # day_1 + day_3 + day_7

    second = drip_mod.run_drip_cycle()
    assert second["sent"] == 0
    assert second["steps_considered"] == 0

    rows = client.table("drip_email_sends").select("id").eq(
        "company_id", "co-1"
    ).execute().data
    assert len(rows) == 3  # no duplicates


def test_run_drip_cycle_skips_brand_new_member(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=0)
    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 0


def test_run_drip_cycle_respects_company_disable(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client, notification_settings={"drip": {"enabled": False}})
    _seed_member(client, "co-1", "u1", joined_days_ago=30)
    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 0
    rows = client.table("drip_email_sends").select("id").execute().data
    assert rows == []


def test_run_drip_cycle_records_skipped_when_send_fails(isolated_settings, monkeypatch):
    # No RESEND_API_KEY → send returns False → recorded as "skipped" but still
    # de-duped so a later config change won't retro-blast.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    import app.config as config_mod
    importlib.reload(config_mod)
    import app.db.drip as drip_db
    importlib.reload(drip_db)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)

    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=2)

    summary = drip.run_drip_cycle()
    assert summary["skipped"] == 1
    assert summary["sent"] == 0

    rows = client.table("drip_email_sends").select("step_key, status").eq(
        "company_id", "co-1"
    ).execute().data
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"

    # Second pass does not re-attempt the skipped step.
    summary2 = drip.run_drip_cycle()
    assert summary2["steps_considered"] == 0


def test_run_drip_cycle_isolates_companies(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client, company_id="co-1", slug="acme", name="Acme")
    _seed_company(client, company_id="co-2", slug="beta", name="Beta")
    _seed_member(client, "co-1", "u1", joined_days_ago=5)
    _seed_member(client, "co-2", "u2", joined_days_ago=5)

    summary = drip_mod.run_drip_cycle()
    assert summary["companies"] == 2
    assert summary["sent"] == 4  # day_1 + day_3 for each company

    co1 = client.table("drip_email_sends").select("id").eq(
        "company_id", "co-1").execute().data
    co2 = client.table("drip_email_sends").select("id").eq(
        "company_id", "co-2").execute().data
    assert len(co1) == 2
    assert len(co2) == 2


# ── scheduler wiring ───────────────────────────────────────────────────


class _FakeScheduler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, *, trigger=None, id=None, name=None,
                replace_existing=False):
        self.jobs.append({"func": func, "id": id, "name": name})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        pass


def _run_start_scheduler(monkeypatch, *, drip_enabled):
    from app import scheduler as sched_mod
    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", True)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "drip_emails_enabled", drip_enabled)
    monkeypatch.setattr(sched_mod.settings, "drip_interval_hours", 6)
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_start_scheduler_registers_drip_job_when_enabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, drip_enabled=True)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "drip_emails" in ids
    assert "weekly_brief_tick" in ids
    assert fake.started is True


def test_start_scheduler_omits_drip_job_when_disabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, drip_enabled=False)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "drip_emails" not in ids
    assert "weekly_brief_tick" in ids

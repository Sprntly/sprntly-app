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


# ── grace (send-window upper bound) resolution ────────────────────────


def test_resolve_grace_days_default(isolated_settings):
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.resolve_grace_days(None) == drip.DEFAULT_GRACE_DAYS
    assert drip.resolve_grace_days({}) == drip.DEFAULT_GRACE_DAYS
    assert drip.resolve_grace_days({"drip": {}}) == drip.DEFAULT_GRACE_DAYS
    # The default must stay under the smallest gap in the default ladder
    # (day_1 → day_3 = 2 days) so normal members never sit in 3 windows at once.
    offsets = [s.day_offset for s in drip.DEFAULT_CADENCE]
    smallest_gap = min(b - a for a, b in zip(offsets, offsets[1:]))
    assert drip.DEFAULT_GRACE_DAYS <= smallest_gap


def test_resolve_grace_days_global_override(isolated_settings, monkeypatch):
    monkeypatch.setenv("DRIP_GRACE_DAYS", "5")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.resolve_grace_days(None) == 5


def test_resolve_grace_days_per_company_wins(isolated_settings, monkeypatch):
    monkeypatch.setenv("DRIP_GRACE_DAYS", "5")  # should be overridden
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.resolve_grace_days({"drip": {"grace_days": 1}}) == 1
    # 0 is a real value ("same-day only"), not "unset" — it must not fall back.
    assert drip.resolve_grace_days({"drip": {"grace_days": 0}}) == 0


def test_resolve_grace_days_junk_falls_through(isolated_settings, monkeypatch):
    # A typo in one tenant's JSONB must not raise inside the scheduler's email
    # path, and must not silently change the window for everyone else.
    monkeypatch.delenv("DRIP_GRACE_DAYS", raising=False)
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    for junk in ("soon", None, [], {}, "", True, False):
        assert drip.resolve_grace_days(
            {"drip": {"grace_days": junk}}
        ) == drip.DEFAULT_GRACE_DAYS
    # Negative windows are meaningless → clamped to 0, not treated as unset.
    assert drip.resolve_grace_days({"drip": {"grace_days": -3}}) == 0
    # Numeric strings are accepted (JSONB written by hand / by a form).
    assert drip.resolve_grace_days({"drip": {"grace_days": "4"}}) == 4
    # Malformed notification_settings shapes degrade to the default.
    assert drip.resolve_grace_days({"drip": "nope"}) == drip.DEFAULT_GRACE_DAYS


def test_resolve_grace_days_junk_global_falls_through(isolated_settings, monkeypatch):
    monkeypatch.setenv("DRIP_GRACE_DAYS", "not-a-number")
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip.resolve_grace_days(None) == drip.DEFAULT_GRACE_DAYS


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
    # Must send from the Resend-verified mail.sprntly.ai domain — the API
    # key is domain-scoped and 403s any bare-sprntly.ai sender.
    assert captured["json"]["from"] == "Sprntly <onboarding@mail.sprntly.ai>"
    assert captured["json"]["to"] == ["a@b.com"]
    assert captured["json"]["subject"] == "Hello"
    # Both parts ship: branded HTML + the plain-text fallback.
    assert captured["json"]["text"] == "Body"
    assert "Open Sprntly" in captured["json"]["html"]
    assert "Bearer re_test" in captured["headers"]["Authorization"]


def test_from_address_default_and_override(isolated_settings, monkeypatch):
    monkeypatch.delenv("DRIP_FROM_EMAIL", raising=False)
    import app.config as config_mod
    importlib.reload(config_mod)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    assert drip._from_address() == "Sprntly <onboarding@mail.sprntly.ai>"
    assert drip._from_address().split("@")[-1].rstrip(">") == "mail.sprntly.ai"

    monkeypatch.setenv("DRIP_FROM_EMAIL", "Sprntly <hello@mail.sprntly.ai>")
    importlib.reload(config_mod)
    importlib.reload(drip)
    assert drip._from_address() == "Sprntly <hello@mail.sprntly.ai>"


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
    # Every call is recorded on `drip.sent_calls` so a test can assert that an
    # aged-out step produced NO send attempt at all (not merely a failed one).
    sent_calls: list[dict] = []

    def _fake_send(**kw):
        sent_calls.append(kw)
        return True

    monkeypatch.setattr(drip, "send_drip_email", _fake_send)
    drip.sent_calls = sent_calls
    return drip


def test_run_drip_cycle_sends_eligible_steps(drip_mod, isolated_settings):
    client = require_client()
    # Member joined 4 days ago. With the default grace of 2 the windows are
    # day_1=[1,3], day_3=[3,5], day_7=[7,9]: day_1 has aged out, day_3 is in
    # window, day_7 isn't due. (Before the upper bound existed this sent both
    # day_1 and day_3.)
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=4)

    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 1
    assert summary["aged_out"] == 1

    rows = client.table("drip_email_sends").select("step_key, status").eq(
        "company_id", "co-1"
    ).execute().data
    by_key = {r["step_key"]: r["status"] for r in rows}
    assert by_key == {"day_1": "skipped", "day_3": "sent"}
    # Exactly one email actually went out, and it was the in-window step.
    assert len(drip_mod.sent_calls) == 1


def test_run_drip_cycle_does_not_double_send(drip_mod, isolated_settings):
    client = require_client()
    # Wide per-company grace so all three steps stay in window for a 10-day-old
    # member — this test is about de-dup, not about the window.
    _seed_company(client, notification_settings={"drip": {"grace_days": 30}})
    _seed_member(client, "co-1", "u1", joined_days_ago=10)

    first = drip_mod.run_drip_cycle()
    assert first["sent"] == 3  # day_1 + day_3 + day_7
    assert first["aged_out"] == 0

    second = drip_mod.run_drip_cycle()
    assert second["sent"] == 0
    assert second["steps_considered"] == 0
    assert second["aged_out"] == 0

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
    # The kill switch short-circuits the company entirely: resolve_cadence
    # returns [] and the member loop is never entered. In particular a disabled
    # company writes NO rows at all — not even aged-out "skipped" ones — so
    # re-enabling drips later leaves the ladder intact rather than pre-disarmed.
    client = require_client()
    _seed_company(client, notification_settings={"drip": {"enabled": False}})
    _seed_member(client, "co-1", "u1", joined_days_ago=30)
    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 0
    assert summary["aged_out"] == 0
    rows = client.table("drip_email_sends").select("id").execute().data
    assert rows == []
    assert drip_mod.sent_calls == []


def test_run_drip_cycle_disable_wins_over_grace_override(drip_mod, isolated_settings):
    # enabled:false and grace_days set together — the kill switch still wins.
    client = require_client()
    _seed_company(
        client,
        notification_settings={"drip": {"enabled": False, "grace_days": 90}},
    )
    _seed_member(client, "co-1", "u1", joined_days_ago=2)
    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 0
    assert client.table("drip_email_sends").select("id").execute().data == []


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
    # Age 5: day_1=[1,3] aged out, day_3=[3,5] in window (upper edge), day_7
    # not due → one send per company.
    assert summary["sent"] == 2
    assert summary["aged_out"] == 2

    co1 = client.table("drip_email_sends").select("id").eq(
        "company_id", "co-1").execute().data
    co2 = client.table("drip_email_sends").select("id").eq(
        "company_id", "co-2").execute().data
    assert len(co1) == 2
    assert len(co2) == 2


# ── send-window upper bound (the aged-out burst fix) ──────────────────


def _rows_by_key(client, company_id="co-1"):
    rows = client.table("drip_email_sends").select("step_key, status").eq(
        "company_id", company_id
    ).execute().data
    return {r["step_key"]: r["status"] for r in rows}


@pytest.mark.parametrize("age", [1, 2, 3])
def test_step_sends_anywhere_inside_its_window(drip_mod, isolated_settings, age):
    # day_1's window with the default grace of 2 is [1, 3] inclusive — the
    # lower edge, the middle, and the upper edge all send.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=age)

    summary = drip_mod.run_drip_cycle()
    assert _rows_by_key(client)["day_1"] == "sent"
    assert summary["aged_out"] == 0
    assert any(c["to_email"] == "u1@example.com" for c in drip_mod.sent_calls)


def test_step_one_day_past_window_is_recorded_skipped_and_not_sent(
    drip_mod, isolated_settings
):
    # Age 4 is exactly one day past day_1's upper edge (1 + 2). It must be
    # disarmed, and no email may be attempted for it.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=4)

    summary = drip_mod.run_drip_cycle()
    assert _rows_by_key(client)["day_1"] == "skipped"
    assert summary["aged_out"] == 1
    # No send was even attempted for the aged-out step: the only email that
    # went out is the in-window day_3 one.
    assert len(drip_mod.sent_calls) == 1
    assert "first week" not in drip_mod.sent_calls[0]["subject"]


def test_aged_out_step_never_fires_on_a_later_cycle(drip_mod, isolated_settings):
    # The whole point of recording "skipped" rather than ignoring: the step is
    # permanently disarmed, because sent_steps_for_company() treats 'skipped'
    # like 'sent'. Even widening the window afterwards must not resurrect it.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=14)

    first = drip_mod.run_drip_cycle()
    assert first["aged_out"] == 3
    drip_mod.sent_calls.clear()

    # Re-run with a hugely widened per-company grace — the recorded rows win.
    client.table("companies").update(
        {"notification_settings": {"drip": {"grace_days": 365}}}
    ).eq("id", "co-1").execute()

    second = drip_mod.run_drip_cycle()
    assert second["sent"] == 0
    assert second["steps_considered"] == 0
    assert drip_mod.sent_calls == []


def test_member_still_gets_a_later_step_they_are_in_window_for(
    drip_mod, isolated_settings
):
    # Age 7: day_1=[1,3] and day_3=[3,5] have aged out, but day_7=[7,9] is due
    # right now. Ageing out the early steps must not suppress the current one.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=7)

    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 1
    assert summary["aged_out"] == 2
    assert _rows_by_key(client) == {
        "day_1": "skipped",
        "day_3": "skipped",
        "day_7": "sent",
    }
    assert len(drip_mod.sent_calls) == 1
    # And it's the day_7 copy, not a re-run of the "connect your first data
    # source" onboarding nudge.
    assert "first week" in drip_mod.sent_calls[0]["subject"]


def test_backfill_suppression_for_existing_aged_out_member(
    drip_mod, isolated_settings
):
    # THE CASE THIS FIX EXISTS FOR. A member who joined 14 days ago with
    # nothing but 'welcome' recorded: before the upper bound, the first cycle
    # blasted day_1 + day_3 + day_7 at once — including "connect your first
    # data source" to someone who connected two weeks ago.
    #
    # Now the first cycle after this ships must send ZERO emails and write
    # three 'skipped' rows. That backfill-suppression is the desired behaviour
    # and it is what protects existing members on the first deploy.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=14)
    client.table("drip_email_sends").insert(
        {
            "id": "de-welcome",
            "company_id": "co-1",
            "user_id": "u1",
            "step_key": "welcome",
            "email": "u1@example.com",
            "status": "sent",
        }
    ).execute()

    summary = drip_mod.run_drip_cycle()

    assert summary["sent"] == 0
    assert summary["steps_considered"] == 0
    assert summary["aged_out"] == 3
    assert drip_mod.sent_calls == []

    by_key = _rows_by_key(client)
    # The pre-existing welcome row is untouched; the three ladder steps are
    # each disarmed as 'skipped'.
    assert by_key == {
        "welcome": "sent",
        "day_1": "skipped",
        "day_3": "skipped",
        "day_7": "skipped",
    }

    # And the cycle right after the backfill is a clean no-op.
    second = drip_mod.run_drip_cycle()
    assert second == {
        "companies": 1,
        "sent": 0,
        "skipped": 0,
        "aged_out": 0,
        "steps_considered": 0,
    }


def test_brand_new_member_keeps_future_steps_armed(drip_mod, isolated_settings):
    # Not-yet-due steps must NOT be recorded as skipped — only aged-out ones.
    # A day-0 member has to still receive the full ladder later.
    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=0)

    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 0
    assert summary["aged_out"] == 0
    assert client.table("drip_email_sends").select("id").execute().data == []


def test_grace_override_widens_the_window(drip_mod, isolated_settings):
    # A company that wants the old catch-all behaviour can opt back into it.
    client = require_client()
    _seed_company(client, notification_settings={"drip": {"grace_days": 20}})
    _seed_member(client, "co-1", "u1", joined_days_ago=8)

    summary = drip_mod.run_drip_cycle()
    assert summary["sent"] == 3
    assert summary["aged_out"] == 0


def test_grace_zero_makes_the_window_a_single_day(drip_mod, isolated_settings):
    client = require_client()
    _seed_company(client, notification_settings={"drip": {"grace_days": 0}})
    _seed_member(client, "co-1", "u1", joined_days_ago=2)

    summary = drip_mod.run_drip_cycle()
    # age 2 is past day_1's zero-width window and short of day_3.
    assert summary["sent"] == 0
    assert summary["aged_out"] == 1
    assert _rows_by_key(client) == {"day_1": "skipped"}


def test_global_grace_env_applies_to_the_cycle(isolated_settings, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("DRIP_GRACE_DAYS", "10")
    import app.config as config_mod
    importlib.reload(config_mod)
    import app.db.drip as drip_db
    importlib.reload(drip_db)
    drip = importlib.import_module("app.drip_email")
    importlib.reload(drip)
    monkeypatch.setattr(drip, "send_drip_email", lambda **kw: True)

    client = require_client()
    _seed_company(client)
    _seed_member(client, "co-1", "u1", joined_days_ago=9)

    summary = drip.run_drip_cycle()
    # With a 10-day grace every step is still in window at age 9.
    assert summary["sent"] == 3
    assert summary["aged_out"] == 0


def test_aged_out_uses_skipped_status_that_dedup_honours(isolated_settings):
    # Guards the mechanism this fix leans on: sent_steps_for_company() must go
    # on treating 'skipped' as delivered. If that ever narrows to 'sent' only,
    # every aged-out step re-arms and the burst comes back.
    import app.db.drip as drip_db
    importlib.reload(drip_db)
    client = require_client()
    _seed_company(client)
    drip_db.record_drip_sent(
        company_id="co-1", user_id="u1", step_key="day_1",
        email="u1@example.com", status="skipped",
    )
    assert ("u1", "day_1") in drip_db.sent_steps_for_company("co-1")


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
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda **kw: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_start_scheduler_registers_drip_job_when_enabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, drip_enabled=True)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "drip_emails" in ids
    assert "brief_tick" in ids
    assert fake.started is True


def test_start_scheduler_omits_drip_job_when_disabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, drip_enabled=False)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "drip_emails" not in ids
    assert "brief_tick" in ids

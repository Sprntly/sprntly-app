"""Scheduled connector health monitor (app.connector_health).

Covers the contract that makes the on-open badge proactive without spamming:
  - throttle skips rows checked within connector_health_min_recheck_minutes
  - persists health for every probed row
  - sends the transition alert EXACTLY once on healthy(or unchecked)→disconnected
  - does NOT alert on an already-disconnected row (no hourly repeat)
  - does NOT alert on recovery (disconnected→connected) — logs only
  - fails OPEN on a probe transport error (never marks a good connector dead)

DB + probe + Resend are all monkeypatched — no network, no Supabase.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import connector_health
from app.config import settings
from app.connector_probe import ProbeError


@pytest.fixture
def captured_health(monkeypatch):
    """Capture set_connection_health calls and stub list_all_active_connections.
    Returns a dict the test fills with `rows` and reads `writes` from."""
    state: dict = {"rows": [], "writes": []}

    monkeypatch.setattr(
        connector_health.db, "list_all_active_connections",
        lambda: state["rows"],
    )

    def _set(connection_id, *, health, error, checked_at):
        state["writes"].append(
            {"id": connection_id, "health": health, "error": error}
        )

    monkeypatch.setattr(connector_health.db, "set_connection_health", _set)
    return state


def _row(cid: str, provider: str, *, health=None, last_check=None) -> dict:
    return {
        "id": cid,
        "provider": provider,
        "company_id": "co-1",
        "account_label": f"{provider}@x.test",
        "health": health,
        "last_health_check_at": last_check,
        "token_json_encrypted": "blob",
    }


async def test_persists_health_and_alerts_on_transition(captured_health, monkeypatch):
    captured_health["rows"] = [
        _row("c1", "figma", health="connected"),   # → disconnected (transition)
        _row("c2", "github", health="connected"),  # stays healthy
    ]

    def fake_probe(provider, row):
        if provider == "figma":
            return False, "figma rejected the stored credential"
        return True, "octocat"

    monkeypatch.setattr(connector_health, "probe_connection", fake_probe)

    alerts: list[list[dict]] = []
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: alerts.append(rows))

    summary = await connector_health.run_connector_health_check()

    assert summary == {"checked": 2, "healthy": 1, "disconnected": 1, "skipped": 0}
    # Both rows persisted, with correct health values.
    writes = {w["id"]: w for w in captured_health["writes"]}
    assert writes["c1"]["health"] == "disconnected"
    assert writes["c1"]["error"] == "figma rejected the stored credential"
    assert writes["c2"]["health"] == "connected"
    assert writes["c2"]["error"] is None
    # Exactly one alert batch, listing only the transitioned connector.
    assert len(alerts) == 1
    assert [r["id"] for r in alerts[0]] == ["c1"]


async def test_no_alert_on_already_disconnected(captured_health, monkeypatch):
    captured_health["rows"] = [_row("c1", "figma", health="disconnected")]
    monkeypatch.setattr(
        connector_health, "probe_connection",
        lambda p, r: (False, "still down"),
    )
    alerts: list = []
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: alerts.append(rows))

    summary = await connector_health.run_connector_health_check()

    assert summary["disconnected"] == 1
    assert alerts == []  # no repeat alert each hour


async def test_no_alert_on_recovery(captured_health, monkeypatch):
    captured_health["rows"] = [_row("c1", "figma", health="disconnected")]
    monkeypatch.setattr(
        connector_health, "probe_connection",
        lambda p, r: (True, "alice@figma.test"),
    )
    alerts: list = []
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: alerts.append(rows))

    summary = await connector_health.run_connector_health_check()

    assert summary["healthy"] == 1
    assert captured_health["writes"][0]["health"] == "connected"
    assert alerts == []  # recovery is logged only, never alerts


async def test_throttle_skips_recently_checked(captured_health, monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    captured_health["rows"] = [
        _row("recent", "figma", health="connected", last_check=recent),
        _row("stale", "github", health="connected", last_check=stale),
    ]
    monkeypatch.setattr(settings, "connector_health_min_recheck_minutes", 50)
    probed: list[str] = []

    def fake_probe(provider, row):
        probed.append(row["id"])
        return True, "ok"

    monkeypatch.setattr(connector_health, "probe_connection", fake_probe)
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: None)

    summary = await connector_health.run_connector_health_check()

    assert probed == ["stale"]            # recent row never probed
    assert summary["skipped"] == 1
    assert summary["checked"] == 1


async def test_fail_open_on_probe_exception(captured_health, monkeypatch):
    captured_health["rows"] = [_row("c1", "figma", health="connected")]

    def boom(provider, row):
        raise RuntimeError("network blip")

    monkeypatch.setattr(connector_health, "probe_connection", boom)
    alerts: list = []
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: alerts.append(rows))

    summary = await connector_health.run_connector_health_check()

    # Fail-open: not marked dead, not persisted, not alerted — just skipped.
    assert summary == {"checked": 0, "healthy": 0, "disconnected": 0, "skipped": 1}
    assert captured_health["writes"] == []
    assert alerts == []


async def test_fail_open_on_unreadable_token(captured_health, monkeypatch):
    captured_health["rows"] = [_row("c1", "figma", health="connected")]

    def raise_unreadable(provider, row):
        raise ProbeError("unreadable", reason="unreadable")

    monkeypatch.setattr(connector_health, "probe_connection", raise_unreadable)
    alerts: list = []
    monkeypatch.setattr(connector_health, "_send_alert", lambda rows: alerts.append(rows))

    summary = await connector_health.run_connector_health_check()

    # An internal decode issue must not mark the connector dead.
    assert summary["skipped"] == 1
    assert captured_health["writes"] == []
    assert alerts == []


def test_send_alert_noop_without_config(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "connector_health_alert_email", "")
    monkeypatch.setattr(settings, "signin_monitor_alert_email", "")
    # Should log-only and not attempt a send (no exception, no import error).
    connector_health._send_alert([_row("c1", "figma")])


def test_send_alert_sends_one_email_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_key")
    monkeypatch.setattr(settings, "connector_health_alert_email", "ops@sprntly.ai")
    sent: list[dict] = []

    import app.synthesis.email_delivery as email_delivery

    def fake_send(api_key, *, to, subject, html_body, text_body):
        sent.append({"to": to, "subject": subject, "text": text_body})

    monkeypatch.setattr(email_delivery, "_send_via_resend", fake_send)

    import app.db.companies as companies_db

    monkeypatch.setattr(companies_db, "slug_for_company_id", lambda cid: "acme")

    rows = [{**_row("c1", "figma"), "_health_error": "token rejected"}]
    connector_health._send_alert(rows)

    assert len(sent) == 1
    assert sent[0]["to"] == "ops@sprntly.ai"
    assert "1 connector" in sent[0]["subject"]
    assert "figma" in sent[0]["text"]

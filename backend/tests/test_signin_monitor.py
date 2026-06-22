"""Synthetic sign-in monitor — guards the 2026-06-22 failure class (a rotated/
deleted Google OAuth secret silently breaking 'Sign in with Google'). The probe
authenticates the client against Google's token endpoint with a dummy code:
invalid_grant => healthy (creds valid), invalid_client => broken (secret wrong).
Fail-open on transport errors so a network blip never pages."""
import httpx
import pytest

from app import signin_monitor
from app.config import settings


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api/cb")


def test_probe_healthy_on_invalid_grant(google_configured, monkeypatch):
    monkeypatch.setattr(signin_monitor.httpx, "post",
                        lambda *a, **k: _Resp(400, {"error": "invalid_grant"}))
    healthy, detail = signin_monitor.probe_google_oauth_secret()
    assert healthy is True and detail == "invalid_grant"


def test_probe_unhealthy_on_invalid_client(google_configured, monkeypatch):
    monkeypatch.setattr(signin_monitor.httpx, "post",
                        lambda *a, **k: _Resp(401, {"error": "invalid_client"}))
    healthy, detail = signin_monitor.probe_google_oauth_secret()
    assert healthy is False and detail == "invalid_client"


def test_probe_fail_open_on_transport_error(google_configured, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(signin_monitor.httpx, "post", boom)
    healthy, detail = signin_monitor.probe_google_oauth_secret()
    assert healthy is True and detail.startswith("probe_error")


def test_probe_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    healthy, detail = signin_monitor.probe_google_oauth_secret()
    assert healthy is True and detail == "google_oauth_not_configured"


async def test_run_alerts_on_unhealthy(monkeypatch):
    monkeypatch.setattr(signin_monitor, "probe_google_oauth_secret",
                        lambda: (False, "invalid_client"))
    captured = {}
    monkeypatch.setattr(signin_monitor, "_send_alert",
                        lambda detail: captured.setdefault("detail", detail))
    healthy, detail = await signin_monitor.run_google_signin_health_check()
    assert healthy is False
    assert captured.get("detail") == "invalid_client"


async def test_run_no_alert_on_healthy(monkeypatch):
    monkeypatch.setattr(signin_monitor, "probe_google_oauth_secret",
                        lambda: (True, "invalid_grant"))
    captured = {}
    monkeypatch.setattr(signin_monitor, "_send_alert",
                        lambda detail: captured.setdefault("detail", detail))
    healthy, _ = await signin_monitor.run_google_signin_health_check()
    assert healthy is True
    assert "detail" not in captured

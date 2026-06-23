"""Tests for the shared per-provider connector probe (app.connector_probe).

This is the single implementation behind BOTH the on-open "Test connection"
route and the scheduled health monitor. These tests pin its healthy/unhealthy
mapping for a couple of providers — the route's behavioral parity is covered
end-to-end in tests/test_routes_connectors_test_endpoint.py, which still drives
the route (now routed through this function)."""
from __future__ import annotations

import json

import pytest

from app import connector_probe
from app.connector_probe import ProbeError, probe_connection


@pytest.fixture(autouse=True)
def _decrypt_passthrough(monkeypatch):
    """Bypass Fernet — the probe's job is provider dispatch, not crypto. The
    stored blob is the plaintext token JSON in these tests."""
    monkeypatch.setattr(
        connector_probe, "decrypt_token_json", lambda blob: blob
    )


def _row(provider: str, token: dict) -> dict:
    return {
        "id": "conn-1",
        "provider": provider,
        "token_json_encrypted": json.dumps(token),
        "account_label": "label@x.test",
        "google_email": None,
    }


def test_figma_healthy_resolves_label(monkeypatch):
    monkeypatch.setattr(
        connector_probe.figma_oauth, "fetch_me",
        lambda tok: {"email": "alice@figma.test", "handle": "alice"},
    )
    healthy, detail = probe_connection("figma", _row("figma", {"access_token": "t"}))
    assert healthy is True
    assert detail == "alice@figma.test"


def test_figma_unhealthy_on_empty_identity(monkeypatch):
    # Empty identity payload = provider rejected the credential -> unhealthy.
    monkeypatch.setattr(connector_probe.figma_oauth, "fetch_me", lambda tok: {})
    healthy, detail = probe_connection("figma", _row("figma", {"access_token": "t"}))
    assert healthy is False
    assert "rejected" in detail


def test_github_healthy_resolves_login(monkeypatch):
    monkeypatch.setattr(
        connector_probe.github_app, "fetch_authenticated_user",
        lambda tok: {"login": "octocat"},
    )
    healthy, detail = probe_connection("github", _row("github", {"access_token": "t"}))
    assert healthy is True
    assert detail == "octocat"


def test_drive_refreshes_only_when_expired(monkeypatch):
    """Drive proves token validity by refreshing ONLY if expired — never a
    Drive API call. A non-expired token is healthy with no refresh."""
    refreshed = {"called": False}

    class _Creds:
        expired = False
        refresh_token = "r"

        def refresh(self, _req):
            refreshed["called"] = True

    monkeypatch.setattr(
        connector_probe.google_oauth, "credentials_from_token_json",
        lambda blob: _Creds(),
    )
    row = _row("google_drive", {"refresh_token": "r"})
    row["google_email"] = "alice@gmail.test"
    healthy, detail = probe_connection("google_drive", row)
    assert healthy is True
    assert detail == "alice@gmail.test"
    assert refreshed["called"] is False  # not expired -> no refresh


def test_drive_rejected_raises_probe_error(monkeypatch):
    def boom(blob):
        raise ValueError("bad token")

    monkeypatch.setattr(
        connector_probe.google_oauth, "credentials_from_token_json", boom
    )
    with pytest.raises(ProbeError) as ei:
        probe_connection("google_drive", _row("google_drive", {"refresh_token": "r"}))
    assert ei.value.reason == "rejected"


def test_unreadable_token_raises_probe_error(monkeypatch):
    monkeypatch.setattr(
        connector_probe, "decrypt_token_json",
        lambda blob: "not json{{{",
    )
    with pytest.raises(ProbeError) as ei:
        probe_connection("figma", {"id": "x", "token_json_encrypted": "blob"})
    assert ei.value.reason == "unreadable"


def test_unsupported_provider_raises_probe_error():
    with pytest.raises(ProbeError) as ei:
        probe_connection("totally_made_up", _row("totally_made_up", {"access_token": "t"}))
    assert ei.value.reason == "unsupported"

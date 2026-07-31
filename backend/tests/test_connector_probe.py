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


# ─────────────────────────── Confluence ───────────────────────────
#
# Atlassian 3LO: ~1h access tokens with ROTATING refresh tokens, so the probe
# must refresh AND persist near expiry (a throwaway refresh strands the stored
# one). It carries one obligation Jira's branch doesn't: company_id must
# survive the rewrite, because that is the credential the kg_ingest puller is
# handed.


def _confluence_row(token: dict, *, cloud_id: str | None = "cloud-1") -> dict:
    row = _row("confluence", token)
    row["company_id"] = "co-42"
    row["config_json"] = json.dumps({"cloud_id": cloud_id}) if cloud_id else "{}"
    return row


@pytest.fixture
def _confluence_scopes_ok(monkeypatch):
    """Default the scope check to passing. The probe's real assertion is
    list_spaces (see test_confluence_unhealthy_when_v2_scopes_are_missing);
    tests about labelling stub it out so they isolate the label logic."""
    monkeypatch.setattr(
        connector_probe.confluence_oauth, "list_spaces",
        lambda tok, cloud, **kw: [{"id": "s1", "key": "ENG"}],
    )


def test_confluence_healthy_resolves_label(monkeypatch, _confluence_scopes_ok):
    import time

    monkeypatch.setattr(
        connector_probe.confluence_oauth, "fetch_current_user",
        lambda tok, cloud: {"email": "alice@acme.test", "displayName": "Alice"},
    )
    healthy, detail = probe_connection(
        "confluence",
        _confluence_row({
            "access_token": "t", "refresh_token": "r",
            "obtained_at": int(time.time()), "expires_in": 3600,
        }),
    )
    assert healthy is True
    assert detail == "alice@acme.test"


def test_confluence_falls_back_to_public_name(monkeypatch, _confluence_scopes_ok):
    """An org privacy setting can hide emails while content reads still work."""
    import time

    monkeypatch.setattr(
        connector_probe.confluence_oauth, "fetch_current_user",
        lambda tok, cloud: {"publicName": "alice"},
    )
    healthy, detail = probe_connection(
        "confluence",
        _confluence_row({
            "access_token": "t", "refresh_token": "r",
            "obtained_at": int(time.time()), "expires_in": 3600,
        }),
    )
    assert healthy is True
    assert detail == "alice"


def test_confluence_healthy_with_site_name_when_identity_is_refused(
    monkeypatch, _confluence_scopes_ok
):
    """Unlike the other providers, an empty identity payload here is NOT a
    rejected credential: an org can refuse read:confluence-user while content
    reads work perfectly. The scope check is what decides health, so this
    falls back to the site name rather than reporting a dead connection."""
    import time

    monkeypatch.setattr(
        connector_probe.confluence_oauth, "fetch_current_user",
        lambda tok, cloud: {},
    )
    monkeypatch.setattr(
        connector_probe.confluence_oauth, "site_name_for_cloud",
        lambda tok, cloud: "Acme Wiki",
    )
    healthy, detail = probe_connection(
        "confluence",
        _confluence_row({
            "access_token": "t", "refresh_token": "r",
            "obtained_at": int(time.time()), "expires_in": 3600,
        }),
    )
    assert healthy is True
    assert detail == "Acme Wiki"


def test_confluence_unhealthy_when_v2_scopes_are_missing(monkeypatch):
    """The defect this probe exists to catch. Confluence has two scope
    families: the v1 current-user route answers on a CLASSIC scope, while
    every read this connector performs is v2 and needs GRANULAR ones. An
    identity-only probe reports GREEN on a token whose every sync 401s with
    "scope does not match" — precisely the state of a connection made before
    the granular scopes were requested."""
    import time

    def _rejected(tok, cloud, **kw):
        raise connector_probe.confluence_oauth.ConfluenceAuthExpiredError(
            "Unauthorized; scope does not match"
        )

    monkeypatch.setattr(connector_probe.confluence_oauth, "list_spaces", _rejected)
    # Identity would succeed — that is exactly why it must not be the probe.
    monkeypatch.setattr(
        connector_probe.confluence_oauth, "fetch_current_user",
        lambda tok, cloud: {"email": "alice@acme.test"},
    )
    with pytest.raises(ProbeError) as ei:
        probe_connection(
            "confluence",
            _confluence_row({
                "access_token": "t", "refresh_token": "r",
                "obtained_at": int(time.time()), "expires_in": 3600,
            }),
        )
    assert ei.value.reason == "rejected"


def test_confluence_uses_cached_cloud_id_without_resolving(monkeypatch, _confluence_scopes_ok):
    """accessible-resources is an extra round trip per probe; the cached
    config_json.cloud_id exists to avoid it."""
    import time

    called = {"resolved": False}

    def _boom(_tok):
        called["resolved"] = True
        return "should-not-be-used"

    monkeypatch.setattr(connector_probe.confluence_oauth, "first_cloud_id", _boom)
    seen: dict = {}

    def _user(tok, cloud):
        seen["cloud_id"] = cloud
        return {"email": "a@b.test"}

    monkeypatch.setattr(connector_probe.confluence_oauth, "fetch_current_user", _user)
    probe_connection(
        "confluence",
        _confluence_row({
            "access_token": "t", "refresh_token": "r",
            "obtained_at": int(time.time()), "expires_in": 3600,
        }, cloud_id="cloud-cached"),
    )
    assert seen["cloud_id"] == "cloud-cached"
    assert called["resolved"] is False


def test_confluence_refresh_persists_and_keeps_company_id(monkeypatch, _confluence_scopes_ok):
    """The highest-risk regression in the connector: lose company_id on a
    refresh and `runner.token_for` raises on the NEXT sync, far from the
    change that caused it."""
    persisted: dict = {}

    monkeypatch.setattr(
        connector_probe.confluence_oauth, "refresh_access_token",
        lambda rt: {"access_token": "fresh", "refresh_token": "rotated",
                    "expires_in": 3600},
    )
    monkeypatch.setattr(connector_probe, "encrypt_token_json", lambda blob: blob)
    monkeypatch.setattr(
        connector_probe.confluence_oauth, "fetch_current_user",
        lambda tok, cloud: {"email": "alice@acme.test"},
    )

    from app import db

    monkeypatch.setattr(
        db, "update_connection_tokens",
        lambda cid, provider, blob: persisted.update(
            {"company_id": cid, "provider": provider, "blob": blob}
        ),
    )

    healthy, detail = probe_connection(
        "confluence",
        # obtained_at 0 → provably expired → the refresh path runs.
        _confluence_row({
            "access_token": "stale", "refresh_token": "old-refresh",
            "obtained_at": 0, "expires_in": 3600, "company_id": "co-42",
        }),
    )
    assert healthy is True
    assert detail == "alice@acme.test"

    assert persisted["provider"] == "confluence"
    stored = json.loads(persisted["blob"])
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "rotated"   # rotation persisted
    assert stored["company_id"] == "co-42"        # the puller's credential


def test_confluence_refresh_rejection_raises_probe_error(monkeypatch):
    def _dead(_rt):
        raise connector_probe.confluence_oauth.ConfluenceAuthExpiredError("revoked")

    monkeypatch.setattr(
        connector_probe.confluence_oauth, "refresh_access_token", _dead
    )
    with pytest.raises(ProbeError) as ei:
        probe_connection(
            "confluence",
            _confluence_row({
                "access_token": "stale", "refresh_token": "dead",
                "obtained_at": 0, "expires_in": 3600,
            }),
        )
    assert ei.value.reason == "rejected"

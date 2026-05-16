"""Tests for app.routes.health — root and /healthz."""


def test_healthz_returns_200(unauth_client):
    resp = unauth_client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_returns_status_ok(unauth_client):
    resp = unauth_client.get("/healthz")
    body = resp.json()
    assert body["status"] == "ok"
    assert "env" in body


def test_root_returns_service_metadata(unauth_client):
    resp = unauth_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "sprintly-api"
    assert body["status"] == "ok"


def test_healthz_does_not_require_auth(unauth_client):
    """Critical: /healthz is hit by load balancers and uptime checks. It must
    work without a session cookie."""
    # unauth_client has no cookie set — confirm.
    assert not unauth_client.cookies
    resp = unauth_client.get("/healthz")
    assert resp.status_code == 200

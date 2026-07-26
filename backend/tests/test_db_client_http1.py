"""_force_http1 — the sync Supabase client must never speak HTTP/2.

The h2 state machine is not thread-safe, and supabase-py shares one session
per sub-client across every request thread + background job. Concurrent use
corrupts the HTTP/2 connection state; the worst shape (staging 2026-07-24,
`KeyError` in `h2._open_streams`) corrupts without raising, after which every
Supabase-bound request hangs forever while /healthz stays green.

These tests run against the real supabase/postgrest/storage3/gotrue libs
(constructing a client makes no network calls) so a dependency bump that
changes the attribute layout `_force_http1` relies on fails loudly here.
"""
from __future__ import annotations

import pytest

from app.db import client as db_client


def _http2_flag(httpx_client) -> bool:
    return httpx_client._transport._pool._http2


@pytest.fixture
def real_client():
    from supabase import create_client

    client = create_client(
        "https://example-project.supabase.co",
        "dummy-service-role-key",
    )
    yield client


def test_postgrest_session_is_http1(real_client):
    old_base_url = str(real_client.postgrest.session.base_url)
    old_headers = dict(real_client.postgrest.session.headers)

    db_client._force_http1(real_client)

    session = real_client.postgrest.session
    assert _http2_flag(session) is False
    # Routing and auth must survive the swap.
    assert str(session.base_url) == old_base_url
    assert "rest/v1" in old_base_url
    for key in ("apikey", "authorization", "accept-profile"):
        assert session.headers.get(key) == old_headers.get(key)


def test_storage_session_is_http1_and_references_agree(real_client):
    old_base_url = str(real_client.storage.session.base_url)

    db_client._force_http1(real_client)

    st = real_client.storage
    assert _http2_flag(st.session) is False
    # The bucket API keeps its own reference; both must point at the swap.
    assert st._client is st.session
    assert str(st.session.base_url) == old_base_url
    assert "storage/v1" in old_base_url


def test_auth_and_admin_sessions_are_http1(real_client):
    db_client._force_http1(real_client)

    au = real_client.auth
    assert _http2_flag(au._http_client) is False
    # admin shares the parent's client instance.
    assert au.admin._http_client is au._http_client


def test_supabase_client_factory_applies_swap(monkeypatch):
    """The factory (and thus the retry_on_disconnect reconnect path, which
    goes back through the factory) must hand out HTTP/1.1 clients."""
    monkeypatch.setattr(
        db_client.settings, "supabase_url", "https://example-project.supabase.co"
    )
    monkeypatch.setattr(
        db_client.settings, "supabase_service_role_key", "dummy-service-role-key"
    )
    db_client.reset_client()
    try:
        client = db_client.supabase_client()
        assert client is not None
        assert _http2_flag(client.postgrest.session) is False
        assert _http2_flag(client.storage.session) is False
        assert _http2_flag(client.auth._http_client) is False
    finally:
        db_client.reset_client()

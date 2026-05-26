"""Tests for the P1.5 research profile + monitor scaffold.

Covers:
  - Pydantic model invariants (required fields, literal enums)
  - Service-layer CRUD + tenant isolation
  - Signal recording dedup + future-timestamp rejection
  - App Store + changelog parsers (fixtures, requests mocked)
  - Job + social stubs return empty
  - Routes: 401 unauth, 200 happy path, 404 cross-tenant
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError


# ── helpers ───────────────────────────────────────────────────────────


def _make_profile(workspace_id: str = "ws_app", **overrides):
    """Insert a profile via the service, return the model."""
    from app.research import profile_service
    from app.research.profile import CompetitorProfileCreate

    data = CompetitorProfileCreate(
        name=overrides.pop("name", "Linear"),
        product_url=overrides.pop("product_url", "https://linear.app"),
        app_store_ios_url=overrides.pop(
            "app_store_ios_url",
            "https://apps.apple.com/us/app/linear/id1500840122",
        ),
        changelog_url=overrides.pop("changelog_url", "https://linear.app/changelog"),
        **overrides,
    )
    return profile_service.create_profile(workspace_id, data)


def _login_as(client, audience: str = "app"):
    """Mint a fresh session for the given audience."""
    # Drop any prior cookies so we don't carry a stale audience.
    client.cookies.clear()
    resp = client.post(
        "/v1/auth/login",
        json={"password": "test-pw", "audience": audience},
    )
    assert resp.status_code == 200, resp.text


# ── pydantic models ───────────────────────────────────────────────────


def test_profile_model_requires_name(isolated_settings):
    from app.research.profile import CompetitorProfileCreate

    with pytest.raises(ValidationError):
        CompetitorProfileCreate()  # type: ignore[call-arg]


def test_profile_model_rejects_empty_name(isolated_settings):
    from app.research.profile import CompetitorProfileCreate

    with pytest.raises(ValidationError):
        CompetitorProfileCreate(name="")


def test_signal_model_enforces_source_literal(isolated_settings):
    from app.research.profile import CompetitorSignalCreate

    with pytest.raises(ValidationError):
        CompetitorSignalCreate(
            source="not_a_source",  # type: ignore[arg-type]
            signal_type="review",
            title="x",
            published_at=datetime.now(timezone.utc),
        )


def test_signal_model_enforces_signal_type_literal(isolated_settings):
    from app.research.profile import CompetitorSignalCreate

    with pytest.raises(ValidationError):
        CompetitorSignalCreate(
            source="app_store_ios",
            signal_type="bogus",  # type: ignore[arg-type]
            title="x",
            published_at=datetime.now(timezone.utc),
        )


def test_signal_model_sentiment_optional(isolated_settings):
    from app.research.profile import CompetitorSignalCreate

    sig = CompetitorSignalCreate(
        source="changelog",
        signal_type="release",
        title="v1.2",
        published_at=datetime.now(timezone.utc),
    )
    assert sig.sentiment is None


# ── service CRUD ──────────────────────────────────────────────────────


def test_create_and_get_profile(isolated_settings):
    p = _make_profile()
    assert p.name == "Linear"
    assert p.monitoring_enabled is True
    assert p.workspace_id == "ws_app"
    assert p.id


def test_list_profiles_scoped_to_workspace(isolated_settings):
    from app.research import profile_service

    _make_profile(workspace_id="ws_app", name="A")
    _make_profile(workspace_id="ws_app", name="B")
    _make_profile(workspace_id="ws_other", name="C")

    rows_app = profile_service.list_profiles("ws_app")
    rows_other = profile_service.list_profiles("ws_other")

    assert sorted(r.name for r in rows_app) == ["A", "B"]
    assert [r.name for r in rows_other] == ["C"]


def test_update_profile(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorProfileUpdate

    p = _make_profile()
    updated = profile_service.update_profile(
        "ws_app",
        p.id,
        CompetitorProfileUpdate(name="Linear Inc", monitoring_enabled=False),
    )
    assert updated.name == "Linear Inc"
    assert updated.monitoring_enabled is False
    # untouched fields stay
    assert updated.product_url == "https://linear.app"


def test_update_profile_cross_tenant_404(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorProfileUpdate
    from app.research.profile_service import ProfileNotFound

    p = _make_profile(workspace_id="ws_app")
    with pytest.raises(ProfileNotFound):
        profile_service.update_profile(
            "ws_other",
            p.id,
            CompetitorProfileUpdate(name="hijacked"),
        )


def test_delete_profile(isolated_settings):
    from app.research import profile_service

    p = _make_profile()
    profile_service.delete_profile("ws_app", p.id)
    assert profile_service.list_profiles("ws_app") == []


def test_delete_profile_cross_tenant_404(isolated_settings):
    from app.research import profile_service
    from app.research.profile_service import ProfileNotFound

    p = _make_profile(workspace_id="ws_app")
    with pytest.raises(ProfileNotFound):
        profile_service.delete_profile("ws_other", p.id)
    # Original is untouched
    assert len(profile_service.list_profiles("ws_app")) == 1


# ── signal recording ─────────────────────────────────────────────────


def test_record_signal_basic(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    p = _make_profile()
    sig = profile_service.record_signal(
        p.id,
        CompetitorSignalCreate(
            source="changelog",
            signal_type="release",
            title="v1.0",
            body="Initial release",
            url="https://linear.app/changelog/v1",
            published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    )
    assert sig.id
    assert sig.title == "v1.0"
    assert sig.competitor_profile_id == p.id


def test_record_signal_dedupes_by_url(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    p = _make_profile()
    payload = CompetitorSignalCreate(
        source="changelog",
        signal_type="release",
        title="v1.0",
        url="https://linear.app/changelog/v1",
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    sig1 = profile_service.record_signal(p.id, payload)
    sig2 = profile_service.record_signal(p.id, payload)
    assert sig1.id == sig2.id
    assert len(profile_service.list_signals(p.id)) == 1


def test_record_signal_dedupes_by_meta_when_no_url(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    p = _make_profile()
    when = datetime(2026, 5, 1, tzinfo=timezone.utc)
    payload = CompetitorSignalCreate(
        source="changelog",
        signal_type="release",
        title="v1.0",
        url=None,
        published_at=when,
    )
    sig1 = profile_service.record_signal(p.id, payload)
    sig2 = profile_service.record_signal(p.id, payload)
    assert sig1.id == sig2.id
    assert len(profile_service.list_signals(p.id)) == 1


def test_record_signal_rejects_future_timestamp(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate
    from app.research.profile_service import SignalRejected

    p = _make_profile()
    future = datetime.now(timezone.utc) + timedelta(days=7)
    with pytest.raises(SignalRejected):
        profile_service.record_signal(
            p.id,
            CompetitorSignalCreate(
                source="changelog",
                signal_type="release",
                title="from the future",
                published_at=future,
            ),
        )


def test_list_signals_filters_since(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    p = _make_profile()
    profile_service.record_signal(
        p.id,
        CompetitorSignalCreate(
            source="changelog",
            signal_type="release",
            title="old",
            url="https://linear.app/changelog/old",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    profile_service.record_signal(
        p.id,
        CompetitorSignalCreate(
            source="changelog",
            signal_type="release",
            title="new",
            url="https://linear.app/changelog/new",
            published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    )
    cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
    recent = profile_service.list_signals(p.id, since=cutoff)
    assert [s.title for s in recent] == ["new"]


def test_list_signals_filters_source(isolated_settings):
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    p = _make_profile()
    profile_service.record_signal(
        p.id,
        CompetitorSignalCreate(
            source="changelog",
            signal_type="release",
            title="cl",
            url="https://linear.app/changelog/x",
            published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    )
    profile_service.record_signal(
        p.id,
        CompetitorSignalCreate(
            source="app_store_ios",
            signal_type="review",
            title="rev",
            url="https://apps.apple.com/r/1",
            published_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        ),
    )
    only_reviews = profile_service.list_signals(p.id, source="app_store_ios")
    assert [s.title for s in only_reviews] == ["rev"]


# ── monitors: app store ───────────────────────────────────────────────


_APP_STORE_FIXTURE = {
    "feed": {
        "entry": [
            # Index 0: the app metadata, must be skipped
            {"im:name": {"label": "Linear"}},
            {
                "title": {"label": "Game changer"},
                "content": {"label": "Best tool for issue tracking"},
                "im:rating": {"label": "5"},
                "updated": {"label": "2026-05-20T12:00:00-07:00"},
                "link": [{"attributes": {"href": "https://apps.apple.com/r/1"}}],
            },
            {
                "title": {"label": "Broken"},
                "content": {"label": "Crashes on launch"},
                "im:rating": {"label": "1"},
                "updated": {"label": "2026-05-21T12:00:00-07:00"},
                "link": [{"attributes": {"href": "https://apps.apple.com/r/2"}}],
            },
        ]
    }
}


def test_app_store_monitor_parses_fixture(isolated_settings):
    from app.research.monitors.app_store_monitor import AppStoreIOSMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        app_store_ios_url="https://apps.apple.com/us/app/linear/id1500840122",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    class _FakeResp:
        status_code = 200

        def raise_for_status(self):  # pragma: no cover — trivially passes
            return None

        def json(self):
            return _APP_STORE_FIXTURE

    with patch(
        "app.research.monitors.app_store_monitor.requests.get",
        return_value=_FakeResp(),
    ):
        signals = AppStoreIOSMonitor().check_for_new_signals(profile)

    assert len(signals) == 2
    titles = [s.title for s in signals]
    assert "Game changer" in titles
    assert "Broken" in titles
    # Sentiment mapped from rating
    by_title = {s.title: s for s in signals}
    assert by_title["Game changer"].sentiment == "positive"
    assert by_title["Broken"].sentiment == "negative"


def test_app_store_monitor_no_url_returns_empty(isolated_settings):
    from app.research.monitors.app_store_monitor import AppStoreIOSMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert AppStoreIOSMonitor().check_for_new_signals(profile) == []


# ── monitors: changelog ───────────────────────────────────────────────


_CHANGELOG_HTML = """
<html><body>
<main>
  <article>
    <h2>v1.5 release</h2>
    <p>Published 2026-05-20</p>
    <p>Adds dark mode and 3 bug fixes.</p>
  </article>
  <article>
    <h2>v1.4 release</h2>
    <p>Published 2026-04-15</p>
    <p>Performance improvements.</p>
  </article>
</main>
</body></html>
"""


def test_changelog_monitor_parses_fixture(isolated_settings):
    from app.research.monitors.changelog_monitor import ChangelogMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        changelog_url="https://linear.app/changelog",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    class _FakeResp:
        status_code = 200
        text = _CHANGELOG_HTML

        def raise_for_status(self):  # pragma: no cover
            return None

    with patch(
        "app.research.monitors.changelog_monitor.requests.get",
        return_value=_FakeResp(),
    ):
        signals = ChangelogMonitor().check_for_new_signals(profile)

    assert len(signals) == 2
    titles = [s.title for s in signals]
    assert "v1.5 release" in titles
    assert "v1.4 release" in titles
    for s in signals:
        assert s.source == "changelog"
        assert s.signal_type == "release"
        assert s.url == "https://linear.app/changelog"


def test_changelog_monitor_no_url_returns_empty(isolated_settings):
    from app.research.monitors.changelog_monitor import ChangelogMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert ChangelogMonitor().check_for_new_signals(profile) == []


# ── monitors: stubs ──────────────────────────────────────────────────


def test_jobs_monitor_is_stub(isolated_settings):
    from app.research.monitors.jobs_monitor import JobsMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        careers_url="https://linear.app/careers",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert JobsMonitor().check_for_new_signals(profile) == []


def test_social_monitor_is_stub(isolated_settings):
    from app.research.monitors.social_monitor import SocialMonitor
    from app.research.profile import CompetitorProfile

    profile = CompetitorProfile(
        id="p1",
        workspace_id="ws_app",
        name="Linear",
        twitter_handle="@linear",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert SocialMonitor().check_for_new_signals(profile) == []


# ── routes ───────────────────────────────────────────────────────────


def test_routes_require_auth(unauth_client):
    r = unauth_client.get("/v1/research/competitors")
    assert r.status_code == 401


def test_create_and_list_profile_via_route(app_client):
    r = app_client.post(
        "/v1/research/competitors",
        json={
            "name": "Linear",
            "product_url": "https://linear.app",
            "changelog_url": "https://linear.app/changelog",
        },
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["name"] == "Linear"
    assert created["workspace_id"] == "ws_demo"  # default audience

    r2 = app_client.get("/v1/research/competitors")
    listing = r2.json()
    assert len(listing) == 1
    assert listing[0]["id"] == created["id"]


def test_update_profile_route(app_client):
    r = app_client.post(
        "/v1/research/competitors",
        json={"name": "Linear"},
    )
    pid = r.json()["id"]
    r2 = app_client.put(
        f"/v1/research/competitors/{pid}",
        json={"name": "Linear Inc", "monitoring_enabled": False},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "Linear Inc"
    assert r2.json()["monitoring_enabled"] is False


def test_update_nonexistent_profile_404(app_client):
    r = app_client.put(
        "/v1/research/competitors/does-not-exist",
        json={"name": "x"},
    )
    assert r.status_code == 404


def test_delete_profile_route(app_client):
    r = app_client.post("/v1/research/competitors", json={"name": "X"})
    pid = r.json()["id"]
    r2 = app_client.delete(f"/v1/research/competitors/{pid}")
    assert r2.status_code == 200
    r3 = app_client.get("/v1/research/competitors")
    assert r3.json() == []


def test_cross_tenant_access_404(app_client):
    """Profile created under one audience is invisible under the other."""
    # Default app_client login is audience=demo. Create a profile there.
    r = app_client.post("/v1/research/competitors", json={"name": "Demo Profile"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # Now re-login under the `app` audience and try to access it.
    _login_as(app_client, "app")
    r2 = app_client.put(
        f"/v1/research/competitors/{pid}",
        json={"name": "hijack"},
    )
    assert r2.status_code == 404

    r3 = app_client.delete(f"/v1/research/competitors/{pid}")
    assert r3.status_code == 404

    r4 = app_client.get(f"/v1/research/competitors/{pid}/signals")
    assert r4.status_code == 404


def test_list_signals_route(app_client):
    # Create a profile and persist one signal directly via the service,
    # then read it back via the route.
    from app.research import profile_service
    from app.research.profile import CompetitorSignalCreate

    r = app_client.post("/v1/research/competitors", json={"name": "Linear"})
    pid = r.json()["id"]
    profile_service.record_signal(
        pid,
        CompetitorSignalCreate(
            source="changelog",
            signal_type="release",
            title="v1.0",
            url="https://linear.app/changelog/v1",
            published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    )
    r2 = app_client.get(f"/v1/research/competitors/{pid}/signals")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "v1.0"


def test_refresh_runs_all_monitors(app_client):
    """POST /refresh wires every default monitor + returns counts."""
    r = app_client.post(
        "/v1/research/competitors",
        json={
            "name": "Linear",
            "changelog_url": "https://linear.app/changelog",
        },
    )
    pid = r.json()["id"]

    class _FakeChangelogResp:
        status_code = 200
        text = _CHANGELOG_HTML

        def raise_for_status(self):  # pragma: no cover
            return None

    # The app store monitor will short-circuit (no URL); jobs + social
    # are stubs. Only the changelog monitor should produce signals.
    with patch(
        "app.research.monitors.changelog_monitor.requests.get",
        return_value=_FakeChangelogResp(),
    ):
        r2 = app_client.post(f"/v1/research/competitors/{pid}/refresh")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["new_signal_count"] == 2
    assert body["per_monitor"]["changelog"] == 2
    assert body["per_monitor"]["app_store_ios"] == 0
    assert body["per_monitor"]["jobs"] == 0
    assert body["per_monitor"]["social"] == 0

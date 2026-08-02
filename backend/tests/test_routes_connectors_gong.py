"""Tests for the Gong connector (Access Key + Secret, Basic auth).

Gong has no self-serve OAuth — auth is a workspace-scoped Access Key +
Secret pair sent as Basic auth. All outbound HTTP is mocked. Covers the
auth module, the credentials/disconnect routes, and the KG puller's
distilled-only contract (no raw transcript ever reaches extraction).
"""
from __future__ import annotations

import base64
import importlib
import json
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.gong",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def gong_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _workspaces_resp(names=("Meridian Health",)):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "workspaces": [{"id": f"w{i}", "name": n} for i, n in enumerate(names)],
    }
    return mock_resp


# ─────────────────────────── Auth module unit tests ───────────────────────────


def test_basic_token_is_base64_of_key_colon_secret(gong_env):
    from app.connectors import gong

    token = gong.basic_token("AK", "SECRET")
    assert base64.b64decode(token).decode() == "AK:SECRET"


def test_credential_to_store_bundles_base_url_and_basic_token(gong_env):
    from app.connectors import gong

    payload = json.loads(
        gong.credential_to_store(gong.DEFAULT_API_BASE, "AK", "SECRET")
    )
    assert payload["access_key"] == "AK"
    assert payload["access_key_secret"] == "SECRET"
    base_url, token = gong.parse_credential(payload[gong.CREDENTIAL_KEY])
    assert base_url == gong.DEFAULT_API_BASE
    assert token == gong.basic_token("AK", "SECRET")


@pytest.mark.parametrize("raw,expected", [
    (None, "https://api.gong.io/v2"),
    ("", "https://api.gong.io/v2"),
    ("   ", "https://api.gong.io/v2"),
    # Region-specific tenants: host alone gets /v2 appended…
    ("https://us-12345.api.gong.io", "https://us-12345.api.gong.io/v2"),
    # …and a full API root is kept as-is (trailing slash stripped).
    ("https://us-12345.api.gong.io/v2/", "https://us-12345.api.gong.io/v2"),
])
def test_normalize_api_base(gong_env, raw, expected):
    from app.connectors import gong

    assert gong.normalize_api_base(raw) == expected


@pytest.mark.parametrize("bad", [
    "api.gong.io",                      # no scheme
    "ftp://api.gong.io",                # wrong scheme
    "https://",                         # no host
    "https://api.gong.io/v2?x=1",       # query string
])
def test_normalize_api_base_rejects_bad_urls(gong_env, bad):
    from app.connectors import gong

    with pytest.raises(ValueError):
        gong.normalize_api_base(bad)


def test_fetch_workspaces_calls_api_with_basic_auth(gong_env):
    from app.connectors import gong

    with patch(
        "app.connectors.gong.requests.get", return_value=_workspaces_resp()
    ) as mock_get:
        workspaces = gong.fetch_workspaces(gong.DEFAULT_API_BASE, "basic-token")

    assert [w["name"] for w in workspaces] == ["Meridian Health"]
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://api.gong.io/v2/workspaces"
    assert call_args.kwargs["headers"]["Authorization"] == "Basic basic-token"


def test_fetch_workspaces_raises_on_rejected_credentials(gong_env):
    from app.connectors import gong

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    with patch("app.connectors.gong.requests.get", return_value=mock_resp):
        with pytest.raises(gong.GongAuthError, match="rejected"):
            gong.fetch_workspaces(gong.DEFAULT_API_BASE, "bad-token")


def test_account_label_prefers_first_named_workspace(gong_env):
    from app.connectors import gong

    assert gong.account_label_from_workspaces(
        [{"name": ""}, {"name": "Acme Rev Team"}]
    ) == "Acme Rev Team"
    assert gong.account_label_from_workspaces([]) == "Gong workspace"


# ─────────────────────────── Route tests ───────────────────────────


def test_credentials_route_requires_auth(unauth_client, gong_env):
    r = unauth_client.post(
        "/v1/connectors/gong/credentials",
        json={"access_key": "AK", "access_key_secret": "S"},
    )
    assert r.status_code == 401


def test_credentials_route_stores_connection_with_workspace_label(
    gong_env, monkeypatch
):
    ctx = company_client(monkeypatch)

    with patch("app.connectors.gong.requests.get", return_value=_workspaces_resp()):
        r = ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={"access_key": "AK", "access_key_secret": "SECRET"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("provider") == "gong"
    assert body.get("account_label") == "Meridian Health"

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "gong"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "Meridian Health"
    # Dual-typed: meetings + customer-voice ride on the public row.
    assert rows[0]["types"] == ["meetings", "customer-voice"]
    # Non-secret workspace metadata is on config; the key pair is not.
    assert rows[0]["config"]["workspaces"] == [{"id": "w0", "name": "Meridian Health"}]
    assert rows[0]["config"]["base_url"] == "https://api.gong.io/v2"
    assert "token_json_encrypted" not in rows[0]


def test_credentials_route_rejects_invalid_pair(gong_env, monkeypatch):
    ctx = company_client(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    with patch("app.connectors.gong.requests.get", return_value=mock_resp):
        r = ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={"access_key": "AK", "access_key_secret": "WRONG"},
        )

    assert r.status_code == 400
    assert "rejected" in r.json()["detail"]
    listed = ctx.client.get("/v1/connectors").json()
    assert not any(c["provider"] == "gong" for c in listed["connections"])


@pytest.mark.parametrize("body", [
    {"access_key": "", "access_key_secret": "S"},
    {"access_key": "AK", "access_key_secret": ""},
    {"access_key": "AK"},
    {},
])
def test_credentials_route_rejects_missing_fields(gong_env, monkeypatch, body):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/gong/credentials", json=body)
    assert r.status_code == 422


def test_credentials_route_rekey_overwrites_existing(gong_env, monkeypatch):
    ctx = company_client(monkeypatch)

    with patch(
        "app.connectors.gong.requests.get",
        return_value=_workspaces_resp(("First WS",)),
    ):
        ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={"access_key": "K1", "access_key_secret": "S1"},
        )
    with patch(
        "app.connectors.gong.requests.get",
        return_value=_workspaces_resp(("Second WS",)),
    ):
        ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={"access_key": "K2", "access_key_secret": "S2"},
        )

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "gong"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "Second WS"


def test_credentials_route_accepts_a_region_specific_base_url(
    gong_env, monkeypatch
):
    """A tenant whose Gong API page shows a different host can supply it;
    validation and storage both use that host."""
    ctx = company_client(monkeypatch)

    with patch(
        "app.connectors.gong.requests.get", return_value=_workspaces_resp()
    ) as mock_get:
        r = ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={
                "access_key": "AK",
                "access_key_secret": "S",
                "api_base_url": "https://us-12345.api.gong.io",
            },
        )

    assert r.status_code == 200, r.text
    assert mock_get.call_args.args[0] == (
        "https://us-12345.api.gong.io/v2/workspaces"
    )
    listed = ctx.client.get("/v1/connectors").json()
    row = next(c for c in listed["connections"] if c["provider"] == "gong")
    assert row["config"]["base_url"] == "https://us-12345.api.gong.io/v2"


def test_credentials_route_422s_on_a_malformed_base_url(gong_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/gong/credentials",
        json={
            "access_key": "AK",
            "access_key_secret": "S",
            "api_base_url": "not-a-url",
        },
    )
    assert r.status_code == 422


def test_delete_gong_disconnects(gong_env, monkeypatch):
    ctx = company_client(monkeypatch)

    with patch("app.connectors.gong.requests.get", return_value=_workspaces_resp()):
        ctx.client.post(
            "/v1/connectors/gong/credentials",
            json={"access_key": "AK", "access_key_secret": "S"},
        )

    r = ctx.client.delete("/v1/connectors/gong")
    assert r.status_code == 200
    listed = ctx.client.get("/v1/connectors").json()
    assert not any(c["provider"] == "gong" for c in listed["connections"])


def test_delete_gong_404_when_not_connected(gong_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/gong")
    assert r.status_code == 404


def test_gong_is_a_registered_kg_puller(gong_env):
    """Registration in PULLERS is what wires auto-sync-on-connect, the
    scheduled refresh, and the settings 'ingestable' flag — pin it."""
    from app.kg_ingest.runner import PULLERS

    puller_fn, token_key, hint = PULLERS["gong"]
    assert token_key == "gong_credential"
    assert "customer_voice" in hint


# ─────────────────────────── Puller unit tests ───────────────────────────


def _call(call_id="c1", title="Renewal call", *, brief="Customer wants SSO",
          key_points=("Pricing concern",), external=("Dana Buyer",),
          internal=("Sam Seller",)):
    return {
        "metaData": {
            "id": call_id,
            "title": title,
            "started": "2026-07-20T10:00:00Z",
            "duration": 1800,
            "direction": "Outbound",
        },
        "parties": (
            [{"name": n, "affiliation": "External"} for n in external]
            + [{"name": n, "affiliation": "Internal"} for n in internal]
        ),
        "content": {
            "brief": brief,
            "keyPoints": [{"text": t} for t in key_points],
            "highlights": [
                {"title": "Objections", "items": [{"text": "Too pricey"}]},
            ],
            "topics": [{"name": "Pricing"}, {"name": "Security"}],
            "trackers": [{"name": "Churn risk", "count": 2}],
        },
    }


def _credential(base_url="https://api.gong.io/v2", token="basic-token"):
    """The stored credential string the runner hands the puller."""
    return json.dumps({"base_url": base_url, "basic_token": token})


def _page(calls, cursor=None):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "calls": calls,
        "records": {"cursor": cursor} if cursor else {},
    }
    return mock_resp


def test_pull_yields_distilled_records_no_transcript(gong_env):
    from app.kg_ingest.pullers import gong as gong_puller

    with patch(
        "app.kg_ingest.pullers.gong.requests.post", return_value=_page([_call()])
    ) as mock_post:
        records = list(gong_puller.pull(_credential()))

    assert len(records) == 1
    rec = records[0]
    assert rec.provider == "gong"
    assert rec.kind == "call"
    assert rec.external_id == "c1"
    assert rec.title == "Renewal call"
    # Distilled layer present…
    assert "Customer wants SSO" in rec.text
    assert "Pricing concern" in rec.text
    assert "Too pricey" in rec.text
    assert "Pricing" in rec.text
    # …customer-side participants first.
    assert rec.properties["participants"][0] == "Dana Buyer"
    assert rec.timestamp == "2026-07-20T10:00:00Z"

    body = mock_post.call_args.kwargs["json"]
    # The request asks for the distilled layer only — never transcript
    # structure or media (§6 no-raw-dump contract).
    exposed = body["contentSelector"]["exposedFields"]
    assert exposed["content"].get("brief") is True
    assert "structure" not in exposed["content"]
    assert "media" not in exposed
    assert mock_post.call_args.args[0] == "https://api.gong.io/v2/calls/extensive"
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Basic basic-token"
    assert body["filter"]["fromDateTime"]  # Gong requires an explicit window


def test_pull_targets_the_connections_own_base_url(gong_env):
    """Region-specific tenants: the puller hits the stored base URL, never a
    hardcoded api.gong.io."""
    from app.kg_ingest.pullers import gong as gong_puller

    cred = _credential(base_url="https://us-12345.api.gong.io/v2")
    with patch(
        "app.kg_ingest.pullers.gong.requests.post", return_value=_page([_call()])
    ) as mock_post:
        list(gong_puller.pull(cred))

    assert mock_post.call_args.args[0] == (
        "https://us-12345.api.gong.io/v2/calls/extensive"
    )


def test_pull_since_narrows_the_window(gong_env):
    """An incremental sync asks Gong only for calls since the given bound —
    that's what keeps the per-sync cap from becoming a coverage ceiling."""
    from app.kg_ingest.pullers import gong as gong_puller

    since = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    with patch(
        "app.kg_ingest.pullers.gong.requests.post", return_value=_page([_call()])
    ) as mock_post:
        list(gong_puller.pull(_credential(), since=since))

    sent = mock_post.call_args.kwargs["json"]["filter"]["fromDateTime"]
    assert sent.startswith("2026-07-28T06:00")


def test_pull_pages_through_cursor_and_respects_limit(gong_env):
    from app.kg_ingest.pullers import gong as gong_puller

    pages = [
        _page([_call("c1"), _call("c2")], cursor="next-1"),
        _page([_call("c3"), _call("c4")]),
    ]
    with patch(
        "app.kg_ingest.pullers.gong.requests.post", side_effect=pages
    ) as mock_post:
        records = list(gong_puller.pull(_credential(), limit=3))

    assert [r.external_id for r in records] == ["c1", "c2", "c3"]
    # Second request carried the cursor from the first page.
    assert mock_post.call_args_list[1].kwargs["json"]["cursor"] == "next-1"


def test_pull_treats_404_as_empty_window(gong_env):
    """Gong signals 'no calls in this window' as HTTP 404 — an empty pull,
    not an error."""
    from app.kg_ingest.pullers import gong as gong_puller

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 404
    with patch("app.kg_ingest.pullers.gong.requests.post", return_value=mock_resp):
        assert list(gong_puller.pull(_credential())) == []


def test_pull_skips_calls_without_id_and_survives_thin_content(gong_env):
    """A call with no distilled content still yields a (thin) record —
    title/participants/topics — and a call without an id is dropped."""
    from app.kg_ingest.pullers import gong as gong_puller

    thin = {
        "metaData": {"id": "c9", "title": "Quick sync", "started": None},
        "parties": [],
        "content": {},
    }
    no_id = {"metaData": {}, "content": {"brief": "orphan"}}
    with patch(
        "app.kg_ingest.pullers.gong.requests.post",
        return_value=_page([thin, no_id]),
    ):
        records = list(gong_puller.pull(_credential()))

    assert [r.external_id for r in records] == ["c9"]
    assert records[0].text == ""


# ─────────────────── Incremental sync (runner wiring) ───────────────────
#
# Steady state must pull only the delta: with a fixed window, a per-sync
# record cap silently becomes a coverage ceiling for busy workspaces.


def test_incremental_since_subtracts_a_processing_overlap(gong_env):
    """Gong finishes call briefs asynchronously, so `since` reaches back
    before the last sync — otherwise a brief that landed after its window
    closed would be skipped forever."""
    from app.kg_ingest.runner import _INCREMENTAL_OVERLAP, incremental_since

    since = incremental_since("2026-07-30T12:00:00+00:00")
    assert since == datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc) - _INCREMENTAL_OVERLAP


def test_incremental_since_assumes_utc_for_naive_stamps(gong_env):
    from app.kg_ingest.runner import incremental_since

    assert incremental_since("2026-07-30T12:00:00").tzinfo is timezone.utc


@pytest.mark.parametrize("bad", [None, "", "not-a-date"])
def test_incremental_since_degrades_to_full_window(gong_env, bad):
    """Never-synced or unparseable ⇒ None ⇒ the puller's own backfill
    window. A bad stamp must yield MORE data, never silently less."""
    from app.kg_ingest.runner import incremental_since

    assert incremental_since(bad) is None


def test_sync_provider_pulls_incrementally_for_gong(gong_env):
    from app.kg_ingest import runner

    seen: dict = {}

    def fake_pull(token, **kwargs):
        seen.update(kwargs)
        return iter(())

    with patch.dict(
        runner.PULLERS, {"gong": (fake_pull, "gong_credential", "hint")}
    ):
        runner.sync_provider(
            object(), "co-1", "gong",
            token=_credential(),
            last_sync_at="2026-07-30T12:00:00+00:00",
        )

    assert "since" in seen and seen["since"] is not None


def test_sync_provider_full_window_on_first_ever_sync(gong_env):
    """No last_sync_at (just connected) ⇒ no `since` ⇒ the backfill."""
    from app.kg_ingest import runner

    seen: dict = {}

    def fake_pull(token, **kwargs):
        seen.update(kwargs)
        return iter(())

    with patch.dict(
        runner.PULLERS, {"gong": (fake_pull, "gong_credential", "hint")}
    ):
        runner.sync_provider(
            object(), "co-1", "gong", token=_credential(), last_sync_at=None
        )

    assert "since" not in seen


def test_sync_provider_leaves_non_incremental_pullers_alone(gong_env):
    """Providers outside INCREMENTAL_PULLERS keep their existing call shape
    — this change must not alter Fireflies/ClickUp/etc."""
    from app.kg_ingest import runner

    calls: list = []

    def fake_pull(token, **kwargs):
        calls.append(kwargs)
        return iter(())

    assert "fireflies" not in runner.INCREMENTAL_PULLERS
    with patch.dict(
        runner.PULLERS, {"fireflies": (fake_pull, "api_key", "hint")}
    ):
        runner.sync_provider(
            object(), "co-1", "fireflies", token="k",
            last_sync_at="2026-07-30T12:00:00+00:00",
        )

    assert calls == [{}]


# ─────────────────────────── Catalog / sanity ───────────────────────────


def test_gong_is_dual_typed_meetings_and_customer_voice(gong_env):
    from app.connectors.catalog import CUSTOMER_VOICE, MEETINGS, types_for

    assert types_for("gong") == [MEETINGS, CUSTOMER_VOICE]


def test_gong_does_not_appear_in_start_oauth_dispatch(gong_env, monkeypatch):
    """Gong is key-pair based, not OAuth — the start-oauth endpoint should
    NOT recognise it (returns 404)."""
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/gong/start-oauth")
    assert r.status_code == 404

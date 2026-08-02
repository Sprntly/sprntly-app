"""Tenant-scoping + token-refresh tests for connector sync routes.

Covers the gaps closed in fix/connector-sync-scoping:

  * GET  /v1/connectors/sync-status      → require_company; returns only
    the caller's company's connections (cross-tenant denial).
  * POST /v1/connectors/figma/sync-to-corpus    → require_company; threads
    company_id into the figma token lookup (no TypeError; scoped).
  * POST /v1/connectors/hubspot/sync            → require_company; threads
    company_id into sync_hubspot (no TypeError; scoped).
  * POST /v1/connectors/hubspot/sync-to-corpus  → same.
  * A foreign company's connection is never synced for the caller.
  * Figma token refresh-on-expiry in `_figma_access_token`: expired stored
    token → refresh called, new token persisted + returned; non-expired →
    no refresh; refresh failure → clear error (no dead token handed back).

Plus the cross-tenant DATASET gate (fix/connectors/sync-dataset-ownership).
The scoping tests above all posted `dataset: "acme"` — the CALLER'S OWN slug
(`_company_helpers.seed_company` defaults to it) — so they proved the
connection lookup was company-scoped but asserted nothing about the
client-supplied `body.dataset`. The section at the bottom of this file seeds a
SECOND company with a real corpus on disk and drives every sync route at the
victim's slug, asserting 404 + an untouched victim corpus + no corpus-seed
kickoff on a mismatched (company_id, slug) pair.

All outbound HTTP is mocked; the fake in-memory Supabase backs the DB.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from tests._company_helpers import company_client, seed_company, seed_connection


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.figma_oauth",
        "app.connectors.hubspot_oauth",
        "app.connectors.hubspot_sync",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def sync_env(isolated_settings, monkeypatch):
    """Configure Figma + HubSpot creds and a token encryption key, then
    reload the app so the routes pick everything up."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FIGMA_CLIENT_ID", "figma-client-id")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "figma-client-secret")
    monkeypatch.setenv(
        "FIGMA_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/figma/callback",
    )
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "hubspot-client-id")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "hubspot-client-secret")
    monkeypatch.setenv(
        "HUBSPOT_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/hubspot/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    yield


def _figma_token_blob(*, expires_in: int, age_s: int = 0) -> dict:
    """A stored Figma token blob whose obtained_at is `age_s` seconds old."""
    return {
        "access_token": "figma-access-old",
        "refresh_token": "figma-refresh-old",
        "expires_in": expires_in,
        "obtained_at": int(time.time()) - age_s,
    }


# ───────────────────────── /sync-status scoping ─────────────────────────


def test_sync_status_requires_company(sync_env, monkeypatch):
    """No auth → not 200 (require_company gate, not require_session)."""
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.get("/v1/connectors/sync-status")
    assert r.status_code in (401, 403), r.text


def test_sync_status_returns_only_callers_connections(sync_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "mine"},
        label="mine@co.com",
    )

    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    providers = [c["provider"] for c in r.json()["connectors"]]
    assert providers == ["figma"]


def test_sync_status_excludes_foreign_company_connections(sync_env, monkeypatch):
    """A connection owned by another company must not leak into the
    caller's sync-status (cross-tenant denial)."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="hubspot",
        token_blob={"access_token": "foreign"},
        label="foreign@other.com",
    )
    # Caller has its own, distinct connection.
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "mine"},
        label="mine@co.com",
    )

    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    providers = {c["provider"] for c in r.json()["connectors"]}
    assert providers == {"figma"}
    assert "hubspot" not in providers


def test_sync_status_empty_for_company_with_no_connections(sync_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    assert r.json()["connectors"] == []


# ───────────────────── figma/sync-to-corpus scoping ─────────────────────


def test_figma_sync_to_corpus_requires_company(sync_env, monkeypatch):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.post(
        "/v1/connectors/figma/sync-to-corpus",
        json={"file_key": "abc", "dataset": "acme"},
    )
    assert r.status_code in (401, 403), r.text


def test_figma_sync_to_corpus_scoped_no_typeerror(sync_env, monkeypatch):
    """The route threads company.company_id into _figma_access_token — a
    regression of the old arity bug (`_figma_access_token()`) would raise
    a TypeError → 500. A fresh (non-expired) token must sync cleanly."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000),
        label="mine@co.com",
    )

    fake_file = {"name": "Design", "lastModified": "x", "document": {"children": []}}
    fake_styles = {"meta": {"styles": []}}
    with (
        patch("app.routes.connectors.figma_oauth.fetch_file", return_value=fake_file),
        patch(
            "app.routes.connectors.figma_oauth.fetch_file_styles",
            return_value=fake_styles,
        ),
    ):
        r = ctx.client.post(
            "/v1/connectors/figma/sync-to-corpus",
            json={"file_key": "abc", "dataset": "acme"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_figma_sync_to_corpus_404_when_caller_not_connected(sync_env, monkeypatch):
    """A foreign company's Figma connection isn't usable by the caller —
    the caller has none, so the scoped lookup 404s rather than borrowing
    another tenant's token."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000),
        label="foreign@other.com",
    )

    r = ctx.client.post(
        "/v1/connectors/figma/sync-to-corpus",
        json={"file_key": "abc", "dataset": "acme"},
    )
    assert r.status_code == 404, r.text


# ───────────────────── hubspot sync scoping ─────────────────────


@pytest.mark.parametrize("path", ["/hubspot/sync", "/hubspot/sync-to-corpus"])
def test_hubspot_sync_requires_company(sync_env, monkeypatch, path):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.post(f"/v1/connectors{path}", json={"dataset": "acme"})
    assert r.status_code in (401, 403), r.text


@pytest.mark.parametrize("path", ["/hubspot/sync", "/hubspot/sync-to-corpus"])
def test_hubspot_sync_threads_company_id(sync_env, monkeypatch, path):
    """The route must call sync_hubspot(dataset, company_id=...). The old
    code called sync_hubspot(dataset) which, given the new required kwarg,
    would TypeError. We patch sync_hubspot and assert it received the
    caller's company_id."""
    ctx = company_client(monkeypatch)

    captured = {}

    class _Result:
        def to_dict(self):
            return {"ok": True}

    def fake_sync(dataset, *, company_id):
        captured["dataset"] = dataset
        captured["company_id"] = company_id
        return _Result()

    with patch("app.connectors.hubspot_sync.sync_hubspot", side_effect=fake_sync):
        r = ctx.client.post(f"/v1/connectors{path}", json={"dataset": "acme"})

    assert r.status_code == 200, r.text
    assert captured["dataset"] == "acme"
    assert captured["company_id"] == ctx.company_id


def test_hubspot_sync_uses_callers_own_connection(sync_env, monkeypatch):
    """End-to-end through the real sync_hubspot: the access-token lookup is
    company-scoped, so a foreign company's HubSpot connection isn't synced
    for the caller (the caller has none → 404)."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="hubspot",
        token_blob={
            "access_token": "foreign-access",
            "refresh_token": "foreign-refresh",
            "expires_in": 1800,
            "obtained_at": int(time.time()),
        },
        label="foreign@other.com",
    )

    # No outbound HTTP should happen — caller has no HubSpot connection.
    r = ctx.client.post("/v1/connectors/hubspot/sync", json={"dataset": "acme"})
    assert r.status_code == 404, r.text


# ───────────────────── figma token refresh-on-expiry ─────────────────────


def test_figma_access_token_refreshes_when_expired(sync_env, monkeypatch):
    """Stored token past expiry → refresh_access_token called; the fresh
    token is persisted to the connection config AND returned."""
    from app.routes import connectors as routes
    from app import db
    from app.connectors import figma_oauth
    from app.connectors.tokens import decrypt_token_json

    ctx = company_client(monkeypatch)
    # expires_in 100s, obtained 1000s ago → well past expiry.
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    fresh = {
        "access_token": "figma-access-NEW",
        "refresh_token": "figma-refresh-NEW",
        "expires_in": 7776000,
    }
    with patch.object(
        figma_oauth, "refresh_access_token", return_value=fresh
    ) as mock_refresh:
        token = routes._figma_access_token(ctx.company_id)

    # Refresh was called with the stored refresh token.
    mock_refresh.assert_called_once_with("figma-refresh-old")
    # Fresh access token is returned.
    assert token == "figma-access-NEW"

    # Fresh token persisted back onto the connection (encrypted).
    row = db.get_connection(ctx.company_id, figma_oauth.FIGMA_PROVIDER)
    stored = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert stored["access_token"] == "figma-access-NEW"
    assert stored["refresh_token"] == "figma-refresh-NEW"
    assert stored["expires_in"] == 7776000
    # obtained_at re-stamped to ~now (not the stale value).
    assert stored["obtained_at"] >= int(time.time()) - 5


def test_figma_access_token_no_refresh_when_valid(sync_env, monkeypatch):
    """A token comfortably within its lifetime is returned as-is; refresh
    is never called."""
    from app.routes import connectors as routes
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000, age_s=10),
        label="mine@co.com",
    )

    with patch.object(figma_oauth, "refresh_access_token") as mock_refresh:
        token = routes._figma_access_token(ctx.company_id)

    mock_refresh.assert_not_called()
    assert token == "figma-access-old"


def test_figma_access_token_refresh_failure_raises_clear_error(sync_env, monkeypatch):
    """If refresh fails, surface a clear error — never hand back the dead
    token. The stored (dead) token must not be returned."""
    from app.routes import connectors as routes
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    def boom(_refresh_token):
        raise HTTPException(400, "Figma token refresh failed")

    with patch.object(figma_oauth, "refresh_access_token", side_effect=boom):
        with pytest.raises(HTTPException) as exc:
            routes._figma_access_token(ctx.company_id)

    assert exc.value.status_code == 502
    assert "reconnect" in exc.value.detail.lower()


def test_figma_access_token_expired_without_refresh_token_errors(sync_env, monkeypatch):
    """Expired token with no refresh_token → clear 401, not a dead token."""
    from app.routes import connectors as routes

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={
            "access_token": "dead",
            "expires_in": 100,
            "obtained_at": int(time.time()) - 1000,
        },
        label="mine@co.com",
    )

    with pytest.raises(HTTPException) as exc:
        routes._figma_access_token(ctx.company_id)
    assert exc.value.status_code == 401


def test_figma_sync_to_corpus_triggers_refresh(sync_env, monkeypatch):
    """End-to-end: an expired token at sync time refreshes, and the sync
    proceeds with the fresh token (no degraded/silent failure)."""
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    fresh = {"access_token": "figma-access-NEW", "expires_in": 7776000}
    fake_file = {"name": "D", "document": {"children": []}}
    fake_styles = {"meta": {"styles": []}}

    seen_tokens = []

    def capture_fetch(token, *a, **k):
        seen_tokens.append(token)
        return fake_file

    with (
        patch.object(figma_oauth, "refresh_access_token", return_value=fresh),
        patch("app.routes.connectors.figma_oauth.fetch_file", side_effect=capture_fetch),
        patch(
            "app.routes.connectors.figma_oauth.fetch_file_styles",
            return_value=fake_styles,
        ),
    ):
        r = ctx.client.post(
            "/v1/connectors/figma/sync-to-corpus",
            json={"file_key": "abc", "dataset": "acme"},
        )

    assert r.status_code == 200, r.text
    # The fetch used the refreshed token, not the stale one.
    assert seen_tokens and seen_tokens[0] == "figma-access-NEW"


# ═══════════════ cross-tenant dataset ownership (body.dataset) ═══════════════
#
# Every sync route below takes its target dataset slug from the REQUEST BODY.
# `require_company` proves who the caller is and
# `_require_admin_for_org_connector` proves they're an admin OF THEIR OWN
# company — neither looks at `body.dataset`. The tests here drive each route at
# a second tenant's slug and assert the ownership gate 404s before any sink
# runs. The three sinks, all reachable from that one unvalidated string:
#
#   1. settings.data_path / <slug> / "<fixed>.md" — fixed filenames, so a write
#      OVERWRITES the victim's real corpus file of the same name.
#   2. _seed_corpus_after_sync → kickoff_corpus_seed(attacker_company, slug) →
#      load_corpus(slug) globs the victim's whole corpus into the ATTACKER's
#      kg_signal rows.
#   3. db.upsert_input_source(slug, …) — flips enterprise_input_sources rows on
#      another tenant's dataset.

VICTIM_SLUG = "victim"

# The fixed filenames each connector sync writes. Fixed (not per-file) is what
# makes sink 1 an overwrite rather than a stray extra file.
_SINK_FILES = (
    "figma_design_context.md",
    "github_active_prs.md",
    "slack_channels.md",
    "hubspot_contacts.md",
    "hubspot_companies.md",
    "hubspot_deals.md",
)

_SENTINEL = "VICTIM CORPUS — MUST SURVIVE A CROSS-TENANT SYNC\n"

# (path, body) for every route that takes a client-supplied dataset slug.
# `{slug}` is substituted per-test.
_SYNC_ROUTES = [
    ("/v1/connectors/figma/sync-to-corpus", {"file_key": "abc", "dataset": "{slug}"}),
    ("/v1/connectors/github/sync-to-corpus", {"dataset": "{slug}"}),
    ("/v1/connectors/hubspot/sync", {"dataset": "{slug}"}),
    ("/v1/connectors/hubspot/sync-to-corpus", {"dataset": "{slug}"}),
    ("/v1/connectors/slack/sync-to-corpus", {"dataset": "{slug}"}),
    ("/v1/connectors/google-drive/files", {"files": [], "dataset": "{slug}"}),
    ("/v1/connectors/google-drive/sync", {"dataset": "{slug}"}),
]

_SYNC_ROUTE_IDS = [
    "figma", "github", "hubspot-sync", "hubspot-sync-to-corpus",
    "slack", "drive-files", "drive-sync",
]


def _body_for(template: dict, slug: str) -> dict:
    return {
        k: (v.format(slug=slug) if isinstance(v, str) else v)
        for k, v in template.items()
    }


class _FakeSyncResult:
    def __init__(self, dataset):
        self.dataset = dataset

    def to_dict(self):
        return {"ok": True, "dataset": self.dataset}


def _seed_victim(slug: str = VICTIM_SLUG) -> SimpleNamespace:
    """A second tenant with a real corpus on disk.

    Seeds the `companies` row (what company_id_for_dataset resolves the slug
    through), its `datasets` row, and a sentinel file under every fixed
    filename a connector sync would clobber."""
    from app import db
    from app.config import settings

    company_id = seed_company(user_id="victim-user", slug=slug)
    db.insert_dataset(slug, slug.title())
    corpus = settings.data_path / slug
    corpus.mkdir(parents=True, exist_ok=True)
    for name in _SINK_FILES:
        (corpus / name).write_text(_SENTINEL, encoding="utf-8")
    return SimpleNamespace(company_id=company_id, slug=slug, corpus=corpus)


def _assert_corpus_intact(victim: SimpleNamespace) -> None:
    """Sink 1: not one of the victim's corpus files may have been rewritten."""
    for name in _SINK_FILES:
        p = victim.corpus / name
        assert p.exists(), f"{name} disappeared from the victim's corpus"
        assert p.read_text(encoding="utf-8") == _SENTINEL, (
            f"{name} was OVERWRITTEN by a cross-tenant sync"
        )


def _clobbering_sync(dataset, *_a, **_kw):
    """Stand-in for sync_slack / sync_hubspot: writes the same fixed filenames
    into DATA_DIR/<dataset> the real ones do. If the gate ever regresses, the
    corpus assertion — not just the status code — catches it."""
    from app.config import settings

    d = settings.data_path / str(dataset)
    d.mkdir(parents=True, exist_ok=True)
    for name in _SINK_FILES:
        (d / name).write_text("CLOBBERED BY ANOTHER TENANT\n", encoding="utf-8")
    return _FakeSyncResult(dataset)


def _clobbering_drive_sync(*, company_id=None, dataset=None, files=None):  # noqa: ARG001
    return _clobbering_sync(dataset)


@pytest.fixture
def cross_tenant(sync_env, monkeypatch):
    """Attacker client (slug 'acme') + victim tenant, with every outbound sync
    stubbed so the ONLY thing that can stop a cross-tenant write is the
    ownership gate. Connections are seeded for the caller so a 404 can never be
    'you aren't connected' masquerading as the gate."""
    ctx = company_client(monkeypatch)
    victim = _seed_victim()

    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000),
        label="mine@co.com",
    )
    seed_connection(
        company_id=ctx.company_id,
        provider="google_drive",
        token_blob={"access_token": "drive-mine"},
        label="mine@co.com",
    )

    seeds: list[tuple] = []
    import app.db as db_mod
    import app.routes.connectors as conn_route

    monkeypatch.setattr(
        conn_route, "kickoff_corpus_seed",
        lambda cid, slug: seeds.append((cid, slug)),
    )

    # Sink 3 is observed with a spy rather than by reading the table back: the
    # fake Supabase schema doesn't carry enterprise_input_sources, and every
    # caller wraps upsert_input_source in a try/except that would swallow the
    # write we're trying to assert about.
    input_sources: list[tuple] = []
    monkeypatch.setattr(
        db_mod, "upsert_input_source",
        lambda dataset, source_type, **kw: input_sources.append(
            (dataset, source_type)
        ),
    )

    fake_file = {"name": "D", "lastModified": "x", "document": {"children": []}}
    with (
        patch("app.routes.connectors.figma_oauth.fetch_file", return_value=fake_file),
        patch(
            "app.routes.connectors.figma_oauth.fetch_file_styles",
            return_value={"meta": {"styles": []}},
        ),
        patch("app.connectors.hubspot_sync.sync_hubspot", side_effect=_clobbering_sync),
        patch("app.connectors.slack_sync.sync_slack", side_effect=_clobbering_sync),
        patch(
            "app.routes.connectors.sync_google_drive",
            side_effect=_clobbering_drive_sync,
        ),
    ):
        yield SimpleNamespace(
            ctx=ctx, victim=victim, seeds=seeds, input_sources=input_sources,
        )


@pytest.mark.parametrize("path,body", _SYNC_ROUTES, ids=_SYNC_ROUTE_IDS)
def test_sync_route_404s_on_another_companys_dataset(cross_tenant, path, body):
    """The core regression: a signed-in admin of company A naming company B's
    dataset slug gets 404 (never 403 — no existence disclosure) and B's corpus
    is untouched."""
    r = cross_tenant.ctx.client.post(
        path, json=_body_for(body, cross_tenant.victim.slug)
    )

    assert r.status_code == 404, (
        f"{path} accepted a foreign dataset slug: {r.status_code} {r.text}"
    )
    _assert_corpus_intact(cross_tenant.victim)


@pytest.mark.parametrize("path,body", _SYNC_ROUTES, ids=_SYNC_ROUTE_IDS)
def test_sync_route_never_seeds_kg_from_another_companys_corpus(
    cross_tenant, path, body
):
    """Sink 2: kickoff_corpus_seed must never be handed a (company_id, slug)
    pair that don't belong together — that call is what globs the victim's
    whole corpus into the ATTACKER's kg_signal rows."""
    cross_tenant.ctx.client.post(path, json=_body_for(body, cross_tenant.victim.slug))

    assert cross_tenant.victim.slug not in [s for _cid, s in cross_tenant.seeds], (
        f"{path} kicked a corpus seed for the victim's slug: {cross_tenant.seeds}"
    )
    for cid, slug in cross_tenant.seeds:
        assert cid == cross_tenant.ctx.company_id
        assert slug != cross_tenant.victim.slug


@pytest.mark.parametrize("path,body", _SYNC_ROUTES, ids=_SYNC_ROUTE_IDS)
def test_sync_route_never_flips_another_companys_input_source(
    cross_tenant, path, body
):
    """Sink 3: enterprise_input_sources rows on the victim's dataset must be
    untouched (figma/github/google_drive auto-enable their source on sync)."""
    cross_tenant.ctx.client.post(path, json=_body_for(body, cross_tenant.victim.slug))

    touched = [d for d, _t in cross_tenant.input_sources]
    assert cross_tenant.victim.slug not in touched, (
        f"{path} flipped an input source on the victim's dataset: "
        f"{cross_tenant.input_sources}"
    )


@pytest.mark.parametrize("path,body", _SYNC_ROUTES, ids=_SYNC_ROUTE_IDS)
def test_sync_route_rejects_path_traversal_slug(cross_tenant, path, body):
    """`../../escaped` must never reach `settings.data_path / slug`. The slug is
    shape-validated (422) before any filesystem join, so the traversal primitive
    is gone rather than merely unreachable."""
    from app.config import settings

    r = cross_tenant.ctx.client.post(path, json=_body_for(body, "../../escaped"))

    assert r.status_code == 422, (
        f"{path} accepted a traversal slug: {r.status_code} {r.text}"
    )
    escaped = (settings.data_path / ".." / ".." / "escaped").resolve()
    assert not escaped.exists(), f"{path} wrote outside DATA_DIR at {escaped}"


@pytest.mark.parametrize(
    "bad_slug",
    ["../../escaped", "../sibling", "a/b", "", "  ", "UPPER/CASE", "x" * 64],
    ids=["traversal", "parent", "subpath", "empty", "blank", "slashy", "too-long"],
)
def test_slack_sync_rejects_malformed_slugs(cross_tenant, bad_slug):
    """Slack has NO admin gate — any member can call it — so it gets the
    fullest slug-shape sweep. Nothing non-slug-shaped may reach the sink."""
    r = cross_tenant.ctx.client.post(
        "/v1/connectors/slack/sync-to-corpus", json={"dataset": bad_slug}
    )
    assert r.status_code == 422, f"slack accepted {bad_slug!r}: {r.text}"


def test_slack_sync_404s_on_foreign_dataset_for_plain_member(cross_tenant, monkeypatch):
    """Slack's sync route is deliberately open to non-admins
    (`_PERSONAL_PROVIDERS`), so the dataset gate is the ONLY thing standing
    between an ordinary member and another tenant's corpus."""
    from app.db.client import require_client

    require_client().table("company_members").update(
        {"role": "member"}
    ).eq("company_id", cross_tenant.ctx.company_id).execute()

    r = cross_tenant.ctx.client.post(
        "/v1/connectors/slack/sync-to-corpus",
        json={"dataset": cross_tenant.victim.slug},
    )
    assert r.status_code == 404, r.text
    _assert_corpus_intact(cross_tenant.victim)


def test_drive_sync_404s_on_foreign_slug_planted_in_connection_config(
    cross_tenant,
):
    """The Drive routes resolve their slug as `body.dataset or
    config["dataset"]`, and the OAuth authorize step writes that config value
    from an equally unchecked `?dataset=`. Gating only the body would leave the
    stored value as a live bypass, so the EFFECTIVE slug is what's gated."""
    from app import db
    from app.connectors import google_oauth

    db.patch_connection_config(
        cross_tenant.ctx.company_id,
        google_oauth.GOOGLE_DRIVE_PROVIDER,
        {"dataset": cross_tenant.victim.slug},
    )

    r = cross_tenant.ctx.client.post("/v1/connectors/google-drive/sync", json={})
    assert r.status_code == 404, r.text
    _assert_corpus_intact(cross_tenant.victim)
    assert cross_tenant.victim.slug not in [s for _c, s in cross_tenant.seeds]


# ───────────────── same-tenant happy paths (must stay green) ─────────────────
#
# These are the other half of the gate: they prove the 404s above come from the
# ownership check and not from the stubs, the RBAC gate, or a missing
# connection — the identical request against the caller's OWN slug still 200s.


@pytest.mark.parametrize("path,body", _SYNC_ROUTES, ids=_SYNC_ROUTE_IDS)
def test_sync_route_still_accepts_the_callers_own_dataset(cross_tenant, path, body):
    r = cross_tenant.ctx.client.post(path, json=_body_for(body, "acme"))
    assert r.status_code == 200, f"{path} rejected the caller's own slug: {r.text}"


def test_drive_sync_still_accepts_an_absent_dataset(cross_tenant):
    """`GoogleDriveSyncIn.dataset` is optional; when absent and nothing is
    stored on the connection, the slug stays None and the downstream fallback
    (`slug_for_company_id`) owns the decision. The gate must not turn that into
    a 404 or make the field required."""
    r = cross_tenant.ctx.client.post("/v1/connectors/google-drive/sync", json={})
    assert r.status_code == 200, r.text


def test_drive_files_still_accepts_an_absent_dataset(cross_tenant):
    r = cross_tenant.ctx.client.post(
        "/v1/connectors/google-drive/files", json={"files": []}
    )
    assert r.status_code == 200, r.text

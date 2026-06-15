"""Tests for the public share-viewer routes (P2-05):

    GET  /v1/design-agent/by-token/{token}
    POST /v1/design-agent/by-token/{token}/passcode

These are the ONLY no-auth Design Agent routes — the share_token is the access
primitive (F6), so they carry no `require_app_session` dependency and no
workspace filter. The security posture under test:

  - 404-not-401: bad token, private mode, not-ready, and random-UUID scan all
    return 404 (invisibility — AC3/AC4).
  - minimum disclosure: the body is EXACTLY {share_mode, requires_passcode,
    bundle_url, is_complete} — no prototype_id / prd_id / workspace_id leak (AC5).
  - rate-limit BEFORE hash compare: 6th wrong attempt in a minute is 429, not 401
    (AC6).

Runs fully in isolation against the in-memory FakeSupabaseClient — same fixture
shape as test_design_agent_routes.py, with the P2-06 sharing columns added to the
prototypes DDL. We reload app.db.prototypes → app.routes.design_agent → app.main
in dependency order so the route binds to the fake-wired helpers AND so the
module-level passcode rate-limit state is reset per test.
"""
from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# SQLite-compatible end-state of `prototypes` after the P1-06 + P2-06 migrations
# (mirrors test_db_prototypes_sharing.py — the fake exercises SQL semantics, not
# Postgres DDL). The five sharing columns are present so find_prototype_by_share_token
# can resolve a row.
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_DEFAULT_BUNDLE = "https://cdn.example/p/abc/index.html"


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables (sharing columns) + feature flag ON,
    with the design agent module stack reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)            # rebind require_client + fresh rate-limit state
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)           # rebind its `from app.db.prototypes import ...`
    import app.main as main_mod
    importlib.reload(main_mod)             # rebuild the app with the reloaded router

    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO session cookie — proves the routes need no auth."""
    return TestClient(env.main.app)


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed(
    *,
    share_mode: str,
    status: str = "ready",
    bundle_url: str | None = _DEFAULT_BUNDLE,
    passcode_hash: str | None = None,
    is_complete: int = 0,
    workspace_id: str = "app",
) -> str:
    """Insert one prototype row directly into the fake DB; return its share_token.

    Direct SQL (same approach as test_db_prototypes_sharing's CHECK test) keeps
    the seed independent of set_share_config's workspace guard — we are testing
    the public read path, which is workspace-blind on purpose.
    """
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    # The owning company so the resolver can map workspace_id → slug. Idempotent
    # so multiple seeds in one test (same workspace) don't collide on the PK.
    db.execute(
        "INSERT OR IGNORE INTO companies (id, slug, display_name) VALUES (?, ?, ?)",
        [workspace_id, f"slug-{workspace_id}", f"Company {workspace_id}"],
    )
    token = str(uuid.uuid4())
    db.execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " share_passcode_hash, bundle_url, is_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [1, workspace_id, 1, status, share_mode, token, passcode_hash, bundle_url, is_complete],
    )
    return token


# ─── GET /by-token/{token} ────────────────────────────────────────────────


def test_get_by_token_returns_200_unauthenticated_for_public_mode(unauth):
    # AC1 — public + ready resolves to 200 with NO auth cookie, NO redirect to
    # /sign-in, and NO Set-Cookie on the response.
    token = _seed(share_mode="public", is_complete=1)
    resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 200, resp.text
    assert resp.history == []                       # no redirect (e.g. to /sign-in)
    assert "set-cookie" not in {k.lower() for k in resp.headers}
    body = resp.json()
    assert body["share_mode"] == "public"
    assert body["requires_passcode"] is False
    assert body["bundle_url"] == _DEFAULT_BUNDLE
    assert body["is_complete"] is True


def test_response_body_keys_are_minimum_disclosure(unauth):
    # AC5 — EXACTLY the disclosed fields; no prototype_id / prd_id / workspace_id
    # leak. company_slug is the one intentional addition (cosmetic URL segment).
    token = _seed(share_mode="public")
    body = unauth.get(f"/v1/design-agent/by-token/{token}").json()
    assert set(body.keys()) == {
        "share_mode", "requires_passcode", "bundle_url", "is_complete", "company_slug",
    }


def test_get_by_token_returns_owning_company_slug(unauth):
    # company_slug is the cosmetic /p/<slug>/<token> segment — it must be the
    # OWNING company's slug, resolved from the prototype's workspace_id.
    token = _seed(share_mode="public", workspace_id="acme")
    body = unauth.get(f"/v1/design-agent/by-token/{token}").json()
    assert body["company_slug"] == "slug-acme"  # _seed creates company id=acme slug=slug-acme


def test_get_by_token_passcode_mode_withholds_bundle_url(unauth):
    # AC2 (resolver half) — passcode mode signals requires_passcode and returns a
    # null bundle_url until the passcode is verified.
    token = _seed(share_mode="passcode", passcode_hash="$argon2id$irrelevant")
    body = unauth.get(f"/v1/design-agent/by-token/{token}").json()
    assert body["share_mode"] == "passcode"
    assert body["requires_passcode"] is True
    assert body["bundle_url"] is None


def test_get_by_token_returns_404_for_private_mode(unauth):
    # AC3 — a private prototype is invisible: 404, not 401/403.
    token = _seed(share_mode="private", bundle_url=None)
    resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 404


def test_get_by_token_returns_404_for_missing_token(unauth):
    # AC4 — brute-force scan of a random UUID returns 404, not 401.
    resp = unauth.get(f"/v1/design-agent/by-token/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_by_token_returns_404_when_status_not_ready(unauth):
    # AC3/AC4 — a public row that is still generating is not viewable; 404 (we do
    # not disclose that it exists yet).
    token = _seed(share_mode="public", status="generating")
    resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 404


def test_get_by_token_returns_404_when_feature_flag_off(unauth, monkeypatch):
    # Feature-flag gate: off → 404 regardless of row state (request-time read).
    token = _seed(share_mode="public")
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 404


# ─── POST /by-token/{token}/passcode ───────────────────────────────────────


def test_verify_passcode_returns_bundle_url_on_correct_passcode(unauth, env):
    # AC2 (verify half) — correct passcode releases the bundle_url.
    h = env.proto.hash_share_passcode("hunter2")
    token = _seed(share_mode="passcode", passcode_hash=h, is_complete=1)
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "hunter2"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle_url"] == _DEFAULT_BUNDLE
    assert body["is_complete"] is True
    assert set(body.keys()) == {
        "share_mode", "requires_passcode", "bundle_url", "is_complete", "company_slug",
    }


def test_verify_passcode_returns_401_on_wrong_passcode(unauth, env):
    # AC6 — wrong passcode (under the limit) returns 401 invalid_passcode.
    h = env.proto.hash_share_passcode("hunter2")
    token = _seed(share_mode="passcode", passcode_hash=h)
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_passcode"


def test_verify_passcode_returns_429_after_5_failures(unauth, env):
    # AC6 — the 6th wrong attempt within the window returns 429, not 401. The
    # rate-limit check runs BEFORE the hash compare.
    h = env.proto.hash_share_passcode("hunter2")
    token = _seed(share_mode="passcode", passcode_hash=h)
    url = f"/v1/design-agent/by-token/{token}/passcode"
    for _ in range(5):
        assert unauth.post(url, json={"passcode": "nope"}).status_code == 401
    sixth = unauth.post(url, json={"passcode": "nope"})
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "Too many attempts"


def test_verify_passcode_404_for_non_passcode_token(unauth, env):
    # POSTing a passcode to a public-mode token is a 404 (not a passcode share).
    token = _seed(share_mode="public")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "x"}
    )
    assert resp.status_code == 404


def test_verify_passcode_empty_body_is_422(unauth, env):
    # PasscodeAttempt requires min_length=1 — an empty passcode is a validation
    # error, not a silent miss.
    h = env.proto.hash_share_passcode("hunter2")
    token = _seed(share_mode="passcode", passcode_hash=h)
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": ""}
    )
    assert resp.status_code == 422


# ─── Non-breakage (AC7) ─────────────────────────────────────────────────────


def test_existing_routes_still_resolve(env):
    # AC7 — the new routes are appended to the same router; the existing
    # generate / get_one routes and the include_router wiring are intact.
    paths = {r.path for r in env.main.app.router.routes}
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/{prototype_id}" in paths
    assert "/v1/design-agent/by-token/{token}" in paths
    assert "/v1/design-agent/by-token/{token}/passcode" in paths


# ─── bundle_url re-signed on read (permanent share, 24h stored URL) ──────────
#
# The stored bundle_url is a 24h-TTL signed URL minted at stage time, but a
# public/passcode share is permanent — serving the stored URL 403s the iframe
# once the TTL lapses. The public view paths must mint a FRESH signed URL per
# request from the bundle object path. We stub the routes module's bound
# `fresh_bundle_url` to assert the route (a) calls it with the prototype_id +
# current_checkpoint_id (so it can re-derive the object path) and (b) serves the
# freshly-signed value, NOT the stale stored bundle_url.


def _seed_with_checkpoint(
    *, share_mode: str, checkpoint_id: int = 99, passcode_hash: str | None = None
) -> str:
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " share_passcode_hash, bundle_url, current_checkpoint_id, is_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [1, "app", 1, "ready", share_mode, token, passcode_hash,
         "https://cdn.example/STALE-signed-url?token=expired", checkpoint_id, 1],
    )
    return token


def test_get_by_token_public_resigns_bundle_url(unauth, env, monkeypatch):
    fresh = "https://cdn.example/p/abc/index.html?token=FRESH"
    calls: list[dict] = []

    def _stub(*, prototype_id, checkpoint_id, stored_bundle_url):
        calls.append(
            {"prototype_id": prototype_id, "checkpoint_id": checkpoint_id,
             "stored_bundle_url": stored_bundle_url}
        )
        return fresh

    monkeypatch.setattr(env.routes, "fresh_bundle_url", _stub)
    token = _seed_with_checkpoint(share_mode="public", checkpoint_id=99)
    body = unauth.get(f"/v1/design-agent/by-token/{token}").json()
    # Served the freshly-signed URL, not the stored stale one.
    assert body["bundle_url"] == fresh
    assert "STALE" not in body["bundle_url"]
    # Called with the inputs needed to re-derive prototypes/<pid>/<cid>/index.html.
    assert len(calls) == 1
    assert calls[0]["checkpoint_id"] == 99
    assert "STALE" in calls[0]["stored_bundle_url"]


def test_verify_passcode_resigns_bundle_url(unauth, env, monkeypatch):
    fresh = "https://cdn.example/p/abc/index.html?token=FRESH"
    monkeypatch.setattr(
        env.routes, "fresh_bundle_url",
        lambda *, prototype_id, checkpoint_id, stored_bundle_url: fresh,
    )
    h = env.proto.hash_share_passcode("hunter2")
    token = _seed_with_checkpoint(share_mode="passcode", checkpoint_id=42, passcode_hash=h)
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "hunter2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["bundle_url"] == fresh


def test_public_bundle_url_helper_falls_back_without_checkpoint(env):
    # When the row has no current_checkpoint_id there is no object path to derive,
    # so the helper returns the stored URL unchanged (never None-crashes).
    row = {"id": 7, "bundle_url": "https://cdn.example/stored", "current_checkpoint_id": None}
    assert env.routes._public_bundle_url(row) == "https://cdn.example/stored"


def test_fresh_bundle_url_no_bucket_returns_stored():
    # On the filesystem/dev backend (no SUPABASE_STORAGE_BUCKET) the stored URL is a
    # stable public/file:// URL that never expires, so fresh_bundle_url returns it.
    from app.design_agent import storage

    assert storage._bucket_name() is None  # default env: no bucket configured
    out = storage.fresh_bundle_url(
        prototype_id=1, checkpoint_id=2, stored_bundle_url="file:///tmp/x/index.html"
    )
    assert out == "file:///tmp/x/index.html"


def test_fresh_bundle_url_signs_from_object_path_with_bucket(monkeypatch):
    # With a bucket configured, fresh_bundle_url signs the derived object path
    # (prototypes/<pid>/<cid>/index.html) afresh and returns the new signed URL,
    # NOT the stale stored one.
    from app.design_agent import storage

    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "prototypes-bucket")
    signed_paths: list[str] = []

    class _FakeStorageObj:
        def create_signed_url(self, *, path, expires_in):
            signed_paths.append(path)
            return {"signedURL": f"https://signed.example/{path}?fresh=1"}

    class _FakeStorage:
        def from_(self, bucket):
            assert bucket == "prototypes-bucket"
            return _FakeStorageObj()

    class _FakeClient:
        storage = _FakeStorage()

    monkeypatch.setattr("app.db.client.require_client", lambda: _FakeClient())
    out = storage.fresh_bundle_url(
        prototype_id=11, checkpoint_id=22, stored_bundle_url="https://STALE"
    )
    assert signed_paths == ["prototypes/11/22/index.html"]
    assert out == "https://signed.example/prototypes/11/22/index.html?fresh=1"
    assert "STALE" not in out

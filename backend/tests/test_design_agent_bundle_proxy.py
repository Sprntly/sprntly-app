"""Tests for the bundle PROXY router (Option B — same-origin serving).

Covers the authorizing streaming proxy that serves every prototype bundle object
through one route family:

    GET  /v1/design-agent/by-token/{token}/bundle/{asset_path:path}   public/passcode
    GET  /v1/design-agent/{prototype_id}/bundle/{asset_path:path}     authed twin
    POST /v1/design-agent/{prototype_id}/view-grant                   mint grant
    POST /v1/design-agent/by-token/{token}/passcode                   passcode → grant

The security posture (asset-level — BOTH index.html AND a deep assets/x.js):
  - traversal denied (no storage read);
  - per-mode auth (public/passcode/authed) re-resolved per object;
  - revocation under a still-valid grant (flip to private ⇒ next asset denied);
  - view-grant ownership (non-owner ⇒ 404);
  - token-secret fail-closed (empty secret ⇒ mint+validate both refuse);
  - URL↔grant equality (grant for one prototype rejected on another's URL);
  - MIME per object; Range ⇒ 206 + Content-Range + Accept-Ranges;
  - no-bypass (read responses carry a proxy URL, no signed/direct object URL).

Runs against the in-memory FakeSupabaseClient + the filesystem storage fallback
(no SUPABASE_STORAGE_BUCKET → bundle objects are read from storage_dir). The grant
cookies are read off the mint response and re-sent explicitly as a Cookie header
(the cookie Path is the prod nginx-prefixed path, which the bare TestClient jar
would not match — the proxy logic is path-prefix-agnostic so this is faithful).
"""
from __future__ import annotations

import re as _re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# End-state prototypes DDL (sharing columns) — mirrors the public-routes suite.
_DDL = """
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

_INDEX_HTML = "<!doctype html><html><body><script src=\"./assets/app.js\"></script></body></html>"
_APP_JS = "console.log('hello from the bundle');\n" * 200  # big enough for a Range slice


@pytest.fixture
def env(isolated_settings, monkeypatch, tmp_path):
    """Feature flag ON + token secret SET + storage_dir at a tmp dir, with the DA
    module stack + the bundle router reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("DESIGN_AGENT_TOKEN_SECRET", "test-grant-secret")
    # Make require_company verify minted HS256 bearers.
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "shared-hs256-test-secret")

    # NOTE: do NOT importlib.reload() these modules — reload is never torn down
    # and rebinds the shared `settings` singleton + module globals, polluting the
    # rest of the suite (caused 24 spurious vite/typecheck reds in full-suite order).
    # The feature flag is read from os.environ at call time (_feature_enabled) and
    # the token secret from settings at call time (_require_token_secret), so
    # monkeypatch.setenv + setattr on the shared singleton is sufficient.
    # Point storage at a tmp dir (filesystem fallback; no Supabase bucket).
    import app.design_agent.storage as storage_mod
    monkeypatch.setattr(storage_mod.settings, "storage_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage_mod.settings, "design_agent_token_secret", "test-grant-secret", raising=False)

    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod.settings, "supabase_jwt_secret", "shared-hs256-test-secret", raising=False)

    import app.db.prototypes as proto_mod
    # Reset the per-process in-memory rate limiter for test isolation (the removed
    # importlib.reload used to do this incidentally; the 429 test needs a fresh store).
    proto_mod._passcode_failures.clear()
    import app.routes.design_agent as routes_mod
    import app.routes.design_agent_bundle as bundle_mod
    # bundle_mod closes over `storage` + `settings`; align its secret too.
    monkeypatch.setattr(bundle_mod.settings, "design_agent_token_secret", "test-grant-secret", raising=False)
    monkeypatch.setattr(bundle_mod.storage.settings, "storage_dir", str(tmp_path), raising=False)
    import app.main as main_mod

    return SimpleNamespace(
        proto=proto_mod, routes=routes_mod, bundle=bundle_mod, main=main_mod,
        storage=storage_mod, tmp=Path(tmp_path),
    )


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(env.main.app)


# ─── seeding helpers ─────────────────────────────────────────────────────────

_SUPABASE_SECRET = "shared-hs256-test-secret"
_OWNER_COMPANY = "co-owner"
_OWNER_USER = "user-owner"
_OTHER_COMPANY = "co-other"
_OTHER_USER = "user-other"


def _mint_bearer(sub: str) -> str:
    import time

    import jwt as pyjwt

    return pyjwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 300},
        _SUPABASE_SECRET, algorithm="HS256",
    )


def _seed_company(company_id: str, user_id: str) -> None:
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT OR IGNORE INTO companies (id, slug, display_name) VALUES (?, ?, ?)",
        [company_id, f"slug-{company_id}", company_id],
    )
    db.execute(
        "INSERT OR IGNORE INTO company_members (id, company_id, user_id, role) VALUES (?, ?, ?, ?)",
        [f"cm-{company_id}-{user_id}", company_id, user_id, "owner"],
    )
    db.execute("INSERT OR IGNORE INTO profiles (id) VALUES (?)", [user_id])


def _stage_bundle_files(env, prototype_id: int, checkpoint_id: int) -> None:
    """Write index.html + assets/app.js into the filesystem bundle prefix."""
    prefix = env.tmp / "prototypes" / str(prototype_id) / str(checkpoint_id)
    (prefix / "assets").mkdir(parents=True, exist_ok=True)
    (prefix / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (prefix / "assets" / "app.js").write_text(_APP_JS, encoding="utf-8")


def _seed_prototype(
    env,
    *,
    share_mode: str = "private",
    status: str = "ready",
    workspace_id: str = _OWNER_COMPANY,
    passcode_hash: str | None = None,
    checkpoint_id: int = 1,
    stage_files: bool = True,
) -> tuple[int, str]:
    """Insert a prototype row + (optionally) stage its bundle files. Returns
    (prototype_id, share_token)."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    token = str(uuid.uuid4())
    cur = db.execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " share_passcode_hash, bundle_url, current_checkpoint_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [1, workspace_id, 1, status, share_mode, token, passcode_hash,
         "http://app/_da-bundle/x", checkpoint_id],
    )
    pid = cur.lastrowid
    # Reflect the no-bypass migration: the stored bundle_url is the proxy base
    # (what complete_prototype now persists), not a signed/direct object URL.
    db.execute(
        "UPDATE prototypes SET bundle_url = ? WHERE id = ?",
        [env.storage.authed_bundle_url(pid), pid],
    )
    if stage_files:
        _stage_bundle_files(env, pid, checkpoint_id)
    return pid, token


def _grant_cookie_from(resp, name: str) -> str:
    """Pull the raw grant cookie value from a mint response's Set-Cookie header.

    The cookie Path is the prod nginx-prefixed path the bare TestClient jar will
    not match, so tests re-send the value explicitly as a Cookie header."""
    sc = resp.headers.get("set-cookie", "")
    m = _re.search(rf"{name}=([^;]+)", sc)
    assert m, f"no {name} in Set-Cookie: {sc!r}"
    return m.group(1)


# ─── TRAVERSAL ───────────────────────────────────────────────────────────────

_TRAVERSALS = [
    "../etc/passwd",
    "..%2f..%2fetc/passwd",
    "assets/../../etc/passwd",
    "/etc/passwd",
    "..\\..\\etc",
    "%2e%2e%2fpasswd",
    "%2e%2e/%2e%2e/passwd",
    "assets/app.js%0d%0aX-Injected:%201",  # CRLF header-injection (percent-encoded)
    "assets/app.js%0afoo",                  # bare LF
    "assets/app.js%00.js",                  # NUL
]


@pytest.mark.parametrize("evil", _TRAVERSALS)
def test_traversal_denied_public(client, env, evil):
    _, token = _seed_prototype(env, share_mode="public")
    resp = client.get(f"/v1/design-agent/by-token/{token}/bundle/{evil}")
    assert resp.status_code == 404, (evil, resp.status_code)


def test_no_storage_read_on_traversal(client, env, monkeypatch):
    # A traversal attempt must 404 BEFORE any storage object read (no sign/open).
    _, token = _seed_prototype(env, share_mode="public")
    calls: list = []
    orig = env.storage.serve_bundle_object

    async def _spy(**kw):
        calls.append(kw)
        return await orig(**kw)

    monkeypatch.setattr(env.bundle.storage, "serve_bundle_object", _spy)
    resp = client.get(f"/v1/design-agent/by-token/{token}/bundle/..%2f..%2fetc/passwd")
    assert resp.status_code == 404
    assert calls == [], "serve_bundle_object was called for a traversal path"


# ─── PUBLIC mode (asset-level) ───────────────────────────────────────────────


def test_public_serves_index_and_deep_asset(client, env):
    _, token = _seed_prototype(env, share_mode="public")
    r1 = client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html")
    assert r1.status_code == 200, r1.text
    assert "text/html" in r1.headers["content-type"]
    assert r1.headers["cache-control"] == "public, max-age=60, must-revalidate"
    r2 = client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js")
    assert r2.status_code == 200
    assert "javascript" in r2.headers["content-type"]


def test_public_private_row_404(client, env):
    _, token = _seed_prototype(env, share_mode="private")
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html").status_code == 404
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js").status_code == 404


def test_public_not_ready_404(client, env):
    _, token = _seed_prototype(env, share_mode="public", status="generating")
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html").status_code == 404


def test_public_unknown_token_404(client, env):
    # An unknown/bogus share_token resolves to no row → 404 (not 500, not 401):
    # invisibility, same as a wrong token. (Replaces the deleted re-sign tests'
    # missing-row edge.)
    bogus = str(uuid.uuid4())
    assert client.get(f"/v1/design-agent/by-token/{bogus}/bundle/index.html").status_code == 404
    assert client.get(f"/v1/design-agent/by-token/{bogus}/bundle/assets/app.js").status_code == 404


def test_public_no_checkpoint_404(client, env):
    # A ready, public row whose current_checkpoint_id is NULL has no bundle object
    # path to serve → 404, NOT a 500 (replaces the deleted no-checkpoint fallback
    # test). Both the public and authed serve paths gate on _checkpoint_for_row.
    _, token = _seed_prototype(
        env, share_mode="public", checkpoint_id=None, stage_files=False,
    )
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html").status_code == 404
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js").status_code == 404


def test_public_revocation_flip_to_private(client, env):
    # PUBLIC index 200 → set private → next asset 404 (per-object DB re-read).
    pid, token = _seed_prototype(env, share_mode="public")
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html").status_code == 200
    env.proto.set_share_config(prototype_id=pid, workspace_id=_OWNER_COMPANY, share_mode="private")
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js").status_code == 404


# ─── PASSCODE mode (asset-level) ─────────────────────────────────────────────


def test_passcode_without_grant_denied(client, env):
    h = env.proto.hash_share_passcode("hunter2")
    _, token = _seed_prototype(env, share_mode="passcode", passcode_hash=h)
    # No grant cookie → 404 for BOTH index and a deep asset.
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html").status_code == 404
    assert client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js").status_code == 404


def test_passcode_grant_then_serve(client, env):
    h = env.proto.hash_share_passcode("hunter2")
    _, token = _seed_prototype(env, share_mode="passcode", passcode_hash=h)
    grant_resp = client.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "hunter2"}
    )
    # The passcode-verify route returns the view body (200) AND sets the grant.
    assert grant_resp.status_code == 200, grant_resp.text
    cookie = _grant_cookie_from(grant_resp, "da_share_grant")
    headers = {"Cookie": f"da_share_grant={cookie}"}
    r1 = client.get(f"/v1/design-agent/by-token/{token}/bundle/index.html", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.headers["cache-control"] == "private, no-store"
    assert "Cookie" in r1.headers.get("vary", "")
    r2 = client.get(f"/v1/design-agent/by-token/{token}/bundle/assets/app.js", headers=headers)
    assert r2.status_code == 200


def test_passcode_wrong_then_rate_limit(client, env):
    h = env.proto.hash_share_passcode("hunter2")
    _, token = _seed_prototype(env, share_mode="passcode", passcode_hash=h)
    url = f"/v1/design-agent/by-token/{token}/passcode"
    for _ in range(5):
        assert client.post(url, json={"passcode": "nope"}).status_code == 401
    assert client.post(url, json={"passcode": "nope"}).status_code == 429


# ─── AUTHED twin (asset-level) ───────────────────────────────────────────────


def _view_grant(client, env, prototype_id: int, sub: str = _OWNER_USER) -> str:
    resp = client.post(
        f"/v1/design-agent/{prototype_id}/view-grant",
        headers={"Authorization": f"Bearer {_mint_bearer(sub)}"},
    )
    assert resp.status_code == 204, resp.text
    return _grant_cookie_from(resp, "da_view_grant")


def test_authed_unauth_public_path_401(client, env):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    # No grant cookie → 401 (BOTH index + deep asset).
    assert client.get(f"/v1/design-agent/{pid}/bundle/index.html").status_code == 401
    assert client.get(f"/v1/design-agent/{pid}/bundle/assets/app.js").status_code == 401


def test_authed_with_valid_grant_serves(client, env):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    cookie = _view_grant(client, env, pid)
    headers = {"Cookie": f"da_view_grant={cookie}"}
    r1 = client.get(f"/v1/design-agent/{pid}/bundle/index.html", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.headers["cache-control"] == "private, no-store"
    assert "Cookie" in r1.headers.get("vary", "")
    r2 = client.get(f"/v1/design-agent/{pid}/bundle/assets/app.js", headers=headers)
    assert r2.status_code == 200


def test_view_grant_non_owner_404(client, env):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    _seed_company(_OTHER_COMPANY, _OTHER_USER)
    pid, _ = _seed_prototype(env, share_mode="private", workspace_id=_OWNER_COMPANY)
    resp = client.post(
        f"/v1/design-agent/{pid}/view-grant",
        headers={"Authorization": f"Bearer {_mint_bearer(_OTHER_USER)}"},
    )
    assert resp.status_code == 404


def test_view_grant_unauth_401(client, env):
    pid, _ = _seed_prototype(env, share_mode="private")
    # No bearer → require_company 401.
    assert client.post(f"/v1/design-agent/{pid}/view-grant").status_code == 401


def test_view_grant_cookie_is_host_only_and_path_scoped(client, env):
    # Option A (v3 §1.6): the da_view_grant cookie MUST be HOST-ONLY (NO Domain
    # attr ⇒ no cookie_domain dependency, no broadening) and Path-scoped to THIS
    # prototype's bundle route, HttpOnly + SameSite=Lax. Locks the host-only
    # property in CI so a regression to a domain cookie fails the suite.
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    resp = client.post(
        f"/v1/design-agent/{pid}/view-grant",
        headers={"Authorization": f"Bearer {_mint_bearer(_OWNER_USER)}"},
    )
    assert resp.status_code == 204
    sc = resp.headers.get("set-cookie", "")
    assert "da_view_grant=" in sc
    assert "domain=" not in sc.lower()                       # HOST-ONLY — no Domain attr
    assert f"/v1/design-agent/{pid}/bundle" in sc            # path-scoped to this proto
    assert "httponly" in sc.lower()
    assert "samesite=lax" in sc.lower()


def test_view_grant_over_limit_429(client, env):
    # Repeated non-owner mint attempts register failures → 6th is 429.
    _seed_company(_OTHER_COMPANY, _OTHER_USER)
    pid, _ = _seed_prototype(env, share_mode="private", workspace_id=_OWNER_COMPANY)
    url = f"/v1/design-agent/{pid}/view-grant"
    hdr = {"Authorization": f"Bearer {_mint_bearer(_OTHER_USER)}"}
    for _ in range(5):
        assert client.post(url, headers=hdr).status_code == 404
    assert client.post(url, headers=hdr).status_code == 429


def test_authed_revocation_under_valid_grant(client, env):
    # THE CRUX: authed asset 200 with a valid grant → flip share_mode (public) →
    # next asset with the SAME unexpired grant ⇒ 404 (per-object DB re-read, not
    # the grant, is the authorization gate).
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    cookie = _view_grant(client, env, pid)
    headers = {"Cookie": f"da_view_grant={cookie}"}
    assert client.get(f"/v1/design-agent/{pid}/bundle/index.html", headers=headers).status_code == 200
    # Owner flips share_mode AFTER the grant was minted (it bound 'private').
    env.proto.set_share_config(prototype_id=pid, workspace_id=_OWNER_COMPANY, share_mode="public")
    r = client.get(f"/v1/design-agent/{pid}/bundle/assets/app.js", headers=headers)
    assert r.status_code == 404, "grant kept serving after a share-mode flip"


def test_authed_url_grant_mismatch(client, env):
    # A valid grant for prototype A used on prototype B's URL ⇒ rejected.
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid_a, _ = _seed_prototype(env, share_mode="private", checkpoint_id=1)
    pid_b, _ = _seed_prototype(env, share_mode="private", checkpoint_id=2)
    cookie_a = _view_grant(client, env, pid_a)
    headers = {"Cookie": f"da_view_grant={cookie_a}"}
    r = client.get(f"/v1/design-agent/{pid_b}/bundle/index.html", headers=headers)
    assert r.status_code == 401


def test_authed_forged_grant_401(client, env):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    headers = {"Cookie": "da_view_grant=forged.deadbeef"}
    assert client.get(f"/v1/design-agent/{pid}/bundle/index.html", headers=headers).status_code == 401


# ─── TOKEN-SECRET FAIL-CLOSED ────────────────────────────────────────────────


def test_token_secret_unset_mint_fails_closed(client, env, monkeypatch):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    monkeypatch.setattr(env.bundle.settings, "design_agent_token_secret", "", raising=False)
    resp = client.post(
        f"/v1/design-agent/{pid}/view-grant",
        headers={"Authorization": f"Bearer {_mint_bearer(_OWNER_USER)}"},
    )
    assert resp.status_code == 503
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_token_secret_unset_validate_fails_closed(client, env, monkeypatch):
    # Mint WITH a secret, then drop the secret and present the (now-unverifiable)
    # grant: validate must fail closed (503) — never serve with a forgeable grant.
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    cookie = _view_grant(client, env, pid)
    monkeypatch.setattr(env.bundle.settings, "design_agent_token_secret", "", raising=False)
    r = client.get(
        f"/v1/design-agent/{pid}/bundle/index.html",
        headers={"Cookie": f"da_view_grant={cookie}"},
    )
    assert r.status_code == 503


# ─── MIME + RANGE ────────────────────────────────────────────────────────────


def test_mime_per_object(client, env):
    _, token = _seed_prototype(env, share_mode="public")
    assert "text/html" in client.get(
        f"/v1/design-agent/by-token/{token}/bundle/index.html"
    ).headers["content-type"]
    assert "javascript" in client.get(
        f"/v1/design-agent/by-token/{token}/bundle/assets/app.js"
    ).headers["content-type"]


def test_range_request_206(client, env):
    _, token = _seed_prototype(env, share_mode="public")
    r = client.get(
        f"/v1/design-agent/by-token/{token}/bundle/assets/app.js",
        headers={"Range": "bytes=0-99"},
    )
    assert r.status_code == 206, r.text
    assert "content-range" in {k.lower() for k in r.headers}
    assert r.headers.get("accept-ranges") == "bytes"
    assert len(r.content) == 100


# ─── NO-BYPASS (read responses carry a proxy URL, no signed/direct object URL) ─


def test_no_bypass_public_view_returns_proxy_url(client, env):
    pid, token = _seed_prototype(env, share_mode="public")
    body = client.get(f"/v1/design-agent/by-token/{token}").json()
    url = body["bundle_url"]
    assert "/_da-bundle/v1/design-agent/by-token/" in url
    assert token in url
    assert "supabase" not in url
    assert "token=" not in url  # no signed-URL query param


def test_no_bypass_passcode_view_returns_proxy_url(client, env):
    h = env.proto.hash_share_passcode("hunter2")
    pid, token = _seed_prototype(env, share_mode="passcode", passcode_hash=h)
    resp = client.post(
        f"/v1/design-agent/by-token/{token}/passcode", json={"passcode": "hunter2"}
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["bundle_url"]
    assert "/_da-bundle/v1/design-agent/by-token/" in url
    assert token in url
    assert "supabase" not in url
    assert "token=" not in url


def test_no_bypass_authed_row_returns_proxy_url(client, env):
    _seed_company(_OWNER_COMPANY, _OWNER_USER)
    pid, _ = _seed_prototype(env, share_mode="private")
    resp = client.get(
        f"/v1/design-agent/{pid}",
        headers={"Authorization": f"Bearer {_mint_bearer(_OWNER_USER)}"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["bundle_url"]
    assert "/_da-bundle/v1/design-agent/" in url and f"/{pid}/bundle/" in url
    assert "supabase" not in url
    assert "token=" not in url

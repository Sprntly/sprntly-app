"""Screenshot-as-context intake: POST /v1/design-agent/uploads/screenshot,
the stage_screenshot/read_screenshot storage helpers, and the screenshot_key
threading through GenerateRequest → start_prototype.

Two test layers, mirroring the sibling suites:

1. **Pure storage units** — stage/read round-trip on BOTH backends (filesystem
   fallback + mocked Supabase client), key shape, and the workspace-prefix
   isolation refusal. Settings are patched on the same `storage.settings`
   reference the module holds (test_design_agent_storage.py convention).
2. **Route layer** (fake Supabase, mirrors test_design_agent_routes.py): the
   upload route's guards (empty/oversize/magic-byte sniff), auth + feature-flag
   + Origin gates, catch-all reachability, and the /generate ownership check +
   row persistence.

NOTE: `app/design_agent/screenshot.py` (server-side preview CAPTURE of staged
bundles) is unrelated to the user-uploaded screenshots exercised here.
"""
from __future__ import annotations

import importlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.design_agent.storage as storage
from tests.conftest import _TEST_COMPANY_ID

# SQLite-compatible translation of the prototypes DDL (mirrors
# test_design_agent_routes.py) + the additive screenshot_key column this
# suite exercises.
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
    screenshot_key         TEXT,
    created_by_user_id     TEXT,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
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

# Minimal valid byte signatures (the sniffer reads a 12-byte prefix; the tail is
# arbitrary — the server stores bytes, it never decodes the image).
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"png-tail-bytes"
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"jpeg-tail-bytes"
_WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 webp-tail"

_KEY_RE = re.compile(
    r"^uploads/(?P<ws>[^/]+)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(png|jpg|webp)$"
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260716130000_prototypes_screenshot_key.sql"
)


# ─── fixtures (mirror test_design_agent_routes.py) ───────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import importlib as _il
    import app.config as _config_mod
    _il.reload(_config_mod)
    import app.connectors.tokens as _tokens_mod
    _il.reload(_tokens_mod)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) resolving _TEST_COMPANY_ID."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient without any Authorization header."""
    return TestClient(env.main.app)


@pytest.fixture
def fs_storage(monkeypatch, tmp_path):
    """Filesystem-fallback storage: no bucket, storage_dir → tmp_path."""
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)
    monkeypatch.setattr(storage.settings, "storage_dir", str(tmp_path), raising=False)
    return tmp_path


def _mock_supabase_storage(monkeypatch) -> MagicMock:
    """Wire SUPABASE_STORAGE_BUCKET + a mock storage client; return the
    .from_() mock (test_design_agent_storage.py convention)."""
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "proto-bundles")
    storage_obj = MagicMock()
    client = MagicMock()
    client.storage.from_.return_value = storage_obj
    import app.db.client as db_client_mod

    monkeypatch.setattr(db_client_mod, "require_client", lambda: client)
    return storage_obj


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _stub_generate(monkeypatch, routes_mod):
    """Patch routes.generate_prototype so no agent loop / vite build runs."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="complete", iters=1), {}

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)
    return calls


def _post_screenshot(c: TestClient, data: bytes, filename="shot.png", ct="image/png", **kw):
    return c.post(
        "/v1/design-agent/uploads/screenshot",
        files={"file": (filename, data, ct)},
        **kw,
    )


# ─── storage helpers: key shape + round-trip (AC1) ───────────────────────────


async def test_stage_screenshot_key_shape_and_roundtrip(fs_storage, monkeypatch):
    # Filesystem backend: key shape + byte-identical round-trip per media type.
    for data, media_type, ext in (
        (_PNG_BYTES, "image/png", "png"),
        (_JPEG_BYTES, "image/jpeg", "jpg"),
        (_WEBP_BYTES, "image/webp", "webp"),
    ):
        key = await storage.stage_screenshot(
            workspace_id="ws-a", data=data, media_type=media_type
        )
        m = _KEY_RE.match(key)
        assert m and m.group("ws") == "ws-a" and key.endswith(f".{ext}"), key
        got, got_mt = await storage.read_screenshot(key=key, workspace_id="ws-a")
        assert got == data
        assert got_mt == media_type

    # Supabase backend (client mocked): upload carries the key + sniffed
    # content-type; download round-trips the same bytes.
    storage_obj = _mock_supabase_storage(monkeypatch)
    key = await storage.stage_screenshot(
        workspace_id="ws-a", data=_PNG_BYTES, media_type="image/png"
    )
    assert _KEY_RE.match(key) and key.startswith("uploads/ws-a/")
    upload_kwargs = storage_obj.upload.call_args.kwargs
    assert upload_kwargs["path"] == key
    assert upload_kwargs["file"] == _PNG_BYTES
    assert upload_kwargs["file_options"]["content-type"] == "image/png"
    storage_obj.download.return_value = _PNG_BYTES
    got, got_mt = await storage.read_screenshot(key=key, workspace_id="ws-a")
    assert got == _PNG_BYTES and got_mt == "image/png"
    storage_obj.download.assert_called_once_with(key)


async def test_read_screenshot_refuses_cross_workspace_key(fs_storage):
    # Isolation boundary: a key from another tenant's prefix raises ValueError —
    # never an empty/404-style return (that would mask an isolation bug).
    key = await storage.stage_screenshot(
        workspace_id="ws-a", data=_PNG_BYTES, media_type="image/png"
    )
    # Same workspace succeeds (proves the refusal below is the prefix check).
    got, _ = await storage.read_screenshot(key=key, workspace_id="ws-a")
    assert got == _PNG_BYTES
    with pytest.raises(ValueError):
        await storage.read_screenshot(key=key, workspace_id="ws-b")
    # Traversal inside a syntactically-owned prefix must not escape either.
    with pytest.raises(ValueError):
        await storage.read_screenshot(
            key="uploads/ws-b/../ws-a/whatever.png", workspace_id="ws-b"
        )


# ─── upload route (AC1, AC2) ─────────────────────────────────────────────────


def test_upload_route_returns_key_and_media_type(env, client, fs_storage):
    resp = _post_screenshot(client, _PNG_BYTES)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_type"] == "image/png"
    assert body["screenshot_key"].startswith(f"uploads/{_TEST_COMPANY_ID}/")
    assert _KEY_RE.match(body["screenshot_key"])
    # The staged object is byte-identical on read (route half of the round-trip).
    import asyncio

    got, got_mt = asyncio.run(
        storage.read_screenshot(
            key=body["screenshot_key"], workspace_id=_TEST_COMPANY_ID
        )
    )
    assert got == _PNG_BYTES and got_mt == "image/png"


def test_upload_rejects_empty_and_oversize(env, client, fs_storage):
    resp = _post_screenshot(client, b"")
    assert resp.status_code == 400, resp.text

    # 8 MB exactly passes; one byte over → 413.
    max_bytes = 8 * 1024 * 1024
    exact = _PNG_BYTES[:8] + b"\x00" * (max_bytes - 8)
    assert len(exact) == max_bytes
    assert _post_screenshot(client, exact).status_code == 200
    assert _post_screenshot(client, exact + b"\x00").status_code == 413


def test_upload_sniffs_magic_bytes_not_declared_type(env, client, fs_storage):
    # Text bytes declared as PNG → 422: no allowed signature matches.
    resp = _post_screenshot(client, b"just some text, not an image")
    assert resp.status_code == 422, resp.text

    # JPEG bytes declared/named as PNG → accepted, stored as the SNIFFED type.
    resp = _post_screenshot(client, _JPEG_BYTES, filename="shot.png", ct="image/png")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_type"] == "image/jpeg"
    assert body["screenshot_key"].endswith(".jpg")

    # WebP accepted too (third allowed signature).
    resp = _post_screenshot(client, _WEBP_BYTES, filename="shot.webp", ct="image/webp")
    assert resp.status_code == 200, resp.text
    assert resp.json()["media_type"] == "image/webp"


# ─── gates (AC3) ─────────────────────────────────────────────────────────────


def test_upload_requires_session_and_feature_flag(env, client, unauth, fs_storage, monkeypatch):
    # Unauthenticated → 401 (require_company).
    assert _post_screenshot(unauth, _PNG_BYTES).status_code == 401
    # Feature flag off → 404 (invisible), even authed.
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert _post_screenshot(client, _PNG_BYTES).status_code == 404


def test_upload_rejects_cross_origin(env, client, fs_storage):
    # A foreign Origin is rejected by require_same_origin (403) before the
    # handler runs — the CSRF gate for authed mutating routes.
    resp = _post_screenshot(
        client, _PNG_BYTES, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403, resp.text


def test_upload_route_reachable_alongside_catchall(env, client, fs_storage):
    # AC7: the 2-segment POST resolves to the upload handler (not a 404/405 from
    # mis-ordering), while the single-segment GET /{prototype_id} catch-all
    # still matches a seeded row.
    resp = _post_screenshot(client, _PNG_BYTES)
    assert resp.status_code == 200, resp.text
    assert "screenshot_key" in resp.json()

    pid = env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    assert client.get(f"/v1/design-agent/{pid}").status_code == 200


# ─── generate threading (AC4, AC6) ───────────────────────────────────────────


def test_generate_403_on_foreign_screenshot_key(env, client, fs_storage, monkeypatch):
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)

    # Foreign workspace prefix → 403 with the ownership error, no row inserted.
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_key": "uploads/other-ws/abc.png"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"error": "screenshot_key_forbidden"}

    # A non-upload key (path shape games) is refused the same way.
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_key": "prototypes/1/1/index.html"},
    )
    assert resp.status_code == 403, resp.text

    # A valid same-workspace key generates and persists on the row.
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_key": key},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["screenshot_key"] == key


def test_generate_without_screenshot_key_unchanged(env, client, monkeypatch):
    # AC6 pin: a keyless generate behaves exactly as before — 200, generating
    # row, and the row's screenshot_key reads NULL.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    row = env.proto.get_prototype(
        prototype_id=body["prototype_id"], workspace_id=_TEST_COMPANY_ID
    )
    assert row["screenshot_key"] is None


# ─── start_prototype threading (AC4, conditional-write pin) ──────────────────


def test_start_prototype_persists_screenshot_key(env):
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    pid = env.proto.start_prototype(
        prd_id=1,
        workspace_id=_TEST_COMPANY_ID,
        template_version=1,
        screenshot_key=key,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["screenshot_key"] == key

    # Default (arg omitted) stays honest-NULL.
    pid2 = env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    row2 = env.proto.get_prototype(prototype_id=pid2, workspace_id=_TEST_COMPANY_ID)
    assert row2["screenshot_key"] is None


def test_start_prototype_payload_omits_null_screenshot_key(env, monkeypatch):
    # Conditional-write pin: a keyless insert's payload carries NO screenshot_key
    # key at all (optional-column convention — environments whose prototypes
    # schema predates the column must keep inserting cleanly).
    captured: list[dict] = []
    real_require_client = env.proto.require_client

    class _TableSpy:
        def __init__(self, table):
            self._table = table

        def insert(self, payload):
            captured.append(payload)
            return self._table.insert(payload)

        def __getattr__(self, name):
            return getattr(self._table, name)

    class _ClientSpy:
        def __init__(self, inner):
            self._inner = inner

        def table(self, name):
            t = self._inner.table(name)
            return _TableSpy(t) if name == "prototypes" else t

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(
        env.proto, "require_client", lambda: _ClientSpy(real_require_client())
    )

    env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    assert captured and "screenshot_key" not in captured[-1]

    env.proto.start_prototype(
        prd_id=1,
        workspace_id=_TEST_COMPANY_ID,
        template_version=1,
        screenshot_key=f"uploads/{_TEST_COMPANY_ID}/x.png",
    )
    assert captured[-1].get("screenshot_key") == f"uploads/{_TEST_COMPANY_ID}/x.png"


# ─── migration (AC5) ─────────────────────────────────────────────────────────


def test_screenshot_key_migration_is_idempotent():
    # String-level check (no live Postgres in this lane, per the sibling
    # migration tests' convention): additive `add column if not exists` on
    # prototypes only, so a double-apply is a no-op.
    assert _MIGRATION_PATH.exists()
    sql = "\n".join(
        line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()
    ).lower()
    assert "alter table prototypes" in sql
    assert "add column if not exists screenshot_key" in sql
    # Additive-only: nothing destructive, nothing on sibling tables.
    for forbidden in ("drop ", "delete ", "update ", "alter table prds", "alter table briefs"):
        assert forbidden not in sql

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

import base64
import importlib
import logging
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
CREATE TABLE prototype_screenshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    storage_key   TEXT NOT NULL,
    position      INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
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
    import app.db.prototype_screenshots as screenshots_mod
    importlib.reload(screenshots_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(
        proto=proto_mod, screenshots=screenshots_mod, routes=routes_mod,
        main=main_mod, db=db_mod,
    )


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
    # Same AC3 ownership-check contract as before this ticket, plural wire
    # shape: each value is wrapped as a 1-item screenshot_keys list.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)

    # Foreign workspace prefix → 403 with the ownership error, no row inserted.
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": ["uploads/other-ws/abc.png"]},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"error": "screenshot_key_forbidden"}

    # A non-upload key (path shape games) is refused the same way.
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": ["prototypes/1/1/index.html"]},
    )
    assert resp.status_code == 403, resp.text

    # A valid same-workspace key generates and persists into the join table.
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": [key]},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    keys = env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert keys == [key]


def test_generate_accepts_screenshot_keys_list(env, client, monkeypatch):
    # AC1, AC4 — a valid 3-key list persists 3 prototype_screenshots rows with
    # matching storage_key/position.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    keys = [f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png" for _ in range(3)]
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": keys},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    persisted = env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert persisted == keys


def test_generate_rejects_more_than_ten_screenshot_keys(env, client, monkeypatch):
    # AC2 — an 11-key list gets 422 {"error": "too_many_screenshots", "max": 10},
    # zero DB writes.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    keys = [f"uploads/{_TEST_COMPANY_ID}/{i}.png" for i in range(11)]
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": keys},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {"error": "too_many_screenshots", "max": 10}
    assert env.proto.find_existing_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID,
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION, variant="v1",
    ) is None


def test_generate_rejects_screenshot_key_outside_workspace_prefix(env, client, monkeypatch):
    # AC3 (regression-pin, extended to plural) — a key not starting with
    # uploads/{workspace_id}/ in a 3-key list gets 403 screenshot_key_forbidden,
    # zero DB writes even for the other 2 valid keys.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    valid_a = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    valid_b = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": [valid_a, "uploads/other-ws/abc.png", valid_b]},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"error": "screenshot_key_forbidden"}
    assert env.proto.find_existing_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID,
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION, variant="v1",
    ) is None


def test_generate_rejects_traversal_segment_in_any_key(env, client, monkeypatch):
    # AC3 — a `..`-containing key ANYWHERE in the list (not just first) gets
    # the same 403.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    valid = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    hostile = f"uploads/{_TEST_COMPANY_ID}/../other-ws/evil.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": [valid, hostile]},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"error": "screenshot_key_forbidden"}


def test_generate_no_screenshot_keys_writes_zero_rows(env, client, monkeypatch):
    # AC6 — screenshot_keys omitted -> 0 prototype_screenshots rows,
    # prototypes.screenshot_key still null.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    assert env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == []
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["screenshot_key"] is None


def test_generate_never_writes_legacy_screenshot_key_column(env, client, monkeypatch):
    # AC5 — even a 1-key list leaves prototypes.screenshot_key null on the
    # persisted row.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_keys": [key]},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["screenshot_key"] is None


def test_generate_accepts_legacy_bare_screenshot_key_shape(env, client, monkeypatch):
    # AC21 (BLOCKER-2 backward compat) — POST with ONLY the old bare
    # {"screenshot_key": ...} body (no screenshot_keys at all) is accepted,
    # coerced into a 1-item screenshot_keys list, and attaches identically to
    # a request that sent screenshot_keys: [key] directly.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": prd_id, "screenshot_key": key},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    assert env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == [key]


def test_generate_legacy_field_ignored_when_plural_present(env, client, monkeypatch):
    # AC22 (BLOCKER-2 precedence) — POST with both screenshot_key (old,
    # foreign-workspace value that would 403 if consulted) AND screenshot_keys
    # (new, valid 2-item list) succeeds and persists exactly the 2 keys from
    # screenshot_keys — the foreign-workspace screenshot_key value never
    # triggers a 403 and is never persisted.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    keys = [f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png" for _ in range(2)]
    resp = client.post(
        "/v1/design-agent/generate",
        json={
            "prd_id": prd_id,
            "screenshot_key": "uploads/other-ws/evil.png",
            "screenshot_keys": keys,
        },
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    persisted = env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert persisted == keys


def test_generate_explicit_empty_screenshot_keys_list_not_overridden_by_legacy_field(
    env, client, monkeypatch
):
    # AC23 (BLOCKER-2 explicit-empty precedence) — POST with BOTH an explicit
    # screenshot_keys: [] AND a non-empty bare screenshot_key persists ZERO
    # prototype_screenshots rows — the explicit empty list is authoritative
    # because the coercion only fires when screenshot_keys is absent from the
    # request body entirely, not merely falsy.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    resp = client.post(
        "/v1/design-agent/generate",
        json={
            "prd_id": prd_id,
            "screenshot_keys": [],
            "screenshot_key": f"uploads/{_TEST_COMPANY_ID}/x.png",
        },
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    assert env.screenshots.list_screenshot_keys(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == []


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


def test_generate_403_on_traversal_screenshot_key(env, client, fs_storage, monkeypatch):
    # NO FUNCTIONAL CHANGE NEEDED (this test only POSTs the bare legacy shape
    # and never reads back a persisted row; the BLOCKER-2 shim coerces it into
    # a 1-item screenshot_keys list before the ownership-check loop runs, and
    # the loop's traversal check rejects each hostile value with the SAME
    # error shape). A `..` segment can satisfy the literal workspace prefix
    # yet resolve into another workspace's directory on the filesystem backend
    # — refused at the ROUTE (same 403 shape as a foreign key), not just at
    # read time.
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    for hostile in (
        f"uploads/{_TEST_COMPANY_ID}/../other-ws/evil.png",
        f"uploads/{_TEST_COMPANY_ID}/a/../../other-ws/evil.png",
        f"uploads/{_TEST_COMPANY_ID}/..",
    ):
        resp = client.post(
            "/v1/design-agent/generate",
            json={"prd_id": prd_id, "screenshot_key": hostile},
        )
        assert resp.status_code == 403, (hostile, resp.text)
        assert resp.json()["detail"] == {"error": "screenshot_key_forbidden"}


# ─── generation engine: vision wiring (scaffold) ─────────────────────────────


@pytest.mark.asyncio
async def test_generate_user_message_prepends_image_block(env, monkeypatch):
    # The stored screenshot rides the FIRST user message as a base64 image
    # block PRECEDING the text block, and the text block carries the directive.
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )
    env.screenshots.insert_screenshots(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, storage_keys=[key],
    )

    async def _fake_read(*, key, workspace_id):
        return b"png-reference-bytes", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        design_source="screenshot", screenshot_keys=[key],
    )
    content = calls[0]["user_message"]["content"]
    assert len(content) == 2
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[0]["source"]["data"] == base64.b64encode(b"png-reference-bytes").decode("ascii")
    assert content[1]["type"] == "text"
    from app.design_agent.prompts import DESIGN_AGENT_SCREENSHOT_DIRECTIVE

    assert DESIGN_AGENT_SCREENSHOT_DIRECTIVE in content[1]["text"]


@pytest.mark.asyncio
async def test_generate_message_without_key_is_single_text_block(env, monkeypatch):
    # No screenshot_key → the user message is the pre-ticket single text block:
    # no image, no directive, and the storage read never fires.
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )
    reads: list[str] = []

    async def _spy_read(*, key, workspace_id):
        reads.append(key)
        return b"", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _spy_read)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    content = calls[0]["user_message"]["content"]
    assert [b["type"] for b in content] == ["text"]
    from app.design_agent.prompts import DESIGN_AGENT_SCREENSHOT_DIRECTIVE

    assert DESIGN_AGENT_SCREENSHOT_DIRECTIVE not in content[0]["text"]
    assert reads == []


@pytest.mark.asyncio
async def test_generate_proceeds_without_missing_screenshot(env, monkeypatch, caplog):
    # FAIL-OPEN reference at generate time: an unreadable stored object logs ONE
    # WARNING (identifiers only) and generation proceeds image-less — with the
    # directive withheld (it must not talk about an image that is not attached).
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    key = f"uploads/{_TEST_COMPANY_ID}/gone.png"
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )
    env.screenshots.insert_screenshots(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, storage_keys=[key],
    )

    async def _boom(*, key, workspace_id):
        raise FileNotFoundError(key)

    monkeypatch.setattr(env.routes, "read_screenshot", _boom)

    with caplog.at_level(logging.WARNING):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            design_source="screenshot", screenshot_keys=[key],
        )
    content = calls[0]["user_message"]["content"]
    assert [b["type"] for b in content] == ["text"]
    from app.design_agent.prompts import DESIGN_AGENT_SCREENSHOT_DIRECTIVE

    assert DESIGN_AGENT_SCREENSHOT_DIRECTIVE not in content[0]["text"]
    warnings = [
        r for r in caplog.records if "screenshot_context_unavailable" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "gone.png" in warnings[0].getMessage()          # key suffix only
    assert _TEST_COMPANY_ID not in warnings[0].getMessage()  # never the prefix


# ─── _screenshot_reference_blocks: N-image LLM context builder ──────────────


async def test_screenshot_reference_blocks_empty_list_returns_empty(env):
    # AC7
    assert await env.routes._screenshot_reference_blocks(
        [], prototype_id=1, workspace_id=_TEST_COMPANY_ID
    ) == []


async def test_screenshot_reference_blocks_none_returns_empty(env):
    # AC7
    assert await env.routes._screenshot_reference_blocks(
        None, prototype_id=1, workspace_id=_TEST_COMPANY_ID
    ) == []


async def test_screenshot_reference_blocks_single_key_no_label(env, monkeypatch):
    # AC8 — exactly ONE {"type": "image", ...} block, NO preceding "Image 1:"
    # label block — byte-identical in shape to the pre-ticket single-image
    # behaviour.
    async def _fake_read(*, key, workspace_id):
        return b"one-shot-bytes", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)
    blocks = await env.routes._screenshot_reference_blocks(
        [f"uploads/{_TEST_COMPANY_ID}/a.png"], prototype_id=1, workspace_id=_TEST_COMPANY_ID
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["data"] == base64.b64encode(b"one-shot-bytes").decode("ascii")


async def test_screenshot_reference_blocks_multi_key_labels_each_image(env, monkeypatch):
    # AC9 — 3 keys -> 2N=6 blocks, alternating "Image 1:"/image/"Image 2:"/
    # image/"Image 3:"/image, in submitted order.
    async def _fake_read(*, key, workspace_id):
        return key.encode(), "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)
    keys = [f"uploads/{_TEST_COMPANY_ID}/{n}.png" for n in ("a", "b", "c")]
    blocks = await env.routes._screenshot_reference_blocks(
        keys, prototype_id=1, workspace_id=_TEST_COMPANY_ID
    )
    assert len(blocks) == 6
    assert [b["type"] for b in blocks] == ["text", "image", "text", "image", "text", "image"]
    assert [b["text"] for b in blocks if b["type"] == "text"] == ["Image 1:", "Image 2:", "Image 3:"]
    assert [b["source"]["data"] for b in blocks if b["type"] == "image"] == [
        base64.b64encode(k.encode()).decode("ascii") for k in keys
    ]


async def test_screenshot_reference_blocks_skips_unreadable_key_fail_open(env, monkeypatch, caplog):
    # AC10 (regression-style: proves the pre-ticket fail-open guarantee
    # generalizes to N) — one of 3 keys raises inside read_screenshot, the
    # other 2 still produce blocks, one WARNING logged with the failing key's
    # suffix only.
    keys = [
        f"uploads/{_TEST_COMPANY_ID}/good1.png",
        f"uploads/{_TEST_COMPANY_ID}/bad.png",
        f"uploads/{_TEST_COMPANY_ID}/good2.png",
    ]

    async def _flaky_read(*, key, workspace_id):
        if "bad.png" in key:
            raise FileNotFoundError(key)
        return b"ok-bytes", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _flaky_read)
    with caplog.at_level(logging.WARNING):
        blocks = await env.routes._screenshot_reference_blocks(
            keys, prototype_id=1, workspace_id=_TEST_COMPANY_ID
        )
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2
    warnings = [r for r in caplog.records if "screenshot_context_unavailable" in r.getMessage()]
    assert len(warnings) == 1
    assert "bad.png" in warnings[0].getMessage()
    assert _TEST_COMPANY_ID not in warnings[0].getMessage()


async def test_screenshot_reference_blocks_enforces_aggregate_budget(env, monkeypatch, caplog):
    # AC11 — 4 keys of 40 bytes each, budget=100: key a (total 40) and key b
    # (total 80) fit; key c's read pushes the running total to 120 > 100, so
    # its bytes are excluded from the returned blocks and the loop breaks —
    # key d (after the break) is skipped WITHOUT ever being read. The
    # trip-causing key (c) IS read (its size has to be known to detect the
    # trip) but does not produce a block; skipped_budget counts BOTH c and d
    # (the "remaining keys after the break", per the documented contract).
    monkeypatch.setattr(env.routes, "_MAX_TOTAL_SCREENSHOT_BYTES", 100)
    keys = [f"uploads/{_TEST_COMPANY_ID}/{n}.png" for n in ("a", "b", "c", "d")]
    read_calls: list[str] = []

    async def _fake_read(*, key, workspace_id):
        read_calls.append(key)
        return b"x" * 40, "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)
    with caplog.at_level(logging.WARNING):
        blocks = await env.routes._screenshot_reference_blocks(
            keys, prototype_id=1, workspace_id=_TEST_COMPANY_ID
        )
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2
    # Key d (after the break) is never read at all.
    assert read_calls == keys[:3]
    warnings = [
        r for r in caplog.records if "screenshot_context_budget_exceeded" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "skipped_budget=2" in warnings[0].getMessage()
    assert "attached=2" in warnings[0].getMessage()


async def test_screenshot_reference_blocks_budget_and_unreadable_both_present(env, monkeypatch, caplog):
    # Edge case — one unreadable key + a later budget trip in the same call
    # produces correct, non-double-counted skipped_unreadable/skipped_budget
    # values.
    monkeypatch.setattr(env.routes, "_MAX_TOTAL_SCREENSHOT_BYTES", 100)
    keys = [
        f"uploads/{_TEST_COMPANY_ID}/bad.png",
        f"uploads/{_TEST_COMPANY_ID}/big1.png",
        f"uploads/{_TEST_COMPANY_ID}/big2.png",
    ]

    async def _fake_read(*, key, workspace_id):
        if "bad.png" in key:
            raise FileNotFoundError(key)
        return b"x" * 60, "image/png"  # 2 * 60 = 120 > 100; the 2nd valid key trips it

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)
    with caplog.at_level(logging.WARNING):
        blocks = await env.routes._screenshot_reference_blocks(
            keys, prototype_id=1, workspace_id=_TEST_COMPANY_ID
        )
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 1  # only big1.png fit under budget
    budget_warnings = [
        r for r in caplog.records if "screenshot_context_budget_exceeded" in r.getMessage()
    ]
    assert len(budget_warnings) == 1
    assert "skipped_budget=1" in budget_warnings[0].getMessage()
    assert "attached=1" in budget_warnings[0].getMessage()


async def test_screenshot_reference_blocks_never_logs_full_storage_key(env, monkeypatch, caplog):
    # AC20 — assert the captured WARNING text contains only the trailing
    # suffix, never the full uploads/{workspace_id}/... key.
    key = f"uploads/{_TEST_COMPANY_ID}/secretname.png"

    async def _boom(*, key, workspace_id):
        raise FileNotFoundError(key)

    monkeypatch.setattr(env.routes, "read_screenshot", _boom)
    with caplog.at_level(logging.WARNING):
        await env.routes._screenshot_reference_blocks(
            [key], prototype_id=1, workspace_id=_TEST_COMPANY_ID
        )
    warnings = [r for r in caplog.records if "screenshot_context_unavailable" in r.getMessage()]
    assert len(warnings) == 1
    assert "secretname.png" in warnings[0].getMessage()
    assert key not in warnings[0].getMessage()
    assert _TEST_COMPANY_ID not in warnings[0].getMessage()


# ─── source selection: the screenshot arm ────────────────────────────────────


def test_screenshot_source_skips_design_system_resolver():
    # Even with EVERY other arm satisfiable, design_source="screenshot" returns
    # the un-seeded tuple — provider None makes _resolve_design_system a no-op
    # (early return), so no extractor runs and no design_systems row is written.
    from app.design_agent.runner import _design_source_for_generation

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key="figkey",
        figma_access_token="tok",
        website_url="https://brand.example",
        website_sample={"colors": []},
        github_repo="org/repo",
        github_installation_id=7,
        design_source="screenshot",
    )
    assert (provider, source_ref, raw_factory, version_factory) == (None, None, None, None)


@pytest.mark.asyncio
async def test_generate_screenshot_source_skips_website_extractor(env, monkeypatch):
    # Route-side arm behaviour: a screenshot-sourced generate never runs the
    # website extractor (the image IS the design context), and the runner sees
    # design_source="screenshot" with no website sample.
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    key = f"uploads/{_TEST_COMPANY_ID}/{uuid.uuid4()}.png"
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )
    env.screenshots.insert_screenshots(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, storage_keys=[key],
    )

    async def _fake_read(*, key, workspace_id):
        return b"shot", "image/png"

    monkeypatch.setattr(env.routes, "read_screenshot", _fake_read)

    extractor_calls: list = []

    async def _spy_extract(url):
        extractor_calls.append(url)
        return None

    monkeypatch.setattr(env.routes, "_extract_website_sample", _spy_extract)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        website_url="https://brand.example",
        design_source="screenshot", screenshot_keys=[key],
    )
    assert extractor_calls == []
    assert calls[0]["design_source"] == "screenshot"
    assert calls[0]["website_sample"] is None


# ─── template-version bump + dedupe keying ───────────────────────────────────


def test_template_version_bumped_and_dedupe_keys_on_it(env, client, monkeypatch):
    from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION

    assert DESIGN_AGENT_TEMPLATE_VERSION == 9  # the mobile-capability bump

    # Deterministic bg: the fired task must not mutate row status mid-test.
    async def _noop_bg(**kwargs):
        return None

    monkeypatch.setattr(env.routes, "_run_generation_bg", _noop_bg)
    prd_id = _seed_prd(env.db)

    r1 = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert r1.status_code == 200, r1.text
    pid = r1.json()["prototype_id"]
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["template_version"] == DESIGN_AGENT_TEMPLATE_VERSION

    # find_existing dedupe keys on the NEW version: a second identical generate
    # returns the same prototype, and the prior version no longer matches.
    r2 = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert r2.status_code == 200, r2.text
    assert r2.json()["prototype_id"] == pid
    assert env.proto.find_existing_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID,
        template_version=DESIGN_AGENT_TEMPLATE_VERSION - 1, variant="v1",
    ) is None


# ─── start_prototype threading ────────────────────────────────────────────────
#
# The two tests formerly here (`test_start_prototype_persists_screenshot_key`,
# `test_start_prototype_payload_omits_null_screenshot_key`) asserted
# `start_prototype`'s OLD conditional `screenshot_key` payload-write —
# behavior this ticket's own Deliverables explicitly remove (the parameter no
# longer exists at all). Deleted rather than migrated: keeping them would mean
# testing deleted code. Replacement coverage: AC5 ("prototypes.screenshot_key
# is never written by a new /generate call") is covered by
# `test_generate_never_writes_legacy_screenshot_key_column` above (route-level);
# `start_prototype`'s base no-screenshot insert path remains covered by the
# many pre-existing non-screenshot callers across test_db_prototypes.py and
# 15+ other test files (confirmed via `git grep -n "start_prototype(" backend/tests/`).


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

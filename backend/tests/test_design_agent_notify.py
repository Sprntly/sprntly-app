"""Prototype-ready notification: the channel-agnostic notifier seam
(`design_agent/notify.py`), the `created_by_user_id` threading
(GenerateRequest → start_prototype → row), and the completion-path hook in
`_run_generation_bg` (fires on first-completion stage only; never on iterate).

Test layers, mirroring the sibling suites (test_design_agent_screenshot_upload.py):

1. **Notifier units** — recipient resolution against seeded per-user Slack
   connections, the forced DM target, message copy (title + deep link), the
   guard reasons (disabled / no_recipient / slack_not_connected), the
   never-raises posture, the provider-registry seam, and the identifiers-only
   log discipline.
2. **Threading** — /generate persists the authed user; the conditional-write
   pin (a creator-less insert's payload carries NO created_by_user_id key).
3. **Completion hook** — `_run_generation_bg` fires the notifier exactly once
   after a successful stage; a notifier explosion leaves the row `ready`;
   the iterate path (`_run_iterate_bg`) fires NO notification.
4. **Migration** — additive + idempotent, string-level (sibling convention).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID, _TEST_USER_ID

# SQLite-compatible translation of the prototypes DDL (mirrors
# test_design_agent_routes.py / test_design_agent_screenshot_upload.py) + the
# additive created_by_user_id column this suite exercises.
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

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260716140000_prototypes_created_by.sql"
)

NOTIFY_LOGGER = "app.design_agent.notify"


# ─── fixtures (mirror test_design_agent_screenshot_upload.py) ────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    # connections.company_id carries an FK to companies(id) (PRAGMA foreign_keys
    # is ON in the fake) — seed the parent company row up front. Company ONLY:
    # company_client's _seed_company_membership seeds the membership itself and
    # is existence-guarded on the company, so composing double-seeds nothing.
    fake = isolated_settings["supabase"]
    if not fake.table("companies").select("id").eq("id", _TEST_COMPANY_ID).execute().data:
        fake.table("companies").insert({
            "id": _TEST_COMPANY_ID,
            "slug": f"slug-{_TEST_COMPANY_ID}",
            "display_name": _TEST_COMPANY_ID.title(),
        }).execute()

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import importlib as _il
    import app.config as _config_mod
    _il.reload(_config_mod)
    import app.connectors.tokens as _tokens_mod
    _il.reload(_tokens_mod)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    # notify binds settings + get_prototype at import; reload it AFTER config +
    # prototypes and BEFORE routes so the whole stack shares one settings object.
    import app.design_agent.notify as notify_mod
    importlib.reload(notify_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(
        proto=proto_mod, notify=notify_mod, routes=routes_mod,
        main=main_mod, db=db_mod,
    )


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) resolving _TEST_COMPANY_ID."""
    return company_client


@pytest.fixture
def slack_spy(monkeypatch):
    """Record every post_to_target call at the Slack HTTP boundary (no network)."""
    calls: list[dict] = []

    def _spy(bot_token, *, config, authed_user_id, text, blocks=None):
        calls.append({
            "bot_token": bot_token,
            "config": config,
            "authed_user_id": authed_user_id,
            "text": text,
            "blocks": blocks,
        })
        return {"ok": True, "channel": "D-test"}

    from app.connectors import slack_oauth
    monkeypatch.setattr(slack_oauth, "post_to_target", _spy)
    return calls


@pytest.fixture
def email_spy(env, monkeypatch):
    """Record every send_drip_email call at the outbound boundary (no
    network) — mirrors slack_spy. Explicitly depends on `env` (unlike
    slack_spy) because notify.py binds `send_drip_email` as a direct name
    import (not a module reference like slack_oauth), so the patch target
    must be `env.notify.send_drip_email`, resolved AFTER env's reload."""
    calls: list[dict] = []

    def _spy(*, to_email, subject, body_text):
        calls.append({"to_email": to_email, "subject": subject, "body_text": body_text})
        return True

    monkeypatch.setattr(env.notify, "send_drip_email", _spy)
    return calls


# ─── helpers ─────────────────────────────────────────────────────────────────


def _seed_prd(db_mod, title: str = "Checkout Flow") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title=title, template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title=title, md="# PRD body")
    return prd_id


def _seed_slack_connection(env, *, user_id: str, authed_user_id: str) -> None:
    """One per-user Slack connection row. The stored config points at a CHANNEL
    target so the DM-override assertion below is load-bearing (the notifier
    must NOT honor the stored weekly-brief preference)."""
    from app.connectors.tokens import encrypt_token_json

    enc = encrypt_token_json(json.dumps({
        "access_token": f"xoxb-{user_id}",
        "authed_user_id": authed_user_id,
    }))
    env.db.upsert_slack_connection(
        company_id=_TEST_COMPANY_ID,
        user_id=user_id,
        token_encrypted=enc,
        scopes="chat:write,im:write",
        config_json=json.dumps({"target_type": "channel", "channel_id": "C-brief"}),
    )


def _seed_profile_email(*, user_id: str, email: str | None) -> None:
    from app.db.client import require_client
    require_client().table("profiles").insert({"id": user_id, "email": email}).execute()


def _seed_ready_prototype(env, *, prd_id: int = 1, created_by: str | None = None) -> int:
    pid = env.proto.start_prototype(
        prd_id=prd_id,
        workspace_id=_TEST_COMPANY_ID,
        template_version=1,
        created_by_user_id=created_by,
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        bundle_url="https://app.example/bundle/index.html",
    )
    return pid


def _stub_generation(monkeypatch, env, *, virtual_fs=None):
    """Stub the agent loop + the stage step for direct _run_generation_bg runs.
    The stage fake honors _stage_complete_run's contract: it marks the row
    ready (complete_prototype) and returns True."""
    vfs = {"src/App.tsx": "export default () => null"} if virtual_fs is None else virtual_fs

    async def _fake_generate(**kwargs):
        return SimpleNamespace(status="complete", iters=1, usage=None), dict(vfs)

    async def _fake_stage(*, prototype_id, workspace_id, **kwargs):
        env.proto.complete_prototype(
            prototype_id=prototype_id, workspace_id=workspace_id,
            bundle_url="https://app.example/bundle/index.html",
        )
        return True

    monkeypatch.setattr(env.routes, "generate_prototype", _fake_generate)
    monkeypatch.setattr(env.routes, "_stage_complete_run", _fake_stage)


# ═══ Layer 1 — notifier units ════════════════════════════════════════════════


def test_notify_dm_targets_creator_connection(env, slack_spy):
    # AC1/AC2: two members hold Slack connections; the DM goes to the CREATOR's
    # row (their bot token + their authed_user_id), target forced to "dm" even
    # though both stored configs point at a channel.
    prd_id = _seed_prd(env.db)
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    _seed_slack_connection(env, user_id="user-b", authed_user_id="U-BBB")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": True, "provider": "slack", "reason": None}
    assert len(slack_spy) == 1
    call = slack_spy[0]
    assert call["config"] == {"target_type": "dm"}
    assert call["authed_user_id"] == "U-AAA"
    assert call["bot_token"] == "xoxb-user-a"


def test_notify_message_carries_title_and_deep_link(env, slack_spy):
    prd_id = _seed_prd(env.db, title="Checkout Flow")
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    env.notify.notify_prototype_ready(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)

    assert len(slack_spy) == 1
    call = slack_spy[0]
    assert "Checkout Flow" in call["text"]
    assert f"/prototype?pid={pid}" in call["text"]
    # One section block carrying the same copy + link.
    assert len(call["blocks"]) == 1
    block_text = call["blocks"][0]["text"]["text"]
    assert call["blocks"][0]["type"] == "section"
    assert "Checkout Flow" in block_text
    assert f"/prototype?pid={pid}" in block_text


def test_notify_disabled_reason(env, slack_spy, monkeypatch):
    # AC3: kill switch off → no Slack call, reason 'disabled'.
    prd_id = _seed_prd(env.db)
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    monkeypatch.setattr(
        env.notify.settings, "prototype_ready_notify_enabled", False
    )

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": None, "reason": "disabled"}
    assert slack_spy == []


def test_notify_no_recipient_reasons(env, slack_spy):
    # AC3: a NULL-creator (legacy/pre-column) row and a creator without a Slack
    # connection produce DISTINCT reasons; neither reaches Slack.
    prd_id = _seed_prd(env.db)

    legacy_pid = _seed_ready_prototype(env, prd_id=prd_id, created_by=None)
    result = env.notify.notify_prototype_ready(
        prototype_id=legacy_pid, workspace_id=_TEST_COMPANY_ID
    )
    assert result == {"delivered": False, "provider": None, "reason": "no_recipient"}

    # A missing row entirely is also just "no recipient" (row vanished under us).
    result = env.notify.notify_prototype_ready(
        prototype_id=999999, workspace_id=_TEST_COMPANY_ID
    )
    assert result == {"delivered": False, "provider": None, "reason": "no_recipient"}

    # No Slack connection AND no profile row for user-z: the email fallback
    # triggers (this ticket) but can't resolve a recipient address either.
    unconnected_pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-z")
    result = env.notify.notify_prototype_ready(
        prototype_id=unconnected_pid, workspace_id=_TEST_COMPANY_ID
    )
    assert result == {"delivered": False, "provider": "email", "reason": "no_email"}

    assert slack_spy == []


def test_notify_slack_failure_never_raises(env, monkeypatch, caplog):
    # A provider explosion at the Slack boundary is caught → WARNING (identifiers
    # only) + delivered False. The caller never sees an exception.
    prd_id = _seed_prd(env.db)
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    def _boom(*args, **kwargs):
        raise RuntimeError("slack exploded")

    from app.connectors import slack_oauth
    monkeypatch.setattr(slack_oauth, "post_to_target", _boom)

    with caplog.at_level(logging.WARNING, logger=NOTIFY_LOGGER):
        result = env.notify.notify_prototype_ready(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID
        )

    assert result == {"delivered": False, "provider": "slack", "reason": "error"}
    warnings = [
        r for r in caplog.records if "prototype_ready_notify" in r.getMessage()
    ]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    # Identifiers only — the raw exception text stays out of the log line.
    assert "slack exploded" not in warnings[0].getMessage()


def test_provider_registry_dispatch(env, monkeypatch):
    # AC7 (seam contract): a test-registered fake provider receives the SAME
    # normalized payload the Slack provider gets — the call-site is
    # channel-agnostic; swapping providers is a registry+config change only.
    prd_id = _seed_prd(env.db)
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    slack_payload: dict = {}

    def _wrapped_slack(**kwargs):
        slack_payload.update(kwargs)
        return {"delivered": True, "provider": "slack", "reason": None}

    fake_payload: dict = {}

    def _fake_provider(**kwargs):
        fake_payload.update(kwargs)
        return {"delivered": True, "provider": "fake", "reason": None}

    monkeypatch.setitem(env.notify._PROVIDERS, "slack", _wrapped_slack)
    env.notify.notify_prototype_ready(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)

    monkeypatch.setitem(env.notify._PROVIDERS, "fake", _fake_provider)
    monkeypatch.setattr(env.notify, "_DEFAULT_PROVIDER", "fake")
    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result["provider"] == "fake" and result["delivered"] is True
    assert fake_payload == slack_payload
    assert set(fake_payload) == {
        "workspace_id", "recipient_user_id", "prototype_id", "text", "blocks",
    }
    assert fake_payload["recipient_user_id"] == "user-a"
    assert fake_payload["prototype_id"] == pid


def test_notify_logs_identifiers_only(env, slack_spy, caplog):
    # AC8: exactly one structured line per attempt, carrying prototype_id /
    # delivered / reason — and never the PRD title or the recipient's Slack id.
    prd_id = _seed_prd(env.db, title="Checkout Flow")
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    with caplog.at_level(logging.INFO, logger=NOTIFY_LOGGER):
        env.notify.notify_prototype_ready(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID
        )

    lines = [
        r.getMessage() for r in caplog.records
        if "prototype_ready_notify" in r.getMessage()
    ]
    assert len(lines) == 1
    assert f"prototype_id={pid}" in lines[0]
    assert "delivered=True" in lines[0]
    assert "reason=None" in lines[0]
    for message in (r.getMessage() for r in caplog.records):
        assert "Checkout Flow" not in message   # no PRD title
        assert "U-AAA" not in message           # no Slack recipient id
        assert "xoxb-" not in message           # no token material


# ─── Slack -> email fallback (this ticket) ───────────────────────────────────


def test_notify_falls_back_to_email_when_slack_not_connected(env, email_spy):
    # AC1: no Slack connection at all for the creator; a resolvable profile
    # email → the email provider delivers.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": True, "provider": "email", "reason": None}
    assert len(email_spy) == 1
    assert email_spy[0]["to_email"] == "creator@example.com"


def test_notify_email_fallback_carries_title_and_deep_link(env, email_spy):
    # AC1: the email body carries the same title + deep-link copy the Slack
    # provider would have sent.
    prd_id = _seed_prd(env.db, title="Checkout Flow")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")

    env.notify.notify_prototype_ready(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)

    assert len(email_spy) == 1
    body_text = email_spy[0]["body_text"]
    assert "Checkout Flow" in body_text
    assert f"/prototype?pid={pid}" in body_text


def test_notify_email_fallback_no_profile_row_reason(env, email_spy):
    # AC2: no Slack connection AND no profiles row at all for the creator.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "email", "reason": "no_email"}
    assert email_spy == []


def test_notify_email_fallback_blank_profile_email_reason(env, email_spy):
    # AC2 (edge case): a profiles row exists but its email is blank — collapses
    # to the same reason as a missing row (emails_for_user_ids' truthy filter).
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="")

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "email", "reason": "no_email"}
    assert email_spy == []


def test_notify_email_fallback_send_failed_reason(env, monkeypatch):
    # AC3: a resolvable email, but send_drip_email itself reports failure.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")
    monkeypatch.setattr(env.notify, "send_drip_email", lambda **kwargs: False)

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "email", "reason": "send_failed"}


def test_notify_token_unreadable_does_not_fall_back_to_email(env, email_spy):
    # AC4: a Slack connection row EXISTS but its stored ciphertext is garbage
    # (not valid Fernet) — this is a broken connection, not an absent one;
    # the email fallback must NOT trigger.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")
    env.db.upsert_slack_connection(
        company_id=_TEST_COMPANY_ID,
        user_id="user-a",
        token_encrypted="not-valid-ciphertext",
        scopes="chat:write,im:write",
        config_json=json.dumps({"target_type": "channel", "channel_id": "C-brief"}),
    )

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "slack", "reason": "token_unreadable"}
    assert email_spy == []


def test_notify_no_bot_token_does_not_fall_back_to_email(env, email_spy):
    # AC4: a Slack connection row EXISTS, decrypts cleanly, but the decrypted
    # token JSON carries no access_token — again a broken (not absent)
    # connection; the email fallback must NOT trigger.
    from app.connectors.tokens import encrypt_token_json

    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")
    enc = encrypt_token_json(json.dumps({"authed_user_id": "U-AAA"}))
    env.db.upsert_slack_connection(
        company_id=_TEST_COMPANY_ID,
        user_id="user-a",
        token_encrypted=enc,
        scopes="chat:write,im:write",
        config_json=json.dumps({"target_type": "channel", "channel_id": "C-brief"}),
    )

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "slack", "reason": "no_bot_token"}
    assert email_spy == []


def test_notify_successful_slack_skips_email_fallback(env, slack_spy, email_spy):
    # AC5: Slack delivers successfully — no email attempt, unmodified result.
    prd_id = _seed_prd(env.db)
    _seed_slack_connection(env, user_id="user-a", authed_user_id="U-AAA")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result["provider"] == "slack"
    assert email_spy == []


def test_notify_email_fallback_exception_never_raises(env, monkeypatch):
    # AC7: the email provider itself raises — the entry point's never-raises
    # guard still catches it and returns the email-provider error shape.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")

    def _boom(**kwargs):
        raise RuntimeError("resend exploded")

    monkeypatch.setattr(env.notify, "send_drip_email", _boom)

    result = env.notify.notify_prototype_ready(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID
    )

    assert result == {"delivered": False, "provider": "email", "reason": "error"}


def test_notify_email_fallback_logs_identifiers_only(env, email_spy, caplog):
    # AC8: the ONE log line _log_outcome itself emits carries identifiers
    # only — never the recipient's email address or the PRD title. (The
    # pre-existing send_drip_email logging is out of scope — see AC8.)
    prd_id = _seed_prd(env.db, title="Checkout Flow")
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by="user-a")
    _seed_profile_email(user_id="user-a", email="creator@example.com")

    with caplog.at_level(logging.INFO, logger=NOTIFY_LOGGER):
        env.notify.notify_prototype_ready(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID
        )

    lines = [
        r.getMessage() for r in caplog.records
        if "prototype_ready_notify" in r.getMessage()
    ]
    assert len(lines) == 1
    assert f"prototype_id={pid}" in lines[0]
    assert "delivered=True" in lines[0]
    assert "reason=None" in lines[0]
    assert "creator@example.com" not in lines[0]
    assert "Checkout Flow" not in lines[0]


def test_notify_registry_gains_email_provider_default_stays_slack(env):
    # AC9: the registry now carries exactly two entries; the default is
    # unchanged — email is fallback-only, never selected by default.
    assert set(env.notify._PROVIDERS) == {"slack", "email"}
    assert env.notify._DEFAULT_PROVIDER == "slack"


# ═══ Layer 2 — created_by_user_id threading ══════════════════════════════════


def test_generate_persists_created_by_user_id(env, client, monkeypatch):
    # AC6: a generate request persists the authed user (require_company) into
    # the row's created_by_user_id.
    async def _fake_generate(**kwargs):
        return SimpleNamespace(status="complete", iters=1, usage=None), {}

    monkeypatch.setattr(env.routes, "generate_prototype", _fake_generate)
    prd_id = _seed_prd(env.db)

    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    row = env.proto.get_prototype(
        prototype_id=resp.json()["prototype_id"], workspace_id=_TEST_COMPANY_ID
    )
    assert row["created_by_user_id"] == _TEST_USER_ID


def test_start_prototype_payload_omits_null_created_by(env, monkeypatch):
    # Conditional-write pin: a creator-less insert's payload carries NO
    # created_by_user_id key at all (optional-column convention — environments
    # whose prototypes schema predates the column must keep inserting cleanly).
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
    assert captured and "created_by_user_id" not in captured[-1]

    env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1,
        created_by_user_id="user-a",
    )
    assert captured[-1].get("created_by_user_id") == "user-a"

    # Existing (pre-column-era) rows read NULL through the normal read path.
    legacy_pid = env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    row = env.proto.get_prototype(
        prototype_id=legacy_pid, workspace_id=_TEST_COMPANY_ID
    )
    assert row["created_by_user_id"] is None


# ═══ Layer 3 — completion hook (generation path vs iterate path) ═════════════


def test_generation_hook_fires_once_after_successful_stage(env, monkeypatch):
    # AC1 wiring at the hook site: a stubbed run that stages successfully fires
    # the notifier exactly once, with the row's own identifiers.
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
        created_by_user_id=_TEST_USER_ID,
    )
    _stub_generation(monkeypatch, env)

    notify_calls: list[dict] = []
    monkeypatch.setattr(
        env.routes, "notify_prototype_ready",
        lambda **kw: notify_calls.append(kw) or {"delivered": True},
    )

    asyncio.run(env.routes._run_generation_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    ))

    assert notify_calls == [
        {"prototype_id": pid, "workspace_id": _TEST_COMPANY_ID}
    ]
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"


def test_generation_completes_ready_when_notify_explodes(env, monkeypatch, caplog):
    # AC4 (mutation-proof target): break the notifier → the generation is
    # unharmed. The row is 'ready' with no error; the hook logs a WARNING.
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
        created_by_user_id=_TEST_USER_ID,
    )
    _stub_generation(monkeypatch, env)

    def _boom(**kwargs):
        raise RuntimeError("notifier exploded")

    monkeypatch.setattr(env.routes, "notify_prototype_ready", _boom)

    with caplog.at_level(logging.WARNING):
        asyncio.run(env.routes._run_generation_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
        ))

    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"
    assert row["error"] is None
    assert any(
        "prototype_ready_notify_hook_failed" in r.getMessage()
        for r in caplog.records
    )


def test_iterate_advance_fires_no_notification(env, monkeypatch):
    # AC5: an execute iterate that advances a checkpoint is NOT a first
    # completion — the notifier must not fire.
    prd_id = _seed_prd(env.db)
    pid = _seed_ready_prototype(env, prd_id=prd_id, created_by=_TEST_USER_ID)

    async def _fake_iterate(**kwargs):
        return (
            SimpleNamespace(status="complete", iters=1, usage=None),
            {"src/App.tsx": "export default () => null"},
        )

    async def _fake_stage_iterate(**kwargs):
        return True  # emulates the advance-checkpoint stage succeeding

    monkeypatch.setattr(env.routes, "iterate_prototype", _fake_iterate)
    monkeypatch.setattr(env.routes, "_stage_iterate_run", _fake_stage_iterate)
    monkeypatch.setattr(env.routes, "list_comments", lambda **kw: [])

    notify_calls: list[dict] = []
    monkeypatch.setattr(
        env.routes, "notify_prototype_ready",
        lambda **kw: notify_calls.append(kw) or {"delivered": True},
    )

    asyncio.run(env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID,
        body=env.routes.IterateRequest(prompt="make the header blue"),
    ))

    assert notify_calls == []
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["status"] == "ready"


# ═══ Layer 4 — migration ═════════════════════════════════════════════════════


def test_created_by_migration_is_idempotent():
    # String-level check (no live Postgres in this lane, per the sibling
    # migration tests' convention): additive `add column if not exists` on
    # prototypes only, so a double-apply is a no-op.
    assert _MIGRATION_PATH.exists()
    sql = "\n".join(
        line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()
    ).lower()
    assert "alter table prototypes" in sql
    assert "add column if not exists created_by_user_id" in sql
    # Additive-only: nothing destructive, nothing on sibling tables.
    for forbidden in ("drop ", "delete ", "update ", "alter table prds", "alter table briefs"):
        assert forbidden not in sql

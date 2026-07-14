"""Per-company Claude API key — resolution policy, factories, middleware, routes.

Policy under test:
  * company has its own key                              → use it (never platform)
  * no key, still onboarding                             → platform (allowed)
  * no key, onboarding complete, use_platform_key=false  → FAIL
  * no key, onboarding complete, use_platform_key=true   → platform (allowed)
  * unbound (no company in scope)                        → platform
  * OpenAI embeddings                                    → never touched
"""
from __future__ import annotations

import contextlib

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key(monkeypatch):
    """A valid TOKEN_ENCRYPTION_KEY on the exact settings object the encryption
    helpers use, so encrypt/decrypt round-trips in tests."""
    import app.connectors.tokens as tokens_mod

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(tokens_mod.settings, "token_encryption_key", key)
    return key


@contextlib.contextmanager
def _bind(company_id: str):
    import app.llm_keys as llm_keys

    llm_keys.invalidate(company_id)
    token = llm_keys._current_company_id.set(company_id)
    try:
        yield
    finally:
        llm_keys._current_company_id.reset(token)
        llm_keys.invalidate(company_id)


def _stub_config(monkeypatch, *, cipher=None, use_platform=False, onboarded=False):
    import app.db.companies as companies_mod

    monkeypatch.setattr(
        companies_mod,
        "get_company_llm_config",
        lambda _cid: (cipher, use_platform, onboarded),
    )


# ── resolver policy ──────────────────────────────────────────────────────────

def test_unbound_uses_platform(isolated_settings):
    from app.llm_keys import resolve_llm_api_key

    assert resolve_llm_api_key("sk-ant-platform") == "sk-ant-platform"


def test_company_key_wins(isolated_settings, monkeypatch, fernet_key):
    from app.connectors.tokens import encrypt_token_json
    from app.llm_keys import resolve_llm_api_key

    _stub_config(monkeypatch, cipher=encrypt_token_json("sk-ant-COMPANY"), onboarded=True)
    with _bind("co-1"):
        assert resolve_llm_api_key("sk-ant-platform") == "sk-ant-COMPANY"


def test_no_key_while_onboarding_allows_platform(isolated_settings, monkeypatch):
    from app.llm_keys import resolve_llm_api_key

    _stub_config(monkeypatch, cipher=None, use_platform=False, onboarded=False)
    with _bind("co-1"):
        assert resolve_llm_api_key("sk-ant-platform") == "sk-ant-platform"


def test_no_key_after_onboarding_fails(isolated_settings, monkeypatch):
    from app.llm_keys import CompanyKeyRequiredError, resolve_llm_api_key

    _stub_config(monkeypatch, cipher=None, use_platform=False, onboarded=True)
    with _bind("co-1"):
        with pytest.raises(CompanyKeyRequiredError):
            resolve_llm_api_key("sk-ant-platform")


def test_use_platform_flag_allows_platform_after_onboarding(isolated_settings, monkeypatch):
    from app.llm_keys import resolve_llm_api_key

    _stub_config(monkeypatch, cipher=None, use_platform=True, onboarded=True)
    with _bind("co-1"):
        assert resolve_llm_api_key("sk-ant-platform") == "sk-ant-platform"


def test_non_uuid_company_id_is_missing_row_not_an_error(isolated_settings):
    # An older gateway caller can bind a legacy dataset slug or other telemetry
    # tag as the acting "company". That is definitionally not an onboarded
    # company — it must resolve like a missing row (lenient, platform allowed),
    # not blow up the uuid-typed DB lookup and fail the whole LLM call.
    from app.db.companies import get_company_llm_config

    assert get_company_llm_config("313") == (None, False, False)
    assert get_company_llm_config("legacy-dataset-slug") == (None, False, False)


def test_config_read_failure_raises_unavailable_not_key_required(
    isolated_settings, monkeypatch
):
    import app.db.companies as companies_mod
    from app.llm_keys import KeyResolutionUnavailableError, resolve_llm_api_key

    def _boom(_cid):
        raise RuntimeError("Invalid input ConnectionInputs.RECV_DATA in state ConnectionState.CLOSED")

    monkeypatch.setattr(companies_mod, "get_company_llm_config", _boom)
    with _bind("co-1"):
        with pytest.raises(KeyResolutionUnavailableError) as exc_info:
            resolve_llm_api_key("sk-ant-platform")
    assert exc_info.value.status_code == 503


def test_config_read_failure_is_not_cached(isolated_settings, monkeypatch):
    """A transient DB error must not poison the company's calls for the cache
    TTL — the very next call re-reads the DB and succeeds."""
    import app.db.companies as companies_mod
    from app.llm_keys import KeyResolutionUnavailableError, resolve_llm_api_key

    calls = {"n": 0}

    def _flaky(_cid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Server disconnected")
        return (None, True, True)  # use_platform_key=true, onboarded

    monkeypatch.setattr(companies_mod, "get_company_llm_config", _flaky)
    with _bind("co-1"):
        with pytest.raises(KeyResolutionUnavailableError):
            resolve_llm_api_key("sk-ant-platform")
        assert resolve_llm_api_key("sk-ant-platform") == "sk-ant-platform"
    assert calls["n"] == 2


# ── client factories go through the resolver ─────────────────────────────────

def test_all_three_factories_honor_company_key(isolated_settings, monkeypatch, fernet_key):
    import app.design_agent.client as da_client
    import app.llm as llm
    import app.routes.agent_chat as agent_chat
    from app.connectors.tokens import encrypt_token_json

    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-platform")
    monkeypatch.setattr(da_client.settings, "anthropic_api_key", "sk-ant-platform")
    monkeypatch.setattr(da_client.settings, "design_agent_anthropic_api_key", "sk-ant-design")
    monkeypatch.setattr(agent_chat.settings, "anthropic_api_key", "sk-ant-platform")

    _stub_config(monkeypatch, cipher=encrypt_token_json("sk-ant-COMPANY"), onboarded=True)
    with _bind("co-1"):
        assert llm.get_client().api_key == "sk-ant-COMPANY"
        # Company key overrides even the dedicated design-agent key.
        assert da_client.get_design_agent_client().api_key == "sk-ant-COMPANY"
        assert agent_chat.get_llm_client().api_key == "sk-ant-COMPANY"


def test_factory_uses_platform_when_unbound(isolated_settings, monkeypatch):
    import app.llm as llm

    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-platform")
    assert llm.get_client().api_key == "sk-ant-platform"


def test_factory_raises_after_onboarding_without_key(isolated_settings, monkeypatch):
    import app.llm as llm
    from app.llm_keys import CompanyKeyRequiredError

    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-platform")
    _stub_config(monkeypatch, cipher=None, use_platform=False, onboarded=True)
    with _bind("co-1"):
        with pytest.raises(CompanyKeyRequiredError):
            llm.get_client()


def test_embeddings_ignore_company_binding(isolated_settings, monkeypatch, fernet_key):
    """The OpenAI embedding path never routes through the company Claude key —
    with no OpenAI key it returns zero-vectors regardless of the binding."""
    from app.connectors.tokens import encrypt_token_json
    from app.graph.embeddings import EMBEDDING_DIM, embed_texts

    _stub_config(monkeypatch, cipher=encrypt_token_json("sk-ant-COMPANY"), onboarded=True)
    with _bind("co-1"):
        vecs = embed_texts(["hello"])
    assert len(vecs) == 1 and vecs[0] == [0.0] * EMBEDDING_DIM


# ── middleware binds the request ─────────────────────────────────────────────

def test_middleware_binds_and_resets(isolated_settings, monkeypatch):
    import app.middleware_llm_key as mw_mod
    from app.llm_keys import current_company_id

    monkeypatch.setattr(mw_mod, "company_id_for_request", lambda **_kw: "co-42")

    seen: dict = {}

    async def fake_app(scope, receive, send):
        seen["during"] = current_company_id()

    mw = mw_mod.CompanyLLMKeyMiddleware(fake_app)
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer x")]}

    import asyncio

    asyncio.run(mw(scope, None, None))
    assert seen["during"] == "co-42"
    assert current_company_id() is None  # reset after the request


def test_middleware_passes_through_when_unresolved(isolated_settings, monkeypatch):
    import app.middleware_llm_key as mw_mod
    from app.llm_keys import current_company_id

    monkeypatch.setattr(mw_mod, "company_id_for_request", lambda **_kw: None)
    seen: dict = {}

    async def fake_app(scope, receive, send):
        seen["during"] = current_company_id()

    mw = mw_mod.CompanyLLMKeyMiddleware(fake_app)
    import asyncio

    asyncio.run(mw({"type": "http", "headers": []}, None, None))
    assert seen["during"] is None


# ── company_id_for_request resolves a real membership ────────────────────────

def test_company_id_for_request_resolves_membership(tenant_client):
    from app.auth import company_id_for_request

    t = tenant_client.make(slug="acme")
    bearer = tenant_client.bearer(t.user_id)["Authorization"]
    cid = company_id_for_request(
        authorization=bearer, sprntly_app_session=None, sprntly_demo_session=None
    )
    assert cid == t.company_id


# ── Admin routes (unchanged behaviour) ───────────────────────────────────────

def test_put_get_delete_llm_key_roundtrip(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    c = t.client

    assert c.get("/v1/admin/llm-key").json() == {"configured": False, "masked": None}

    r = c.put("/v1/admin/llm-key", json={"api_key": "sk-ant-abcdef1234567890WXYZ"})
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": True, "masked": "sk-ant-…WXYZ"}

    assert c.get("/v1/admin/llm-key").json() == {"configured": True, "masked": "sk-ant-…WXYZ"}

    assert c.delete("/v1/admin/llm-key").json() == {"configured": False, "masked": None}
    assert c.get("/v1/admin/llm-key").json()["configured"] is False


def test_put_rejects_non_anthropic_key(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    r = t.client.put("/v1/admin/llm-key", json={"api_key": "sk-openai-nope-123456"})
    assert r.status_code == 400
    assert "sk-ant-" in r.json()["detail"]


def test_llm_key_restricted_to_owner_admin(tenant_client, fernet_key):
    from app.db.client import require_client

    t = tenant_client.make(slug="acme")
    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", t.company_id
    ).execute()

    assert t.client.get("/v1/admin/llm-key").status_code == 403
    assert t.client.put(
        "/v1/admin/llm-key", json={"api_key": "sk-ant-abcdef1234567890WXYZ"}
    ).status_code == 403


def test_llm_key_requires_auth(unauth_client):
    assert unauth_client.get("/v1/admin/llm-key").status_code == 401

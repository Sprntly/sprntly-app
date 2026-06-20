"""The design-agent Node subprocesses (Vite build + autofixer) run agent-
generated, user-influenced code, so they must execute with a secret-free
environment. Regression guard for the 2026-06-18 build-time env-exfiltration
vector (an obfuscated payload in postcss.config.js read the full process env)."""
import pytest

from app.design_agent import autofixer
from app.design_agent.build_env import _ALLOWLIST, scrubbed_node_env

# Representative secrets that live in the backend env and must never reach a build.
SECRET_KEYS = [
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_DB_PASSWORD",
    "TOKEN_ENCRYPTION_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "SLACK_SIGNING_SECRET",
    "JWT_SECRET",
]


@pytest.fixture
def env_with_secrets(monkeypatch):
    for k in SECRET_KEYS:
        monkeypatch.setenv(k, f"super-secret-{k}")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


def test_scrubbed_env_drops_every_secret(env_with_secrets):
    env = scrubbed_node_env()
    leaked = [k for k in SECRET_KEYS if k in env]
    assert not leaked, f"secrets leaked into the build env: {leaked}"


def test_scrubbed_env_keeps_path(env_with_secrets):
    assert scrubbed_node_env()["PATH"] == "/usr/bin:/bin"


def test_scrubbed_env_applies_extra_without_reintroducing_secrets(env_with_secrets):
    env = scrubbed_node_env({"NODE_PATH": "/x/node_modules"})
    assert env["NODE_PATH"] == "/x/node_modules"
    assert not [k for k in SECRET_KEYS if k in env]


def test_allowlist_contains_no_secret_shaped_names():
    # Default-deny: the allowlist itself must never carry a secret-shaped var.
    for name in _ALLOWLIST:
        assert not any(t in name for t in ("SECRET", "TOKEN", "KEY", "PASSWORD")), name


def test_autofixer_subprocess_env_is_secret_free(env_with_secrets):
    env = autofixer._subprocess_env()
    leaked = [k for k in SECRET_KEYS if k in env]
    assert not leaked, f"autofixer leaked secrets: {leaked}"
    # ...while still setting NODE_PATH so `require('@babel/parser')` resolves.
    assert env.get("NODE_PATH")

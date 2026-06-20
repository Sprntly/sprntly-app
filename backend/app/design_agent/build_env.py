"""Minimal, secret-free environment for design-agent Node subprocesses.

The Vite build (``storage.py``) and the autofixer (``autofixer.py``) both run
Node on agent-generated — i.e. *user-influenced* — code. Those subprocesses
must NOT inherit the backend's process environment, which holds every secret
the service has: DB keys (``SUPABASE_*``), OAuth client secrets, the Fernet
``TOKEN_ENCRYPTION_KEY``, and LLM API keys.

The 2026-06-18 incident proved why: an obfuscated payload appended to
``prototype-runtime/postcss.config.js`` executed during ``vite build`` and had
``require`` + the full ``os.environ`` to exfiltrate from. The build subprocess
inheriting the whole env is the exact vector. With real users feeding inputs
that get built, a crafted component / config / dependency is a build-time RCE
that reaches every secret.

So we pass an *allowlisted* env: everything the Node/npm/Vite toolchain needs to
build, and nothing it doesn't. Default-deny — a new secret added to ``.env``
never leaks here unless it is explicitly added to the allowlist (it shouldn't
be).
"""
import os

# Variables the Node / npm / Vite toolchain legitimately needs to run a build.
# Deliberately conservative: anything not listed — notably *_KEY, *_SECRET,
# TOKEN_*, SUPABASE_*, ANTHROPIC_*, OPENAI_*, *_CLIENT_SECRET, JWT_* — is
# withheld. The real build is exercised by the integration tests, so a missing
# var surfaces there (widen the allowlist) rather than leaking secrets to fix it.
_ALLOWLIST = frozenset({
    "PATH",            # locate node/npx
    "HOME",            # npm cache / config live under $HOME by default
    "USER", "LOGNAME", "SHELL", "TERM",
    "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE",
    "NODE_PATH",       # module resolution (autofixer points it at @babel/parser)
    "NODE_OPTIONS",    # from our env, not user input — safe to pass through
    "NPM_CONFIG_CACHE", "XDG_CACHE_HOME",
})


def scrubbed_node_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an allowlisted copy of ``os.environ`` for a Node subprocess.

    Only the build-relevant variables above are carried over; the backend's
    secrets are dropped. ``extra`` (e.g. a computed ``NODE_PATH``) is applied
    last so callers can add what they need without re-opening the whole env.
    """
    env = {k: v for k, v in os.environ.items() if k in _ALLOWLIST}
    # Guarantee a PATH even on an exotic host so npx is still findable.
    env.setdefault("PATH", os.defpath)
    if extra:
        env.update(extra)
    return env

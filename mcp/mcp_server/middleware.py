"""Bearer-token auth as a pure ASGI wrapper around the MCP app.

Deliberately NOT `starlette.middleware.base.BaseHTTPMiddleware` — that base
class buffers the entire response before forwarding it, which breaks
Streamable HTTP's chunked/event-stream responses. A plain ASGI callable
class has no such buffering.

Also deliberately NOT a second `Starlette(...)` instance mounting the MCP
app via `Mount(...)` — composing two Starlette apps requires manually
threading the inner app's lifespan into the outer one (a well-documented
footgun: "a mounted sub-application's lifespan never runs" otherwise). This
class sidesteps that entirely: it isn't a second app with its own lifespan,
it's a transparent wrapper that forwards any non-"http" scope (i.e.
"lifespan") straight to the wrapped app untouched, so the MCP app's own
startup/shutdown fires exactly as if this wrapper weren't there.
"""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs

from .auth import CompanyContext, _current_company
from .backend_client import BackendError, resolve_token

# Opt-in (default OFF): also accept the bearer token from a `?token=` query
# param, not just the Authorization header. This exists for MCP clients that
# CANNOT set a custom header — notably claude.ai's custom-connector UI, which
# authenticates via OAuth and gives no field to paste a static token. With
# this on, the connector URL itself carries the credential
# (https://host/mcp?token=sprn_mcp_...).
#
# SECURITY TRADEOFF: a token in a URL leaks into access logs, proxy logs, and
# ngrok's request inspector far more readily than a header does. Keep this OFF
# in production (header-only); turn it on only for a trusted local/tunnel test.
_ALLOW_URL_TOKEN = os.environ.get("MCP_ALLOW_URL_TOKEN", "").lower() in (
    "1",
    "true",
    "yes",
)


def _token_from_scope(scope) -> str | None:
    """Extract the bearer token from the Authorization header, or (when
    MCP_ALLOW_URL_TOKEN is on) from a `?token=` query param as a fallback."""
    headers = dict(scope.get("headers") or [])
    raw_auth = headers.get(b"authorization", b"").decode("latin-1")
    if raw_auth.startswith("Bearer "):
        return raw_auth[len("Bearer "):].strip() or None

    if _ALLOW_URL_TOKEN:
        qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        values = qs.get("token")
        if values and values[0].strip():
            return values[0].strip()

    return None


async def _send_json(send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class BearerAuthMiddleware:
    def __init__(self, app, exempt_paths: frozenset[str] = frozenset()):
        self.app = app
        self.exempt_paths = exempt_paths

    def _is_exempt(self, path: str) -> bool:
        if path in self.exempt_paths:
            return True
        # OAuth discovery + dynamic-registration probes. An MCP client
        # (e.g. claude.ai) hits these to decide whether the server speaks
        # OAuth. We DON'T — auth is a static bearer token — so letting them
        # fall through to the inner app returns a clean 404 ("no OAuth here")
        # instead of a 401, which otherwise reads as "auth required, go do
        # OAuth" and traps the client in a failing OAuth handshake.
        if path.startswith("/.well-known/"):
            return True
        if path == "/register":
            return True
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._is_exempt(scope["path"]):
            await self.app(scope, receive, send)
            return

        token = _token_from_scope(scope)
        if not token:
            await _send_json(send, 401, {"error": "missing_bearer_token"})
            return

        try:
            resolved = await resolve_token(token)
        except BackendError:
            await _send_json(send, 401, {"error": "invalid_or_revoked_token"})
            return

        ctx = CompanyContext(
            company_id=resolved["company_id"],
            user_id=resolved["user_id"],
            role=resolved["role"],
        )
        reset_token = _current_company.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_company.reset(reset_token)

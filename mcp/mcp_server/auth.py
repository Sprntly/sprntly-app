"""Resolved-caller context for the current MCP request.

Populated by BearerAuthMiddleware (see middleware.py) before a tool call
runs, read by tool functions (see tools.py) via require_current_company().
Deliberately independent of any auth machinery the `mcp` SDK itself ships
(e.g. its OAuth-oriented TokenVerifier/AuthSettings) — that surface is built
for a full OAuth resource-server flow (issuer_url, resource_server_url,
etc.), which is heavier than this server's model of "customer pastes one
static bearer token minted from Settings". Resolving the token ourselves
against the backend, entirely outside the SDK's auth wiring, is simpler and
has no dependency on SDK internals that were hard to pin down offline.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyContext:
    company_id: str
    user_id: str
    role: str


class McpAuthError(Exception):
    """Raised when no authenticated company is available in context."""


_current_company: contextvars.ContextVar["CompanyContext | None"] = (
    contextvars.ContextVar("_current_company", default=None)
)


def require_current_company() -> CompanyContext:
    ctx = _current_company.get()
    if ctx is None:
        raise McpAuthError("no authenticated company in context")
    return ctx

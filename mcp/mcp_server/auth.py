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
    # Company-membership role (owner/admin/member) — NOT the tool gate.
    role: str
    # What the TOKEN was minted for: 'developer' (ticket + PRD tools only)
    # or 'pm' (full tool set). Defaults to 'pm' so a backend that predates
    # token roles keeps resolving to the full set it always granted.
    token_role: str = "pm"


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


def current_company_or_none() -> CompanyContext | None:
    """The resolved caller if any — for read paths (tool listing) that must
    degrade rather than raise when no request context is set."""
    return _current_company.get()

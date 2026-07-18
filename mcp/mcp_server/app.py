"""ASGI app factory for the Sprntly MCP server.

VERIFY-ON-FIRST-BUILD: the exact `mcp` SDK surface used below (`FastMCP`,
`stateless_http=`, `.custom_route()`, `.streamable_http_app()`) was
cross-checked against multiple independent sources (PyPI quickstart for
`mcp` 1.28.1, the SDK's own ASGI-mounting docs, several third-party
walkthroughs) but NOT against a locally installed copy, since this service
is meant to be built inside Docker rather than the host environment. If any
of these calls raise an AttributeError on first `docker build`/run, check
`python -c "from mcp.server.fastmcp import FastMCP; help(FastMCP)"` inside
the built image for the current method names — the MCP SDK's HTTP-transport
API has shifted across versions.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from .auth import current_company_or_none
from .middleware import BearerAuthMiddleware
from .sentry import init_sentry
from .tools import PM_ONLY_TOOLS, register_tools

# Shown to the MCP client/model on connect (FastMCP `instructions`) to orient
# it on the intended workflow. Everything is already scoped to the one Sprntly
# workspace the token belongs to — the model never needs to pass a company id.
_INSTRUCTIONS = (
    "This server connects your Sprntly workspace: product briefs, PRDs, and "
    "tickets. Typical developer flow: call list_tickets to see the tickets "
    "assigned to you — it only ever returns the token owner's own tickets "
    "(optionally filter by status), get_ticket for the full detail you need to "
    "implement it "
    "(description, acceptance criteria, scope, comments), get_prd for the "
    "parent product context, list_prd_tickets for every ticket in that "
    "PRD (the full scope, not just yours), get_prd_prototype for the "
    "interactive design prototype behind it, and get_prd_evidence for the "
    "research evidence explaining why the PRD exists. As you work, "
    "update_ticket_fields to move status "
    "(e.g. 'In progress' -> 'In review' -> 'Done'), add_ticket_comment to note "
    "progress, and add_ticket_attachment to link your PR or branch. Ticket ids "
    "are opaque keys like 'prd-42-a1b2c3d4e5f6' — always pass them back exactly "
    "as returned by list_tickets/get_ticket, never shortened or re-derived. All "
    "data is scoped to this one workspace; you never pass a company or dataset id. "
    "The tools you see match your token's role: developer tokens cover tickets "
    "and PRDs; workspace tools (datasets, backlog, weekly brief) need a PM token."
)


class RoleScopedFastMCP(FastMCP):
    """FastMCP that filters tools/list by the caller's token role.

    FastMCP._setup_handlers registers the BOUND `self.list_tools`, so this
    override IS the handler the low-level server calls for every tools/list
    request. BearerAuthMiddleware has already resolved the bearer token into
    a CompanyContext contextvar by then, so the listing is per-request even
    though the tool registry itself is process-global.

    Listing is UX, not the security boundary — a client can still call a
    tool it wasn't shown, which is why every PM-only tool impl re-checks the
    role itself (see tools.py). Fail closed: no resolved context (can't
    happen on the authed /mcp path, but cheap to be safe) lists the
    developer subset, never the full set.
    """

    async def list_tools(self):
        tools = await super().list_tools()
        ctx = current_company_or_none()
        if ctx is None or ctx.token_role != "pm":
            tools = [t for t in tools if t.name not in PM_ONLY_TOOLS]
        return tools


def _transport_security() -> TransportSecuritySettings | None:
    """DNS-rebinding / Host-validation config for the Streamable HTTP transport.

    The SDK's default protection validates the request Host against an
    allow-list (localhost only) and returns 421 Misdirected Request for
    anything else — which rejects requests arriving through a tunnel/proxy
    (ngrok, a load balancer, api.sprntly.ai) whose Host isn't localhost.

    Two env knobs, both default-safe (protection stays ON with the SDK
    default when neither is set):
      - MCP_ALLOWED_HOSTS: comma-separated Host allow-list (the correct prod
        setting, e.g. "api.sprntly.ai"). Also used for allowed_origins.
      - MCP_DISABLE_DNS_REBINDING_PROTECTION: turn the check off entirely.
        Convenient for a throwaway tunnel test where the ngrok subdomain
        changes each restart; do NOT use in production.
    """
    if os.environ.get("MCP_DISABLE_DNS_REBINDING_PROTECTION", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    raw_hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if raw_hosts:
        hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=hosts,
        )

    return None  # SDK default (localhost-only protection)


def _stateless_http() -> bool:
    """Whether to run the Streamable HTTP transport statelessly (no
    server-side session, no Mcp-Session-Id issued).

    Default FALSE (stateful): remote MCP clients — notably claude.ai's
    custom connector — establish a persistent session and expect an
    Mcp-Session-Id from `initialize` to reuse on follow-up requests. In
    stateless mode the server tears the session down after every request
    ("Terminating session: None"), which those clients read as a dropped
    connection ("couldn't reach the MCP server") even though each request
    individually returns 200.

    Stateful is safe here: our deploy is a single uvicorn process (see
    mcp/deploy/sprntly-mcp.service — no --workers), so in-memory session
    state has no cross-worker coherence problem. Auth is unaffected either
    way — the bearer token is re-resolved by the middleware on every request
    regardless of session mode. Override with MCP_STATELESS_HTTP=1 only if
    the service is ever fanned out across workers/instances (then also add a
    shared session/event store or sticky routing).
    """
    return os.environ.get("MCP_STATELESS_HTTP", "").lower() in ("1", "true", "yes")


def create_app():
    # No-op unless SENTRY_DSN is set (loaded from .env by __main__).
    init_sentry()
    mcp = RoleScopedFastMCP(
        "sprntly",
        instructions=_INSTRUCTIONS,
        stateless_http=_stateless_http(),
        transport_security=_transport_security(),
    )
    register_tools(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return PlainTextResponse("ok")

    mcp_asgi_app = mcp.streamable_http_app()

    # Pure ASGI wrap (see middleware.py's docstring for why this is NOT a
    # second Starlette()/Mount() composition) — /health stays unauthenticated
    # since health probes must be reachable before any token exists.
    return BearerAuthMiddleware(mcp_asgi_app, exempt_paths=frozenset({"/health"}))

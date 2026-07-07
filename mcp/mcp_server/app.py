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

from .middleware import BearerAuthMiddleware
from .tools import register_tools

# Shown to the MCP client/model on connect (FastMCP `instructions`) to orient
# it on the intended workflow. Everything is already scoped to the one Sprntly
# workspace the token belongs to — the model never needs to pass a company id.
_INSTRUCTIONS = (
    "This server connects your Sprntly workspace: product briefs, PRDs, and "
    "tickets. Typical developer flow: call list_tickets to find work (optionally "
    "filter by status), get_ticket for the full detail you need to implement it "
    "(description, acceptance criteria, scope, comments), and get_prd for the "
    "parent product context. As you work, update_ticket_fields to move status "
    "(e.g. 'In progress' -> 'In review' -> 'Done'), add_ticket_comment to note "
    "progress, and add_ticket_attachment to link your PR or branch. All data is "
    "scoped to this one workspace; you never pass a company or dataset id."
)


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
    mcp = FastMCP(
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

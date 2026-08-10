"""Per-request server-side timing — the measurement that did not exist.

Before this, the only latency instrumentation in the repo was the LLM
gateway's `agent_decision_log` row. Every NON-model endpoint — auth, page
data, ticket lists, the poll loops the UI runs constantly — was unmeasured,
so no API p95 could be computed for any route.

nginx now logs `$upstream_response_time` (see backend/deploy/nginx.conf), which
covers much of this. What it CANNOT give is the route TEMPLATE: nginx sees
`/v1/prd/1043/stream` and every id becomes its own bucket, so a p95 per
endpoint is not derivable from the access log alone. This middleware logs the
template (`/v1/prd/{prd_id}/stream`), which is the thing you can actually
aggregate on. It also keeps working for anything that bypasses nginx.

Pure-ASGI, NOT BaseHTTPMiddleware — same reason as `middleware_llm_key`: the
BaseHTTPMiddleware wrapper breaks contextvar propagation, and it also forces
every response through an anyio stream, which would add a copy to exactly the
streaming responses this codebase cares most about.

Log discipline: one line per request, identifiers only — method, route
template, status, duration. Never the path's query string (tokens ride there
on the EventSource routes), never a body.
"""
from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Above this, the line is a WARNING so it stands out without needing a
# dashboard. 3s is well above every healthy non-generation endpoint and well
# below the generation ones, which are expected to be slow and are already
# attributed in agent_decision_log.
_SLOW_MS = 3000

# Long-lived by design: SSE subscriptions stay open for the whole generation,
# so their duration measures how long the client watched, not how slow the
# server was. Logged at DEBUG so they never pollute a p95 or trip the slow
# warning. Matched on the route TEMPLATE, so a new stream route must be added
# here deliberately.
_STREAMING_SUFFIXES = ("/stream", "/events")


def _route_template(scope: Scope) -> str:
    """The parameterised path (`/v1/prd/{prd_id}`), falling back to the raw path.

    Starlette assigns `scope["route"]` once the router has matched, and this
    middleware reads it AFTER awaiting the app, so the match has happened by
    then. A 404 never matches a route, so it falls back to the concrete path —
    which is correct: an unrouted path has no template.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return scope.get("path") or "-"


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp, *, slow_ms: int = _SLOW_MS) -> None:
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # In `finally` so a raising endpoint is still timed — an endpoint
            # that blows up after 30s is exactly the one worth seeing.
            duration_ms = int((time.perf_counter() - start) * 1000)
            template = _route_template(scope)
            if template.endswith(_STREAMING_SUFFIXES):
                level = logging.DEBUG
            elif duration_ms >= self.slow_ms:
                level = logging.WARNING
            else:
                level = logging.INFO
            logger.log(
                level,
                "request method=%s route=%s status=%d duration_ms=%d",
                scope.get("method", "-"), template, status, duration_ms,
            )

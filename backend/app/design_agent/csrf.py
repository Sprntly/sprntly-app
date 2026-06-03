"""Server-side CSRF / Origin backstop for the authed mutating Design Agent routes.

Sprntly's main app has no CSRF defense (codebase-agent-patterns.md): a cookie-authed
mutating route is forgeable cross-site because the browser still attaches the session
cookie to a cross-origin POST. CORS does NOT close this hole — CORS is enforced by the
*browser*, and only stops a script from READING the cross-origin response; the request
still REACHES the server and the cookie-authed mutation still runs. This dependency
rejects such a request at the server, before the handler runs — the actual CSRF defense.

It reuses the SAME allow-list CORSMiddleware uses (`settings.origins_list`, derived from
`ALLOWED_ORIGINS` in config.py) — there is intentionally NO second allow-list. CORS and
this check operate at different layers but share one source of truth.

Applied ONLY to authed mutating Design Agent routes (the `require_app_session` POST/
PATCH/DELETE handlers). It is NEVER attached to the anonymous public `/by-token/*` share
routes: those are cross-origin by design (a share link opened from any context) and carry
no forgeable session, so an Origin gate there would break public prototype viewing and
public commenting (F6). Exemption is by construction — the public routes simply do not
list this dependency.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger("app.design_agent.csrf")


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: reject a mutating request whose `Origin` header is missing
    or not in `settings.origins_list`.

    Fail-closed: a missing `Origin` is rejected too. The authed Design Agent surface is
    browser-driven (the Next.js app always sends `Origin` on fetch), so a same-origin
    legitimate caller always passes; a same-origin *non-CORS* caller (e.g. server-to-
    server) would lack `Origin` and be rejected — acceptable, and a handoff exception if a
    legitimate non-browser authed caller ever emerges (not a P5-06 concern).

    On rejection logs `csrf_origin_rejected route=<path> origin_present=<bool>` — never the
    raw Origin value (Rule #24: attacker-controlled header, log a boolean + the route only).
    """
    origin = request.headers.get("origin")
    if not origin or origin not in settings.origins_list:
        logger.warning(
            "csrf_origin_rejected route=%s origin_present=%s",
            request.url.path,
            origin is not None,
        )
        raise HTTPException(status_code=403, detail={"error": "origin_mismatch"})

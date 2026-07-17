"""Request middleware that binds the acting company for LLM-key resolution.

A pure-ASGI middleware (NOT BaseHTTPMiddleware — that one breaks contextvar
propagation) that resolves the request's company from its credentials and binds
it via `app.llm_keys.company_llm_key` for the whole request. Because the bind
happens in the request's asyncio task before the app is awaited, it propagates
to sync endpoints (run in a threadpool with a copied context), async endpoints,
and any task the request spawns (`asyncio.create_task`, BackgroundTasks).

This is what guarantees EVERY request-scoped Claude call is enforced against the
company's key policy without each call site opting in. Non-request contexts (the
KG gateway, the weekly-brief scheduler, warm Ask jobs, the design-agent worker
process) bind explicitly via `company_llm_key(...)` at their own entry points.

Best-effort: any request without a resolvable single-company membership (public
routes, unauthenticated, legacy cookie sessions) is left unbound → the platform
key, exactly as before.
"""
from __future__ import annotations

import functools

import anyio
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import company_id_for_request
from app.llm_keys import company_llm_key


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _cookie(scope: Scope, name: str) -> str | None:
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


class CompanyLLMKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        company_id: str | None = None
        try:
            # company_id_for_request does blocking I/O (JWT/JWKS + PostgREST
            # membership lookup) — run it on the threadpool, never the loop.
            company_id = await anyio.to_thread.run_sync(
                functools.partial(
                    company_id_for_request,
                    authorization=_header(scope, b"authorization"),
                    sprntly_app_session=_cookie(scope, "sprntly_app_session"),
                    sprntly_demo_session=_cookie(scope, "sprntly_demo_session"),
                )
            )
        except Exception:  # noqa: BLE001 — binding must never break a request
            company_id = None

        if not company_id:
            await self.app(scope, receive, send)
            return

        with company_llm_key(company_id):
            await self.app(scope, receive, send)

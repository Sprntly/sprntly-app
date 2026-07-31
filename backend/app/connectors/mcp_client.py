"""Minimal Model Context Protocol client over Streamable HTTP.

Sprntly already SERVES MCP (see the top-level `mcp/` package). This is the
other direction: talking to somebody else's MCP server as a data source,
because some vendors now ship MCP as their only programmatic read surface.
Marvin (heymarvin.com) is the first — it has no public REST API at all.

Why hand-rolled instead of the official `mcp` SDK: that SDK is async-first and
would be the only async dependency inside the otherwise synchronous,
`requests`-based connector/puller layer. The client surface we actually need is
three calls (initialize / tools/list / tools/call) over plain JSON-RPC, so the
SDK's transport machinery buys us nothing and costs a concurrency model.

Transport notes (spec revision 2025-06-18, "Streamable HTTP"):
  - One endpoint. Every request is a POST carrying a JSON-RPC message.
  - `Accept` MUST offer both `application/json` and `text/event-stream`; the
    server picks. A single-response call usually comes back as plain JSON, but
    servers are free to answer any request as a one-message SSE stream, so both
    are parsed here.
  - The server MAY assign a session via the `Mcp-Session-Id` response header on
    initialize; if it does, every later request must echo it back.
  - `MCP-Protocol-Version` is required on requests after initialization.
  - Notifications get `202 Accepted` with an empty body — never parsed.

Auth is out of scope: callers pass an already-valid bearer token. HTTP 401/403
surfaces as `McpError` with `status_code` set, which is what the ingest
auto-sync inspects to decide whether to force a token refresh and retry.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

#: Spec revision we negotiate. Servers that only speak an older revision reply
#: with their own version in the initialize result; we accept whatever they say
#: rather than hard-failing, since we only use the universally-stable subset.
PROTOCOL_VERSION = "2025-06-18"

_TIMEOUT = 45


class McpError(RuntimeError):
    """An MCP call failed — transport, HTTP status, or JSON-RPC error object.

    ``status_code`` carries the HTTP status when there was one (None for a
    JSON-RPC-level error or a transport failure). Callers key token-refresh
    retries off 401/403, mirroring how the REST-based pullers behave.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Pull JSON-RPC messages out of an SSE body.

    Only `data:` lines matter to us — event names, ids and retry hints are
    transport bookkeeping. A multi-line data field is joined with newlines per
    the SSE spec. Undecodable payloads are skipped rather than fatal: a server
    is allowed to interleave messages we don't care about.
    """
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.debug("mcp: skipping non-JSON SSE payload: %s", raw[:200])
            return
        if isinstance(parsed, dict):
            messages.append(parsed)

    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.strip():
            flush()
    flush()
    return messages


class McpSession:
    """One initialized MCP conversation with a remote server.

    Use as a context manager so the session is torn down on the way out:

        with McpSession(url, token) as s:
            tools = s.list_tools()
            out = s.call_tool("search", {"query": "onboarding"})
    """

    def __init__(
        self,
        url: str,
        access_token: str,
        *,
        timeout: int = _TIMEOUT,
        client_name: str = "sprntly",
        client_version: str = "1.0.0",
    ):
        self.url = url.rstrip("/")
        self._token = access_token
        self._timeout = timeout
        self._client_name = client_name
        self._client_version = client_version
        self._session_id: str | None = None
        self._protocol_version = PROTOCOL_VERSION
        self._next_id = 0
        self.server_info: dict[str, Any] = {}
        self._initialized = False

    # ── context management ──────────────────────────────────────────────

    def __enter__(self) -> "McpSession":
        self.initialize()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Best-effort session teardown (DELETE with the session id).

        Servers that don't do sessions have nothing to delete and will 405;
        that's expected and ignored. Teardown failures never propagate — the
        caller has already got its data.
        """
        if not self._session_id:
            return
        try:
            requests.delete(self.url, headers=self._headers(), timeout=10)
        except requests.RequestException:
            logger.debug("mcp: session teardown failed for %s", self.url)
        finally:
            self._session_id = None
            self._initialized = False

    # ── plumbing ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _post(self, payload: dict[str, Any], *, expect_reply: bool) -> dict[str, Any]:
        """POST one JSON-RPC message. Returns the matching response object.

        `expect_reply=False` is for notifications, which the server answers
        with an empty 202 — we return {} rather than hunting for a response
        that will never come.
        """
        try:
            resp = requests.post(
                self.url,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise McpError(f"MCP request to {self.url} failed: {e}") from e

        # Capture (or refresh) the session id whenever the server offers one.
        session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
            "mcp-session-id"
        )
        if session_id:
            self._session_id = session_id

        if not resp.ok:
            detail = (resp.text or "")[:300]
            raise McpError(
                f"MCP server returned HTTP {resp.status_code}: {detail}",
                status_code=resp.status_code,
            )

        if not expect_reply:
            return {}

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in content_type:
            messages = _parse_sse(resp.text)
        else:
            body = (resp.text or "").strip()
            if not body:
                return {}
            try:
                parsed = json.loads(body)
            except ValueError as e:
                raise McpError(
                    f"MCP server returned non-JSON body: {body[:200]}"
                ) from e
            messages = parsed if isinstance(parsed, list) else [parsed]

        want = payload.get("id")
        for message in messages:
            if not isinstance(message, dict) or message.get("id") != want:
                continue  # server-initiated requests / unrelated notifications
            if "error" in message:
                err = message["error"] or {}
                raise McpError(
                    f"MCP error {err.get('code')}: {err.get('message')}"
                )
            return message.get("result") or {}

        raise McpError(
            f"MCP server sent no response for {payload.get('method')!r}"
        )

    # ── protocol ────────────────────────────────────────────────────────

    def initialize(self) -> dict[str, Any]:
        """Handshake. Returns the server's initialize result.

        Sends the `notifications/initialized` follow-up the spec requires
        before any other request is legal. We advertise no client capabilities
        because we neither sample, elicit, nor expose roots — we only read.
        """
        if self._initialized:
            return self.server_info
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": self._client_name,
                        "version": self._client_version,
                    },
                },
            },
            expect_reply=True,
        )
        negotiated = result.get("protocolVersion")
        if isinstance(negotiated, str) and negotiated:
            self._protocol_version = negotiated
        self.server_info = result.get("serverInfo") or {}
        self._initialized = True
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_reply=False,
        )
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool the server exposes, following `nextCursor` pagination.

        Page count is capped: a server that returns a cursor forever would
        otherwise spin a sync thread indefinitely.
        """
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = self._post(
                {
                    "jsonrpc": "2.0",
                    "id": self._rpc_id(),
                    "method": "tools/list",
                    "params": params,
                },
                expect_reply=True,
            )
            tools.extend(t for t in (result.get("tools") or []) if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool. Returns the raw `tools/call` result.

        A tool that fails *at the domain level* sets `isError: true` in the
        result rather than raising a JSON-RPC error — the distinction is
        deliberate in MCP (it lets a model see and recover from the failure).
        We surface it as McpError so callers treat it like any other failure.
        """
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            expect_reply=True,
        )
        if result.get("isError"):
            raise McpError(f"MCP tool {name!r} failed: {text_from_result(result)[:300]}")
        return result


# ── result helpers ──────────────────────────────────────────────────────


def text_from_result(result: dict[str, Any]) -> str:
    """Concatenate the text blocks of a `tools/call` result.

    MCP content is a list of typed blocks; image/audio/resource-link blocks
    carry nothing we can extract, so only `text` blocks contribute. Embedded
    resources are unwrapped one level to reach their text.
    """
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "resource":
            resource = block.get("resource") or {}
            if isinstance(resource.get("text"), str):
                parts.append(resource["text"])
    return "\n".join(p for p in parts if p.strip())


def records_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort list-of-objects out of a `tools/call` result.

    Servers disagree about how to return collections, so we try, in order:
    `structuredContent` (the spec's typed channel, either a list or a dict
    wrapping one), then JSON parsed out of the text blocks. Returns [] when the
    payload is prose rather than data — callers fall back to the text.
    """
    structured = result.get("structuredContent")
    found = _coerce_records(structured)
    if found is not None:
        return found

    text = text_from_result(result).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        return []
    return _coerce_records(parsed) or []


def _coerce_records(payload: Any) -> list[dict[str, Any]] | None:
    """A list of dicts out of `payload`, or None if it isn't collection-shaped.

    Handles the three shapes seen in the wild: a bare list, a dict that IS one
    record, and a dict wrapping the list under some key (`results`, `items`,
    `files`, …). The wrapper key is discovered rather than hardcoded — the
    first value that is a list of dicts wins.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return None
    for value in payload.values():
        if isinstance(value, list) and any(isinstance(r, dict) for r in value):
            return [r for r in value if isinstance(r, dict)]
    # A dict with no nested collection is itself a single record — but only if
    # it carries something identifier-ish, else it's a status envelope.
    if any(k in payload for k in ("id", "name", "title", "key", "uuid")):
        return [payload]
    return None

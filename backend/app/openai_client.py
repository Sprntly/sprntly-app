"""OpenAI, wearing the Anthropic Messages API's clothes.

A company can now choose OpenAI as its LLM provider (Settings → Admin, or the
onboarding api-key step). Rather than teach ~40 call sites to speak two dialects,
this module exposes ONE object — `OpenAIMessagesClient` — that quacks like
`anthropic.Anthropic` for everything this codebase actually uses:

    client.messages.create(model=..., system=..., messages=[...], tools=[...])
    with client.messages.stream(...) as s: s.text_stream / iter(s) / s.get_final_message()

...and translates on the way in and out. `app.llm.get_client()` returns this
instead of an `Anthropic` when the acting company's provider is 'openai', and
`call_json` / `call_md` / `run_tool_loop` / `_create_with_retries` / the metering
proxy are all unchanged. The alternative — a second set of runners, prompts and
retry logic per provider — is the drift problem that eventually eats the whole
codebase.

WHAT IS TRANSLATED
------------------
Request:
  * `system` (str, or the cache_control block list `_build_base_kwargs` emits)
    → one leading `{"role": "system"}` message. `cache_control` is DROPPED, not
    ported: OpenAI's prompt caching is automatic on prompts over ~1024 tokens
    and has no opt-in marker, so the hint is simply unnecessary there.
  * Anthropic content blocks → OpenAI message parts. `tool_use` becomes
    `assistant.tool_calls`, `tool_result` becomes a `{"role": "tool"}` message.
    Blocks arrive as dicts from callers and as OUR OWN block objects when
    `run_tool_loop` feeds a previous response back in, so `_field` reads both.
  * `tools` → the nested `{"type": "function", "function": {...}}` shape;
    `input_schema` → `parameters`. `tool_choice={"type":"tool","name":X}` →
    `{"type":"function","function":{"name":X}}`.
  * `max_tokens` → `max_completion_tokens`, PLUS reasoning headroom (see
    `_max_completion_tokens`).
  * `temperature` is dropped for the GPT-5 family — those models reject any
    value but the default 1 with a 400 `unsupported_value`, so forwarding the
    repo's `temperature=0.2` call sites would fail every one of them.
  * The Anthropic server-side `web_search_20250305` tool has no Chat Completions
    equivalent as a tool; OpenAI exposes search through dedicated models. The
    tool is stripped and the request is routed to `_SEARCH_MODEL`, which always
    searches before answering — the behaviour `call_with_web_search` wants.

Response:
  * `choices[0].message` → an Anthropic-shaped `content` block list (`text` and
    `tool_use` blocks), `stop_reason`, `model`, `usage`.
  * Usage is re-based, not copied. OpenAI's `prompt_tokens` INCLUDES cached and
    cache-written tokens; Anthropic's `input_tokens` EXCLUDES them and reports
    them in sibling fields. Copying the number straight across would bill cached
    tokens at the full input rate in `RunUsage.est_cost_usd`. `_usage_from`
    subtracts them out so the three fields mean what the pricing table thinks
    they mean.

MODEL IDS
---------
Call sites name Claude models (`claude-sonnet-4-6`, `claude-opus-4-7`, …) in ~25
places. `map_model` translates a Claude id to the OpenAI model of the same TIER,
so no call site needs a provider-conditional. Already-OpenAI ids pass through, so
a future call site can name one directly.

ERRORS
------
Raised as `OpenAIAPIError` subclasses carrying `.status_code`, which
`app.llm._is_retryable` classifies exactly like the Anthropic ones (429/5xx and
transport failures retry; 4xx does not). No `anthropic.*` exception is ever
synthesised — those need a real httpx response object and would lie about where
the failure came from.

No new dependency: this uses `httpx`, already pinned for the rest of the backend,
the same way `app/graph/embeddings.py` uses stdlib urllib rather than pulling in
the OpenAI SDK.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"

# --- model tiers -------------------------------------------------------------
# The repo's three Claude tiers mapped onto the GPT-5.6 family, matched on ROLE
# rather than on price: the cheap router/classifier tier, the default working
# tier, and the deep-reasoning tier. Verified against OpenAI's model + pricing
# docs (developers.openai.com/api/docs/models, /api/docs/pricing) on 2026-08-07 —
# gpt-5.6 Sol / Terra / Luna are the current flagship / balanced / low-cost trio
# and all three serve /v1/chat/completions.
FLAGSHIP_MODEL = "gpt-5.6-sol"
DEFAULT_MODEL = "gpt-5.6-terra"
CHEAP_MODEL = "gpt-5.6-luna"

# Chat Completions has no `web_search` TOOL — OpenAI ships search as dedicated
# models that always search before answering (the Responses API is where search
# is an optional tool). `gpt-5-search-api` is the current, non-deprecated one.
_SEARCH_MODEL = "gpt-5-search-api"

_MODEL_MAP = {
    "claude-haiku-4-5": CHEAP_MODEL,
    "claude-haiku-4-5-20251001": CHEAP_MODEL,
    "claude-sonnet-4-6": DEFAULT_MODEL,
    "claude-sonnet-4-7": DEFAULT_MODEL,
    "claude-opus-4-7": FLAGSHIP_MODEL,
}


def map_model(model: str | None) -> str:
    """Translate a model id to the OpenAI model of the same tier.

    Exact match first, then a family-prefix fallback so a Claude model added to
    a call site tomorrow lands on a sensible tier instead of a 404 from OpenAI.
    Anything that already looks like an OpenAI id is returned untouched.
    """
    if not model:
        return DEFAULT_MODEL
    if model in _MODEL_MAP:
        return _MODEL_MAP[model]
    if not model.startswith("claude"):
        return model  # already an OpenAI id (or a proxy's own name) — pass through
    if "haiku" in model:
        return CHEAP_MODEL
    if "opus" in model:
        return FLAGSHIP_MODEL
    return DEFAULT_MODEL


# GPT-5 family models are reasoning models: they reject `temperature` at any
# value but the default (400 unsupported_value). Several call sites here pass
# temperature=0.2 for determinism, so it is dropped rather than forwarded.
def _supports_temperature(openai_model: str) -> bool:
    return not openai_model.startswith(("gpt-5", "o1", "o3", "o4"))


# Anthropic's `max_tokens` budgets VISIBLE output. OpenAI's
# `max_completion_tokens` budgets reasoning + visible output on the GPT-5 family,
# and reasoning tokens are invisible. Passing the caller's number straight
# through means a long generation (the 2-part PRD asks for 16k) can spend its
# whole budget thinking and return an empty string with finish_reason='length'.
# So: give reasoning its own headroom on top of what the caller asked for.
_REASONING_HEADROOM_TOKENS = 4096
_MAX_COMPLETION_CEILING = 128_000


def _max_completion_tokens(max_tokens: int | None, openai_model: str) -> int:
    asked = int(max_tokens or 4096)
    if not openai_model.startswith(("gpt-5", "o1", "o3", "o4")):
        return min(asked, _MAX_COMPLETION_CEILING)
    headroom = max(asked, _REASONING_HEADROOM_TOKENS)
    return min(asked + headroom, _MAX_COMPLETION_CEILING)


# --- errors ------------------------------------------------------------------


class OpenAIAPIError(Exception):
    """Base for every failure this client raises.

    `status_code` is the HTTP status, or None for a transport-level failure
    (connection refused, DNS, read timeout) where no response was received.
    `app.llm._is_retryable` reads both this type and the status.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenAIStatusError(OpenAIAPIError):
    """The API returned a non-2xx response."""


class OpenAIAuthenticationError(OpenAIStatusError):
    """401/403 — the key is wrong, revoked, or lacks access to the model."""


class OpenAIConnectionError(OpenAIAPIError):
    """Could not reach the API at all (transport failure)."""


class OpenAITimeoutError(OpenAIAPIError):
    """The request exceeded its read timeout."""


def _error_from_response(resp: httpx.Response) -> OpenAIStatusError:
    """Turn a non-2xx into a typed error, preferring OpenAI's own message.

    The body is `{"error": {"message": ..., "code": ...}}` on every documented
    failure; falling back to raw text keeps a gateway/proxy's HTML error page
    from crashing the error path.
    """
    detail = ""
    try:
        body = resp.json()
        detail = ((body or {}).get("error") or {}).get("message") or ""
    except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
        detail = (resp.text or "")[:300]
    message = f"OpenAI API error {resp.status_code}: {detail or 'no detail'}"
    if resp.status_code in (401, 403):
        return OpenAIAuthenticationError(message, status_code=resp.status_code)
    return OpenAIStatusError(message, status_code=resp.status_code)


# --- Anthropic-shaped response objects ---------------------------------------


class TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict) -> None:  # noqa: A002
        self.id = id
        self.name = name
        self.input = input


class Usage:
    """The four token fields `RunUsage` / `_capture_meta` read, in Anthropic's
    meaning of them (see the module docstring's note on re-basing)."""

    def __init__(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class Message:
    """The subset of `anthropic.types.Message` this codebase reads."""

    def __init__(
        self,
        *,
        content: list,
        model: str,
        stop_reason: str | None,
        usage: Usage,
    ) -> None:
        self.content = content
        self.model = model
        self.stop_reason = stop_reason
        self.usage = usage


class _StreamEvent:
    """A streamed delta, in the shape `_create_with_retries` inspects: it filters
    on `type == "input_json"` and reads `partial_json`."""

    def __init__(self, type: str, **fields: Any) -> None:  # noqa: A002
        self.type = type
        for k, v in fields.items():
            setattr(self, k, v)


# `finish_reason` → Anthropic's `stop_reason`. Callers branch on 'tool_use'
# (run_tool_loop) and log the rest, so anything unrecognised passes through.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
    "function_call": "tool_use",
}


def _usage_from(raw: dict | None) -> Usage:
    """Build Anthropic-meaning usage from an OpenAI `usage` object.

    `prompt_tokens` counts EVERY input token including cache hits and writes;
    Anthropic's `input_tokens` counts only the ones billed at the full input
    rate. Subtracting keeps `est_cost_usd` honest (cached reads are ~10x cheaper
    and would otherwise be billed as fresh input). Clamped at zero so a provider
    that reports the fields differently can never produce a negative charge.
    """
    raw = raw or {}
    details = raw.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    written = int(details.get("cache_write_tokens") or 0)
    prompt = int(raw.get("prompt_tokens") or 0)
    return Usage(
        input_tokens=max(0, prompt - cached - written),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cache_creation_input_tokens=written,
        cache_read_input_tokens=cached,
    )


# --- request translation -----------------------------------------------------


def _field(block: Any, name: str, default: Any = None) -> Any:
    """Read a field off a content block that may be a dict OR one of our own
    block objects — `run_tool_loop` appends a previous response's `msg.content`
    straight back onto the message list, so both forms reach the translator."""
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _text_of(content: Any) -> str:
    """Flatten Anthropic content (str, or a list of blocks) to plain text.

    Multiple text blocks exist only because of `cache_control` splitting — the
    cacheable prefix and the request body are one prompt to the model, so they
    are rejoined rather than sent as separate parts.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif _field(block, "type") == "text":
                parts.append(_field(block, "text") or "")
        return "\n\n".join(p for p in parts if p)
    return str(content)


def _translate_messages(system: Any, messages: list) -> list[dict]:
    """Anthropic `system` + `messages` → an OpenAI `messages` array."""
    out: list[dict] = []
    system_text = _text_of(system)
    if system_text:
        out.append({"role": "system", "content": system_text})

    for msg in messages or []:
        role = _field(msg, "role") or "user"
        content = _field(msg, "content")

        if isinstance(content, list):
            tool_calls = []
            tool_results = []
            for block in content:
                btype = _field(block, "type")
                if btype == "tool_use":
                    tool_calls.append({
                        "id": _field(block, "id") or "",
                        "type": "function",
                        "function": {
                            "name": _field(block, "name") or "",
                            "arguments": json.dumps(_field(block, "input") or {}),
                        },
                    })
                elif btype == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": _field(block, "tool_use_id") or "",
                        "content": _text_of(_field(block, "content")) or "",
                    })
            text = _text_of(content)

            # A tool_result batch is its own set of `role: "tool"` messages and
            # must NOT be wrapped in a user turn — OpenAI matches each one to the
            # assistant tool_call it answers, by id.
            if tool_results:
                out.extend(tool_results)
                if text:
                    out.append({"role": role, "content": text})
                continue
            if tool_calls:
                # OpenAI wants content omitted (not "") when the turn is purely
                # tool calls; a stray empty string is rejected by some models.
                entry: dict[str, Any] = {"role": role, "tool_calls": tool_calls}
                if text:
                    entry["content"] = text
                out.append(entry)
                continue
            out.append({"role": role, "content": text})
            continue

        out.append({"role": role, "content": _text_of(content)})
    return out


def _translate_tools(tools: list | None) -> tuple[list[dict], bool]:
    """Anthropic tool defs → OpenAI function tools.

    Returns `(tools, wants_web_search)`. Anthropic's SERVER-side tools (the
    `web_search_20250305` block, which has a `type` but no `input_schema`) have
    no Chat Completions counterpart, so they are stripped and reported through
    the flag — the caller switches to the search model instead.
    """
    out: list[dict] = []
    wants_search = False
    for tool in tools or []:
        ttype = _field(tool, "type")
        if ttype and str(ttype).startswith("web_search"):
            wants_search = True
            continue
        name = _field(tool, "name")
        schema = _field(tool, "input_schema") or _field(tool, "parameters")
        if not name or schema is None:
            # An unrecognised server-side tool. Dropping it degrades the call to
            # a plain completion, which beats a 400 that loses the whole answer.
            logger.warning("openai: dropping unsupported tool %r", ttype or name)
            continue
        fn: dict[str, Any] = {"name": name, "parameters": schema}
        description = _field(tool, "description")
        if description:
            fn["description"] = description
        out.append({"type": "function", "function": fn})
    return out, wants_search


def _translate_tool_choice(tool_choice: Any) -> Any:
    if not tool_choice:
        return None
    ctype = _field(tool_choice, "type")
    if ctype == "tool":
        return {"type": "function", "function": {"name": _field(tool_choice, "name")}}
    if ctype == "any":
        return "required"
    if ctype == "auto":
        return "auto"
    return tool_choice if isinstance(tool_choice, str) else "auto"


def _build_payload(kwargs: dict) -> tuple[dict, float | None]:
    """Anthropic `messages.create` kwargs → an OpenAI chat/completions body.

    Returns `(payload, timeout_override)`; `timeout` is an SDK request option in
    the Anthropic shape, not part of the body.
    """
    requested_model = kwargs.get("model")
    model = map_model(requested_model)
    tools, wants_search = _translate_tools(kwargs.get("tools"))
    if wants_search:
        # Search is a property of the MODEL on this API, not a tool.
        model = _SEARCH_MODEL

    payload: dict[str, Any] = {
        "model": model,
        "messages": _translate_messages(kwargs.get("system"), kwargs.get("messages") or []),
        "max_completion_tokens": _max_completion_tokens(kwargs.get("max_tokens"), model),
    }
    if tools:
        payload["tools"] = tools
        choice = _translate_tool_choice(kwargs.get("tool_choice"))
        if choice:
            payload["tool_choice"] = choice
    temperature = kwargs.get("temperature")
    if temperature is not None and _supports_temperature(model):
        payload["temperature"] = temperature
    return payload, kwargs.get("timeout")


def _message_from_payload(data: dict, fallback_model: str) -> Message:
    """OpenAI response body → an Anthropic-shaped Message."""
    choices = data.get("choices") or [{}]
    choice = choices[0] or {}
    raw_message = choice.get("message") or {}

    content: list = []
    text = raw_message.get("content")
    if text:
        content.append(TextBlock(text))
    for call in raw_message.get("tool_calls") or []:
        fn = call.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            # The model is documented as capable of emitting invalid JSON here.
            # An empty dict lets the caller's own "tool wasn't invoked" branch
            # fire with a clear 502 instead of a JSONDecodeError from deep in
            # the client.
            logger.warning("openai: tool arguments were not valid JSON; dropping")
            parsed = {}
        content.append(ToolUseBlock(
            id=call.get("id") or "", name=fn.get("name") or "", input=parsed
        ))

    return Message(
        content=content,
        model=data.get("model") or fallback_model,
        stop_reason=_STOP_REASONS.get(choice.get("finish_reason") or "", choice.get("finish_reason")),
        usage=_usage_from(data.get("usage")),
    )


# --- streaming ---------------------------------------------------------------


class _OpenAIStream:
    """One in-flight streamed completion, in the shape `_create_with_retries` uses.

    That function reaches for the stream in exactly three ways and this covers
    all of them: `iter(stream)` for tool-use `input_json` deltas, `text_stream`
    for text deltas, and `get_final_message()` for the assembled result. All
    three pull from ONE underlying SSE generator, so `get_final_message()` after
    a partial read simply drains the remainder rather than re-reading the body.
    """

    def __init__(self, response: httpx.Response, fallback_model: str) -> None:
        self._response = response
        self._fallback_model = fallback_model
        self._events = self._consume()
        self._final: Message | None = None
        # Accumulated across chunks; tool arguments arrive as string fragments
        # keyed by their index in the tool_calls array.
        self._text_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._finish_reason: str | None = None
        self._usage: dict | None = None
        self._model: str | None = None

    def _consume(self) -> Iterator[_StreamEvent]:
        for line in self._response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("openai: skipping unparseable SSE chunk")
                continue

            if chunk.get("model"):
                self._model = chunk["model"]
            # With stream_options.include_usage the LAST chunk carries usage and
            # an empty choices array — hence reading usage before choices.
            if chunk.get("usage"):
                self._usage = chunk["usage"]

            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    self._finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    self._text_parts.append(text)
                    yield _StreamEvent("text", text=text)
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index") or 0)
                    slot = self._tool_calls.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if call.get("id"):
                        slot["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    fragment = fn.get("arguments")
                    if fragment:
                        slot["arguments"] += fragment
                        # The Anthropic event name the tool-use streaming path
                        # filters on (app.llm._create_with_retries).
                        yield _StreamEvent("input_json", partial_json=fragment)

    def __iter__(self) -> Iterator[_StreamEvent]:
        return self._events

    @property
    def text_stream(self) -> Iterator[str]:
        for event in self._events:
            if event.type == "text":
                yield event.text

    def get_final_message(self) -> Message:
        if self._final is not None:
            return self._final
        for _ in self._events:  # drain whatever the caller did not read
            pass
        content: list = []
        text = "".join(self._text_parts)
        if text:
            content.append(TextBlock(text))
        for _index, slot in sorted(self._tool_calls.items()):
            try:
                parsed = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                logger.warning("openai: streamed tool arguments were not valid JSON")
                parsed = {}
            content.append(ToolUseBlock(id=slot["id"], name=slot["name"], input=parsed))
        self._final = Message(
            content=content,
            model=self._model or self._fallback_model,
            stop_reason=_STOP_REASONS.get(self._finish_reason or "", self._finish_reason),
            usage=_usage_from(self._usage),
        )
        return self._final


class _OpenAIStreamManager:
    """`with client.messages.stream(...) as s:` — opens the HTTP stream on enter
    and always closes it on exit, including when the body was only part-read."""

    def __init__(self, messages: "_OpenAIMessages", kwargs: dict) -> None:
        self._messages = messages
        self._kwargs = kwargs
        self._ctx: Any = None
        self._response: httpx.Response | None = None

    def __enter__(self) -> _OpenAIStream:
        payload, timeout = _build_payload(self._kwargs)
        payload["stream"] = True
        # Without this the terminal chunk omits `usage` entirely and every
        # streamed call would meter as zero tokens.
        payload["stream_options"] = {"include_usage": True}
        self._ctx = self._messages._open_stream(payload, timeout)
        self._response = self._ctx.__enter__()
        return _OpenAIStream(self._response, payload["model"])

    def __exit__(self, exc_type, exc, tb) -> Any:
        if self._ctx is not None:
            return self._ctx.__exit__(exc_type, exc, tb)
        return None


# --- client ------------------------------------------------------------------


class _OpenAIMessages:
    """The `client.messages` namespace: `create` and `stream`.

    A plain instance attribute on the client rather than a property, because
    `app.llm_metering.install_metering` swaps it for a metering proxy.
    """

    def __init__(self, client: "OpenAIMessagesClient") -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Message:
        payload, timeout = _build_payload(kwargs)
        data = self._client._post("/chat/completions", payload, timeout)
        return _message_from_payload(data, payload["model"])

    def stream(self, **kwargs: Any) -> _OpenAIStreamManager:
        return _OpenAIStreamManager(self, kwargs)

    def _open_stream(self, payload: dict, timeout: float | None):
        return self._client._stream("/chat/completions", payload, timeout)


class OpenAIMessagesClient:
    """An OpenAI client that speaks the Anthropic Messages API.

    `api_key` and `max_retries` exist because the rest of the codebase reads
    them off the client it gets back (tests assert on `.api_key`; the factories
    document `max_retries=0` because `app.llm._create_with_retries` owns retry).
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.max_retries = 0
        self.base_url = (base_url or getattr(settings, "openai_base_url", "") or _DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout
        # One httpx.Client per API key, reused across calls (it is thread-safe
        # and pools connections). The clients themselves are lru_cached by the
        # factories, so this is effectively one pool per distinct key.
        self._http = httpx.Client(timeout=timeout)
        self.messages = _OpenAIMessages(self)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict, timeout: float | None) -> dict:
        try:
            resp = self._http.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self._headers(),
                timeout=timeout if timeout is not None else self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise OpenAITimeoutError(f"OpenAI request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OpenAIConnectionError(f"Could not reach OpenAI: {exc}") from exc
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        return resp.json()

    def _stream(self, path: str, payload: dict, timeout: float | None):
        """Return the httpx streaming context manager, with errors normalised.

        The non-2xx check has to happen INSIDE the context (the body is not read
        until then), so this wraps the raw context manager in one that raises on
        enter — keeping the error shape identical to `_post`'s.
        """
        outer = self

        class _Ctx:
            def __enter__(self) -> httpx.Response:
                try:
                    self._inner = outer._http.stream(
                        "POST",
                        f"{outer.base_url}{path}",
                        json=payload,
                        headers=outer._headers(),
                        timeout=timeout if timeout is not None else outer._timeout,
                    )
                    resp = self._inner.__enter__()
                except httpx.TimeoutException as exc:
                    raise OpenAITimeoutError(f"OpenAI request timed out: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise OpenAIConnectionError(f"Could not reach OpenAI: {exc}") from exc
                if resp.status_code >= 400:
                    resp.read()  # the error body is not loaded on a stream
                    error = _error_from_response(resp)
                    self._inner.__exit__(None, None, None)
                    raise error
                return resp

            def __exit__(self, exc_type, exc, tb):
                return self._inner.__exit__(exc_type, exc, tb)

        return _Ctx()


def verify_api_key(api_key: str, *, base_url: str | None = None) -> None:
    """Prove a key works, without spending a token.

    `GET /v1/models` requires the same authentication as a completion but bills
    nothing, so the admin "Test key" button costs the customer zero. Raises the
    same `OpenAIAPIError` subclasses as a real call; returns None on success.
    """
    url = (base_url or getattr(settings, "openai_base_url", "") or _DEFAULT_BASE_URL).rstrip("/")
    try:
        resp = httpx.get(
            f"{url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise OpenAITimeoutError(f"OpenAI request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OpenAIConnectionError(f"Could not reach OpenAI: {exc}") from exc
    if resp.status_code >= 400:
        raise _error_from_response(resp)

"""Incremental extraction of the `answer` field from a streamed tool-use JSON.

Chat answers are generated via forced tool use (`_ASK_RESPONSE_SCHEMA`), so the
model's streamed deltas are PARTIAL JSON fragments of the tool input — e.g.
`{"answer": "The top the`…`me is", "key_points": […]` — not display text. To
token-stream the answer to the client (see app.graph.token_stream), this module
decodes just the `answer` string's content out of those fragments as they land,
forwarding plain markdown text to a sink and dropping everything else
(key_points / citations / confidence never reach the client stream).

`answer` is the first property in the schema and models emit tool input in
schema order, so extraction starts on the first fragments. The output is
ADVISORY DISPLAY ONLY — the authoritative answer is the persisted job payload
the client already polls for — so every failure mode here (key never found,
malformed escape, pathological fragment split) degrades to "stream less/none",
never to a wrong final answer.
"""
from __future__ import annotations

import re
from typing import Callable

# `"answer"` key followed by `:` and the opening quote of its string value.
_KEY_RE = re.compile(r'"answer"\s*:\s*"')

# Give up scanning for the key past this much prefix — the key is the first
# property, so if it hasn't appeared by now this isn't the payload shape we
# expect and streaming (display only) is simply skipped.
_SEEK_MAX = 65536

_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b",
    "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}


class AnswerFieldExtractor:
    """Feed partial-JSON fragments in; decoded `answer` text comes out via `sink`.

    Stateful across `feed()` calls: fragments can split anywhere — mid-key,
    mid-escape, even mid `\\uXXXX` — and surrogate pairs split across two
    escapes are recombined. `reset()` rewinds to the initial state; the LLM
    retry layer calls it when a transient mid-stream failure restarts the
    generation from zero (the re-emitted text is advisory; the client's poll
    renders the authoritative final answer either way).

    `on_restart` (optional) is called by `reset()` — NOT by construction — so
    the sink's owner learns that everything emitted so far is about to be
    superseded. Rewinding only the parse state is not enough: whoever is
    accumulating the decoded text downstream (the token_stream replay buffer,
    the browser's accumulator) would otherwise glue attempt 2 onto attempt 1
    and show both. Callback failures are swallowed — this whole path is
    advisory display.
    """

    def __init__(
        self,
        sink: Callable[[str], None],
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        self._sink = sink
        self._on_restart = on_restart
        self._reset_state()

    def _reset_state(self) -> None:
        self._seek_buf = ""      # accumulated prefix while hunting for the key
        self._state = "seek"     # seek | in_string | done
        self._carry = ""         # incomplete escape tail (starts with "\")
        self._pending_high = 0   # decoded high surrogate awaiting its pair

    def reset(self) -> None:
        """Rewind the parse state and announce the restart downstream.

        Split from `_reset_state` so construction sets up the initial state
        WITHOUT firing `on_restart`: a restart frame published before the
        generation has emitted anything would be a claim about text that never
        existed.
        """
        self._reset_state()
        if self._on_restart is not None:
            try:
                self._on_restart()
            except Exception:  # noqa: BLE001 — display only, never break the answer
                pass

    # The extractor object IS the stream callback (passed as `on_delta`), so the
    # retry layer can reach `reset()` on it when a transient failure restarts
    # the stream — a bare bound method would hide it.
    def __call__(self, fragment: str) -> None:
        self.feed(fragment)

    def feed(self, fragment: str) -> None:
        if not fragment or self._state == "done":
            return
        if self._state == "seek":
            self._seek_buf += fragment
            m = _KEY_RE.search(self._seek_buf)
            if not m:
                if len(self._seek_buf) > _SEEK_MAX:
                    self._state = "done"
                return
            fragment = self._seek_buf[m.end():]
            self._seek_buf = ""
            self._state = "in_string"
            if not fragment:
                return
        out = self._decode(fragment)
        if out:
            try:
                self._sink(out)
            except Exception:  # noqa: BLE001 — display only, never break the answer
                pass

    def _decode(self, fragment: str) -> str:
        """Decode string-content chars up to the closing quote, handling escapes
        (and escape sequences split across fragments via `_carry`)."""
        s = self._carry + fragment
        self._carry = ""
        out: list[str] = []
        i, n = 0, len(s)
        while i < n:
            c = s[i]
            if c == '"':
                self._flush_lone_high(out)
                self._state = "done"
                break
            if c != "\\":
                self._flush_lone_high(out)
                # Fast path: copy plain chars up to the next quote/backslash.
                j = i
                while j < n and s[j] not in '"\\':
                    j += 1
                out.append(s[i:j])
                i = j
                continue
            tail = s[i:]
            if len(tail) < 2:
                self._carry = tail
                break
            e = tail[1]
            if e in _SIMPLE_ESCAPES:
                self._flush_lone_high(out)
                out.append(_SIMPLE_ESCAPES[e])
                i += 2
                continue
            if e == "u":
                if len(tail) < 6:
                    self._carry = tail
                    break
                try:
                    cp = int(tail[2:6], 16)
                except ValueError:
                    cp = 0xFFFD
                i += 6
                if 0xD800 <= cp <= 0xDBFF:
                    # High surrogate: hold until the low half arrives (possibly
                    # in a later fragment) so emoji never split into garbage.
                    self._flush_lone_high(out)
                    self._pending_high = cp
                elif 0xDC00 <= cp <= 0xDFFF and self._pending_high:
                    combined = (
                        0x10000
                        + ((self._pending_high - 0xD800) << 10)
                        + (cp - 0xDC00)
                    )
                    out.append(chr(combined))
                    self._pending_high = 0
                else:
                    self._flush_lone_high(out)
                    # A lone low surrogate is invalid — replace, don't emit.
                    out.append("�" if 0xDC00 <= cp <= 0xDFFF else chr(cp))
                continue
            # Unknown escape (invalid JSON, but stay lenient): emit verbatim.
            self._flush_lone_high(out)
            out.append(e)
            i += 2
        return "".join(out)

    def _flush_lone_high(self, out: list[str]) -> None:
        """A held high surrogate not followed by its low half is invalid on its
        own — emit the replacement char rather than an unencodable lone half."""
        if self._pending_high:
            out.append("�")
            self._pending_high = 0

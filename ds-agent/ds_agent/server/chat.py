"""Claude-driven chat agent for /agent.

Architecture:

  - Claude has Anthropic's server-side `code_execution` tool. It writes
    Python (pandas, numpy, sklearn, scipy, matplotlib, statsmodels,
    shap, openpyxl, pypdf — all pre-installed) and runs it in an
    Anthropic-managed sandbox. The sandbox persists across turns
    within a session via the `container_id` we pass back.
  - The dataset(s) are uploaded via the Files API at ingest time. Each
    unattached file is sent as its own `container_upload` block on the
    next turn.
  - The /api/chat endpoint streams NDJSON events back to the client as
    Claude makes progress: text deltas, code-execution starts, and
    code-execution results all flow live. This keeps Vercel's proxy
    happy (no 150s gap to first byte) and gives the UI a real-time
    progress feed.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from anthropic import Anthropic

from .agents import AgentConfig
from .state import SessionState

logger = logging.getLogger(__name__)


# Sonnet by default: the ask agent is an interactive streaming tool-dispatch
# loop (MEDIUM routing work), so the cheaper/faster tier. Override per-deploy
# with AGENT_MODEL if a heavier model is ever warranted.
_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
_MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "32000"))
_EFFORT = os.environ.get("AGENT_EFFORT", "high")  # low | medium | high | xhigh | max
_MAX_PAUSE_RESUMES = 5


# Kept here for any caller that still imports it; the live system prompt
# now comes from AgentConfig.system_prompt.
_LEGACY_SYSTEM_PROMPT = """You are Sprntly's senior data scientist.

You have one tool: a Python sandbox (`code_execution`) with pandas, numpy, \
scipy, scikit-learn, statsmodels, matplotlib, seaborn, shap, openpyxl, pypdf \
pre-installed. `pip install` works for anything else. State persists across \
your code-execution calls within this conversation.

THE FILES. Every attached file is mounted at \
`os.environ['INPUT_DIR'] + '/' + <filename>`. If multiple files are \
attached, treat them as related. List them first with \
`os.listdir(os.environ['INPUT_DIR'])` and inspect each (header rows for \
CSVs, summary for PDFs/text). Filenames may carry path info via `__` \
separators (e.g. `archive__data__users.csv` came from \
`archive.zip/data/users.csv`).

YOUR JOB.

When the user first loads data (or asks you to "analyze" / "look at this" / \
"what's in here") you run a **comprehensive analysis on your own**, not a \
back-and-forth. Cover:

  1. **Data quality.** Shape, dtypes, missing values, suspicious columns \
     (e.g. numeric stored as string with whitespace), duplicates.
  2. **Goal metric.** What's the column they most likely care about? Pick \
     it explicitly and justify in one sentence.
  3. **Univariate.** Distribution of the goal metric and the most \
     informative explanatory columns. Save a chart for each non-obvious \
     finding (skewness, bimodality, heavy tails).
  4. **Drivers of the goal metric.** Which columns most strongly predict \
     it? Use the right method for the data type — grouped means / SHAP / \
     correlations / mutual info as appropriate. Quantify.
  5. **Segments.** Where do the drivers flip or amplify? Cut by the most \
     meaningful categorical columns. Note any segment that's small but \
     unusually high-impact.
  6. **Time trends** if there's a date column. Is the metric stable, \
     improving, degrading?
  7. **Weirdness.** Outliers, threshold effects, unexpected interactions.

CHARTS. Save a chart whenever it's the clearer way to convey a finding. \
Use `matplotlib` or `seaborn`. ALWAYS:
  - Save **directly to `$OUTPUT_DIR`**, e.g. \
    `plt.savefig(os.path.join(os.environ['OUTPUT_DIR'], 'chartname.png'), \
    dpi=120, bbox_inches='tight')`. Do NOT save to /tmp first and then copy \
    in a separate step — files only surface to the user when they land in \
    `$OUTPUT_DIR`, and saving inline means each chart appears in the same \
    code block as the analysis that produced it (instead of bundled at the \
    end disconnected from context).
  - Then call `plt.close()` to free the figure.
  - Give the chart a `plt.title(...)` that's the finding in plain English \
    ("Users with profile picture retain 2.3× longer"), not a column name.
  - Keep them small and readable — single insight per chart, no \
    multi-panel figures unless genuinely necessary.
  - Do **not** reference charts in your text via markdown image syntax \
    (`![title](file.png)`). The UI surfaces each chart automatically next \
    to the code that wrote it; markdown image refs won't resolve and just \
    add clutter. Refer to charts in prose ("the chart above shows…") if \
    you need to call back to one.

OUTPUT STYLE.

Stream insight summaries as you go — short headlines the reader can \
glance at, each followed (in the same text block) by 1-2 sentences \
explaining what the chart shows and why it matters. Use Markdown headings \
(`## Finding N: Posts in week 1 are the strongest retention driver`).

End with a **TL;DR** of the top 3-5 insights ranked by business impact, \
each labeled with confidence (HIGH / MEDIUM / LOW) and a recommended action.

Don't pad the prose. PMs are skimming. If a finding is LOW confidence, \
say "early signal" not "result". Distinguish correlational from causal — \
if you can run a quick propensity match or DiD, do it; otherwise say so.

DON'T ask permission to start; the user uploaded data because they want \
analysis. Don't ask "what would you like me to analyze first?" — pick the \
goal metric yourself and go.

For follow-up questions after the auto-analysis is done, be conversational \
and answer the specific question with one targeted code execution."""


@dataclass
class CodeExecution:
    """One code-execution bundle to render in the UI."""

    code: str
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    file_ids: list[str] = field(default_factory=list)
    error_code: str | None = None
    server_tool_use_id: str | None = None


@dataclass
class TurnResult:
    assistant_text: str
    code_executions: list[CodeExecution] = field(default_factory=list)


class ChatRunner:
    def __init__(
        self,
        agent: AgentConfig,
        api_key: str | None = None,
        model: str = _MODEL,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for the agent service.")
        self.agent = agent
        self.client = Anthropic(api_key=key)
        self.model = model

    # ─────────────────────── streaming entry ───────────────────────

    def stream_turn(
        self, session: SessionState, user_message: str
    ) -> Iterator[dict[str, Any]]:
        """Run one user turn, yielding events as they happen.

        Events emitted (all dicts; serialize one-per-line as NDJSON):
          {"type": "text_delta", "text": "..."}
          {"type": "code_start", "id": "...", "code": "..."}
          {"type": "code_result", "id": "...", "stdout": ..., "stderr": ...,
                                  "return_code": ..., "file_ids": [...],
                                  "error_code": null}
          {"type": "done"}
          {"type": "error", "error": "..."}    (terminal)
        """
        try:
            yield from self._stream_turn_inner(session, user_message)
        except Exception as exc:  # noqa: BLE001 — translate to a stream error
            yield {"type": "error", "error": f"{type(exc).__name__}: {exc}"}

    def _stream_turn_inner(
        self, session: SessionState, user_message: str
    ) -> Iterator[dict[str, Any]]:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
        unattached = session.unattached_files
        for f in unattached:
            user_content.append(
                {"type": "container_upload", "file_id": f.anthropic_file_id}
            )
        session.messages.append({"role": "user", "content": user_content})
        for f in unattached:
            f.attached = True

        pause_resumes = 0

        # Build system prompt — optionally enriched with corpus context.
        system_text = self.agent.system_prompt
        if session.corpus_context:
            system_text += (
                "\n\nBACKGROUND KNOWLEDGE (company corpus):\n"
                + session.corpus_context
            )

        while True:
            kwargs = {
                "model": self.model,
                "max_tokens": _MAX_TOKENS,
                "system": [
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "tools": [
                    {"type": "code_execution_20260120", "name": "code_execution"}
                ],
                "messages": session.messages,
                "extra_headers": {"anthropic-beta": "files-api-2025-04-14"},
            }
            if session.container_id:
                kwargs["container"] = session.container_id

            # Stream this Claude turn. Text deltas + code_start fire live;
            # tool result blocks land fully populated in `final_message` after
            # the stream completes (the per-event snapshot doesn't carry
            # server-side tool result content), so we emit code_result from
            # the final message at the end.
            stop_reason = "end_turn"
            emitted_code_starts: set[str] = set()
            with self.client.messages.stream(**kwargs) as stream:
                for event in stream:
                    et = getattr(event, "type", None)

                    if et == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", None) == "text_delta":
                            yield {"type": "text_delta", "text": delta.text}
                        # input_json_delta (tool input being constructed) — skip;
                        # we emit code on content_block_stop with the full code.

                    elif et == "content_block_stop":
                        block = _get_resolved_block(stream, event.index)
                        if block is None:
                            continue
                        btype = getattr(block, "type", None)
                        if btype == "server_tool_use":
                            code = ""
                            if isinstance(block.input, dict):
                                code = (
                                    block.input.get("code")
                                    or block.input.get("command")
                                    or ""
                                )
                            emitted_code_starts.add(block.id)
                            yield {
                                "type": "code_start",
                                "id": block.id,
                                "code": code,
                            }

                    elif et == "message_delta":
                        if getattr(event.delta, "stop_reason", None):
                            stop_reason = event.delta.stop_reason

                final_message = stream.get_final_message()

            # Emit code_result events from the fully-assembled final message.
            # Walk in order so the UI can pair them with the code_start events
            # by tool_use_id.
            for block in final_message.content:
                btype = getattr(block, "type", None)
                if btype in (
                    "bash_code_execution_tool_result",
                    "code_execution_tool_result",
                    "text_editor_code_execution_tool_result",
                ):
                    ev = {
                        "type": "code_result",
                        "id": getattr(block, "tool_use_id", None),
                    }
                    ev.update(_extract_result(block))
                    yield ev
                elif btype == "server_tool_use" and block.id not in emitted_code_starts:
                    # Defensive: if we somehow missed the streaming code_start
                    # for this server_tool_use, emit it now so the UI has the
                    # pair.
                    code = ""
                    if isinstance(block.input, dict):
                        code = (
                            block.input.get("code")
                            or block.input.get("command")
                            or ""
                        )
                    yield {"type": "code_start", "id": block.id, "code": code}

            # Capture / refresh the container id.
            container = getattr(final_message, "container", None)
            if container and getattr(container, "id", None):
                session.container_id = container.id

            # Persist the assistant turn for the next iteration.
            session.messages.append(
                {"role": "assistant", "content": final_message.content}
            )

            if stop_reason != "pause_turn":
                break

            pause_resumes += 1
            if pause_resumes >= _MAX_PAUSE_RESUMES:
                yield {
                    "type": "text_delta",
                    "text": "\n\n_(stopped after too many pause-turn resumes — "
                    "ask me to narrow the question)_",
                }
                break

        yield {"type": "done"}

        # Best-effort: push analysis results back to the backend corpus
        # so they inform the next brief-generation run.
        if session.dataset_slug:
            self._push_analysis(session)

    def _push_analysis(self, session: SessionState) -> None:
        """Extract the latest assistant text and push it to the backend."""
        # Find the last assistant message text.
        for msg in reversed(session.messages):
            if msg.get("role") != "assistant":
                continue
            text_parts: list[str] = []
            for block in msg.get("content", []):
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                if btype == "text":
                    text = getattr(block, "text", None) or (
                        block.get("text", "") if isinstance(block, dict) else ""
                    )
                    if text:
                        text_parts.append(text)
            if text_parts:
                markdown = "\n".join(text_parts).strip()
                if len(markdown) < 50:
                    return  # too short to be useful
                filename = f"ds_analysis_{session.sid}_{int(time.time())}.md"
                try:
                    from . import backend_client as _backend
                    _backend.push_analysis(
                        dataset_slug=session.dataset_slug,
                        filename=filename,
                        markdown=markdown,
                    )
                    logger.info(
                        "Pushed analysis to %s/%s (%d chars)",
                        session.dataset_slug, filename, len(markdown),
                    )
                except Exception:
                    logger.warning(
                        "Failed to push analysis to backend (best-effort)",
                        exc_info=True,
                    )
                return

    # ─────────────────────── non-streaming wrapper (tests) ───────────────────────

    def turn(self, session: SessionState, user_message: str) -> TurnResult:
        """Drain the stream into a single TurnResult — used by tests and any
        non-streaming caller. Preserves the old return shape.
        """
        text_chunks: list[str] = []
        by_id: dict[str | None, CodeExecution] = {}
        ordered: list[CodeExecution] = []

        for ev in self.stream_turn(session, user_message):
            t = ev.get("type")
            if t == "text_delta":
                text_chunks.append(ev.get("text", ""))
            elif t == "code_start":
                ce = CodeExecution(code=ev.get("code", ""), server_tool_use_id=ev.get("id"))
                by_id[ev.get("id")] = ce
                ordered.append(ce)
            elif t == "code_result":
                ce = by_id.get(ev.get("id"))
                if ce is None:
                    ce = CodeExecution(code="")
                    ordered.append(ce)
                ce.stdout = ev.get("stdout", "")
                ce.stderr = ev.get("stderr", "")
                ce.return_code = ev.get("return_code")
                ce.file_ids = ev.get("file_ids", [])
                ce.error_code = ev.get("error_code")
            elif t == "error":
                text_chunks.append(f"\n[error: {ev.get('error')}]")

        return TurnResult(
            assistant_text="".join(text_chunks).strip(),
            code_executions=ordered,
        )


# ─────────────────────── helpers ───────────────────────


def _get_resolved_block(stream: Any, index: int) -> Any:
    """Fish a fully-resolved content block out of the streaming accumulator.

    Both `current_message_snapshot` (current SDK) and `snapshot()` (older)
    expose the running accumulation; we try both.
    """
    snapshot = getattr(stream, "current_message_snapshot", None)
    if snapshot is None:
        snap_attr = getattr(stream, "snapshot", None)
        if callable(snap_attr):
            try:
                snapshot = snap_attr()
            except Exception:  # noqa: BLE001
                snapshot = None
        else:
            snapshot = snap_attr
    if snapshot is None:
        return None
    content = getattr(snapshot, "content", None) or []
    if 0 <= index < len(content):
        return content[index]
    return None


def _extract_result(block: Any) -> dict[str, Any]:
    """Pull stdout/stderr/return_code/file refs out of a tool-result block.

    Handles both pydantic-model content (returned by non-streaming
    messages.create) and plain-dict content (returned inside the
    streaming SDK's final_message). Same shape, different access
    pattern.
    """
    content = getattr(block, "content", None)
    if content is None:
        return {"stdout": "", "stderr": "", "return_code": None, "file_ids": [], "error_code": "no_content"}

    rtype = _attr(content, "type")

    if rtype and rtype.endswith("_result") and not rtype.endswith("error_result"):
        file_ids: list[str] = []
        for f in _attr(content, "content") or []:
            ftype = _attr(f, "type")
            file_id = _attr(f, "file_id")
            if file_id and ftype and ftype.endswith("_output"):
                file_ids.append(file_id)
        return {
            "stdout": _attr(content, "stdout") or "",
            "stderr": _attr(content, "stderr") or "",
            "return_code": _attr(content, "return_code"),
            "file_ids": file_ids,
            "error_code": None,
        }

    return {
        "stdout": "",
        "stderr": "",
        "return_code": None,
        "file_ids": [],
        "error_code": _attr(content, "error_code") or rtype or "unknown_error",
    }


def _attr(obj: Any, name: str) -> Any:
    """Access `name` on either a dict or an object."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)

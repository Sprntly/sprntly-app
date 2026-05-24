"""Claude-driven chat agent for /agent.

Architecture (replaces the prior fixed-pipeline tool wrapper):

  - Claude has Anthropic's server-side `code_execution` tool. It writes
    Python (pandas, numpy, sklearn, scipy, matplotlib, statsmodels,
    shap — all pre-installed) and runs it in an Anthropic-managed
    sandbox. The sandbox persists across turns within a session via the
    `container_id` we pass back.
  - The dataset is uploaded once per session to the Files API. We attach
    it as a `container_upload` block on the first chat turn after a
    fresh dataset is loaded; subsequent turns reuse the container.
  - We do NOT execute any application-defined tools. Claude orchestrates
    everything; we just parse the returned content blocks and pass
    structured chunks (text, code, stdout, stderr, file refs) back to
    the UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from .state import SessionState


_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-7")
_MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "16000"))
_EFFORT = os.environ.get("AGENT_EFFORT", "high")  # low | medium | high | xhigh | max
_MAX_PAUSE_RESUMES = 5


_SYSTEM_PROMPT = """You are Sprntly's senior data scientist.

A product manager has loaded one or more files into your session. They \
want insights they can act on this week. You have one tool: a Python \
sandbox (`code_execution`) with pandas, numpy, scipy, scikit-learn, \
statsmodels, matplotlib, seaborn, shap, openpyxl, pypdf pre-installed. \
`pip install` works for anything else. State persists across your \
code-execution calls within this conversation.

THE FILES. Every attached file is mounted at \
`os.environ['INPUT_DIR'] + '/' + <filename>`. If multiple files are \
attached, treat them as related — they belong to one analysis. Spend \
your first turn calling `os.listdir(os.environ['INPUT_DIR'])` and \
peeking at each (header rows for CSVs, summary for PDFs/text). Build a \
mental model of how they relate before diving in: are they multiple \
tables that join? A data file plus a README/spec? Several samples of \
the same shape? File extensions: .csv/.tsv/.json/.jsonl → pandas; \
.parquet → pd.read_parquet; .xlsx/.xls → pd.read_excel; .pdf → pypdf; \
.txt/.md → plain read. Filenames sometimes carry path info via `__` \
separators (e.g. `archive__data__users.csv` came from \
`archive.zip/data/users.csv`) — use that to infer structure.

On later turns the same files and any variables you've defined are \
still in the container; don't re-load unless the user has attached \
something new.

HOW YOU OPERATE. Apply judgment; don't follow as a rigid checklist.

1. SCOPE FIRST. Profile the data — shape, dtypes, nulls, cardinalities, \
distributions of the most informative columns. Identify the plausible \
goal metric (the column the user most likely cares about). Confirm with \
the user before going deep.

2. FORM HYPOTHESES, THEN TEST. Decide what's probably true given what \
you've seen, then write code to confirm or reject. Cite numbers from \
your code; never invent them.

3. TRIANGULATE. A finding from one method is a guess. Cross-check (e.g. \
SHAP vs grouped means, regression vs decision tree) before you call it \
a finding.

4. PURSUE THE WEIRD. Bimodal distributions, tiny-but-high-impact \
segments, correlations that flip inside a stratum — chase them. The \
interesting patterns aren't always the obvious ones.

5. BE HONEST ABOUT CONFIDENCE. Label findings HIGH / MEDIUM / LOW. \
Hedge low-confidence ones explicitly. Distinguish causal from \
correlational. If you can run a quick propensity-score match or \
difference-in-differences to support a causal claim, do it; if not, \
say "this is correlational."

6. REPORT IN PM LANGUAGE. After analysis, summarize for a product \
manager: behavior → effect → confidence → recommended action. Plain \
English. Two lines per finding. Use the user's own column names. Don't \
spend tokens explaining your code in prose if the user can see it.

OUTPUT STYLE. Be terse. Prefer one well-chosen analysis over five \
shallow ones. Default `effort` is high; use it to think before acting, \
then act once."""


@dataclass
class CodeExecution:
    """One code-execution bundle to render in the UI."""

    code: str
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    file_ids: list[str] = field(default_factory=list)
    error_code: str | None = None  # sandbox-side error (non-zero stderr is different)


@dataclass
class TurnResult:
    assistant_text: str
    code_executions: list[CodeExecution] = field(default_factory=list)


class ChatRunner:
    def __init__(self, api_key: str | None = None, model: str = _MODEL) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for the agent service.")
        self.client = Anthropic(api_key=key)
        self.model = model

    def turn(self, session: SessionState, user_message: str) -> TurnResult:
        # Build the user content. Attach every still-unattached file as
        # its own container_upload block on this turn; later turns reuse
        # the existing container.
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
        unattached = session.unattached_files
        for f in unattached:
            user_content.append(
                {"type": "container_upload", "file_id": f.anthropic_file_id}
            )

        session.messages.append({"role": "user", "content": user_content})
        # Mark them attached now — if the API call fails the session is
        # already in an inconsistent state anyway (half-turn), so don't
        # waste cycles rolling back per-file flags.
        for f in unattached:
            f.attached = True

        text_chunks: list[str] = []
        executions: list[CodeExecution] = []
        # Map server_tool_use.id -> CodeExecution slot, so we can fill in
        # the result when the matching tool_result block comes back.
        executions_by_tool_use_id: dict[str, CodeExecution] = {}
        pause_resumes = 0

        while True:
            kwargs = {
                "model": self.model,
                "max_tokens": _MAX_TOKENS,
                "system": [
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "tools": [
                    {"type": "code_execution_20260120", "name": "code_execution"}
                ],
                "messages": session.messages,
                # Files API is still beta; required because we use container_upload.
                "extra_headers": {"anthropic-beta": "files-api-2025-04-14"},
            }
            if session.container_id:
                kwargs["container"] = session.container_id

            resp = self.client.messages.create(**kwargs)

            # Capture / refresh the container id (Anthropic may rotate it).
            if getattr(resp, "container", None) and resp.container.id:
                session.container_id = resp.container.id

            # The whole assistant turn (incl. server_tool_use and result
            # blocks) must be echoed back in `messages` for subsequent
            # requests; just append the raw content list.
            session.messages.append({"role": "assistant", "content": resp.content})

            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_chunks.append(block.text)

                elif btype == "server_tool_use":
                    # Claude is about to run code. We allocate a CodeExecution
                    # slot and fill in the result when the matching
                    # *_tool_result block arrives in the same response.
                    code = ""
                    if isinstance(block.input, dict):
                        code = (
                            block.input.get("code")
                            or block.input.get("command")
                            or ""
                        )
                    ce = CodeExecution(code=code)
                    executions.append(ce)
                    executions_by_tool_use_id[block.id] = ce

                elif btype in (
                    "bash_code_execution_tool_result",
                    "code_execution_tool_result",
                    "text_editor_code_execution_tool_result",
                ):
                    ce = executions_by_tool_use_id.get(
                        getattr(block, "tool_use_id", "")
                    )
                    if ce is None:
                        # Defensive: append a standalone result if we somehow
                        # missed the server_tool_use header.
                        ce = CodeExecution(code="")
                        executions.append(ce)
                    _fill_result(ce, block)

            if resp.stop_reason == "pause_turn":
                # Server-side loop hit its iteration cap. Re-issue with the
                # same messages list (we already appended the assistant turn)
                # so Anthropic can continue.
                pause_resumes += 1
                if pause_resumes >= _MAX_PAUSE_RESUMES:
                    text_chunks.append(
                        "_(stopped after too many pause-turn resumes; ask me to "
                        "narrow the question)_"
                    )
                    break
                continue

            # Any other stop_reason ends the turn.
            break

        return TurnResult(
            assistant_text="\n\n".join(t.strip() for t in text_chunks if t.strip()),
            code_executions=executions,
        )


def _fill_result(ce: CodeExecution, block: Any) -> None:
    """Pull stdout/stderr/return_code/file refs out of a tool-result block."""
    content = getattr(block, "content", None)
    if content is None:
        return
    rtype = getattr(content, "type", None)

    # Success branch — type ends in "_result"
    if rtype and rtype.endswith("_result") and not rtype.endswith("error_result"):
        ce.stdout = getattr(content, "stdout", "") or ""
        ce.stderr = getattr(content, "stderr", "") or ""
        ce.return_code = getattr(content, "return_code", None)
        for f in getattr(content, "content", None) or []:
            ftype = getattr(f, "type", None)
            file_id = getattr(f, "file_id", None)
            if file_id and ftype and ftype.endswith("_output"):
                ce.file_ids.append(file_id)
    else:
        # Error branch — sandbox-side failure (different from non-zero return code).
        ce.error_code = getattr(content, "error_code", None) or rtype or "unknown_error"

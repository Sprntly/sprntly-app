"""Chat data analysis via Claude + Anthropic's server-side code_execution tool.

The flag-gated replacement for the vendored v5.8 deterministic battery
(`app.ds.chat_analysis`, which stays as the permanent fail-open floor). Same
entry contract — `answer(enterprise_id=, question=, history=, is_cancelled=)`
returning an Ask-shaped payload — but instead of running a fixed battery that
never reads `question`, we hand the company's uploaded CSVs to Claude in an
Anthropic-managed Python sandbox and let it write and run the pandas that
actually answers what was asked.

Architecture (ported from `ds-agent/ds_agent/server/chat.py`, the standalone
service that already does exactly this):

  - Staging: `app.ds.staging.stage_workspace` — the same raw/ → CSV staging the
    deterministic engine uses, so the two engines see the same files.
  - Upload: one Files API upload per staged CSV (beta `files-api-2025-04-14`),
    through `app.llm.get_client()` so BYOK key routing and usage metering both
    apply. Under BYOK the files land in the COMPANY'S OWN Anthropic org.
  - Loop: streamed `messages.create` with `code_execution` declared as a
    server-side tool and each staged file attached as a `container_upload`
    block. Claude writes Python, Anthropic runs it in a network-isolated
    container, and the results come back as tool-result blocks in the same
    response. `stop_reason == "pause_turn"` means the server-side loop hit its
    own iteration cap — we resend to resume (capped), exactly as the SDK
    prescribes. We deliberately do NOT reuse `app.llm.run_tool_loop`: that is
    shaped for CLIENT-side tools (dispatch + tool_result), which is a different
    protocol from the pause_turn/container one server tools use.
  - Tenancy: a fresh container per ask, bound to a `(enterprise_id, run_id)`
    scope key (`ContainerScope`) — a container id captured for one ask can never
    be replayed into another ask or another tenant, by construction. The
    container itself is Anthropic-managed, isolated, and has no network egress.
  - Rendering: a deterministic HTML report (narrative + the charts Claude saved,
    embedded as base64 PNGs) into `payload["answer"]`, riding the existing
    sandboxed-iframe path the VoC/public-feedback reports already use. Every
    model-supplied string is HTML-escaped here — the model never writes markup.
  - Cleanup: best-effort `files.delete` of every uploaded CSV and every chart we
    downloaded. See the data-handling note in the PR: until deletion lands the
    raw CSVs are resident in the Files API.

Guardrails, all of them cheap and none of them optional: a 12-model-turn cap, a
5-pause-resume cap, a ~5-minute wall-clock budget after which we stop and render
what we have, and an `is_cancelled()` poll before every model call so a user Stop
lands before the next (expensive) turn rather than after it.
"""
from __future__ import annotations

import base64
import html
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_SKILL_TAGS = {
    "_skill": "ds-agent",
    "_skill_action": "Analyze data",
    # Distinguishes this path from the deterministic engine in the payload, so a
    # staging/prod answer can be attributed to an engine without guessing.
    "_skill_source": "ds-claude",
}

# Sonnet by default: an interactive analysis loop is MEDIUM routing work under
# the model-tiering policy, and both precedents for this architecture (the
# standalone ds-agent service, app.llm.DEFAULT_MODEL) are on the sonnet tier.
# The env override exists for a per-deploy escalation; it is validated against
# MODEL_PRICING and FAILS CLOSED to the default, so an unpriced/typo'd value can
# never produce unmetered spend. Nothing needs to be set for this to work.
_MODEL_ENV = "SPRNTLY_DS_ANALYSIS_MODEL"

# Server-side code execution. Same tool version the standalone ds-agent service
# runs (`code_execution_20260120` — REPL persistence + container uploads).
_CODE_EXECUTION_TOOL = {"type": "code_execution_20260120", "name": "code_execution"}
_FILES_BETA = "files-api-2025-04-14"

_MAX_TOKENS_PER_TURN = 16000
# Model turns (one streamed messages.create each). The pause-resume cap below is
# stricter in practice; this is the belt for a model that keeps ending its turn
# without finishing.
_MAX_TURNS = 12
_MAX_PAUSE_RESUMES = 5
# Wall clock for the whole analysis. The ask-job poll surface tolerates minutes
# (the public-feedback report runs ~7), but an unbounded loop must not sit on an
# LLM concurrency slot forever.
_WALL_CLOCK_BUDGET_S = 300.0

# Chart embedding caps — the report is inlined into a chat payload, so a runaway
# figure count or a 40 MB PNG must not blow up the response.
_MAX_CHARTS = 8
_MAX_CHART_BYTES = 2 * 1024 * 1024

_HISTORY_TURNS = 6

_TOOL_RESULT_BLOCK_TYPES = (
    "bash_code_execution_tool_result",
    "code_execution_tool_result",
    "text_editor_code_execution_tool_result",
)


class DsClaudeAnalysisError(RuntimeError):
    """The Claude path could not produce an analysis.

    Raised (rather than returning an apology payload) so the qa_agent gate falls
    back to the deterministic v5.8 engine — the user gets a real answer instead
    of an error, and the flag stays safe to leave on.
    """


# ── system prompt ────────────────────────────────────────────────────────────
# Ported from ds-agent/ds_agent/server/agents.py DS_AGENT.system_prompt, with
# three adaptations for the chat surface:
#   (a) QUESTION MODE — the standalone agent is kicked off by a file upload and
#       always runs the full battery. In chat there IS a question, and answering
#       it beats reciting a battery; the full sweep is for open-ended asks only.
#       This is the single biggest fix over the v5.8 engine, which never reads
#       `question` at all.
#   (b) Charts are embedded into a rendered report in the order they were
#       produced, so the "UI shows it next to the code" wording is wrong here.
#   (c) The sandbox has no network egress on this path — say so, so the model
#       doesn't plan around fetching anything.
_SYSTEM_PROMPT = """You are Sprntly's senior data scientist, answering inside a \
product manager's chat.

You have one tool: a Python sandbox (`code_execution`) with pandas, numpy, \
scipy, scikit-learn, statsmodels, matplotlib, seaborn, shap, openpyxl, pypdf \
pre-installed. State persists across your code-execution calls within this \
conversation. The sandbox has NO network access — everything you need is in the \
attached files, and `pip install` will not work.

THE FILES. Every attached file is mounted at \
`os.environ['INPUT_DIR'] + '/' + <filename>`. If multiple files are attached, \
treat them as related. List them first with \
`os.listdir(os.environ['INPUT_DIR'])` and inspect each (shape, dtypes, header \
rows). These are the company's own uploaded exports; they may be raw per-user or \
per-event tables, or they may be small aggregated summary sheets. Work with what \
is actually there — if a column holds formatted strings ("84%", "$1.2M"), parse \
them rather than giving up.

ANSWER THE QUESTION THAT WAS ASKED.

When the user asks something SPECIFIC ("which cohort churned most after the \
pricing change?", "what's the worst week-4 retention feature?"), answer THAT \
question with targeted executions. Compute the number, say what it is, show the \
one chart that makes it obvious, and note the caveat that matters. Do not run a \
full battery the user did not ask for.

When the ask is OPEN-ENDED ("analyze my data", "what's in here", "find \
anything interesting"), run a comprehensive analysis on your own — not a \
back-and-forth. Cover:

  1. **Data quality.** Shape, dtypes, missing values, suspicious columns \
     (e.g. numeric stored as string with whitespace), duplicates.
  2. **Goal metric.** What's the column they most likely care about? Pick it \
     explicitly and justify in one sentence.
  3. **Univariate.** Distribution of the goal metric and the most informative \
     explanatory columns. Chart each non-obvious finding (skew, bimodality, \
     heavy tails).
  4. **Drivers of the goal metric.** Which columns most strongly predict it? \
     Use the right method for the data type — grouped means / SHAP / \
     correlations / mutual info as appropriate. Quantify.
  5. **Segments.** Where do the drivers flip or amplify? Cut by the most \
     meaningful categorical columns. Note any segment that's small but \
     unusually high-impact.
  6. **Time trends** if there's a date column. Is the metric stable, \
     improving, degrading?
  7. **Weirdness.** Outliers, threshold effects, unexpected interactions.

CHARTS. Save a chart whenever it's the clearer way to convey a finding. Use \
`matplotlib` or `seaborn`. ALWAYS:
  - Save **PNG, directly to `$OUTPUT_DIR`**, e.g. \
    `plt.savefig(os.path.join(os.environ['OUTPUT_DIR'], 'chartname.png'), \
    dpi=120, bbox_inches='tight')`, then `plt.close()`. Only files written to \
    `$OUTPUT_DIR` reach the reader.
  - Give the chart a `plt.title(...)` that IS the finding in plain English \
    ("Users with a profile picture retain 2.3x longer"), not a column name.
  - One insight per chart. No multi-panel figures unless genuinely necessary.
  - Do NOT reference charts with markdown image syntax (`![](file.png)`). Each \
    chart is embedded into the report at the point you produced it, so refer to \
    them in prose ("the chart above shows...") if you need to call back.

OUTPUT STYLE.

Write the findings as you go — short headlines the reader can skim, each \
followed by 1-2 sentences on what it shows and why it matters. Use Markdown \
headings (`## Posts in week 1 are the strongest retention driver`).

End with a **TL;DR**: the top 3-5 insights ranked by business impact, each \
labelled with confidence (HIGH / MEDIUM / LOW) and a recommended action. For a \
pointed question, the TL;DR is one or two lines — the answer and its confidence.

Every number you state must come from an execution you actually ran. Never \
estimate from the head of the data or from memory. If the data cannot support \
an answer, say precisely what is missing and which export would fix it. \
Distinguish correlational from causal — if a quick propensity match or DiD is \
feasible, run it; otherwise say so. Say "early signal", not "result", for \
LOW-confidence findings.

Don't pad the prose; PMs are skimming. Don't ask permission to start, and don't \
ask which analysis to run first — decide and go."""


# ── payload helpers ──────────────────────────────────────────────────────────

def _payload(answer: str, *, confidence: float, key_points: list[str] | None = None) -> dict:
    return {
        "answer": answer,
        "key_points": key_points or [],
        "citations": [],
        "confidence": confidence,
        "unanswered": "",
        **_SKILL_TAGS,
    }


_NO_DATA_MESSAGE = (
    "I can run a full data-science analysis for you, but I don't see any "
    "uploaded data yet. Upload your product-analytics exports (CSV or Excel — "
    "Mixpanel, Amplitude, PostHog, GA4, Statsig and other tool exports all "
    "work) under **Sources**, then ask me again."
)

_NO_TABULAR_MESSAGE = (
    "I can run a full data-science analysis for you, but none of your uploaded "
    "files are tabular data I can analyze (I need CSV or Excel exports — e.g. a "
    "users/accounts table, event exports, or experiment exposures). Upload one "
    "under **Sources** and ask me again."
)

_TURN_CAP_NOTE = (
    "I reached my limit for this run before finishing the analysis — the "
    "findings above are what completed. Ask me a narrower question (or about "
    "one file at a time) and I'll go deeper."
)

_TIMEOUT_NOTE = (
    "I stopped here to keep the response timely — the findings above are what "
    "completed within the time budget. Ask me a narrower question and I'll go "
    "deeper on it."
)


# ── model selection ──────────────────────────────────────────────────────────

def resolve_model() -> str:
    """The model for this path: sonnet by default, env-overridable, fails closed.

    An override that isn't in MODEL_PRICING is IGNORED (logged, then the default)
    rather than honoured: `est_cost_usd` fails closed on unknown models, so an
    unpriced override would produce spend the usage ledger cannot cost.
    """
    from app.llm import DEFAULT_MODEL
    from app.llm_telemetry import MODEL_PRICING

    override = (os.environ.get(_MODEL_ENV) or "").strip()
    if not override:
        return DEFAULT_MODEL
    if override in MODEL_PRICING:
        return override
    logger.warning(
        "%s=%r is not a priced model (see llm_telemetry.MODEL_PRICING); "
        "falling back to %s",
        _MODEL_ENV, override, DEFAULT_MODEL,
    )
    return DEFAULT_MODEL


# ── container tenancy ────────────────────────────────────────────────────────

def container_scope_key(enterprise_id: str, run_id: str) -> str:
    """The identity a container id is bound to: this tenant, this ask.

    v1 does not persist containers across asks, so the key includes a per-run
    id. Two asks — even from the same company, even concurrent — derive
    different keys, and two companies can never derive the same key.
    """
    return f"{enterprise_id}:{run_id}"


@dataclass
class ContainerScope:
    """Holds the Anthropic container id for ONE ask, keyed by its scope.

    `resume_kwargs` only hands the container back when the caller presents the
    same key it was captured under, so a cross-ask or cross-tenant replay is
    impossible by construction rather than by convention.
    """

    key: str
    container_id: str | None = None

    def capture(self, message: Any) -> None:
        container = getattr(message, "container", None)
        cid = getattr(container, "id", None) if container is not None else None
        if cid:
            self.container_id = cid

    def resume_kwargs(self, key: str) -> dict:
        if not self.container_id or key != self.key:
            return {}
        return {"container": self.container_id}


# ── report segments ──────────────────────────────────────────────────────────

@dataclass
class _Report:
    """The analysis as an ordered stream of narrative and charts.

    Order is content-block order across every turn, so a chart lands in the
    report exactly where Claude produced it — next to the prose that explains it.
    """

    segments: list[tuple[str, str]] = field(default_factory=list)
    chart_file_ids: list[str] = field(default_factory=list)
    turns: int = 0
    pause_resumes: int = 0
    stopped_reason: str | None = None

    def add_text(self, text: str) -> None:
        if text and text.strip():
            self.segments.append(("text", text.strip()))

    def add_chart(self, file_id: str) -> None:
        if file_id and file_id not in self.chart_file_ids:
            self.chart_file_ids.append(file_id)
            self.segments.append(("chart", file_id))

    @property
    def narrative(self) -> str:
        return "\n\n".join(t for kind, t in self.segments if kind == "text")


# ── entry point ──────────────────────────────────────────────────────────────

def answer(
    *,
    enterprise_id: str,
    question: str,
    history: list[dict] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Analyze the company's uploaded data with Claude; Ask-shaped payload.

    Raises `DsClaudeAnalysisError` when the loop yields no analysis, and
    `AskCancelled` when the caller's `is_cancelled()` fires — both are meaningful
    to the qa_agent gate (fall back to the v5.8 engine / abandon the ask). The
    two "nothing to analyze" cases return a payload instead, with NO API call.
    """
    from app.datasets import raw_path
    from app.db.companies import slug_for_company_id
    from app.ds.staging import stage_workspace

    slug = slug_for_company_id(enterprise_id)
    raw_dir = raw_path(slug) if slug else None
    if not raw_dir or not raw_dir.is_dir():
        return _payload(_NO_DATA_MESSAGE, confidence=0.0)

    workdir = Path(tempfile.mkdtemp(prefix="sprntly-ds-claude-"))
    try:
        staged = stage_workspace(raw_dir, workdir)
        if not staged:
            return _payload(_NO_TABULAR_MESSAGE, confidence=0.0)
        return _run(
            enterprise_id=enterprise_id,
            question=question,
            history=history,
            workdir=workdir,
            staged=staged,
            is_cancelled=is_cancelled,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _run(
    *,
    enterprise_id: str,
    question: str,
    history: list[dict] | None,
    workdir: Path,
    staged: list[str],
    is_cancelled: Callable[[], bool] | None,
) -> dict:
    from app.llm import get_client
    from app.llm_keys import company_llm_key
    from app.llm_telemetry import RunUsage
    from app.usage_context import Feature, usage_scope

    model = resolve_model()
    run_id = uuid.uuid4().hex
    scope = ContainerScope(key=container_scope_key(enterprise_id, run_id))
    usage = RunUsage()
    report = _Report()
    uploaded: list[str] = []
    deadline = time.monotonic() + _WALL_CLOCK_BUDGET_S

    # This path calls app.llm directly rather than through the gateway, so the
    # company key binding and the usage label both have to be stated here.
    with company_llm_key(enterprise_id), usage_scope(
        feature=Feature.ASK, operation="ds_claude_analysis"
    ):
        client = get_client()
        try:
            _check_cancelled(is_cancelled)
            uploaded = _upload_staged(client, workdir, staged)
            if not uploaded:
                raise DsClaudeAnalysisError("no staged file could be uploaded")
            _loop(
                client=client,
                model=model,
                question=question,
                history=history,
                file_ids=uploaded,
                scope=scope,
                report=report,
                usage=usage,
                deadline=deadline,
                is_cancelled=is_cancelled,
            )
            if not report.narrative:
                raise DsClaudeAnalysisError("analysis produced no narrative")
            charts = _download_charts(client, report.chart_file_ids)
            html_report = _render_html(question, report, charts, staged)
        finally:
            _cleanup_files(client, uploaded, report.chart_file_ids)

    _log_run(
        enterprise_id=enterprise_id,
        model=model,
        staged=staged,
        report=report,
        usage=usage,
    )
    return _payload(html_report, confidence=0.8)


def _check_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    """Raise AskCancelled when the caller reports the ask was stopped.

    Imported lazily: qa_agent imports this module inside its intercept, so a
    module-level import back into qa_agent would be a cycle.
    """
    if is_cancelled is None or not is_cancelled():
        return
    from app.qa_agent import AskCancelled

    raise AskCancelled()


# ── Files API ────────────────────────────────────────────────────────────────

def _upload_staged(client: Any, workdir: Path, staged: list[str]) -> list[str]:
    """Upload each staged CSV once; return the Anthropic file ids.

    Per-file best effort: one unreadable file must not lose the whole analysis.
    """
    file_ids: list[str] = []
    for name in staged:
        path = workdir / name
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                uploaded = client.beta.files.upload(file=(name, fh, "text/csv"))
            file_id = getattr(uploaded, "id", None)
            if file_id:
                file_ids.append(file_id)
        except Exception:  # noqa: BLE001 — partial coverage beats no answer
            logger.warning("DS Claude: upload failed for %s", name, exc_info=True)
    return file_ids


def _download_charts(client: Any, file_ids: list[str]) -> dict[str, str]:
    """file_id → base64 PNG, for the charts we can actually embed.

    Only PNGs, only up to the per-chart and per-report caps. Anything else (a
    CSV the model wrote, an oversized figure, a download failure) is skipped —
    the narrative still stands on its own.
    """
    charts: dict[str, str] = {}
    for file_id in file_ids:
        if len(charts) >= _MAX_CHARTS:
            break
        try:
            raw = _download_bytes(client, file_id)
        except Exception:  # noqa: BLE001 — a missing chart is not a failed run
            logger.warning("DS Claude: chart download failed for %s", file_id, exc_info=True)
            continue
        if not raw or len(raw) > _MAX_CHART_BYTES:
            continue
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            continue
        charts[file_id] = base64.b64encode(raw).decode("ascii")
    return charts


def _download_bytes(client: Any, file_id: str) -> bytes:
    """Read a Files API download across the SDK's response shapes."""
    resp = client.beta.files.download(file_id)
    if isinstance(resp, (bytes, bytearray)):
        return bytes(resp)
    for attr in ("read", "content", "text"):
        value = getattr(resp, attr, None)
        if callable(value):
            out = value()
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
        elif isinstance(value, (bytes, bytearray)):
            return bytes(value)
    return b""


def _cleanup_files(client: Any, uploaded: Iterable[str], charts: Iterable[str]) -> None:
    """Best-effort delete of everything this run put in the Files API.

    Uploads are the company's raw data and charts are derived from it, so both
    are removed as soon as the report is rendered. Best-effort by necessity (the
    delete can fail); a leftover file is a data-handling note, never an error the
    user sees.
    """
    for file_id in list(uploaded) + list(charts):
        try:
            client.beta.files.delete(file_id)
        except Exception:  # noqa: BLE001 — cleanup must never surface
            logger.warning("DS Claude: file cleanup failed for %s", file_id, exc_info=True)


# ── the loop ─────────────────────────────────────────────────────────────────

def _loop(
    *,
    client: Any,
    model: str,
    question: str,
    history: list[dict] | None,
    file_ids: list[str],
    scope: ContainerScope,
    report: _Report,
    usage: Any,
    deadline: float,
    is_cancelled: Callable[[], bool] | None,
) -> None:
    """Drive the code-execution conversation until the analysis is done.

    Server tools run inside ONE messages.create: Claude writes code, Anthropic
    executes it, and the results are already in the response. We only come back
    around for `pause_turn` (the server-side loop hit its own iteration cap).
    """
    from app.llm import _create_with_retries

    messages: list[dict] = [{"role": "user", "content": _user_content(question, history, file_ids)}]
    system = [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

    while True:
        if report.turns >= _MAX_TURNS:
            report.stopped_reason = "turn_cap"
            return
        if report.turns and time.monotonic() >= deadline:
            report.stopped_reason = "timeout"
            return
        # Poll before spending, not after: a Stop that lands here saves the turn.
        _check_cancelled(is_cancelled)

        kwargs = {
            "model": model,
            "max_tokens": _MAX_TOKENS_PER_TURN,
            "system": system,
            "tools": [_CODE_EXECUTION_TOOL],
            "messages": messages,
            "extra_headers": {"anthropic-beta": _FILES_BETA},
            **scope.resume_kwargs(scope.key),
        }
        message = _create_with_retries(client, stream=True, **kwargs)
        report.turns += 1
        _accumulate_usage(usage, message)
        _collect(report, message)
        scope.capture(message)
        messages.append({"role": "assistant", "content": getattr(message, "content", []) or []})

        if getattr(message, "stop_reason", None) != "pause_turn":
            return
        report.pause_resumes += 1
        if report.pause_resumes >= _MAX_PAUSE_RESUMES:
            report.stopped_reason = "pause_cap"
            return


def _user_content(question: str, history: list[dict] | None, file_ids: list[str]) -> list[dict]:
    """The opening user turn: the question (+ recent thread) and the files."""
    content: list[dict] = [{"type": "text", "text": _render_question(question, history)}]
    # One container_upload block per file — how server-side code execution
    # receives data (the files mount under $INPUT_DIR).
    content.extend({"type": "container_upload", "file_id": fid} for fid in file_ids)
    return content


def _render_question(question: str, history: list[dict] | None) -> str:
    parts: list[str] = []
    rendered = _render_history(history)
    if rendered:
        parts.append("Recent conversation, for context:\n" + rendered)
    parts.append(f"The product manager asks: {question.strip()}")
    return "\n\n".join(parts)


def _render_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = []
    for turn in history[-_HISTORY_TURNS:]:
        role = (turn.get("role") or "").strip() or "user"
        text = (turn.get("content") or turn.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _accumulate_usage(usage: Any, message: Any) -> None:
    """Fold one turn's usage into the run total (the decision-log row)."""
    try:
        u = getattr(message, "usage", None)
        if u is not None:
            usage.add(u)
    except Exception:  # noqa: BLE001 — telemetry is never load-bearing
        logger.exception("DS Claude: usage accumulation failed (continuing)")


def _collect(report: _Report, message: Any) -> None:
    """Walk one response's content blocks into ordered report segments."""
    for block in getattr(message, "content", None) or []:
        btype = _attr(block, "type")
        if btype == "text":
            report.add_text(_attr(block, "text") or "")
        elif btype in _TOOL_RESULT_BLOCK_TYPES:
            for file_id in _result_file_ids(block):
                report.add_chart(file_id)


def _result_file_ids(block: Any) -> list[str]:
    """Output file ids from a code-execution result block.

    A non-zero `return_code` or a stderr-only result is NOT a failure of the run
    — Claude sees the error and writes different code on the next execution — so
    this only harvests files and never aborts.
    """
    content = _attr(block, "content")
    if content is None:
        return []
    rtype = _attr(content, "type") or ""
    if not rtype.endswith("_result") or rtype.endswith("error_result"):
        return []
    out: list[str] = []
    for item in _attr(content, "content") or []:
        ftype = _attr(item, "type") or ""
        file_id = _attr(item, "file_id")
        if file_id and ftype.endswith("_output"):
            out.append(file_id)
    return out


def _attr(obj: Any, name: str) -> Any:
    """Access `name` on either a dict or an SDK model object.

    The streamed final message mixes both shapes (pydantic blocks, plain-dict
    tool-result content), so every read goes through here.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ── HTML rendering ───────────────────────────────────────────────────────────
# The frontend renders an answer that looks like a self-contained HTML document
# in a sandboxed, script-less iframe (looksLikeHtmlBrief → HtmlReportView), the
# same path the VoC and public-feedback reports ride. The document must therefore
# START with a doctype/html/div/style tag, and — since it is model-influenced —
# every model string is escaped here. The model never writes markup.

_REPORT_STYLE = """
:root{--ink:#1a1813;--ink2:#403c34;--soft:#8a857a;--line:#e6e3da;--rule:#d4cfc2;
--accent:#1f6f52;--serif:Georgia,'Newsreader',serif;--sans:Inter,system-ui,sans-serif;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:var(--ink);font-family:var(--serif);line-height:1.6;padding:24px 12px 56px;
-webkit-font-smoothing:antialiased}
.page{max-width:820px;margin:0 auto;padding:28px 44px 44px}
.eyebrow{font-family:var(--sans);font-size:11px;letter-spacing:.18em;text-transform:uppercase;
color:var(--accent);font-weight:700}
h1{font-weight:600;font-size:30px;line-height:1.15;margin:10px 0 8px;letter-spacing:-.01em}
.askedline{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#f6f5f0;
border-left:3px solid var(--accent);padding:9px 13px;margin:14px 0;border-radius:0 6px 6px 0}
.askedline b{color:var(--ink)}
.runline{font-family:var(--sans);font-size:12px;color:var(--soft);border-top:1px solid var(--rule);
border-bottom:1px solid var(--rule);padding:10px 0;margin-bottom:20px}
h2{font-weight:600;font-size:19px;margin:30px 0 8px;letter-spacing:-.005em}
h3{font-weight:600;font-size:16px;margin:22px 0 6px}
p{margin:10px 0;font-size:16px}
ul,ol{margin:10px 0 10px 22px}
li{margin:5px 0;font-size:16px}
strong{font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13.5px;background:#f4f2ec;
padding:1px 5px;border-radius:4px}
figure{margin:20px 0;border:1px solid var(--line);border-radius:8px;padding:10px;background:#fff}
figure img{display:block;width:100%;height:auto;border-radius:4px}
.note{font-family:var(--sans);font-size:13px;color:var(--ink2);background:#faf8f3;
border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-top:26px}
"""


def _render_html(question: str, report: _Report, charts: dict[str, str], staged: list[str]) -> str:
    body: list[str] = []
    for kind, value in report.segments:
        if kind == "text":
            body.append(_md_to_html(value))
        elif value in charts:
            body.append(
                '<figure><img alt="Analysis chart" '
                f'src="data:image/png;base64,{charts[value]}"></figure>'
            )
    note = {
        "turn_cap": _TURN_CAP_NOTE,
        "pause_cap": _TURN_CAP_NOTE,
        "timeout": _TIMEOUT_NOTE,
    }.get(report.stopped_reason or "")
    if note:
        body.append(f'<div class="note">{html.escape(note)}</div>')

    file_count = len(staged)
    runline = (
        f"Computed in a sandboxed Python environment over "
        f"{file_count} uploaded data file{'s' if file_count != 1 else ''} "
        f"({', '.join(staged[:6])}{'…' if file_count > 6 else ''}) · "
        f"{len(charts)} chart{'s' if len(charts) != 1 else ''}"
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<style>{_REPORT_STYLE}</style></head><body><div class=\"page\">"
        '<div class="eyebrow">Data analysis</div>'
        "<h1>What your data shows</h1>"
        f'<div class="askedline"><b>Asked:</b> {html.escape(question.strip())}</div>'
        f'<div class="runline">{html.escape(runline)}</div>'
        + "".join(body)
        + "</div></body></html>"
    )


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)$")


def _inline(text: str) -> str:
    """Escape, then re-apply the inline markdown we support."""
    out = html.escape(text)
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    return out


def _md_to_html(md: str) -> str:
    """The narrow markdown subset the model writes → HTML.

    Deliberately hand-rolled and escape-first (there is no markdown dependency in
    the backend, and this output goes into a document): headings, bullet and
    numbered lists, paragraphs, bold, inline code. Anything else degrades to a
    paragraph of escaped text — never to raw markup.
    """
    parts: list[str] = []
    list_tag: str | None = None
    para: list[str] = []

    def flush_para() -> None:
        if para:
            parts.append(f"<p>{'<br>'.join(_inline(l) for l in para)}</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for line in md.splitlines():
        if not line.strip():
            flush_para()
            flush_list()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_para()
            flush_list()
            level = min(max(len(heading.group(1)), 2), 3)
            parts.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue
        bullet = _BULLET_RE.match(line)
        numbered = None if bullet else _NUMBERED_RE.match(line)
        if bullet or numbered:
            flush_para()
            want = "ul" if bullet else "ol"
            if list_tag != want:
                flush_list()
                list_tag = want
                parts.append(f"<{want}>")
            item = (bullet or numbered).group(1).strip()
            parts.append(f"<li>{_inline(item)}</li>")
            continue
        flush_list()
        para.append(line.strip())

    flush_para()
    flush_list()
    return "".join(parts)


# ── decision log ─────────────────────────────────────────────────────────────

def _log_run(
    *,
    enterprise_id: str,
    model: str,
    staged: list[str],
    report: _Report,
    usage: Any,
) -> None:
    """Best-effort decision-log row, mirroring chat_analysis._log_run.

    The gateway logs the rows for calls made through `llm_call`; this path calls
    `app.llm` directly, so the audit row is written here. Token usage is the sum
    across every turn of the loop.
    """
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="ds",
            decision_type="chat_data_analysis_claude",
            factors={
                "files": len(staged),
                "turns": report.turns,
                "pause_resumes": report.pause_resumes,
                "charts": len(report.chart_file_ids),
                "stopped_reason": report.stopped_reason,
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            },
            model=model,
            prompt_version="ds-chat-analysis-claude-v1",
        )
    except Exception:  # noqa: BLE001
        logger.exception("DS Claude decision-log write failed")

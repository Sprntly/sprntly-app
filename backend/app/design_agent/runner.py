"""Design Agent tool-use loop runner.

Per AD1: direct Anthropic Messages API, no SDK orchestration.
Per AD2: claude-sonnet-4-6 + cache_control ephemeral ttl 1h at end of stable
prefix (system + tool defs); never on per-call user content.
Per AD21: one Claude call per iteration; no manager/editor/verifier sub-agents.

The loop is `while stop_reason == "tool_use"`. Stop reasons handled:
  - "tool_use": dispatch tools, append tool_results, continue
  - "end_turn": loop exit, surface final assistant content
  - "max_tokens": double max_tokens once + retry the same turn; second hit = exit
  - "refusal": exit with status='refused'
Loop-pathology detection (per agent-build-research.md §4.3):
  - same (tool_name, input_hash) 3x in sliding window of 5 -> warn via tool_result
  - tool returns is_error: true 3x in a row -> wrap-up nudge
Iteration cap: max_iters (40; raised from 24 after the convergence fix —
real non-trivial PRDs were running to the old cap without converging). The
loop-pathology circuit-breakers above plus the graduated wrap-up nudges
(_wrap_up_nudge, fired at ~half / ~quarter / last turn) are the real
convergence drivers; the cap is a hard safety rail. On a max_iters exit the
last assistant turn is salvaged as final_content so a near-complete build is
not discarded.
Per-run cost accounting: aggregate usage.{cache_creation,cache_read,input,
output}_input_tokens per turn; emit one structured cost-summary log line
on completion via the shared app.llm_telemetry primitive.

PATTERN NOTE: First structured LLM cost log in the codebase. Format here
becomes the template for retrofitting PRD/Evidence/Ask runners later.
Scenario label is a pass-through string from the route (P1-07); the runner
does NOT re-derive (single inference site, lives in db/prototypes.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.design_agent.design_system.models import DesignSystem

from app.config import settings
from app.db.prototype_comments import list_comments, mark_comments_orphaned
from app.db.prototype_pending_iterations import (
    dequeue_next,
    mark_iteration_done,
    mark_iteration_failed,
)
from app.db.prototypes import get_prototype, set_pending_question
from app.design_agent.autofixer import format_errors_for_agent
from app.design_agent.autofixer import run as autofixer_run
from app.design_agent.client import get_design_agent_client
from app.design_agent.event_stream import close as _sse_close
from app.design_agent.event_stream import publish_step
from app.design_agent.progress import FINISHING_LABEL, friendly_step
from app.design_agent.prompts import DESIGN_AGENT_ITERATE_SYSTEM
from app.design_agent.storage import read_source_files_for_checkpoint
from app.design_agent.tools import (
    ToolContext,
    dispatch,
    tool_definitions_for_mode,
)
from app.llm_telemetry import (
    MODEL_PRICING,
    RunUsage,
    log_llm_run,
    should_abort,
    should_wrap_up,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"  # AD2; NEVER claude-sonnet-4-7
DEFAULT_MAX_ITERS = 40
try:
    DEFAULT_MAX_TOKENS = int(os.environ.get("DESIGN_AGENT_MAX_TOKENS", "4096"))
except ValueError:
    logger.warning("DESIGN_AGENT_MAX_TOKENS is not a valid integer; falling back to 4096")
    DEFAULT_MAX_TOKENS = 4096
TOOL_RESULT_MAX_CHARS = 25000  # per agent-build-research.md §5.1

# ── Pre-flight cost estimate (AD14 / AD15, P3-11) ────────────────────────────
SOFT_CAP_USD = 0.50  # AD15 per-generation soft cap (trust primitive, not a hard gate)
# AD15 BACKSTOP (P6-06): a fail-closed HARD ceiling ABOVE SOFT_CAP_USD. When a
# run's projected next-iteration spend reaches this, agent_loop ABORTS (clean
# terminal "aborted" status, partial bundle salvaged) rather than degrade-and-
# continue. The soft-cap nudge stays the primary AD15 mechanism; this only
# catches pathological runs it failed to converge. Env-overridable so Apurva can
# tighten/loosen in prod without a code change. See config.py for the headroom
# justification on the 2.00 default.
HARD_CAP_USD = settings.design_agent_hard_cap_usd
# Deterministic token heuristic: chars/4 (agent-build-research.md §3.3). No network,
# no SDK token-counter dependency, ±20% accuracy band — the estimate is a "~$" guide,
# not a billing figure (the REAL cost is the post-flight cost-log emitted by P3-05).
_CHARS_PER_TOKEN = 4
# Median iterate output (agent-build-research.md §3.2). A fixed heuristic keeps the
# estimate deterministic (AC4); actual output is whatever the run produces.
_EXPECTED_OUTPUT_TOKENS = 2000

# ── AD12 orphan / re-attach: anchor-id extraction from the BUILT bundle ──────
#
# N2 — cross-language width coupling. This MUST equal `HASH_HEX_LENGTH` in
# `prototype-runtime/vite-plugin-anchor-id.ts` (P0-02), which emits
# `data-anchor-id` via `.slice(0, HASH_HEX_LENGTH)` at BUILD time. The agent
# NEVER emits `data-anchor-id` itself (AD4) — only the Vite plugin does, so the
# raw virtual_fs has no anchors and extraction MUST run over `vite_build`'s
# output. If the plugin's width ever changes, update this constant in lockstep:
# a stale width makes `_ANCHOR_ID_RE` silently match nothing, which would orphan
# EVERY open comment on the next build. A single named site (here) makes that a
# loud one-line change instead of a silent regex break.
_ANCHOR_HEX_WIDTH = 8

# Built from the width constant (N2) rather than a bare `{8}` literal. Matches
# both the plain attribute form (`data-anchor-id="abc12345"`) and the
# JS-string-escaped form (`data-anchor-id=\"abc12345\"`) Vite may emit when the
# attribute lands inside a bundled JS string literal.
_ANCHOR_ID_RE = re.compile(
    rf'data-anchor-id=(?:"|\\")([0-9a-f]{{{_ANCHOR_HEX_WIDTH}}})(?:"|\\")'
)


def extract_anchor_ids(dist_files: dict[str, str]) -> set[str]:
    """Return the distinct set of `data-anchor-id` values present across all
    built dist files. Pure; deterministic; no LLM, no network.

    The regex matches both the plain (`data-anchor-id="abc12345"`) and the
    escaped-in-JS-string (`data-anchor-id=\\"abc12345\\"`) forms, since Vite may
    emit the attribute inside a bundled JS string literal. Width is the
    `_ANCHOR_HEX_WIDTH` constant (coupled to P0-02's `HASH_HEX_LENGTH`).

    AD4-collision ([[ad4-collision-by-design]]): when the same anchor id appears
    on multiple elements (structurally-identical subtrees hash-collide), it is
    returned ONCE — set membership, not per-element. A comment on a collided id
    survives iff that id appears anywhere in the new bundle.
    """
    found: set[str] = set()
    for content in dist_files.values():
        found.update(_ANCHOR_ID_RE.findall(content))
    return found


def reconcile_comments_on_checkpoint(
    *,
    prototype_id: int,
    workspace_id: str,
    dist_files: dict[str, str],
) -> int:
    """AD12: after a new checkpoint's bundle is built, orphan every OPEN comment
    whose anchor_id is absent from the new bundle's surviving anchor IDs. Returns
    the count orphaned. Workspace-filtered (the prototype being regenerated is
    known — NOT a cross-workspace sweep).

    A comment whose anchor SURVIVES is left 'open' (re-attached implicitly — the
    anchor_id is unchanged, so P3-03's pin re-renders against the same id). AD4
    guarantees an unmodified element's anchor id is byte-identical across builds,
    so survival is exact-string membership, not fuzzy matching. There is no
    explicit un-orphan step: orphaning is one-way in P3 (a later build that
    re-introduces a deleted element does NOT auto-revive its comment).

    Called on EVERY new checkpoint build — the GENERATE staging path
    (`_stage_complete_run`) and the ITERATE staging path (`_stage_iterate_run`).
    It keys on `prototype_id` (not `checkpoint_id`), so it is build-path-agnostic.
    Callers wrap this best-effort: a reconcile failure must NOT fail the build.
    """
    surviving = extract_anchor_ids(dist_files)
    orphaned = mark_comments_orphaned(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        surviving_anchor_ids=surviving,
    )
    # Identifiers + counts only (Rule #24) — never anchor values or comment body.
    logger.info(
        "comments_reconciled prototype_id=%s surviving_anchors=%s orphaned=%s",
        prototype_id, len(surviving), orphaned,
    )
    return orphaned

# Pricing constants + RunUsage live in app.llm_telemetry — shared across
# every LLM call site in the repo. design_agent/runner.py only consumes
# the primitive; it does not own LLM telemetry shape.


@dataclass
class RunResult:
    status: str  # "complete" | "max_iters" | "aborted" | "refused" | "max_tokens" | "error" | "awaiting_clarification"
    iters: int
    usage: RunUsage
    duration_ms: int
    final_content: list[dict[str, Any]]  # raw assistant content blocks
    error_class: str | None = None
    error_message: str | None = None
    # F12 (P3-08): set ONLY when the clarifying_question sentinel ends the loop as
    # a terminal-PAUSE (status='awaiting_clarification'). Shape: {question, choices,
    # context}. None on every other exit. Persisted by the entrypoints
    # (iterate_prototype / generate_prototype) onto the prototype's pending_question
    # sidecar column; no checkpoint is staged for a pause (no bundle was built).
    pending_question: dict[str, Any] | None = None


def _hash_tool_call(name: str, input: dict[str, Any]) -> str:
    payload = json.dumps({"n": name, "i": input}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _wrap_up_nudge(iters_remaining: int) -> str:
    if iters_remaining <= 2:
        return (
            f"You have {iters_remaining} tool-call turn(s) left. FIRST, if you "
            f"imported any file you have not written yet, remove that import now — a "
            f"build cannot resolve a missing file and the whole prototype will fail "
            f"to load. THEN STOP now: finish the current file, do NOT start new "
            f"ones, and end your turn with a 1-2 sentence summary. A cut-off build "
            f"is lost."
        )
    return (
        f"You have ~{iters_remaining} tool-call turns left. FIRST, if you imported "
        f"any file you have not written yet, remove that import now — a build cannot "
        f"resolve a missing file. Start converging: make the core flow navigable, "
        f"batch any remaining writes, and prefer finishing the primary flow over "
        f"adding screens. End your turn (no tool calls) as soon as the core flow "
        f"works."
    )


def _resolve_figma_access_token(figma_file_key: str | None, workspace_id: str) -> str | None:
    """Best-effort Figma access-token resolution for the `fetch_figma` tool.

    The tool executor never decrypts tokens itself (keeps tools.py importable
    without the connector stack); the runner injects the token onto the
    ToolContext before dispatch. Mirrors routes/connectors.py `_figma_access_token`
    but is NON-fatal: a prototype may have no Figma connection, or the connector
    may be unauthorised/unreadable. In any failure case we return None and let
    `fetch_figma` degrade to its own `is_error` path rather than aborting the
    whole generation. Returns None immediately when there's no file to fetch.
    """
    if not figma_file_key:
        return None
    try:
        # Lazy import: keeps runner.py importable in unit tests without the
        # FastAPI connector/db stack, and lets tests monkeypatch this resolver.
        from app.routes.connectors import _figma_access_token

        return _figma_access_token(workspace_id)
    except Exception as exc:  # not-connected (HTTPException 404), decrypt errors, etc.
        logger.info(
            "design_agent.figma_token_unresolved figma_file_key=%s error_class=%s",
            figma_file_key,
            type(exc).__name__,
        )
        return None


def _render_palette_css(palette: dict) -> str:
    """Generate a minimal CSS file pre-seeding the design source palette.

    Called ONCE before `generate_prototype`'s agent loop starts. The file is
    written into `virtual_fs["src/index.css"]` so it is already on disk when
    the agent's first `write` call fires. This guarantees the palette is the
    starting point — the agent cannot start from stock Tailwind defaults and
    then ignore a palette instruction it received only inside the tool result.

    The agent prompt (§5 DESIGN SYSTEM) instructs the agent to `view`
    `src/index.css` first and to use `var(--background)` etc. in all
    components rather than hardcoded Tailwind palette classes.
    """
    bg = palette.get("background", "#ffffff")
    accent = palette.get("accent", "#3b82f6")
    is_dark = palette.get("is_dark", False)
    swatches = palette.get("swatches", [])

    # Derive foreground from is_dark (light text on dark bg, dark text on light bg)
    fg = "#f4f1ea" if is_dark else "#1a1a1a"

    # Find a surface color: second-most-common swatch, or bg
    surface = swatches[1] if len(swatches) > 1 else bg

    # muted: third swatch or similar
    muted = swatches[2] if len(swatches) > 2 else surface

    font_family = palette.get("font_family")
    font_weights = palette.get("font_weights") or [400, 700]

    # Generate Google Fonts import for web-safe fonts
    # Only for common Google Fonts — fall back to system stack for others
    GOOGLE_FONTS = {
        "Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins",
        "Source Sans Pro", "Nunito", "Raleway", "Playfair Display",
        "Merriweather", "PT Sans", "Ubuntu", "DM Sans", "Plus Jakarta Sans",
    }

    font_import = ""
    font_stack = "ui-sans-serif, system-ui, sans-serif"
    if font_family and font_family in GOOGLE_FONTS:
        weights_str = ";".join(str(w) for w in sorted(set(font_weights or [400, 700])))
        font_import = f'@import url("https://fonts.googleapis.com/css2?family={font_family.replace(" ", "+")}:wght@{weights_str}&display=swap");\n'
        font_stack = f'"{font_family}", ui-sans-serif, system-ui, sans-serif'
    elif font_family:
        # Non-Google font — use it optimistically in the stack (may fall through)
        font_stack = f'"{font_family}", ui-sans-serif, system-ui, sans-serif'

    return f"""{font_import}/* Design source palette — generated from Figma file */
/* DO NOT replace the :root block; use var(--background) etc. in all components */
:root {{
  --background: {bg};
  --foreground: {fg};
  --card: {surface};
  --card-foreground: {fg};
  --primary: {accent};
  --primary-foreground: {"#000000" if is_dark else "#ffffff"};
  --secondary: {surface};
  --secondary-foreground: {fg};
  --muted: {muted};
  --muted-foreground: {fg}aa;
  --accent: {accent};
  --accent-foreground: {"#000000" if is_dark else "#ffffff"};
  --border: {fg}22;
  --input: {fg}22;
  --ring: {accent};
  --font-sans: {font_stack};
}}

body {{
  background-color: var(--background);
  color: var(--foreground);
  font-family: var(--font-sans);
}}
"""


def _render_design_system_css(ds: "DesignSystem") -> str:
    """Render `src/index.css` from a unified, source-agnostic design system.

    This is the source-independent pre-seed: any source (Figma, a live website,
    a future code repository) normalizes into a `DesignSystem`, and this renders
    the same starting CSS from it. It maps the design-system tokens back onto the
    palette-dict shape the long-standing `_render_palette_css` renderer expects
    and delegates, so a Figma-sourced design system produces byte-identical CSS
    to the legacy Figma palette path — and a website-sourced one now pre-seeds
    the very same way (closing Scenario B's parity gap).

    The reconstructed `swatches` list places the surface color at index 1 and the
    muted color at index 2 because that is exactly where the renderer reads them.
    """
    colors = ds.tokens.colors
    # The default heading family is a generic system stack, NOT a named brand
    # font. The legacy renderer expects None ("no explicit font") in that case so
    # it falls through to its own system stack and emits no @import. Treat the
    # baseline family as None so a Figma design system with no detected font
    # renders byte-identically to the legacy Figma palette path.
    heading_family = ds.tokens.fonts.heading_family
    if "," in (heading_family or ""):
        heading_family = None
    palette = {
        "background": colors.background,
        "accent": colors.accent,
        "is_dark": ds.tokens.is_dark,
        # index 0 = background, 1 = surface (card), 2 = muted — the renderer's
        # swatch positions for the card and muted CSS variables.
        "swatches": [colors.background, colors.surface, colors.muted],
        "font_family": heading_family,
        "font_weights": ds.tokens.fonts.weights,
    }
    return _render_palette_css(palette)


def _resolve_design_system(
    *,
    company_id: str | None,
    provider: str | None,
    source_ref: str | None,
    raw_signals_factory,
    version_factory=None,
    force: bool = False,
) -> "DesignSystem | None":
    """Resolve the unified design system for one source via the company-scoped cache.

    Flow (cache-with-staleness-check):

      1. Probe the source version cheaply via `version_factory()` — best-effort,
         so any probe failure (network, missing token, etc.) is silently caught and
         treated as "undeterminable" (`current = None`). This probe never aborts
         resolution.
      2. Look the source up in the cache by (company, provider, source ref).
         - Cache HIT + version unchanged (`current == stored`) or undeterminable
           (`current is None`): return the cached design system as-is with no
           re-extraction and no upsert.
         - Cache HIT + version changed (`current != stored`): re-extract via
           `raw_signals_factory()`, normalize, and upsert the fresh result with
           `source_version=current`. If re-extraction yields nothing usable, fall
           back to the cached design system so a transient fetch failure does not
           discard a good cached row.
      3. Cache MISS: extract via `raw_signals_factory()`, normalize, upsert with
         `source_version=current` (None when undeterminable), and return the
         freshly-normalized design system.

    When `force` is true, the version probe still runs first, but the cache is
    bypassed entirely: the source is re-pulled, normalized, and upserted with the
    latest marker. This is the manual refresh affordance for sources whose cheap
    version probe may not see a change, such as a website that returns no ETag
    within its cache window.

    Returns the design system, or None when there is no source to resolve (no
    provider/ref) so the caller leaves the virtual filesystem un-seeded exactly
    as before. Best-effort: any failure returns None and generation continues
    without a pre-seed rather than aborting.
    """
    if not (company_id and provider and source_ref):
        return None
    try:
        from app.db.design_systems import (
            lookup_design_system,
            upsert_design_system,
        )
        # Importing the package runs the adapter-registration side-effect, so the
        # registry is populated before we look an adapter up by provider name.
        import app.design_agent.design_system  # noqa: F401 — registers adapters
        from app.design_agent.design_system.extractors import normalize, registry
        from app.design_agent.design_system.models import DesignSystem

        # Best-effort version probe — a failure here must never abort resolution
        # or discard a good cached row.
        current: str | None = None
        if version_factory is not None:
            try:
                current = version_factory()
            except Exception:
                current = None

        if force:
            raw = raw_signals_factory()
            if raw is None:
                return None
            ds = normalize(raw)
            adapter = registry.get(provider)
            upsert_design_system(
                company_id=company_id,
                source_category=getattr(adapter, "category", provider),
                source_provider=provider,
                source_ref=source_ref,
                source_version=current,
                data=ds.model_dump(),
                has_explicit_system=ds.has_explicit_system,
                confidence=ds.confidence,
                extracted_at=None,
            )
            return ds

        cached = lookup_design_system(company_id, provider, source_ref)
        if cached is not None:
            stored = cached.get("source_version")
            if current is not None and current != stored:
                # Source has changed — attempt a fresh extraction.
                try:
                    raw = raw_signals_factory()
                except Exception:
                    raw = None
                if raw is not None:
                    ds = normalize(raw)
                    adapter = registry.get(provider)
                    upsert_design_system(
                        company_id=company_id,
                        source_category=getattr(adapter, "category", provider),
                        source_provider=provider,
                        source_ref=source_ref,
                        source_version=current,
                        data=ds.model_dump(),
                        has_explicit_system=ds.has_explicit_system,
                        confidence=ds.confidence,
                        extracted_at=None,
                    )
                    return ds
            # Version unchanged, undeterminable, or re-extract yielded nothing
            # usable — use the cached design system as-is.
            return DesignSystem.model_validate(cached.get("data") or {})

        raw = raw_signals_factory()
        if raw is None:
            return None
        ds = normalize(raw)
        adapter = registry.get(provider)
        upsert_design_system(
            company_id=company_id,
            source_category=getattr(adapter, "category", provider),
            source_provider=provider,
            source_ref=source_ref,
            source_version=current,
            data=ds.model_dump(),
            has_explicit_system=ds.has_explicit_system,
            confidence=ds.confidence,
            extracted_at=None,
        )
        return ds
    except Exception:
        logger.info(
            "design_agent.design_system_resolve_failed provider=%s", provider
        )
        return None


def _design_source_for_generation(
    *,
    figma_file_key: str | None,
    figma_access_token: str | None,
    website_url: str | None,
    website_sample: dict | None,
):
    """Pick the design source for this generation and return
    ``(provider, source_ref, raw_signals_factory, version_factory)`` for the
    cache-with-staleness-check flow.

    Figma wins when a file key AND an access token are both available (a file we
    cannot read is not a usable source); otherwise a website URL is the source.
    When neither is present, returns ``(None, None, None, None)`` so the caller
    leaves the virtual filesystem un-seeded.

    The raw factory is SYNCHRONOUS and is only invoked on a cache miss (or when
    the staleness check detects a changed version) — so the (potentially
    expensive) Figma document fetch is skipped entirely on an unchanged cache
    hit. The website sample is supplied by the caller (the route already ran the
    headless-browser extraction for its scaffold prose) and is reused here, so
    no second browser run fires.

    The version factory is also SYNCHRONOUS and cheap: it calls either the Figma
    ``/files/<key>/meta`` endpoint or an HTTP HEAD on the website URL. It is
    bound with the relevant token at this point so ``_resolve_design_system`` can
    call it without knowing where the token lives.

    For Figma, a FRESH ``FigmaExtractor`` instance is created with the token set
    — the shared registry singleton carries no token and must never be mutated.
    """
    if figma_file_key and figma_access_token:
        def _figma_raw():
            from app.connectors.figma_oauth import fetch_file as _fetch_file
            from app.design_agent.design_system.adapters import FigmaExtractor
            file_doc = _fetch_file(figma_access_token, figma_file_key, 10)
            return FigmaExtractor().extract_raw_signals(figma_file_key, file_doc=file_doc)

        def _figma_version():
            from app.design_agent.design_system.adapters import FigmaExtractor
            extractor = FigmaExtractor()
            extractor.access_token = figma_access_token
            return extractor.current_version(figma_file_key)

        return "figma", figma_file_key, _figma_raw, _figma_version

    if website_url:
        def _web_raw():
            from app.design_agent.design_system.adapters import WebExtractor
            return WebExtractor().extract_raw_signals(website_url, sample=website_sample)

        def _web_version():
            from app.design_agent.design_system.adapters import WebExtractor
            return WebExtractor().current_version(website_url)

        return "web", website_url, _web_raw, _web_version

    return None, None, None, None


_STEP_LABELS = [
    "Reading the change request",
    "Analyzing the prototype",
    "Applying the change",
    "Rebuilding",
]


def _step_label(iters: int, mode: str) -> str:  # noqa: ARG001 — mode reserved for future
    """Map iteration count to a human-readable step label for the activity stream."""
    idx = min(iters - 1, len(_STEP_LABELS) - 1)
    return _STEP_LABELS[idx]


def _tool_step_label(name: str, args: dict) -> str:
    """Return a plain-English label for a tool call, for the SSE activity stream.

    Delegates to progress.friendly_step so no paths, tool names, or technical
    text ever reach the activity stream.
    """
    return friendly_step(name, args)


async def agent_loop(
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    ctx: ToolContext,
    max_iters: int = DEFAULT_MAX_ITERS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    scenario: str = "A",
    mode: str = "scaffold",
    progress_label: str | None = None,
) -> RunResult:
    """Run the agent's tool-use loop until end_turn / max_iters.

    `system_blocks` is the system prompt as a list of `{"type": "text", "text": ...}`
    blocks. The LAST block must carry `cache_control: {type: "ephemeral", ttl: "1h"}`
    per AD2. `user_message` is the initial user-turn payload (also a list of
    content blocks; PRD + Figma context). Callers (P1-05/P1-07) assemble these.

    `scenario` and `mode` are pass-through labels surfaced in the cost-summary
    log by `generate_prototype`; the loop itself is scenario-agnostic per the
    single-inference-site decision (routing lives in the route layer).
    """
    client = get_design_agent_client()
    # AD17 + AD10: the registry is partitioned PER MODE and computed ONCE here,
    # before the loop — never reassigned inside it (a mid-run tool change would
    # invalidate the prompt cache, agent-build-research.md §3.4). PLAN mode gets
    # the explore-only subset (no write/line_replace); execute/scaffold get all 6
    # action tools. Sentinels (P3-08/P3-09) are filtered per mode by tools_for_mode.
    tools_payload = tool_definitions_for_mode(mode)
    # The set of tool names the model is allowed to call THIS run — frozen here
    # alongside tools_payload. Passed to dispatch so a hallucinated out-of-mode
    # call (e.g. `write` in PLAN mode) is rejected as "Unknown tool" without ever
    # touching the virtual_fs (AD10 "mode is state"). Never recomputed mid-loop.
    allowed_tool_names = {t["name"] for t in tools_payload}
    messages: list[dict[str, Any]] = [user_message]

    usage = RunUsage()
    tool_call_window: list[str] = []
    consec_errors = 0
    start = time.perf_counter()
    iters = 0
    max_tokens_retried = False
    cost_guard_nudged = False
    # Last assistant turn's content, salvaged on a max_iters exit so a near-
    # complete build (the agent ran out of turns mid-flow) is not discarded.
    last_assistant_content: list[dict[str, Any]] = []

    try:
        while iters < max_iters:
            iters += 1
            # Real per-step signal for the SSE activity stream. Advisory and
            # non-blocking: never raises, never alters loop behaviour. The
            # frontend poll loop remains the source of truth for terminal state;
            # these are progress breadcrumbs only.
            # When the caller pins a generic label (the post-build typecheck-repair
            # loop does this), use it for every step so the compiler diagnostics fed
            # to the agent can never surface in a user-facing step; otherwise use the
            # normal per-iteration build label.
            publish_step(
                ctx.prototype_id,
                {
                    "kind": "step",
                    "text": progress_label or _step_label(iters, mode),
                    "state": "active",
                },
            )

            # Graduated wrap-up pressure (per agent-build-research.md §4.2) with
            # the REAL remaining count — was a single hardcoded "2 remaining"
            # nudge at N-1, too late to change a build's trajectory. Gentle
            # heads-up at ~half budget, firmer at ~quarter, hard stop in the last
            # turn. The trailing message here is always a user turn (the prior
            # iteration's tool_results, or the initial user message on iter 1), so
            # we append the nudge as a text block to that turn rather than a
            # second consecutive user message — the Messages API treats turns as
            # alternating, and a standalone consecutive user turn is unsafe.
            remaining = max_iters - iters
            if remaining in {max_iters // 2, max(2, max_iters // 4), 1}:
                _append_text_block(messages[-1], _wrap_up_nudge(remaining))

            loop = asyncio.get_running_loop()
            _last_step: list[str] = [""]  # mutable container for dedup

            def _stream() -> object:
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=max_tokens,
                    system=system_blocks,
                    tools=tools_payload,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        etype = type(event).__name__
                        if etype == "RawContentBlockStartEvent":
                            block = getattr(event, "content_block", None)
                            if block and getattr(block, "type", None) == "tool_use":
                                label = progress_label or friendly_step(getattr(block, "name", ""), None)
                                if label != _last_step[0]:
                                    _last_step[0] = label
                                    loop.call_soon_threadsafe(
                                        publish_step,
                                        ctx.prototype_id,
                                        {"kind": "step", "text": label, "state": "active"},
                                    )
                    return stream.get_final_message()

            resp = await asyncio.to_thread(_stream)
            usage.add(resp.usage)
            # AD15 cost guard: when the projected next-iteration spend would
            # cross the soft cap, inject the EXISTING wrap-up nudge ONCE so the
            # agent converges on a partial bundle instead of starting new files.
            # Independent of (and coexists with) the iteration-count graduated
            # nudge above — count vs spend are separate convergence signals. The
            # trailing message here is still the user turn (the assistant turn is
            # appended below), so the nudge lands alternation-safe.
            if not cost_guard_nudged and should_wrap_up(usage, MODEL, SOFT_CAP_USD):
                _append_text_block(messages[-1], _wrap_up_nudge(0))  # hard-stop wording
                cost_guard_nudged = True
                logger.info(
                    "cost_guard.degraded prototype_id=%s mode=%s reason=soft_cap_projection "
                    "est_cost_usd=%.4f cap=%.2f",
                    ctx.prototype_id, mode, usage.est_cost_usd(MODEL), SOFT_CAP_USD,
                )

            stop = resp.stop_reason
            # Reconstruct blocks using only API-legal input fields.
            # model_dump() on streamed response objects includes SDK-added keys
            # (e.g. parsed_output on text blocks) that the API rejects when
            # sent back in conversation history. Whitelist only: text→{type,text},
            # tool_use→{type,id,name,input}; any other block type falls back to
            # model_dump() stripped of SDK-only keys via exclude_none/unset.
            content = [_to_api_block(b) for b in resp.content]
            messages.append({"role": "assistant", "content": content})
            last_assistant_content = content

            # AD15 BACKSTOP (P6-06): the soft nudge above is advisory; if projected
            # spend crosses the HARD cap the run is pathological — abort with a
            # clean terminal status (not an exception) so the route's existing
            # terminal handling persists the partial work + the cost log fires.
            # Salvage the CURRENT iteration's assistant turn (just assigned to
            # last_assistant_content above), exactly as the max_iters exit does.
            # Placed AFTER the assignment so the salvaged content is this turn's,
            # not the prior iteration's (or the initial [] on iteration 1).
            if should_abort(usage, MODEL, HARD_CAP_USD):
                logger.warning(
                    "cost_guard.aborted prototype_id=%s mode=%s reason=hard_cap_projection "
                    "est_cost_usd=%.4f hard_cap=%.2f soft_cap=%.2f iters=%d",
                    ctx.prototype_id, mode, usage.est_cost_usd(MODEL),
                    HARD_CAP_USD, SOFT_CAP_USD, iters,
                )
                return _finish(usage, "aborted", iters, start, last_assistant_content, ctx.prototype_id)

            if stop == "end_turn":
                return _finish(usage, "complete", iters, start, content, ctx.prototype_id)

            if stop == "max_tokens":
                if max_tokens_retried:
                    return _finish(usage, "max_tokens", iters, start, content, ctx.prototype_id)
                max_tokens *= 2
                max_tokens_retried = True
                # The truncated assistant turn was appended above. When the cap
                # is hit MID-tool_use (the `write` content arg never finishes
                # serialising, leaving a tool_use block with partial/missing
                # input) re-sending it 400s the Messages API: "tool_use ids were
                # found without tool_result blocks immediately after" — the
                # dangling tool_use has no answering tool_result, and the loop's
                # retry never produces one. (A pure-text truncation would instead
                # 400 as two consecutive assistant turns.) Discard the truncated
                # turn and retry the SAME turn with the doubled budget, exactly as
                # this function's docstring intends ("retry the same turn"). The
                # usage from the truncated call is already counted above. (P2-03)
                messages.pop()
                continue

            if stop == "refusal":
                return _finish(usage, "refused", iters, start, content, ctx.prototype_id)

            if stop != "tool_use":
                return _finish(usage, "complete", iters, start, content, ctx.prototype_id)

            # Collect tool_use blocks; dispatch concurrently per parallel-tool-use rule.
            tool_uses = [b for b in content if b.get("type") == "tool_use"]

            # ── Exit-sentinel detection (AD17). A sentinel tool_use ENDS the loop;
            # the RESULTING state is per-sentinel, keyed on the tool NAME (NOT
            # "any sentinel" uniformly). The branch fires BEFORE dispatch, so a
            # terminal sentinel batched with action tools WINS: the action tools
            # in the same turn are NOT dispatched and the virtual_fs is untouched
            # (AC5 terminal precedence). The detection runs here rather than in
            # dispatch because the loop-break is a control-flow decision, not a
            # tool execution (agent-build-research.md §4.4: "tool name ==
            # clarifying_question -> break").
            #
            #   clarifying_question -> terminal-PAUSE: status='awaiting_clarification',
            #       carry pending_question, stage NO completion checkpoint (the run
            #       is incomplete; the answer arrives as a NEW iterate, P3-16).
            #
            # P3-09 adds the second arm WITHOUT redesigning this block — an
            # `elif (patch := next(... "propose_prd_patch" ...)):` that ends the
            # loop as terminal-COMPLETE (normal iterate completion + a prd_patches
            # row). Do NOT collapse the two into a `category == "sentinel"` check:
            # the two sentinels end the loop with DIFFERENT downstream effects.
            clar = next(
                (tu for tu in tool_uses if tu.get("name") == "clarifying_question"),
                None,
            )
            if clar:
                payload = clar.get("input") or {}
                result = _finish(usage, "awaiting_clarification", iters, start, content, ctx.prototype_id)
                result.pending_question = {
                    "question": payload.get("question"),
                    "choices": payload.get("choices"),
                    "context": payload.get("context"),
                }
                return result

            # P3-09 sentinel #2 — propose_prd_patch -> terminal-COMPLETE. Gated on
            # the tool being in THIS run's allowed set (execute-only): a scaffold/
            # plan-mode emission is NOT a registered sentinel there, so it falls
            # through to dispatch's out-of-mode "Unknown tool" rejection and the
            # loop continues (AD10 "mode is state"; keeps P3-08's
            # other-sentinel-name test green). Unlike clarifying_question (which
            # breaks BEFORE dispatch and persists nothing), this sentinel's effect
            # is a side-effecting INSERT, so we dispatch it explicitly here to run
            # `_exec_propose_prd_patch` (persists the pending prd_patches row), THEN
            # end the loop as a NORMAL iterate completion (status='complete'): the
            # agent's prior-turn write/line_replace edits stay in `virtual_fs` and
            # the caller's `_stage_iterate_run` stages them as the new checkpoint —
            # NO `complete_prototype` re-stamp, NO pause. Like clarifying_question,
            # a terminal sentinel batched with action tools WINS (the batched action
            # tools in this same turn are NOT dispatched).
            patch = next(
                (tu for tu in tool_uses
                 if tu.get("name") == "propose_prd_patch"
                 and "propose_prd_patch" in allowed_tool_names),
                None,
            )
            if patch:
                await dispatch(patch["name"], patch.get("input") or {}, ctx, allowed_tool_names)
                return _finish(usage, "complete", iters, start, content, ctx.prototype_id)

            # Emit a per-tool step event BEFORE dispatch so the frontend
            # activity stream shows what the agent is about to do at tool
            # granularity (not just the coarse per-iteration label above).
            # Advisory/non-blocking — a publish failure never alters loop behaviour.
            for tu in tool_uses:
                publish_step(
                    ctx.prototype_id,
                    {
                        "kind": "step",
                        "text": _tool_step_label(tu.get("name", ""), tu.get("input") or {}),
                        "state": "active",
                    },
                )

            results = await asyncio.gather(*[
                dispatch(tu["name"], tu.get("input") or {}, ctx, allowed_tool_names)
                for tu in tool_uses
            ])

            # Static AST autofixer (P1-10): after every successful write/
            # line_replace on a .tsx/.ts file, validate the emitted content.
            # On failure, mutate the result to is_error so the agent receives
            # the analysis feedback as a normal tool_result and self-corrects
            # (per agent-build-research.md §2.4 + §4.1). Runs BEFORE the next
            # user message is built. Not an LLM call — does not touch `usage`.
            for i, (tu, result) in enumerate(zip(tool_uses, results)):
                if tu["name"] not in {"write", "line_replace"} or result.get("is_error"):
                    continue
                fpath = (tu.get("input") or {}).get("path", "")
                if not fpath.endswith((".tsx", ".ts")):
                    continue
                af = await autofixer_run(fpath, ctx.virtual_fs.get(fpath, ""), ctx.virtual_fs)
                if not af.get("ok"):
                    results[i] = {
                        "is_error": True,
                        "content": format_errors_for_agent(af),
                        "tool_name": tu["name"],
                    }

            # Pathology detection (per §4.3): same (name, input) 3x in window of 5.
            new_warnings: list[str] = []
            for tu in tool_uses:
                h = _hash_tool_call(tu["name"], tu.get("input") or {})
                tool_call_window.append(h)
                tool_call_window = tool_call_window[-5:]
                if tool_call_window.count(h) >= 3:
                    new_warnings.append(
                        f"You have called {tu['name']} with identical input "
                        f"3 times in the last 5 calls. Either change parameters "
                        f"or proceed without re-querying."
                    )

            # Consecutive error tracking.
            had_error = any(r.get("is_error") for r in results)
            consec_errors = (consec_errors + 1) if had_error else 0
            if consec_errors >= 3:
                new_warnings.append(
                    "Tool errors have repeated 3 times consecutively. Step back, "
                    "reassess the approach before retrying the same tool."
                )

            # Build the next user message: tool_result blocks FIRST per
            # agent-build-research.md §1.3, then any warnings as text blocks.
            next_content: list[dict[str, Any]] = []
            for tu, result in zip(tool_uses, results):
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": _serialise_tool_result(result),
                }
                if result.get("is_error"):
                    block["is_error"] = True
                next_content.append(block)
            for warn in new_warnings:
                next_content.append({"type": "text", "text": warn})

            messages.append({"role": "user", "content": next_content})

        # Exited because iters == max_iters. Salvage the last assistant turn as
        # final_content (was discarded as []) — a build that ran out of turns
        # mid-flow is usually near-complete and worth staging, not throwing away.
        return _finish(usage, "max_iters", iters, start, last_assistant_content, ctx.prototype_id)

    except Exception as exc:
        result = _finish(usage, "error", iters, start, [], ctx.prototype_id)
        result.error_class = type(exc).__name__
        result.error_message = str(exc)
        return result


def _append_text_block(message: dict[str, Any], text: str) -> None:
    """Append a text block to an existing message's content, keeping the turn
    single (alternation-safe). Promotes a bare-string content to a block list
    if a caller passed the older `content: str` shape."""
    block = {"type": "text", "text": text}
    content = message.get("content")
    if isinstance(content, list):
        content.append(block)
    elif isinstance(content, str):
        message["content"] = [{"type": "text", "text": content}, block]
    else:
        message["content"] = [block]


def _to_api_block(block: Any) -> dict[str, Any]:
    """Reconstruct a content block using only API-legal input fields.

    Sources all values from model_dump() so SDK-added extras (e.g.
    parsed_output on TextBlock) never leak into conversation history.
    Works with both real SDK objects and test fakes that implement
    model_dump().
    """
    d = block.model_dump()
    if d.get("type") == "text":
        return {"type": "text", "text": d["text"]}
    if d.get("type") == "tool_use":
        return {"type": "tool_use", "id": d["id"], "name": d["name"], "input": d["input"]}
    return {k: v for k, v in d.items() if v is not None}


def _serialise_tool_result(result: dict[str, Any]) -> str:
    """Compress a tool result dict to a JSON string for the Anthropic API."""
    safe = {k: v for k, v in result.items() if k != "is_error"}
    return json.dumps(safe, default=str)[:TOOL_RESULT_MAX_CHARS]  # truncate per §5.1


def _persist_pending_question_if_paused(
    result: RunResult, prototype_id: int, workspace_id: str
) -> None:
    """F12 (P3-08): if the run ended as a clarifying_question terminal-PAUSE,
    write the question payload to the prototype's `pending_question` sidecar.

    No-op for every other status. Workspace-filtered (the helper applies the
    filter). Shared by `generate_prototype` (scaffold) and `iterate_prototype`
    (execute/plan) so the persistence is a single site. Does NOT touch the
    prototype `status` (the sidecar IS the awaiting-answer signal) and stages no
    checkpoint — the answer arrives as a NEW iterate (P3-16)."""
    if result.status != "awaiting_clarification":
        return
    set_pending_question(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        question=result.pending_question,
    )


def _finish(
    usage: RunUsage,
    status: str,
    iters: int,
    start: float,
    final_content: list,
    prototype_id: int | None = None,
) -> RunResult:
    # Flush the SSE terminal event to all active subscribers so every open
    # /events stream ends cleanly. Covers every exit path (complete / max_iters /
    # aborted / error) in one place. awaiting_clarification is a pause, not a
    # terminal — the stream stays open while the user composes an answer.
    if prototype_id is not None and status != "awaiting_clarification":
        _sse_close(prototype_id, kind="done" if status == "complete" else "error")
    duration_ms = int((time.perf_counter() - start) * 1000)
    return RunResult(
        status=status,
        iters=iters,
        usage=usage,
        duration_ms=duration_ms,
        final_content=final_content,
    )


async def generate_prototype(
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    figma_file_key: str | None,
    figma_node_id: str | None = None,
    scenario: str = "A",
    github_repo: str | None = None,
    website_url: str | None = None,
    website_sample: dict | None = None,
) -> tuple[RunResult, dict[str, str]]:
    """Public entrypoint: run agent_loop with a fresh ToolContext, emit the
    cost-summary log line, and return `(result, virtual_fs)` for P1-07 + P1-08
    to persist + stage.

    P1-08 extends the return type: the `virtual_fs` map (the raw TSX/TS files the
    agent built up via `write`/`line_replace`) is returned alongside the
    `RunResult` so the route hook can run `vite_build` over it and stage the
    bundle. The loop itself never persisted `virtual_fs`; it lives on the
    `ToolContext`, which is local to this function — hence the threading.

    The Figma access token is resolved here (runner-injected onto the
    ToolContext, before any tool dispatch) so `fetch_figma` can reach the
    Figma data API. Resolution is best-effort: a prototype without a Figma
    connection runs fine, with fetch_figma reporting its own is_error.
    """
    # Pre-seed virtual_fs with the source's design-system CSS before the agent's
    # first write, so it cannot start from stock Tailwind defaults and then ignore
    # a design instruction embedded only in a tool result. The design system is
    # resolved through the company-scoped cache and rendered the SAME way for any
    # source — a Figma file or a live brand website — so Scenario B now pre-seeds
    # exactly as Scenario A does. Best-effort throughout: any failure here leaves
    # the virtual filesystem un-seeded and generation continues.
    #
    # `workspace_id` carries the company id (the route resolves it from the
    # company-scoped session before calling in), which is the cache scope.
    figma_access_token = _resolve_figma_access_token(figma_file_key, workspace_id)
    virtual_fs: dict[str, str] = {}

    provider, source_ref, raw_factory, version_factory = _design_source_for_generation(
        figma_file_key=figma_file_key,
        figma_access_token=figma_access_token,
        website_url=website_url,
        website_sample=website_sample,
    )
    design_system = await asyncio.to_thread(
        _resolve_design_system,
        company_id=workspace_id,
        provider=provider,
        source_ref=source_ref,
        raw_signals_factory=raw_factory,
        version_factory=version_factory,
    )
    if design_system is not None and design_system.has_explicit_system:
        try:
            virtual_fs["src/index.css"] = _render_design_system_css(design_system)
            logger.info(
                "design_agent.design_system_pre_seeded prototype_id=%s provider=%s bg=%s accent=%s is_dark=%s",
                prototype_id, provider,
                design_system.tokens.colors.background,
                design_system.tokens.colors.accent,
                design_system.tokens.is_dark,
            )
        except Exception:
            pass  # best-effort — generation continues without pre-seeded CSS

    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        virtual_fs=virtual_fs,  # pre-seeded with palette CSS (may be {} if no Figma)
        figma_file_key=figma_file_key,
        figma_node_id=figma_node_id,  # frame-level targeting; None when absent
        figma_access_token=figma_access_token,
    )
    result = await agent_loop(
        system_blocks=system_blocks,
        user_message=user_message,
        ctx=ctx,
        scenario=scenario,
        mode="scaffold",
    )
    # F12 (P3-08): the clarifying_question sentinel can fire in scaffold mode too
    # (it is registered in all modes). On a pause, persist the question on the
    # prototype's pending_question sidecar — the prototype status is left untouched
    # (the `pending_question IS NOT NULL` signal is the "awaiting answer" marker,
    # NOT a new status enum value) and NO checkpoint is staged here (no bundle was
    # built). The answer arrives as a NEW iterate (P3-16), which clears it.
    _persist_pending_question_if_paused(result, prototype_id, workspace_id)
    # Cost-summary log line per TICKET_STANDARD §2 LLM-calling AC —
    # emitted via the shared llm_telemetry.log_llm_run primitive so the
    # log shape stays identical across every LLM call site in the repo
    # (and future PRD/Evidence/Ask/Brief runners can adopt with one call).
    # The connected repo full_name (e.g. "org/repo") is a non-secret identifier;
    # carry it into the cost-summary identifier so a codebase-grounded run is
    # observable in the telemetry. Included only when present — never a token,
    # PRD body, or comment content. Prompt context only; no fetch.
    run_identifier: dict[str, Any] = {
        "prototype_id": prototype_id,
        "scenario": scenario,
        "mode": "scaffold",
    }
    if github_repo:
        run_identifier["codebase_repo"] = github_repo
    log_llm_run(
        operation="design_agent.run.complete",
        identifier=run_identifier,
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


def _render_typecheck_repair_user(diagnostics: str) -> str:
    """Build the user turn for a repair re-entry. Plain English; the diagnostics
    are the build's own message (real file and symbol names), never internal IDs."""
    return (
        "The prototype was built, but it will not run yet. The compiler found "
        "references to things that do not exist:\n\n"
        f"{diagnostics}\n\n"
        "Fix this so the app runs. For each problem, either write the missing file "
        "or, if the reference is left over and no longer needed, remove the import "
        "that points at it. Prefer writing the missing screen so the flow stays "
        "complete. Make the smallest set of changes that resolves every problem, "
        "then end your turn with a one-sentence summary. Do not start unrelated work."
    )


async def repair_typecheck_run(
    *,
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    virtual_fs: dict[str, str],
    diagnostics: str,
    figma_file_key: str | None = None,
    figma_node_id: str | None = None,
    scenario: str = "A",
    max_iters: int = 6,
) -> tuple[RunResult, dict[str, str]]:
    """Re-enter the agent once to repair a runtime-breaking type diagnostic.

    Used by the route's post-build typecheck-repair loop: the agent is handed the
    files it already built plus the compiler diagnostics and asked to write the
    missing file(s) or drop the dangling import so the bundle resolves. Returns
    `(result, virtual_fs)` — the second element is the (possibly) updated source.

    Runs with the same scaffold tools as the first pass, but pins a generic
    progress label so the diagnostics handed to it here never reach a user-facing
    step event. The Figma token is re-resolved best-effort so a repair turn can
    still read the design if it needs to. The caller's `virtual_fs` is copied, not
    mutated in place.
    """
    figma_access_token = _resolve_figma_access_token(figma_file_key, workspace_id)
    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        virtual_fs=dict(virtual_fs),
        figma_file_key=figma_file_key,
        figma_node_id=figma_node_id,
        figma_access_token=figma_access_token,
    )
    user_message = {
        "role": "user",
        "content": [{"type": "text", "text": _render_typecheck_repair_user(diagnostics)}],
    }
    result = await agent_loop(
        system_blocks=system_blocks,
        user_message=user_message,
        ctx=ctx,
        max_iters=max_iters,
        scenario=scenario,
        mode="scaffold",
        progress_label=FINISHING_LABEL,
    )
    log_llm_run(
        operation="design_agent.run.typecheck_repair",
        identifier={"prototype_id": prototype_id, "scenario": scenario, "mode": "scaffold"},
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


def prepend_plan_addendum(
    system_blocks: list[dict[str, Any]], plan_text: str
) -> list[dict[str, Any]]:
    """Plan->Execute transition (P3-07, AD10): return a NEW system-block list with
    the approved plan prepended as a leading addendum block.

    The addendum goes BEFORE the iterate system prompt, so the AD2 cache breakpoint
    (which lives on the LAST block — system + tool defs) is untouched: the existing
    blocks keep their position and their `cache_control`. The plan is constant for
    the whole execute run (it never changes across the run's turns), so it belongs
    in the cached stable prefix. The input list is not mutated (a fresh list is
    returned) so the caller's blocks are reusable."""
    addendum = {
        "type": "text",
        "text": (
            "APPROVED PLAN (the team reviewed and approved this in Plan mode — "
            "execute it with the smallest possible diff):\n"
            f"{plan_text.strip()}"
        ),
    }
    return [addendum, *system_blocks]


async def iterate_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    current_source: dict[str, str],
    figma_file_key: str | None,
    scenario: str = "A",
    mode: str = "execute",
    approved_plan: str | None = None,
) -> tuple[RunResult, dict[str, str]]:
    """Iterate entrypoint (P3-05): mirror of `generate_prototype` for the EDIT
    path (AD8). The difference from scaffold is the seed: the ToolContext's
    `virtual_fs` is PRE-POPULATED with the current checkpoint's source files
    (loaded by the caller via `read_source_files_for_checkpoint`, P2-04) so a
    `view` of an existing file returns its content instead of a not-found error.
    The loop, cache discipline, and Figma-token injection are identical.

    `mode` is the tool-partition value threaded into `agent_loop` (and through to
    `tools_for_mode`, P3-07). The canonical values are `'execute'` (default) and
    `'plan'` (the AD10 explore-only run). NEVER `'iterate'` — P3-07 partitions on
    `scaffold`/`plan`/`execute`. The `mode="iterate"` string below is a DIFFERENT
    thing: the cost-log identifier (telemetry), distinguishing iterate runs from
    scaffold runs in the structured log, independent of the tool-partition mode.

    `approved_plan` (P3-07 Plan->Execute transition): when set (the confirm-plan
    path passes the approved plan text), it is prepended to `system_blocks` as a
    leading addendum so the EXECUTE run is told exactly what the team approved. It
    is None for a plain re-prompt iterate and for plan-mode runs.

    Returns `(result, virtual_fs)` — the post-run virtual_fs (seed + the agent's
    edits) for the caller's iterate-staging path (`_stage_iterate_run`).
    """
    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        # Copy so the agent's in-loop mutations never write back into the caller's
        # source dict; `view` returns real content because the seed is present.
        virtual_fs=dict(current_source),
        figma_file_key=figma_file_key,
        figma_access_token=_resolve_figma_access_token(figma_file_key, workspace_id),
    )
    effective_system_blocks = (
        prepend_plan_addendum(system_blocks, approved_plan)
        if approved_plan and approved_plan.strip()
        else system_blocks
    )
    result = await agent_loop(
        system_blocks=effective_system_blocks,
        user_message=user_message,
        ctx=ctx,
        scenario=scenario,
        mode=mode,
    )
    # F12 (P3-08): on an awaiting_clarification pause, persist the question on the
    # prototype's pending_question sidecar and stage NO checkpoint (no bundle was
    # built). iterate_prototype itself never stages checkpoints (the route's
    # _stage_iterate_run does, and only on status=='complete'), so the "no
    # checkpoint on a pause" guarantee holds end-to-end. The prototype status is
    # left untouched; pending_question IS NOT NULL is the awaiting-answer signal.
    _persist_pending_question_if_paused(result, prototype_id, workspace_id)
    # Cost-summary log line — same shared primitive as generate_prototype. The
    # operation + mode identifier mark this as an ITERATE run for telemetry; the
    # log carries identifiers + token counts only (Rule #24), never PRD/comment/
    # Figma content.
    log_llm_run(
        operation="design_agent.run.iterate",
        identifier={
            "prototype_id": prototype_id,
            "scenario": scenario,
            "mode": "iterate",
        },
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


MANUAL_EDIT_MAX_ITERS = 4  # AD23 / P4-02 / P4-11: a manual commit is tiny, but the
# manual-edit prompt's own workflow is search (locate) → view (confirm) → line_replace
# (batched edits) → +1 self-correction turn for an autofixer-rejected edit ≈ 3–4 turns.
# The old 2-cap could not hold that for a realistic multi-anchor edit (it exited
# `max_iters` → checkpoint never advanced — P4-10 live failure). 4 is the smallest cap
# that holds search→view→batched-edit + ONE recovery turn. Still a TIGHT runaway rail,
# NOT DEFAULT_MAX_ITERS (40); the expected count is 2–3, the 4th turn is margin only.


async def manual_edit_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    current_source: dict[str, str],
    figma_file_key: str | None,
    scenario: str = "A",
) -> tuple[RunResult, dict[str, str]]:
    """Manual-edit commit entrypoint (P4-02, AD23): a THIN sibling of
    `iterate_prototype` for the F13 commit-back path. The user already applied the
    visual change in the live preview (no LLM computed it); this run's ONLY job is
    to make the SOURCE match — translating the `{anchor_id, property, old_value,
    new_value}` triples into the smallest `line_replace` edits.

    The difference from `iterate_prototype` is the cap: this passes an EXPLICIT
    `max_iters=MANUAL_EDIT_MAX_ITERS` (4) into `agent_loop` — it does NOT inherit
    `DEFAULT_MAX_ITERS` (40). The prompt's own faithful workflow is search (locate the
    target lines — the source carries no `data-anchor-id`) → view (confirm before a
    blind write) → batched `line_replace` → one optional self-correction turn when the
    autofixer rejects an edit ≈ 3–4 turns; 4 is the smallest cap that holds it plus one
    recovery turn (P4-11). It is still a TIGHT runaway rail (expected count 2–3, the 4th
    turn is margin only), not the 40-turn generate budget. The tool-partition `mode` is
    `"manual"` (all 6 action tools, NO sentinels — a commit never pauses or
    proposes a PRD patch, per tools_for_mode's manual branch). Cache discipline,
    Figma-token injection, and the cost-log shape are identical to iterate.

    Returns `(result, virtual_fs)` — the post-run virtual_fs (seed + the agent's
    committed edits) for the caller's iterate-staging path (`_stage_iterate_run`).
    """
    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        # Copy so the agent's in-loop mutations never write back into the caller's
        # source dict; `view` returns real content because the seed is present.
        virtual_fs=dict(current_source),
        figma_file_key=figma_file_key,
        figma_access_token=_resolve_figma_access_token(figma_file_key, workspace_id),
    )
    result = await agent_loop(
        system_blocks=system_blocks,
        user_message=user_message,
        ctx=ctx,
        # AD23 hard cap — pass EXPLICITLY; never inherit DEFAULT_MAX_ITERS (40).
        max_iters=MANUAL_EDIT_MAX_ITERS,
        scenario=scenario,
        mode="manual",
    )
    # Cost-summary log line — same shared primitive as iterate/generate. The
    # operation + mode identifier mark this as a MANUAL-EDIT run for telemetry; the
    # log carries identifiers + token counts only (Rule #24), never source/edit
    # content. AC4/AC6: the line carries mode="manual" + iters (≤2).
    log_llm_run(
        operation="design_agent.run.manual_edit",
        identifier={
            "prototype_id": prototype_id,
            "scenario": scenario,
            "mode": "manual",
        },
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


def _chars(source: dict[str, str]) -> int:
    """Total character count of a source bundle (paths + contents). The path is
    counted because it appears in the rendered prefix (`--- <path> ---` headers in
    render_iterate_user, P3-05). Deterministic over the same dict."""
    return sum(len(path) + len(content) for path, content in source.items())


def _chars_comments(open_comments: list[dict]) -> int:
    """Total character count of the open comment threads as they enter the cacheable
    prefix (anchor + body). Deterministic over the same list."""
    return sum(
        len(c.get("anchor_id") or "") + len((c.get("body") or ""))
        for c in open_comments
    )


async def estimate_iterate_cost(
    *,
    prototype_id: int,
    workspace_id: str,
    prompt: str,
    applied_comment_id: int | None = None,
) -> dict:
    """Pre-flight cost estimate for an iterate run (AD14). Deterministic; makes NO
    Anthropic call — a token-count + price calc only, so cancelling provably costs
    nothing (the iterate route is only hit on Continue).

    Counts the CACHEABLE prefix (iterate system prompt + the current bundle source +
    the open comment threads) and the VOLATILE suffix (the user's iterate prompt),
    converts chars→tokens via the chars/4 heuristic (`_CHARS_PER_TOKEN`), then prices
    via `llm_telemetry.MODEL_PRICING[MODEL]` — the SAME constants `RunUsage.est_cost_usd`
    uses (no second pricing table, AC1). The estimate prices the cache-READ path for
    the cacheable prefix (the common iterate case re-uses recent context within the 1h
    window) plus fresh input for the volatile prompt plus the expected output.

    Cost framing per AD14: returns the cached-vs-fresh token split so the UI can render
    "reusing context from your last run". `exceeds_soft_cap` flags projected spend over
    the $0.50 AD15 guide.

    S2: `read_source_files_for_checkpoint(prototype_id, checkpoint_id)` is positional,
    async, and storage-path (NOT workspace-filtered) — `get_prototype` FIRST (workspace-
    filtered) to obtain `current_checkpoint_id`, then read. A missing/None checkpoint
    yields an empty bundle (mirrors `_run_iterate_bg`'s defensive read).

    `applied_comment_id` is accepted for parity with the iterate request shape; the F10
    applied comment is already part of the open-comment set counted above, so it does
    not change the estimate (kept in the signature so the route can forward the body
    verbatim).
    """
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    checkpoint_id = proto.get("current_checkpoint_id") if proto else None
    source = (
        await read_source_files_for_checkpoint(prototype_id, checkpoint_id)
        if checkpoint_id
        else {}
    )
    open_comments = [
        c
        for c in list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
        if c.get("status") == "open"
    ]

    cacheable_chars = len(DESIGN_AGENT_ITERATE_SYSTEM) + _chars(source) + _chars_comments(open_comments)
    volatile_chars = len(prompt)
    cached_input_tokens = cacheable_chars // _CHARS_PER_TOKEN
    new_input_tokens = volatile_chars // _CHARS_PER_TOKEN

    p = MODEL_PRICING[MODEL]
    est = (
        cached_input_tokens * p["cache_read"]
        + new_input_tokens * p["input"]
        + _EXPECTED_OUTPUT_TOKENS * p["output"]
    )
    return {
        "cached_input_tokens": cached_input_tokens,
        "new_input_tokens": new_input_tokens,
        "expected_output_tokens": _EXPECTED_OUTPUT_TOKENS,
        "est_cost_usd": round(est, 4),
        "soft_cap_usd": SOFT_CAP_USD,
        "exceeds_soft_cap": est > SOFT_CAP_USD,
        "model": MODEL,
    }


async def drain_iteration_queue(*, prototype_id: int, workspace_id: str) -> None:
    """Serially drain the pending-iteration queue for a prototype (AD11, P3-06).

    Pops the OLDEST pending row, marks it 'running' (`dequeue_next`), runs it
    through the P3-05 iterate body, marks it 'done' (or 'failed'), then chains the
    next pending row via `asyncio.create_task` until the queue is empty. At most
    ONE iteration runs at a time per prototype — each `_run_one_iteration` is
    awaited to completion BEFORE the next is dequeued, so there is never more than
    one 'running' row. A failed iteration marks its row 'failed' and the drain
    CONTINUES to the next pending row (one bad prompt does not stall the queue).

    Idempotent kick: if there is no pending row (e.g. the queue is already being
    drained, or it is empty), this no-ops — so the route can fire it on every
    enqueue without spawning a second concurrent drain.

    Deferred import (`_run_one_iteration`, `_inflight_tasks`): the routes module
    imports this function at load time, so a top-level `import app.routes...` here
    would be a cycle. The function-local import is the established break in this
    codebase (mirrors `_resolve_figma_access_token` and
    `db.prototypes.record_export_at_complete`). `_run_one_iteration` owns the real
    iterate body; `_inflight_tasks` is the route's strong-ref set (AC9).
    """
    row = dequeue_next(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        return
    from app.routes.design_agent import _inflight_tasks, _run_one_iteration
    try:
        await _run_one_iteration(row)
        mark_iteration_done(iteration_id=row["id"], workspace_id=workspace_id)
    except Exception as exc:  # noqa: BLE001 — one bad iteration must not stall the queue.
        mark_iteration_failed(
            iteration_id=row["id"],
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.warning(
            "iteration_failed prototype_id=%s iteration_id=%s error_class=%s",
            prototype_id, row["id"], type(exc).__name__,
        )
    # Chain the next pending iteration. Strong-ref discipline (AC9): hold the task
    # in the route's _inflight_tasks set + discard on done, so it is never GC'd
    # mid-run. The chained drain no-ops if the queue is now empty (the `if not row`
    # guard above), so chaining terminates.
    nxt = asyncio.create_task(
        drain_iteration_queue(prototype_id=prototype_id, workspace_id=workspace_id)
    )
    _inflight_tasks.add(nxt)
    nxt.add_done_callback(_inflight_tasks.discard)

"""Semantic DESCRIBE layer over a deterministically-enumerated screen graph.

The map phase enumerates a connected repo's surfaces structurally
(nodes/edges/shell) with no semantics. This module adds the one deliberate
LLM pass on the index side: a single batched call that annotates each
ALREADY-ENUMERATED surface with ``{summary, contains, user_actions,
key_entities}`` so that structurally-similar surfaces stop colliding once a
downstream consumer reasons over them.

Two guarantees keep the LLM honest:

- It only ANNOTATES the fixed node set; it never adds, removes, merges, or
  renames a node. The node set is ground truth from the map phase.
- The completeness/hallucination gate (``check_completeness``) is a pure,
  deterministic set-diff of the described ids against the enumerated node
  ids. A described id absent from the node set is a hallucination; a node id
  never described is a coverage gap. That diff makes source-only completeness
  provable without a human.

Mirrors the locate service's single-shot ``messages.create`` +
JSON-fence-strip + ``RunUsage`` never-raise discipline. The describe call
never raises: a parse failure degrades to an empty ``DescribedMap``, which
the gate then reports as an all-coverage-gap failure — a loud signal, never
a silent empty pass.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.design_agent.codebase_map.types import MapResult, ScreenNode

logger = logging.getLogger(__name__)

# Canonical model for the describe call; never substitute opus here.
_MODEL = "claude-sonnet-4-6"

# Output cap — the batched descriptor set is compact JSON; the headroom covers
# a large surface count without truncating mid-object.
_DESCRIBE_MAX_TOKENS = 16000

# Per-surface source head sent into the stable describe-input block. The repo
# reader already bounds file bodies; this caps the prefix per surface so a
# handful of large files cannot blow the cached prefix.
_SOURCE_HEAD_CHAR_CAP = 1600

# Component-name suffixes stripped when humanizing a derived title.
_TITLE_SUFFIXES = ("Screen", "Page", "Layout", "View")


# ─── Output schema ────────────────────────────────────────────────────────────


class SurfaceDescriptor(BaseModel):
    """The semantic annotation for one already-enumerated surface."""

    id: str                       # the node id EXACTLY as enumerated (carried)
    kind: str                     # "route" | "section" | "shell" — carried from the node
    route: str = ""               # carried from the node (field is `route`, not `path`)
    title: str = ""               # DERIVED in code from the node's entry_component
    summary: str = ""             # 1-2 sentences: what this surface IS
    contains: list[str] = Field(default_factory=list)        # sub-sections / tabs
    user_actions: list[str] = Field(default_factory=list)    # primary things a user DOES
    key_entities: list[str] = Field(default_factory=list)    # main data shown
    hosts_chrome_level_features: str = ""  # set ONLY for kind == "shell"


class DescribedMap(BaseModel):
    """The described surface set for a connected repo at one commit."""

    repo: str
    commit_sha: str
    surfaces: list[SurfaceDescriptor] = Field(default_factory=list)


class CompletenessReport(BaseModel):
    """The result of diffing the described ids against the enumerated ids."""

    hallucinated_ids: list[str] = Field(default_factory=list)   # described, not enumerated
    coverage_gap_ids: list[str] = Field(default_factory=list)   # enumerated, not described
    ok: bool                                                    # both lists empty


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _derive_title(node: "ScreenNode") -> str:
    """Humanize a node's entry_component into a display title.

    Strips a trailing Screen/Page/Layout/View suffix and splits the remaining
    PascalCase into space-separated words ("MembersSettings" → "Members
    Settings", "TeamScreen" → "Team"). Falls back to the route when the
    entry_component is empty. Derived in code — the node has no title field and
    the model is never asked to invent one.
    """
    comp = (node.entry_component or "").strip()
    if not comp:
        return node.route
    for suffix in _TITLE_SUFFIXES:
        if comp.endswith(suffix) and len(comp) > len(suffix):
            comp = comp[: -len(suffix)]
            break
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", comp)
    return " ".join(words) if words else comp


def _as_str_list(value: object) -> list[str]:
    """Coerce a model-supplied field into a clean list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def _build_describe_input(
    map_result: "MapResult",
    nodes: list["ScreenNode"],
    sources: Optional[Mapping[str, str]],
) -> str:
    """Render the STABLE describe-input block (the cached prefix).

    Lists every enumerated surface with its carried fields plus a bounded head
    of its real source. PRD-independent and volatile-input-free, so it is
    stable for a given (repo, commit_sha) and caches across a describe + any
    re-describe within the cache window.
    """
    lines: list[str] = [
        f"REPO: {map_result.repo} @ {map_result.commit_sha}",
        "",
        "SURFACES TO DESCRIBE:",
    ]
    for node in nodes:
        body = (sources.get(node.file, "") if sources else "") or ""
        head = body[:_SOURCE_HEAD_CHAR_CAP]
        lines.append("")
        lines.append(
            f"- id={node.id} kind={node.kind} route={node.route or '(none)'} "
            f"component={node.entry_component or '(none)'} file={node.file or '(none)'}"
        )
        if head:
            lines.append("  SOURCE HEAD:")
            lines.append(head)
        else:
            lines.append("  SOURCE HEAD: (unavailable)")
    return "\n".join(lines)


# ─── Describe pass ────────────────────────────────────────────────────────────


def describe_surfaces(
    map_result: "MapResult",
    sources: Optional[Mapping[str, str]],
    *,
    client=None,
) -> DescribedMap:
    """Annotate every enumerated surface via a single batched LLM call.

    Returns a ``DescribedMap`` on success, or a degraded ``DescribedMap`` (empty
    surfaces) on any failure. Never raises — a describe failure degrades rather
    than 500ing, and the empty described set then fails ``check_completeness`` as
    an all-coverage-gap, which is the correct loud signal to the caller.

    Parameters
    ----------
    map_result:
        The deterministically-enumerated map. Its ``nodes`` are the fixed set
        the describe pass annotates and the gate diffs against. The describe
        pass NEVER calls ``build_map`` — it only consumes the result.
    sources:
        The bounded in-memory source map produced upstream ({repo-relative
        path: decoded text}). Per-surface source heads are read from it; the
        describe pass does NOT re-fetch.
    client:
        An Anthropic client (or any compatible object). When None, the cached
        design-agent client is used. Injecting a fake here enables unit-testing
        without network calls.
    """
    from app.design_agent.prompts import DESCRIBE_SYSTEM

    start_ms = time.monotonic()
    _usage: Optional[object] = None
    _status = "error"
    _error_class: Optional[str] = None
    _n_dup = 0

    nodes = list(map_result.nodes)
    described = DescribedMap(
        repo=map_result.repo,
        commit_sha=map_result.commit_sha,
        surfaces=[],
    )

    try:
        # Empty node set → vacuously complete; short-circuit BEFORE any LLM call.
        if not nodes:
            _status = "empty"
            return described

        if client is None:
            from app.design_agent.client import get_design_agent_client

            client = get_design_agent_client()

        from app.llm_telemetry import RunUsage

        describe_input = _build_describe_input(map_result, nodes, sources)

        # System blocks: the instruction constant first (no cache_control), then
        # the STABLE describe-input block carrying the cache breakpoint. Describe
        # is PRD-independent, so the whole input is the stable prefix.
        system_blocks = [
            {"type": "text", "text": DESCRIBE_SYSTEM},
            {
                "type": "text",
                "text": describe_input,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        messages = [
            {
                "role": "user",
                "content": "Describe every surface listed above. Return STRICT JSON only.",
            }
        ]

        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_DESCRIBE_MAX_TOKENS,
            system=system_blocks,
            messages=messages,
        )

        _usage = RunUsage()
        _usage.add(resp.usage)

        # Extract text from the first content block.
        raw_text: str = ""
        try:
            raw_text = resp.content[0].text
        except (AttributeError, IndexError, TypeError):
            logger.warning("describe: unexpected response shape; returning degraded")
            _status = "empty"
            return described

        # Strip optional ```json ... ``` fences.
        text = raw_text.strip()
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        parsed = json.loads(text)
        raw_surfaces = parsed.get("surfaces", []) if isinstance(parsed, dict) else []

        node_by_id = {node.id: node for node in nodes}
        surfaces: list[SurfaceDescriptor] = []
        seen: set[str] = set()
        for obj in raw_surfaces:
            if not isinstance(obj, dict):
                continue
            sid = str(obj.get("id", "")).strip()
            if not sid:
                continue
            if sid in seen:
                _n_dup += 1  # duplicate id is a soft signal; keep the first
                continue
            seen.add(sid)

            node = node_by_id.get(sid)
            kind = node.kind if node is not None else str(obj.get("kind", ""))
            surfaces.append(
                SurfaceDescriptor(
                    id=sid,
                    kind=kind,
                    route=node.route if node is not None else "",
                    title=_derive_title(node) if node is not None else "",
                    summary=str(obj.get("summary", "")),
                    contains=_as_str_list(obj.get("contains")),
                    user_actions=_as_str_list(obj.get("user_actions")),
                    key_entities=_as_str_list(obj.get("key_entities")),
                    hosts_chrome_level_features=(
                        str(obj.get("hosts_chrome_level_features", ""))
                        if kind == "shell"
                        else ""
                    ),
                )
            )

        described = DescribedMap(
            repo=map_result.repo,
            commit_sha=map_result.commit_sha,
            surfaces=surfaces,
        )
        _status = "complete"
        return described

    except Exception as exc:
        _error_class = type(exc).__name__
        _status = "error"
        logger.warning("describe: failed; returning degraded map — %r", exc)
        return described

    finally:
        duration_ms = int((time.monotonic() - start_ms) * 1000)
        try:
            from app.llm_telemetry import RunUsage, log_llm_run

            if _usage is None:
                _usage = RunUsage()
            log_llm_run(
                operation="design_agent.describe.complete",
                identifier={"repo": map_result.repo, "sha": map_result.commit_sha},
                usage=_usage,
                duration_ms=duration_ms,
                status=_status,
                model=_MODEL,
                error_class=_error_class,
                iters=1,
                n_surfaces=len(described.surfaces),
                n_dup=_n_dup,
            )
        except Exception:
            logger.debug("describe: telemetry failed (non-fatal)", exc_info=True)


# ─── Completeness / hallucination gate ────────────────────────────────────────


def check_completeness(
    map_result: "MapResult",
    described: DescribedMap,
) -> CompletenessReport:
    """Diff the described ids against the enumerated node ids — pure, no LLM.

    The enumerated node ids are ground truth. A described id absent from that
    set is a hallucination (an invented surface that would poison a downstream
    consumer); an enumerated id never described is a coverage gap (a surface
    that could never be located). Either populates the report and flips ``ok``
    to False. The caller treats ``ok=False`` as a hard error, NOT a degraded
    proceed — EXCEPT on the unknown-stack model-discovery enumeration path,
    where the node set is itself low-confidence and the gate is correspondingly
    advisory. That advisory branch does not apply to the first-class adapters,
    whose node set is deterministic and authoritative.
    """
    ast_ids = {node.id for node in map_result.nodes}
    desc_ids = {s.id.strip() for s in described.surfaces}
    hallucinated = sorted(desc_ids - ast_ids)
    coverage_gap = sorted(ast_ids - desc_ids)
    return CompletenessReport(
        hallucinated_ids=hallucinated,
        coverage_gap_ids=coverage_gap,
        ok=not hallucinated and not coverage_gap,
    )

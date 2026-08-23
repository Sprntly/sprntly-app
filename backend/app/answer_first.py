"""Answer-first streaming for user-facing `_ASK_RESPONSE_SCHEMA` answers.

Behind the `ANSWER_FIRST_STREAMING_ENABLED` flag (default OFF). When off, every
caller keeps its current forced-JSON path byte-for-byte. When on, the four
user-facing answer sites route through this shared helper, which splits one
forced-structured generation into two:

  1. Stream the answer as PLAIN markdown via a dedicated answer-only prompt
     (the ASK formatting rules with the JSON-envelope language stripped, the
     inline `[Source: …]` citation discipline kept). This is the terminal
     streamed call — its deltas are raw text, forwarded to the existing
     token_stream sink, so the user sees prose within ~1-2s instead of waiting
     out the forced-structured buffering gap.
  2. Derive the trailing structured fields (`key_points` / `citations` /
     `confidence` / `unanswered`) with one cheap NON-streamed follow-up call
     over the produced answer. It never touches the display sink.

Why this exists (design spike, 2026-08-22): the latency in a forced-tool answer
is a dead-air gap between the tool_use block opening and its first JSON byte —
the model buffers the whole structured object before streaming any of it. The
same prompt as plain text streams immediately. A prompt/schema ordering nudge
cannot move it; the cost lives in the pre-JSON buffering. See the spike for the
raw measurements.

Two contracts this module owns so no pipeline re-implements them:

* **Answer-only prompt derivation.** `answer_only_system` / `answer_only_user`
  strip the "Return STRICT JSON" / citations-array / JSON-template language from
  the ASK-family prompts. A bare "return markdown" override appended to
  `ASK_SYSTEM` is NOT enough — the model still wraps output in a ```json block;
  the JSON directives have to be removed.

* **One terminal streamed call; fall-through resets the sink.** The streamed
  call is the plain-text answer; the metadata call is non-streamed and never
  publishes display fragments. Any path that streams the answer and can then
  decline and fall through to a SECOND generation into the same sink must call
  `reset_stream(on_delta)` first (re-homed here from the T2 mechanism), so the
  restart frame supersedes the abandoned attempt rather than gluing onto it.

Graceful degrade: if the metadata pass fails, the already-streamed answer ships
with empty `key_points`/`citations` and the caller's default confidence. The
prose already reached the user; metadata is advisory and never breaks the answer.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable

from app.llm import DEFAULT_MODEL

logger = logging.getLogger(__name__)

# Model for the cheap structured metadata pass. Sonnet, not Haiku: `confidence`
# drives the low-confidence downgrade, and Haiku's calibration against the
# forced-JSON baseline (which was Sonnet) is unproven. The pass is small
# (answer text + question in, a few hundred tokens out) and runs AFTER the user
# is already reading, so the cost of the stronger model is marginal and only
# ever delays metadata, never the prose. Overridable for experiments.
METADATA_MODEL = os.environ.get("ANSWER_FIRST_METADATA_MODEL") or DEFAULT_MODEL

# The structured fields to derive after the answer text — the same shape as
# `_ASK_RESPONSE_SCHEMA` MINUS `answer` (the answer is already produced as text).
METADATA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "key_points": {"type": "array", "items": {"type": "string"}},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["source", "evidence"],
            },
        },
        "confidence": {"type": "number"},
        "unanswered": {"type": "string"},
    },
    "required": ["key_points", "citations", "confidence", "unanswered"],
}

_METADATA_SYSTEM = """\
You are Sprntly. Above you have the PM's question and the SOURCE MATERIAL that \
was available to answer it (corpus, connected-source context, and/or documents), \
followed by an ANSWER that was already written from that source material and \
shown to the user. Produce ONLY the structured metadata for that answer. Do NOT \
rewrite, summarize, or extend the answer.

Judge everything against the SAME grounding rule the answer was written under: an \
answer is only as strong as the source material supports — never credit a claim \
the source material does not back, and never speculate.

- `key_points`: 3-6 short bullets capturing the answer's load-bearing takeaways, \
in the answer's own terms. No new claims.
- `citations`: derive from the inline `[Source: …]` attributions present in the \
answer, cross-checked against the source material above. For each distinct \
source, give its `source` label (the text inside `[Source: …]`) and an `evidence` \
phrase — the exact claim or number the source supports. If the answer has no \
inline sources, return an empty array; never invent one.
- `confidence`: a float 0-1 for how well the ANSWER is grounded in and supported \
by the SOURCE MATERIAL above, and how completely it answers the question — the \
same score the answer's own author would assign while writing it, with the \
question and source material in view. A fully grounded answer that the sources \
directly support and that covers the question scores high (~0.85-0.95). Lower it \
when the answer must hedge, reason beyond the sources, or leaves part of the \
question uncovered. If the question is outside Sprntly's product domain and the \
answer is the canned out-of-scope reply, confidence is 1.0. Do NOT lower it for \
prose style, length, or formatting — judge grounding and completeness only.
- `unanswered`: empty string if the answer is complete, else one sentence naming \
what data or scope is still missing.

Return the structured fields only."""


def enabled() -> bool:
    """Read at CALL time so the flag is flippable without a redeploy.

    Default OFF: absent/empty env var => forced-JSON path, byte-identical to
    today. `ANSWER_FIRST_STREAMING_ENABLED=1|true|yes|on` turns it on.
    """
    raw = (os.environ.get("ANSWER_FIRST_STREAMING_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_true(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def report_mapreduce_enabled(report: str) -> bool:
    """Whether the concurrent report map-reduce is active for `report`.

    Three gates, all read at CALL time, all default OFF:
      1. `enabled()` — map-reduce reuses the answer-first streaming/metadata
         contracts, so it never runs unless answer-first streaming is on.
      2. `REPORT_MAPREDUCE_ENABLED` — a global master switch across every report.
      3. `{REPORT}_MAPREDUCE_ENABLED` — the per-report gate (`VOC_MAPREDUCE_ENABLED`,
         `MARKET_INTEL_MAPREDUCE_ENABLED`, …), so a half-validated report can't
         ride a shared flag with a validated one.

    `report` is the lowercase report key ("voc", "market_intel"); the per-report
    env var is its upper-case form. Any gate off ⇒ False ⇒ the caller stays on its
    single-call path, byte-identical to today.
    """
    return (
        enabled()
        and _env_true("REPORT_MAPREDUCE_ENABLED")
        and _env_true(f"{report.upper()}_MAPREDUCE_ENABLED")
    )


def reset_stream(on_delta) -> None:
    """Rewind a streaming answer sink so a re-generation supersedes what a prior
    streamed attempt emitted, rather than gluing onto (or being swallowed by) it.

    Re-homed from the T2 mechanism (`qa_agent._reset_stream`). The Ask worker's
    `on_delta` is an `app.ask_stream.AnswerFieldExtractor`, whose `reset()`
    rewinds parse state AND announces the restart downstream (the token_stream
    replay buffer, the browser accumulator) via `on_restart`. Called when a
    streamed answer-first attempt declines and the turn falls through to a second
    generation into the SAME sink. A `None` sink or any object without `reset()`
    (a plain callback) is a no-op; failures are swallowed — this whole path is
    advisory display and must never break the answer.
    """
    reset = getattr(on_delta, "reset", None)
    if callable(reset):
        try:
            reset()
        except Exception:  # noqa: BLE001 — display only, never break the answer
            logger.debug("answer-first stream reset failed", exc_info=True)


# ── Answer-only prompt derivation ───────────────────────────────────────────

# The JSON-envelope paragraph that `ASK_SYSTEM` ends with (see prompts.py). Its
# removal is what stops the model wrapping the streamed answer in a ```json
# block. Matched by anchor phrases rather than an exact copy so a light reword
# upstream still strips; a drift check below logs if the STRICT-JSON directive
# survives so the failure is visible rather than silent.
_JSON_CLAUSE_RE = re.compile(
    r"Always include a `citations` array.*?JSON itself\.",
    re.DOTALL,
)
_JSON_CLAUSE_REPLACEMENT = (
    "Write your answer as plain GitHub-flavored markdown only — no JSON, no "
    "envelope, and never wrap it in a ```json fence. Keep the inline "
    "`[Source: …]` attribution exactly as described above, placed right where "
    "each claim is made."
)

# The JSON user-template scaffold ("Return JSON of this shape: { … }") that
# `compose_ask_answer` folds into the user turn. The three ASK user templates
# share the same "Return JSON of this shape:" … "\n\nQuestion:" frame. The
# skill / call-digest sites build their user turn WITHOUT this scaffold (they
# rely on the system prompt alone), so this is a no-op there.
_JSON_TEMPLATE_RE = re.compile(
    r"Return JSON of this shape:.*?(\n\nQuestion:)",
    re.DOTALL,
)
_JSON_TEMPLATE_REPLACEMENT = (
    "Answer using markdown only, following the formatting rules in the system "
    "prompt (including inline `[Source: …]` attribution)."
)


def answer_only_system(system: str) -> str:
    """`system` with the JSON-envelope directive replaced by a markdown-only one.

    Everything else — the formatting rules, the chart schema, the inline-source
    discipline, every addendum — is preserved.
    """
    out, n = _JSON_CLAUSE_RE.subn(_JSON_CLAUSE_REPLACEMENT, system)
    if n == 0 or "STRICT JSON" in out:
        # Upstream drift: the anchor moved, or another STRICT-JSON directive
        # remains. Surface it — a system prompt that still demands JSON will make
        # the "fast" first tokens a ```json envelope (the exact failure the spike
        # hit). The answer still generates; it just may not stream cleanly.
        logger.warning(
            "answer-first: JSON-envelope clause not fully stripped from system "
            "prompt (matched=%d, strict_json_remaining=%s); streamed answer may "
            "be wrapped in a JSON fence",
            n, "STRICT JSON" in out,
        )
    return out


def answer_only_user(user: str) -> str:
    """`user` with the "Return JSON of this shape: { … }" scaffold neutralized.

    A no-op for callers whose user turn carries no such scaffold.
    """
    out, _ = _JSON_TEMPLATE_RE.subn(
        _JSON_TEMPLATE_REPLACEMENT + r"\1", user
    )
    return out


def _metadata_user(grounded_user: str, answer_text: str) -> str:
    """The metadata/confidence pass sees the SAME grounding the answer saw.

    `grounded_user` is the answer-only user turn — the question plus any inline
    connected-source / KG context that rode the user turn. The corpus / facts /
    docs that rode the cacheable PREFIX are re-attached by the caller's
    `structured_fn` (a cache hit after the answer call), so the metadata model
    judges `confidence` against the real source material — grounding, not prose
    polish — exactly as the primary model did while generating.
    """
    return (
        grounded_user
        + "\n\n=== ANSWER ALREADY WRITTEN (shown to the user) ===\n"
        + answer_text
        + "\n\nNow produce ONLY the structured metadata for that answer, per the "
        "system instructions above."
    )


def _text_sink(on_delta):
    """A raw-text callback for the streaming answer call.

    In production `on_delta` is an `AnswerFieldExtractor` whose `feed()` decodes
    partial-JSON — feeding it raw markdown would find no `"answer":` key and emit
    nothing. Its `emit_text()` forwards straight to the same downstream
    token_stream sink, so the answer-first text deltas reach the client over the
    EXISTING transport with no JSON decoding. A plain callable sink (the tests,
    and any non-extractor caller) already takes text, so it is used as-is.
    """
    if on_delta is None:
        return None
    emit = getattr(on_delta, "emit_text", None)
    if callable(emit):
        return emit
    return on_delta


# Split-plumbing markers a section pass may prepend despite the directive — the
# observed leak was a "Part 1 of 2" report title. Matched as a phrase
# (numeric or spelled-out N-of-M) plus the "Section A/B of N" label form; a line
# that is ONLY such a marker (optionally wrapped in a heading / bold) is dropped
# whole, an inline occurrence is excised in place. Deliberately narrow: it never
# touches a bare "Section A" or any real report heading, only explicit split
# plumbing.
_SPLIT_MARKER_RE = re.compile(
    r"[\(\[\{]?\s*"
    r"(?:part|section)\s+"
    r"(?:one|two|three|four|[a-d]|\d+)"
    r"\s+of\s+"
    r"(?:one|two|three|four|\d+)"
    r"\s*[\)\]\}]?"
    r"\s*[:\-–—]?",  # also consume a trailing separator ("Part 1 of 2: <title>")
    re.IGNORECASE,
)


def _strip_split_markers(text: str) -> str:
    """Deterministically remove split-plumbing markers ("Part 1 of 2", "Section A
    of 2", a split-label title) so the user-facing merged report never exposes
    that it was generated in halves.

    Conservative and idempotent: only explicit N-of-M split labels are touched,
    never report content. A line that is nothing but the marker (with optional
    `#`/`*` heading wrappers and punctuation) is dropped entirely rather than left
    as an empty heading; an inline marker is cut out and the surrounding text kept,
    with the immediate punctuation/heading residue tidied.
    """
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        if not _SPLIT_MARKER_RE.search(line):
            kept.append(line)
            continue
        cleaned = _SPLIT_MARKER_RE.sub(" ", line)
        # Was the whole line just the marker (maybe inside a heading / bold)?
        residue = cleaned.strip().strip("#*:–—-• ").strip()
        if not residue:
            continue
        # Tidy the excision residue: heading hashes left dangling before a title,
        # and a space stranded before punctuation.
        cleaned = re.sub(r"^(\s*#{1,6})\s*[:\-–—]?\s*", r"\1 ", cleaned)
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        kept.append(re.sub(r"\s{2,}", " ", cleaned).rstrip())
    return "\n".join(kept).strip()


# A machine-readable metadata/structured DUMP fenced as a code block. Some report
# conventions (market-intel's single-pass path) close with a structured metadata
# block; under map-reduce each concurrent section inherited that instinct and
# emitted its OWN block, and the two CONTRADICTED each other (different window
# labels, key names and counts) in the merged report. Reports on the map-reduce
# path are PROSE ONLY — the structured half is derived separately or discarded —
# so a fenced metadata dump is never wanted. `_strip_split_markers` does not catch
# fenced blocks, so this is a separate, deliberately narrow backstop.
#
# Generic (not MI-specific): it lives in `gateway_sections` and benefits every
# report. Safe for VoC and any prose report because it targets ONLY (1) a fence
# whose info-string is `metadata`/`json`/`yaml` — a structured dump, never prose —
# anywhere, and (2) an UNTAGGED fence at the very END of a section whose body reads
# as a key:value / JSON metadata dump. A tagged code example (```python, ```bash)
# and any prose fence are left untouched.
_TAGGED_META_FENCE_RE = re.compile(
    r"(?im)^[ \t]*```[ \t]*(?:metadata|json|yaml)\b[^\n]*\n.*?^[ \t]*```[ \t]*$\n?",
    re.DOTALL,
)
_TRAILING_FENCE_RE = re.compile(
    r"(?is)\n[ \t]*```(?P<info>[^\n]*)\n(?P<body>.*?)\n[ \t]*```[ \t]*\s*\Z",
)
# A body line that looks like structured metadata: a bare brace, or a
# `key:` / `"key":` lead-in (JSON or YAML-style).
_META_KV_LINE_RE = re.compile(r"""(?m)^[ \t]*(?:[{}\[\]]|["']?[\w .-]+["']?[ \t]*:)""")


def _strip_metadata_blocks(text: str) -> str:
    """Remove a stray fenced metadata/structured dump from a prose report section.

    Conservative and idempotent: only structured-dump fences are touched, never
    prose. Tagged `metadata`/`json`/`yaml` fences go anywhere; an untagged fence is
    removed only when it is the LAST thing in the text AND its body reads as a
    key:value / JSON dump (nearly every non-blank line matches a brace or `key:`).
    """
    if not text:
        return text
    trailing_nl = text.endswith("\n")
    out = _TAGGED_META_FENCE_RE.sub("", text)
    m = _TRAILING_FENCE_RE.search(out)
    if m and not m.group("info").strip():
        lines = [ln for ln in m.group("body").splitlines() if ln.strip()]
        kv = sum(1 for ln in lines if _META_KV_LINE_RE.search(ln))
        if lines and kv >= max(2, len(lines) - 1):
            out = out[: m.start()]
    out = out.rstrip()
    return out + "\n" if (trailing_nl and out) else out


def _assemble(answer_text: str, meta: dict, default_confidence) -> dict:
    conf = meta.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        conf = default_confidence
    return {
        "answer": answer_text or "",
        "key_points": meta.get("key_points") or [],
        "citations": meta.get("citations") or [],
        "confidence": conf,
        "unanswered": meta.get("unanswered") or "",
    }


def run(
    *,
    question: str,
    forced_system: str,
    forced_user: str,
    on_delta,
    default_confidence,
    stream_text_fn: Callable[[str, str, object], str],
    structured_fn: Callable[[str, str, dict], dict],
) -> dict:
    """Core answer-first orchestration, transport-agnostic.

    `stream_text_fn(system, user, sink) -> str` runs the streaming plain-text
    answer call and returns the full answer. `structured_fn(system, user, schema)
    -> dict` runs the cheap non-streamed metadata call. The two convenience
    wrappers below (`direct` / `gateway`) bind these to the two transports the
    four sites use; a test can inject fakes.

    Returns the same payload shape as the forced-JSON path
    (`answer` + `key_points` + `citations` + `confidence` + `unanswered`).
    """
    ao_system = answer_only_system(forced_system)
    ao_user = answer_only_user(forced_user)
    text_sink = _text_sink(on_delta)

    answer_text = stream_text_fn(ao_system, ao_user, text_sink)

    meta: dict = {}
    try:
        # The metadata pass judges `confidence` against the SAME grounding the
        # answer saw: `ao_user` carries the question + inline context, and each
        # wrapper's `structured_fn` re-attaches the cacheable corpus/facts/docs
        # prefix. Rating confidence from the finished prose ALONE (no grounding)
        # miscalibrated it badly versus baseline; feeding the source material
        # back in restores the baseline scale.
        meta = structured_fn(
            _METADATA_SYSTEM, _metadata_user(ao_user, answer_text), METADATA_SCHEMA
        ) or {}
    except Exception:  # noqa: BLE001 — metadata is advisory; the prose already shipped
        logger.exception(
            "answer-first metadata pass failed; returning answer with defaults"
        )
        meta = {}

    return _assemble(answer_text, meta, default_confidence)


def direct(
    *,
    question: str,
    forced_system: str,
    forced_user: str,
    cacheable: str | None,
    enterprise_id: str | None,
    on_delta,
    default_confidence,
    max_tokens: int = 12000,
    meta_out: dict | None = None,
) -> dict:
    """Answer-first over the direct `app.llm` transport (compose_ask_answer).

    The corpus / grounding rides `cacheable` (the same cacheable prefix the
    forced-JSON call used), so the streamed answer sees identical source
    material. Both legs bind the tenant's Claude key.
    """
    from app.llm import LONG_REQUEST_TIMEOUT_S, call_json, call_md
    from app.llm_keys import company_llm_key

    def stream_text_fn(system, user, sink):
        with company_llm_key(enterprise_id):
            return call_md(
                system=system,
                user=user,
                user_cacheable_prefix=cacheable,
                max_tokens=max_tokens,
                stream=True,
                on_delta=sink,
                timeout=LONG_REQUEST_TIMEOUT_S,
                meta_out=meta_out,
            )

    def structured_fn(system, user, schema):
        with company_llm_key(enterprise_id):
            return call_json(
                system=system,
                user=user,
                # Re-attach the SAME corpus/facts/docs prefix the answer call
                # used, so the confidence pass judges grounding (cache hit within
                # the TTL — the answer call just warmed it), not prose alone.
                user_cacheable_prefix=cacheable,
                schema=schema,
                model=METADATA_MODEL,
                max_tokens=2000,
            )

    return run(
        question=question,
        forced_system=forced_system,
        forced_user=forced_user,
        on_delta=on_delta,
        default_confidence=default_confidence,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )


def gateway(
    *,
    question: str,
    forced_system: str,
    forced_user: str,
    on_delta,
    default_confidence,
    enterprise_id: str,
    agent: str,
    purpose: str,
    prompt_version: str,
    model: str | None = None,
    skill: str | None = None,
    skill_spec=None,
    user_cacheable_prefix: str | None = None,
    max_tokens: int = 12000,
) -> dict:
    """Answer-first over the attributed `graph.gateway.llm_call` transport
    (the skill-answer, pinned-VoC, and call-digest report sites).

    The streamed answer keeps this site's skill/method grounding and its
    telemetry attribution; the metadata pass is a small, separately-labelled
    (`<purpose>_meta`) structured call on `METADATA_MODEL`.
    """
    from app.graph.gateway import llm_call

    def stream_text_fn(system, user, sink):
        res = llm_call(
            enterprise_id=enterprise_id,
            agent=agent,
            purpose=purpose,
            system=system,
            input=user,
            prompt_version=prompt_version,
            model=model,
            json_schema=None,  # plain-markdown streaming leg (call_md)
            max_tokens=max_tokens,
            user_cacheable_prefix=user_cacheable_prefix,
            skill=skill,
            skill_spec=skill_spec,
            long_output=True,
            on_delta=sink,
        )
        return res.output if isinstance(res.output, str) else str(res.output)

    def structured_fn(system, user, schema):
        res = llm_call(
            enterprise_id=enterprise_id,
            agent=agent,
            purpose=f"{purpose}_meta",
            system=system,
            input=user,
            prompt_version=f"{prompt_version}-meta",
            model=METADATA_MODEL,
            json_schema=schema,
            # Re-attach the answer call's facts/docs prefix (a cache hit) so the
            # confidence pass judges grounding, not prose. The corpus / KG
            # context for these sites rides the user turn (`ao_user`), so it is
            # already carried into `user` by `_metadata_user`. No `skill` here:
            # the method block grounds the ANSWER, not the metadata judgment.
            user_cacheable_prefix=user_cacheable_prefix,
            max_tokens=2000,
        )
        return res.output if isinstance(res.output, dict) else {}

    return run(
        question=question,
        forced_system=forced_system,
        forced_user=forced_user,
        on_delta=on_delta,
        default_confidence=default_confidence,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )


def gateway_sections(
    *,
    question: str,
    forced_system: str,
    forced_user: str,
    sections: list[tuple[str, str]],
    on_delta,
    default_confidence,
    enterprise_id: str,
    agent: str,
    purpose: str,
    prompt_version: str,
    model: str | None = None,
    skill: str | None = None,
    skill_spec=None,
    user_cacheable_prefix: str | None = None,
    max_tokens: int = 8000,
    derive_metadata: bool = True,
) -> dict:
    """Map-reduce variant of `gateway`: split ONE synthesis into N section calls
    that decode CONCURRENTLY over the SAME corpus, then merge deterministically.

    Gated per report by `report_mapreduce_enabled(report)`. Every report's
    wall-clock is
    output-token-decode-bound (~44-55 tok/s, serial within one Anthropic call).
    Decoding two disjoint halves of the report in two concurrent calls cuts
    wall-clock ~2x. Each `sections[i]` is `(label, directive)`; the `directive`
    is appended to the (answer-only) base system so section i writes ONLY its
    part of the report. All sections share one corpus, carried on
    `user_cacheable_prefix`: section 0's call warms the prompt cache and sections
    1..N-1 read it back rather than re-prefilling the ~70k-token corpus.

    Reuse template: competitive_intel's concurrent capture leg — N isolated
    `asyncio.to_thread` tasks fanned out with `asyncio.gather`, merged in FIXED
    input order (section 0, then 1, …), never completion order. `answer()` runs
    on a worker thread with no running loop (dispatched via `asyncio.to_thread`
    from ask_job_runner), so `asyncio.run` here is the same no-running-loop
    pattern competitive_intel's `_run_capture_scan_gather` uses.

    Streaming — concurrent-compute + live-stream-section-0 + ordered block-flush:
    `token_stream` is ONE linear channel with a single accumulating replay buffer
    (interleaving N live streams would garble it). So section 0 streams LIVE to
    the real sink as it decodes (progressive output for the user), while sections
    1..N-1 compute concurrently WITHOUT touching the display sink; once all
    complete, their text is appended to the sink in fixed order via `emit_text`.
    Net effect: the user watches section 0 stream, then the later sections land
    as blocks — no interleave, and wall-clock is still ~the slowest section.

    Preserves the answer-first metadata pass: ONE structured pass runs over the
    MERGED prose after the reduce, exactly as `gateway`'s does over its single
    answer.

    `derive_metadata=False` SKIPS that structured pass and returns the merged
    prose with default/empty metadata. On the report path the metadata pass runs
    AFTER the report is fully on screen but blocks the return (and thus the
    ready-write + terminal `done`), so the user waits ~25-37s on fields nothing
    report-facing consumes (citations are stripped before storage; key_points /
    unanswered are analytics-log only; confidence gates routing/planner/suggestion
    flows, never the answer; the renderer ignores all three). The returned shape
    is IDENTICAL to the metadata-throws graceful-degrade path below, so the
    response contract is unchanged. Default True keeps every other caller as-is.
    """
    import asyncio

    from app.graph.gateway import llm_call

    ao_system = answer_only_system(forced_system)
    ao_user = answer_only_user(forced_user)
    real_sink = _text_sink(on_delta)

    def run_section(idx: int, directive: str, sink) -> tuple[str, str | None]:
        # Isolated task: builds its own section system, runs its own attributed
        # streamed call, returns ONLY its own text plus its stop_reason (for
        # truncation detection below). Touches nothing shared — that isolation is
        # what makes the concurrent dispatch race-free (the corpus prefix is
        # read-only shared input, not mutated state).
        section_system = ao_system + "\n\n" + directive
        res = llm_call(
            enterprise_id=enterprise_id,
            agent=agent,
            purpose=f"{purpose}_s{idx}",
            system=section_system,
            input=ao_user,
            prompt_version=f"{prompt_version}-s{idx}",
            model=model,
            json_schema=None,  # plain-markdown streaming leg (call_md)
            max_tokens=max_tokens,
            user_cacheable_prefix=user_cacheable_prefix,
            skill=skill,
            skill_spec=skill_spec,
            long_output=True,
            on_delta=sink,
        )
        text = res.output if isinstance(res.output, str) else str(res.output)
        return text, getattr(res, "stop_reason", None)

    async def gather_sections() -> list[tuple[str, str | None]]:
        # Section 0 gets the live display sink; the rest run dark and are flushed
        # in order after all complete, so the one linear channel never interleaves.
        return await asyncio.gather(*[
            asyncio.to_thread(run_section, idx, directive,
                              real_sink if idx == 0 else None)
            for idx, (_label, directive) in enumerate(sections)
        ])

    results = asyncio.run(gather_sections())

    # Generic truncation detection (every report). A section that stopped on
    # `max_tokens` is INCOMPLETE — it ran out of budget mid-generation and is
    # missing its tail (e.g. MI Section B losing its Bottom Line). This must never
    # ship silently: log a loud WARNING naming the section + purpose, and surface a
    # marker on the returned payload (`_truncated` / `_truncated_purposes`) so the
    # caller can see it too. Fail-loud / auto-retry-at-a-higher-cap is a follow-up;
    # SILENT truncation ends here.
    truncated_purposes: list[str] = []
    for idx, (_text, stop_reason) in enumerate(results):
        if stop_reason == "max_tokens":
            section_purpose = f"{purpose}_s{idx}"
            truncated_purposes.append(section_purpose)
            logger.warning(
                "answer-first map-reduce: section %d (%s, purpose=%s) hit the "
                "max_tokens cap and is TRUNCATED — the merged report is "
                "incomplete (missing this section's tail). Raise its per-section "
                "max_tokens (currently %s).",
                idx, sections[idx][0], section_purpose, max_tokens,
            )

    # Deterministic post-strip of section plumbing a section may have emitted:
    # (1) a split-plumbing marker (a "Part 1 of 2" title the model can leak) and
    # (2) a stray fenced metadata/structured dump (two sections can each emit
    # contradicting ```metadata blocks). Applied BEFORE the flush and the
    # merge, so both the block-flushed later sections and the stored merged payload
    # are guaranteed clean regardless of what the model wrote. (Section 0 already
    # streamed its raw text live; the tightened directives keep its live view
    # clean, and this guarantees the persisted report.)
    texts = [
        _strip_metadata_blocks(_strip_split_markers(text))
        for (text, _stop_reason) in results
    ]

    # Ordered block-flush of sections 1..N-1 (section 0 already streamed live).
    emit = getattr(on_delta, "emit_text", None)
    if callable(emit):
        for text in texts[1:]:
            if text:
                emit("\n\n" + text)

    merged = "\n\n".join(t for t in texts if t)

    def _finalize(meta: dict) -> dict:
        out = _assemble(merged, meta, default_confidence)
        if truncated_purposes:
            # Marker the caller can see (the WARNING above is the non-silent
            # guarantee; this lets a caller/telemetry act on it too).
            out["_truncated"] = True
            out["_truncated_purposes"] = truncated_purposes
        return out

    if not derive_metadata:
        # Report path: the prose IS the deliverable and is already fully on
        # screen. Skip the blocking structured pass to recover its ~25-37s from
        # the tail — same return shape as the metadata-throws degrade below.
        return _finalize({})

    meta: dict = {}
    try:
        res = llm_call(
            enterprise_id=enterprise_id,
            agent=agent,
            purpose=f"{purpose}_meta",
            system=_METADATA_SYSTEM,
            input=_metadata_user(ao_user, merged),
            prompt_version=f"{prompt_version}-meta",
            model=METADATA_MODEL,
            json_schema=METADATA_SCHEMA,
            # Cache hit on the corpus the section calls just warmed, so the
            # confidence pass judges grounding, not prose alone (see `gateway`).
            user_cacheable_prefix=user_cacheable_prefix,
            max_tokens=2000,
        )
        meta = res.output if isinstance(res.output, dict) else {}
    except Exception:  # noqa: BLE001 — metadata is advisory; the prose already shipped
        logger.exception(
            "answer-first map-reduce metadata pass failed; returning merged "
            "answer with defaults"
        )
        meta = {}

    return _finalize(meta)

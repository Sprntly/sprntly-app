// Friendly progress copy for the chat wait — the egress contract for the ask
// pipeline's SSE `{"kind":"phase"}` events.
//
// This is the chat-side mirror of `backend/app/design_agent/progress.py`
// (`friendly_step`): a PURE, side-effect-free translator that maps whatever the
// backend emitted for a pipeline leg to plain-language, goal-oriented copy the
// user sees. The backend already publishes phase labels
// (`qa_agent.emit_phase` → `token_stream.phase_sink`), but those strings are
// written for engineers and can interpolate raw detail — a competitor NAME
// ("Researched Acme…") or a COUNT ("Writing the review from 47 sourced
// observations…"). Neither may reach a person.
//
// EGRESS CONTRACT (same rule as progress.py): no tool name, mechanism, count,
// file path, extension, or error text ever reaches the returned string. This is
// guaranteed BY CONSTRUCTION — the function only ever returns one of its own
// hardcoded constants and NEVER echoes any part of the input, so a raw/leaky
// backend label cannot pass through. Anything unrecognized FAILS SAFE to the
// generic line. Matching is on the leading intent of the label, not the detail.

/** The claim-free fallback. Used for any label we don't recognize, and as the
 *  calm long-wait line — describes the goal, asserts no specific step. */
export const FRIENDLY_PHASE_GENERIC = "Working on your answer…"

/** Build-time flag, OFF by default — inlined at `next build` like every other
 *  NEXT_PUBLIC_* gate. Single source shared by the wait component (display gate)
 *  and the ask runner (whether to subscribe to phase events at all), so flag-off
 *  is byte-identical AND does no extra work. Accepts "1" or "true". */
export const GROUNDED_PROGRESS_ENABLED =
  process.env.NEXT_PUBLIC_GROUNDED_PROGRESS_ENABLED === "1" ||
  process.env.NEXT_PUBLIC_GROUNDED_PROGRESS_ENABLED === "true"

/**
 * Translate a raw backend phase label to user-facing, goal-oriented copy.
 *
 * Returns a short sentence (with trailing ellipsis, matching the wait UI's
 * voice) that NEVER contains any substring of the input — only our own
 * constants. Unknown/empty input → the generic fallback. Order matters: the
 * more specific "reading/reviewing" report leg is checked before the broader
 * "review" writing leg so it doesn't get swallowed.
 */
export function friendlyPhase(raw: string | null | undefined): string {
  if (!raw) return FRIENDLY_PHASE_GENERIC
  const s = raw.trim().toLowerCase()

  // ── Ask (main/project chat) ────────────────────────────────────────────────
  // qa_agent.py:982 "Searching your connected sources…"
  if (s.startsWith("searching your connected sources") || s.includes("connected sources")) {
    return "Looking through your connected sources…"
  }
  // qa_agent.py:1022 "Writing the answer…"
  if (s.startsWith("writing the answer") || s.startsWith("writing your answer")) {
    return "Writing your answer…"
  }
  // ask_runner.compose_ask_answer boundary 2 "Putting your answer together…" —
  // covers the ~24s prefill window on the common direct-answer path. Already
  // goal-oriented copy; mapped explicitly so it never falls to the generic line.
  if (s.startsWith("putting your answer together")) {
    return "Putting your answer together…"
  }

  // ── Competitive-intelligence report (the long / report path) ───────────────
  // competitive_intel.py:1454 "Reading the last competitive review…"
  if (s.startsWith("reading the last") || s.includes("competitive review")) {
    return "Reviewing your latest report…"
  }
  // competitive_intel.py:1337 "Researched {name}…" — strip the competitor name.
  if (s.startsWith("researched") || s.includes("competitor")) {
    return "Researching your competitors…"
  }
  // competitive_intel.py:1561 "Writing the review from {N} sourced observations…"
  // — strip the count.
  if (s.startsWith("writing the review") || s.includes("the review")) {
    return "Writing your report…"
  }

  // ── Reports — shared vocabulary (backend/app/report_phases.ReportPhase) ─────
  // ONE mapping set that covers every report path (voice-of-customer, market-
  // intel, public-feedback, company-research). The backend emits these generic,
  // already-user-safe raw labels via `emit_report_phase`; each maps to its own
  // hardcoded constant here so the count/detail-stripping guarantee still holds
  // by construction. The company-research stage set is FIXED (non-tenant), so
  // each stage surfaces as its own checklist line.
  //
  // Company-research per-stage lines are checked BEFORE the generic "researching"
  // gathering fallthrough so a stage keeps its specific copy.
  if (s.startsWith("researching products")) {
    return "Researching products & features…"
  }
  if (s.startsWith("researching positioning")) {
    return "Researching positioning…"
  }
  if (s.startsWith("researching pricing")) {
    return "Researching pricing…"
  }
  if (s.startsWith("researching market")) {
    return "Researching market & recent news…"
  }
  // ReportPhase.GATHERING "Gathering the latest information…"
  if (s.startsWith("gathering")) {
    return "Gathering the latest information…"
  }
  // ReportPhase.ANALYZING "Analyzing the findings…" (reserved for the
  // per-section map-reduce fast-follow; mapped now so the vocabulary is whole).
  if (s.startsWith("analyzing") || s.startsWith("analysing")) {
    return "Analyzing the findings…"
  }
  // ReportPhase.WRITING "Writing your report…"
  if (s.startsWith("writing your report") || s.startsWith("writing the report")) {
    return "Writing your report…"
  }

  // Fail safe: anything we don't recognize gets the claim-free generic line,
  // never the raw activity.
  return FRIENDLY_PHASE_GENERIC
}

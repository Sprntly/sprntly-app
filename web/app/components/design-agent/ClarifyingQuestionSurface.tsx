"use client"

/**
 * Clarifying-question answer surface for the SIGNED-IN app.
 *
 * Closes the clarifying-question UI gap: the backend already ships this flow
 * (when the agent calls the `clarifying_question` sentinel the runner pauses,
 * persists `pending_question` on the prototype row, and the prototype enters
 * the `awaiting_clarification` signal — `pending_question IS NOT NULL`, `status`
 * stays `ready`). Nothing surfaced that question to the signed-in user, so a
 * paused prototype sat invisibly stuck. This component reads
 * `prototype.pending_question`, renders the agent's question, captures the
 * user's answer, and routes it as a NEW iterate (the answer IS the iterate
 * prompt — there is no auto-resume; the runner is stateless between calls, per
 * the answer-as-new-iterate decision).
 *
 * Reuse, not addition (KEY constraints):
 *   - NO new backend route, NO new api method. The question arrives via the
 *     EXISTING `GET /v1/design-agent/{id}` poll (the backend column on the
 *     prototype row). The answer routes through the EXISTING
 *     `designAgentApi.iterate` (`{prompt, mode:'execute'}`). The backend clears
 *     `pending_question` on the new iterate kickoff (`clear_pending_question`).
 *
 * DELIBERATE CHOICE (documented): NO cost-estimate modal in front
 * of the answer. Unlike `IterateComposer`'s re-prompt/Apply flows, the user here
 * is RESPONDING to an agent-initiated pause, not initiating a fresh run — gating
 * an answer behind a cost modal is poor pause-resume UX. If we want
 * the estimate gate here too it is a one-line addition (mirror IterateComposer's
 * `runEstimate → modal → runIterate`); flagged, deferred.
 *
 * Gating:
 *   - `pending_question` null/absent → render nothing (`return null`).
 *   - `is_complete` (locked) → render nothing even if a question is set; a
 *     locked prototype cannot iterate (Resume first). Mirrors IterateComposer.
 *   - mounts ONLY in `DesignAgentLauncher` (the authed surface), NEVER on the
 *     public `/p/<token>` route (external viewers cannot answer/iterate).
 *
 * The surface does NOT self-poll or render its own progress: on submit it
 * clears its LOCAL copy optimistically and hands off to the launcher's existing
 * status/poll surface; the backend clears the real `pending_question` on the
 * iterate kickoff. Testability split mirrors IterateComposer / CompletionBar:
 * pure markup in `ClarifyingQuestionSurfaceView` (SSR-renderable in node-env
 * vitest), I/O in exported dependency-injected helpers (`composeAnswerPrompt`,
 * `runAnswer`, `shouldRenderSurface`), the container wires React state to them.
 * No CSS added to the hot `globals.css`; component-scoped class strings only.
 */

import { useState } from "react"
import {
  designAgentApi,
  type IterateResponse,
  type PendingQuestion,
  type PrototypeRecord,
} from "../../lib/api"
import { IconArrowRight } from "../shared/app-icons"

// ---- pure helpers (dependency-injected, SSR-free) ---------------------------

/** The iterate call signature (owned elsewhere; reused here — no new method). */
export type IterateFn = (
  prototypeId: number,
  body: {
    prompt: string
    applied_comment_id?: number | null
    mode?: "plan" | "execute"
  },
) => Promise<IterateResponse>

/** Compose the iterate prompt from the agent's original question + the user's
 *  answer, so the (stateless) agent knows what it asked when the new iterate
 *  runs. The exact phrasing is a nicety, NOT load-bearing — documented.
 *  The original question is prepended as context. */
export function composeAnswerPrompt(question: string, answer: string): string {
  return `You asked: "${question}". My answer: ${answer.trim()}. Continue.`
}

/** Should the surface render at all? Only when a question is pending AND the
 *  prototype is not locked. Pure → unit-testable without a DOM. */
export function shouldRenderSurface(prototype: PrototypeRecord): boolean {
  return prototype.pending_question != null && !(prototype.is_complete ?? false)
}

/** Route the answer as a NEW iterate (the answer IS the iterate prompt). The
 *  ONLY path that calls `iterate`. Pins `mode:'execute'`. NO cost-estimate gate
 *  in front of it (deliberate choice — see file header). Returns the
 *  IterateResponse so the caller can hand off / surface queue position. */
export async function runAnswer(
  iterate: IterateFn,
  args: { prototypeId: number; question: string; answer: string },
): Promise<IterateResponse> {
  return iterate(args.prototypeId, {
    prompt: composeAnswerPrompt(args.question, args.answer),
    mode: "execute",
  })
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

// ---- pure view --------------------------------------------------------------

export type ClarifyingQuestionSurfaceViewProps = {
  question: string
  context?: string | null
  /** When non-empty → render each as a button; otherwise → free-text input. */
  choices?: string[] | null
  /** Current free-text answer (ignored when `choices` is non-empty). */
  answer: string
  busy?: boolean
  error?: string | null
  onAnswerChange?: (value: string) => void
  /** Button-choice answer (choices mode). */
  onChoose?: (choice: string) => void
  /** Free-text Submit (no-choices mode). */
  onSubmit?: () => void
}

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. Renders the agent's question, an optional context line, and EITHER
 *  the choice buttons (when present) OR a free-text answer input + Submit. The
 *  null/locked gating lives in the container (this view is only mounted when
 *  there IS a question to answer), but the answer affordance is ALWAYS present
 *  so the prototype is never UI-dead-ended. */
export function ClarifyingQuestionSurfaceView({
  question,
  context = null,
  choices = null,
  answer,
  busy = false,
  error = null,
  onAnswerChange,
  onChoose,
  onSubmit,
}: ClarifyingQuestionSurfaceViewProps) {
  const hasChoices = !!choices && choices.length > 0
  return (
    <div
      className="clarifying-question-surface"
      data-testid="clarifying-question-surface"
      role="region"
      aria-label="The Design Agent has a question"
    >
      <p
        className="clarifying-question-text"
        data-testid="clarifying-question-text"
      >
        {question}
      </p>
      {context && (
        <p
          className="clarifying-question-context"
          data-testid="clarifying-question-context"
        >
          {context}
        </p>
      )}

      {hasChoices ? (
        <div
          className="clarifying-question-choices"
          data-testid="clarifying-question-choices"
        >
          {choices!.map((choice, i) => (
            <button
              key={`${i}-${choice}`}
              type="button"
              className="clarifying-question-choice"
              data-testid="clarifying-question-choice"
              disabled={busy}
              onClick={() => onChoose?.(choice)}
            >
              {choice.replace(/\s*\([^)]{1,50}\)\s*$/, '').trim() || choice}
            </button>
          ))}
        </div>
      ) : (
        <form
          className="clarifying-question-form"
          data-testid="clarifying-question-form"
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit?.()
          }}
        >
          <textarea
            className="clarifying-question-input"
            data-testid="clarifying-question-input"
            value={answer}
            placeholder="Answer the Design Agent…"
            onChange={(e) => onAnswerChange?.(e.target.value)}
          />
          <div className="clarifying-question-actions">
            <button
              type="submit"
              className="btn btn-accent"
              data-testid="clarifying-question-submit"
              disabled={busy || !answer.trim()}
            >
              Submit
            </button>
          </div>
        </form>
      )}

      {error && (
        <p
          className="clarifying-question-error error"
          role="alert"
          data-testid="clarifying-question-error"
        >
          {error}
        </p>
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

export type ClarifyingQuestionSurfaceProps = {
  /** The prototype row (from the launcher's poll state). The surface reads
   *  `pending_question` + `is_complete` off it. */
  prototype: PrototypeRecord
  /** Injected for tests; defaults to the real iterate (no new method). */
  iterate?: IterateFn
  /** Optional hook so the launcher can refresh/clear after a successful answer.
   *  The surface already clears its LOCAL copy optimistically — this is purely a
   *  notification; the launcher's existing poll is the source of truth. */
  onAnswered?: () => void
}

/**
 * Public component. Reads `pending_question` off the prototype, captures an
 * answer (choice button or free text), and routes it as a NEW iterate via the
 * reused `designAgentApi.iterate`. On success it optimistically clears its LOCAL
 * copy (renders null) and hands off to the launcher's existing status/poll
 * surface (no self-poll, no own progress UI).
 */
export function ClarifyingQuestionSurface({
  prototype,
  iterate = designAgentApi.iterate,
  onAnswered,
}: ClarifyingQuestionSurfaceProps) {
  const [answer, setAnswer] = useState("")
  // Optimistic local clear: once an answer is submitted, the surface renders
  // nothing and hands off to the launcher poll (the backend clears the real
  // pending_question on the iterate kickoff — clear_pending_question).
  const [answered, setAnswered] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // All hooks are declared above any conditional return (rules-of-hooks).
  if (answered) return null
  if (!shouldRenderSurface(prototype)) return null

  // Safe per the guard above: shouldRenderSurface ⇒ pending_question non-null.
  const pq = prototype.pending_question as PendingQuestion

  async function submit(rawAnswer: string) {
    const trimmed = rawAnswer.trim()
    if (!trimmed || busy) return
    setBusy(true)
    setError(null)
    try {
      await runAnswer(iterate, {
        prototypeId: prototype.id,
        question: pq.question,
        answer: trimmed,
      })
      setAnswered(true) // optimistic local clear → surface unmounts itself
      onAnswered?.()
    } catch (e) {
      setError(toMessage(e, "Could not submit your answer"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ClarifyingQuestionSurfaceView
      question={pq.question}
      context={pq.context ?? null}
      choices={pq.choices ?? null}
      answer={answer}
      busy={busy}
      error={error}
      onAnswerChange={setAnswer}
      onChoose={(choice) => submit(choice)}
      onSubmit={() => submit(answer)}
    />
  )
}

// ---- pre-gen locate-confirm view -------------------------------------------

/** One ranked screen candidate for the pre-gen screen-selection gate.
 *  The wiring container maps LocateResponse.ranked → LocateConfirmCandidate[],
 *  marking is_top on the first (highest-confidence) entry. */
export type LocateConfirmCandidate = {
  /** Stable node id. Unique where a route is not — the app shell (empty route)
   *  and an in-page section (shared/empty route) both carry a distinct id. It
   *  is the resolution key the picker forwards on choose. */
  id: string
  route: string
  entry_component: string
  component_count: number
  /** Plain-language, one-sentence description of what this screen is and what a
   *  user does on it — the PM-facing narrative the picker renders as the primary
   *  supporting line. May be empty (older/degraded locate results). */
  rationale: string
  is_top: boolean
}

export type LocateConfirmViewProps = {
  /** Defaults to "Which screen does this change affect?". */
  question?: string
  candidates: LocateConfirmCandidate[]
  busy?: boolean
  error?: string | null
  /** Fires the chosen candidate's exact route string AND its stable id (the
   *  resolution key — route is not unique and is empty for non-route hosts). */
  onChoose: (route: string, id: string) => void
  /** When provided, renders a "Search for another screen…" affordance.
   *  The caller decides what it opens; omit this prop to hide the button. */
  onSearchOther?: () => void
}

/** Derive a human-readable label from an entry_component string.
 *  Strips a trailing Screen/Page suffix then splits camelCase words with spaces
 *  (e.g. TeamScreen → "Team", BriefingPage → "Briefing",
 *  ManualEditOverlay → "Manual Edit Overlay"). Falls back to the raw route
 *  when the result is empty (handles blank or non-standard component names). */
function deriveScreenLabel(entryComponent: string, route: string): string {
  const ec = entryComponent ?? ""
  const rt = route ?? ""
  const stripped = ec.replace(/(Screen|Page)$/, "")
  const label = stripped.replace(/([A-Z])/g, " $1").trim()
  return label || rt
}

/** Split the candidate list into a single lead (the one currently promoted into
 *  the Suggested slot) and the remaining alternatives in original order. Pure →
 *  unit-testable without a DOM. The lead is resolved by `promotedId`; when that
 *  id matches no current candidate (e.g. the candidates prop changed under the
 *  picker) it falls back to the first `is_top` candidate, then to index 0.
 *  Tolerates a candidate whose id is null/missing — never indexes an empty
 *  array unguarded; returns `lead: null` only when the list is empty. */
export function selectLeadAndAlternatives(
  candidates: LocateConfirmCandidate[],
  promotedId: string,
): { lead: LocateConfirmCandidate | null; alternatives: LocateConfirmCandidate[] } {
  if (!candidates || candidates.length === 0) {
    return { lead: null, alternatives: [] }
  }
  const promoted = candidates.find((c) => (c?.id ?? "") === promotedId)
  const fallback = candidates.find((c) => c?.is_top) ?? candidates[0]!
  const lead = promoted ?? fallback
  const alternatives = candidates.filter((c) => c !== lead)
  return { lead, alternatives }
}

/** The id the picker promotes into the Suggested slot on first render: the
 *  highest-confidence (`is_top`) candidate, else the first candidate. */
function defaultPromotedId(candidates: LocateConfirmCandidate[]): string {
  if (!candidates || candidates.length === 0) return ""
  const top = candidates.find((c) => c?.is_top) ?? candidates[0]!
  return top?.id ?? ""
}

/** Presentational view for the pre-gen screen-selection gate, "Suggested +
 *  alternatives" layout. Leads with the suggested screen (full description +
 *  primary action); the remaining candidates render as compact clickable rows
 *  that PROMOTE into the Suggested slot when clicked (local state only — the
 *  promote click never confirms; confirmation is always the explicit "Use this
 *  screen" button so the user commits from the full description).
 *
 *  Scoped class names: a `locate-*` family in the component-scoped
 *  design-agent.css plus the existing global `btn`/`btn-accent` for the primary
 *  action — no new global class, no globals.css change. Every candidate field
 *  read is null-safe so a partial/degraded candidate never throws at render. */
export function LocateConfirmView({
  question = "Which screen does this change affect?",
  candidates,
  busy = false,
  error = null,
  onChoose,
  onSearchOther,
}: LocateConfirmViewProps) {
  const [promotedId, setPromotedId] = useState<string>(() =>
    defaultPromotedId(candidates),
  )

  const { lead, alternatives } = selectLeadAndAlternatives(candidates, promotedId)

  return (
    <div
      className="clarifying-question-surface"
      data-testid="locate-confirm-surface"
      role="region"
      aria-label="Select the screen this change affects"
    >
      <p
        className="clarifying-question-text"
        data-testid="locate-confirm-question"
      >
        {question}
      </p>

      {lead && (
        <div className="locate-lead" data-testid="locate-lead">
          <span className="locate-badge" data-testid="locate-suggested-badge">
            Suggested
          </span>
          <div className="locate-lead-name" data-testid="locate-lead-name">
            {deriveScreenLabel(lead.entry_component, lead.route)}
          </div>
          {lead.rationale && (
            <div
              className="locate-lead-desc"
              data-testid="locate-confirm-narrative"
            >
              {lead.rationale}
            </div>
          )}
          <div
            className="locate-route-info"
            data-testid="locate-confirm-route-info"
          >
            {lead.route ?? ""} · {lead.component_count ?? 0} components
          </div>
          <button
            type="button"
            className="btn btn-accent"
            data-testid="locate-confirm-use"
            disabled={busy}
            onClick={() => onChoose(lead.route ?? "", lead.id ?? "")}
          >
            Use this screen
          </button>
        </div>
      )}

      {alternatives.length > 0 && (
        <>
          <div className="locate-others-label" data-testid="locate-others-label">
            Other options
          </div>
          <div className="locate-alt-list">
            {alternatives.map((c) => (
              <button
                key={c.id}
                type="button"
                className="locate-alt-row"
                data-testid="locate-alt-row"
                disabled={busy}
                onClick={() => setPromotedId(c.id ?? "")}
              >
                <span className="locate-alt-name" data-testid="locate-alt-name">
                  {deriveScreenLabel(c.entry_component, c.route)}
                </span>
                {c.rationale && (
                  <span
                    className="locate-alt-desc"
                    data-testid="locate-alt-desc"
                  >
                    {c.rationale}
                  </span>
                )}
                <span className="locate-alt-chev" aria-hidden>
                  <IconArrowRight />
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {onSearchOther !== undefined && (
        <button
          type="button"
          className="locate-search-other"
          data-testid="locate-confirm-search-other"
          disabled={busy}
          onClick={onSearchOther}
        >
          Search for another screen…
        </button>
      )}
      {error && (
        <p
          className="clarifying-question-error error"
          role="alert"
          data-testid="locate-confirm-error"
        >
          {error}
        </p>
      )}
    </div>
  )
}

"use client"

/**
 * P3-14 — F9/F10 iterate trigger surface for the SIGNED-IN app.
 *
 * Closes the Stage-2 frontend gap: the iterate backend (P3-05 `POST /iterate`),
 * the cost estimate (P3-11), and the comments panel (P3-03) all exist, but no UI
 * let an authenticated user press Re-prompt (F9) or Apply (F10). This component
 * is that surface. It mounts ONLY in `DesignAgentLauncher` (the authed PRD Design
 * surface) — never on the public `/p/<token>` route (F10 is internal-only).
 *
 * Two entry modes, one component:
 *   - Re-prompt (F9): an always-available free-text input; empty body; Submit.
 *   - Apply (F10): opened from a comment's Apply action; pre-filled with the
 *     comment `body` (editable) and carrying `applied_comment_id = comment.id`.
 *     Per spec the pre-fill is "a task handed to the Design Agent"; the comment
 *     body VERBATIM is the accepted P3 pre-fill (the agent-style paraphrase in
 *     the prototype is a nicety, not required).
 *
 * AD14 gate (load-bearing): Submit NEVER calls `designAgentApi.iterate` directly.
 * Submit fetches the cost estimate and opens the modal; `iterate` is reached ONLY
 * from the modal's Continue handler. The flow:
 *     Submit  → runEstimate(estimateIterate) → open modal
 *     Continue → runIterate(iterate, …)       → kicks off the bg run
 *     Cancel  → close, no API call
 *
 * Reconciliation with P3-11 (recorded per the ticket's "API-method ownership" +
 * "resolve overlap at implementation"): P3-11's `CostEstimateModal` CONTAINER
 * fetches the estimate in a `useEffect` on mount. The repo's vitest runs in a
 * `node` env and `renderToStaticMarkup` does NOT run effects, so a container that
 * estimates-on-mount cannot make AC3's "Submit → estimateIterate" spy-assertable.
 * This composer therefore drives the estimate on Submit (exactly the ticket's
 * Implementation-Notes pseudocode) and REUSES P3-11's pure `CostEstimateModalView`
 * for the modal markup — it does NOT fork the modal. `designAgentApi.iterate` is
 * owned here (P3-14); P3-11 ships `estimateIterate` only.
 *
 * The composer does NOT poll or render generation progress (AC5): on a
 * `'generating'` response it resets and hands off to the launcher's existing
 * status/poll surface. The only status it shows is a single read-only
 * queue-position line (P3-06) when `queue_position > 0`.
 *
 * Testability split mirrors CostEstimateModal / CommentsPanel: the pure markup
 * lives in `IterateComposerView` (SSR-renderable), the I/O lives in exported pure
 * dependency-injected helpers (`runEstimate`, `runIterate`), and the container
 * wires React state to those units. Per BUILD.md §6 this adds NO CSS to the hot
 * `globals.css`; component-scoped class strings only.
 */

import { useEffect, useState } from "react"
import {
  designAgentApi,
  type CommentRecord,
  type IterateCostEstimate,
  type IterateResponse,
} from "../../lib/api"
import { CostEstimateModalView } from "./CostEstimateModal"

/** F14 locked-state affordance (spec §4 Stage 3): a complete prototype cannot be
 *  iterated until Resume. */
export const LOCKED_AFFORDANCE = "Resume iteration to make changes"

// ---- pure helpers (dependency-injected, SSR-free) ---------------------------

/** Derive the composer's initial editable state from an Apply target. F10:
 *  Apply pre-fills the prompt with the comment body (verbatim) and carries
 *  `applied_comment_id`. F9 re-prompt (no target) → empty body, null id. */
export function initialComposerState(
  applyTarget: CommentRecord | null | undefined,
): { prompt: string; appliedCommentId: number | null } {
  if (applyTarget) {
    return { prompt: applyTarget.body, appliedCommentId: applyTarget.id }
  }
  return { prompt: "", appliedCommentId: null }
}

/** The estimateIterate call signature (owned by P3-11). */
export type EstimateFn = (
  prototypeId: number,
  body: { prompt: string; applied_comment_id?: number | null },
) => Promise<IterateCostEstimate>

/** The iterate call signature (owned here, P3-14). */
export type IterateFn = (
  prototypeId: number,
  body: { prompt: string; applied_comment_id?: number | null; mode?: "plan" | "execute" },
) => Promise<IterateResponse>

export type ComposerSubmitArgs = {
  prototypeId: number
  prompt: string
  appliedCommentId: number | null
}

/** AD14 Submit step: fetch the cost estimate. Makes NO iterate call — the modal
 *  it opens reaches `iterate` only via Continue (`runIterate`). Returns the
 *  estimate so the caller can render the modal. */
export async function runEstimate(
  estimateIterate: EstimateFn,
  args: ComposerSubmitArgs,
): Promise<IterateCostEstimate> {
  return estimateIterate(args.prototypeId, {
    prompt: args.prompt,
    applied_comment_id: args.appliedCommentId,
  })
}

/** Modal Continue handler (AC3/AC4/B4): the ONLY path that calls `iterate`. Pins
 *  `mode:'execute'` at the call site (the method also defaults it). Returns the
 *  IterateResponse so the caller can surface the queue position. */
export async function runIterate(
  iterate: IterateFn,
  args: ComposerSubmitArgs,
): Promise<IterateResponse> {
  return iterate(args.prototypeId, {
    prompt: args.prompt,
    applied_comment_id: args.appliedCommentId,
    mode: "execute",
  })
}

/** Queue-position indicator text (P3-06, minor). `> 0` → "Queued — position N";
 *  `0`/absent → null (render nothing). A single read-only status line, NOT a
 *  polling surface. */
export function queueIndicator(
  resp: { queue_position?: number } | null | undefined,
): string | null {
  const pos = resp?.queue_position ?? 0
  return pos > 0 ? `Queued — position ${pos}` : null
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

// ---- pure view --------------------------------------------------------------

export type IterateComposerViewProps = {
  prompt: string
  isComplete: boolean
  mode: "reprompt" | "apply"
  showModal: boolean
  busy?: boolean
  error?: string | null
  queueLine?: string | null
  estimate?: IterateCostEstimate | null
  estimateLoading?: boolean
  estimateError?: string | null
  onPromptChange?: (value: string) => void
  onSubmit?: () => void
  onContinue?: () => void
  onCancel?: () => void
}

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. When locked (F14) it renders only the Resume affordance: no input,
 *  no Submit → Submit cannot fire (AC6). Otherwise it renders the prompt input,
 *  Submit, an optional queue line, and (when open) the reused CostEstimateModal
 *  view wired to Continue / Cancel. */
export function IterateComposerView({
  prompt,
  isComplete,
  mode,
  showModal,
  busy = false,
  error = null,
  queueLine = null,
  estimate = null,
  estimateLoading = false,
  estimateError = null,
  onPromptChange,
  onSubmit,
  onContinue,
  onCancel,
}: IterateComposerViewProps) {
  if (isComplete) {
    return (
      <div
        className="iterate-composer iterate-composer--locked"
        data-testid="iterate-composer-locked"
      >
        <p className="iterate-composer-locked-note">{LOCKED_AFFORDANCE}</p>
      </div>
    )
  }

  return (
    <div className="iterate-composer" data-testid="iterate-composer" data-mode={mode}>
      <form
        className="iterate-composer-form"
        data-testid="iterate-composer-form"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit?.()
        }}
      >
        <textarea
          className="iterate-composer-input"
          data-testid="iterate-composer-input"
          value={prompt}
          placeholder={
            mode === "apply"
              ? "Edit this task for the Design Agent…"
              : "Describe a change for the Design Agent to make…"
          }
          onChange={(e) => onPromptChange?.(e.target.value)}
        />
        <div className="iterate-composer-actions">
          <button
            type="submit"
            className="btn btn-accent"
            data-testid="iterate-composer-submit"
            disabled={busy || !prompt.trim()}
          >
            {mode === "apply" ? "Apply" : "Submit"}
          </button>
        </div>
      </form>

      {queueLine && (
        <p
          className="iterate-composer-queue"
          data-testid="iterate-composer-queue"
          role="status"
        >
          {queueLine}
        </p>
      )}

      {error && (
        <p
          className="iterate-composer-error error"
          role="alert"
          data-testid="iterate-composer-error"
        >
          {error}
        </p>
      )}

      {showModal && (
        <CostEstimateModalView
          estimate={estimate}
          loading={estimateLoading}
          errorMsg={estimateError}
          busy={busy}
          onContinue={() => onContinue?.()}
          onCancel={() => onCancel?.()}
        />
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

export type IterateComposerProps = {
  prototypeId: number
  /** F14: when the prototype is complete (locked) the composer disables itself
   *  with the Resume affordance and Submit cannot fire. */
  isComplete?: boolean
  /** F10: the comment selected for Apply (lifted to DesignAgentLauncher so
   *  CommentsPanel's Apply action can set it). Null → F9 re-prompt mode. */
  applyTarget?: CommentRecord | null
  /** Called after a successful iterate / after the Apply target is consumed, so
   *  the launcher can clear its lifted `applyTarget`. */
  onClearApply?: () => void
  /** P6-05 (#5): fired after a successful `runIterate` kickoff so the launcher
   *  can re-poll and refresh its `result` (the preview iframe + View href).
   *  Optional/defaulted so existing callers keep type-checking; the AD14
   *  estimate→Continue→iterate flow is otherwise unchanged. */
  onIterated?: () => void
}

/**
 * Public component. Owns the editable prompt + estimate/modal/busy state and
 * wires the AD14 Submit → estimate → Continue → iterate flow. Delegates all
 * markup to the pure view and reuses P3-11's `CostEstimateModalView`.
 */
export function IterateComposer({
  prototypeId,
  isComplete = false,
  applyTarget = null,
  onClearApply,
  onIterated,
}: IterateComposerProps) {
  const init = initialComposerState(applyTarget)
  const [prompt, setPrompt] = useState<string>(init.prompt)
  const [appliedCommentId, setAppliedCommentId] = useState<number | null>(
    init.appliedCommentId,
  )
  const [showModal, setShowModal] = useState(false)
  const [estimate, setEstimate] = useState<IterateCostEstimate | null>(null)
  const [estimateLoading, setEstimateLoading] = useState(false)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [queueLine, setQueueLine] = useState<string | null>(null)

  // F10: when the Apply target changes (a comment's Apply was clicked), re-seed
  // the editable prompt + applied_comment_id from it.
  useEffect(() => {
    const next = initialComposerState(applyTarget)
    setPrompt(next.prompt)
    setAppliedCommentId(next.appliedCommentId)
  }, [applyTarget])

  const mode: "reprompt" | "apply" = appliedCommentId != null ? "apply" : "reprompt"

  // AD14: Submit fetches the estimate and opens the modal — never calls iterate.
  async function handleSubmit() {
    if (isComplete) return // F14 defense in depth (the locked view has no Submit)
    if (!prompt.trim()) return
    setShowModal(true)
    setEstimateLoading(true)
    setEstimateError(null)
    setEstimate(null)
    try {
      const est = await runEstimate(designAgentApi.estimateIterate, {
        prototypeId,
        prompt,
        appliedCommentId,
      })
      setEstimate(est)
    } catch (e) {
      setEstimateError(toMessage(e, "Could not estimate cost"))
    } finally {
      setEstimateLoading(false)
    }
  }

  // Modal Continue: the ONLY path that calls iterate. On success, reset and hand
  // off to the launcher's existing status/poll surface (AC5 — no self-poll).
  async function handleContinue() {
    setBusy(true)
    setError(null)
    try {
      const resp = await runIterate(designAgentApi.iterate, {
        prototypeId,
        prompt,
        appliedCommentId,
      })
      setQueueLine(queueIndicator(resp))
      setShowModal(false)
      setPrompt("")
      setAppliedCommentId(null)
      onClearApply?.()
      // P6-05 (#5): notify the launcher so it re-polls the prototype and
      // refreshes the preview iframe + View href once the new checkpoint builds.
      onIterated?.()
    } catch (e) {
      setError(toMessage(e, "Could not start the iteration"))
    } finally {
      setBusy(false)
    }
  }

  // Cancel: close the modal, make NO API call.
  function handleCancel() {
    setShowModal(false)
    setEstimate(null)
    setEstimateError(null)
  }

  if (isComplete) {
    return (
      <IterateComposerView
        prompt=""
        isComplete
        mode={mode}
        showModal={false}
      />
    )
  }

  return (
    <IterateComposerView
      prompt={prompt}
      isComplete={false}
      mode={mode}
      showModal={showModal}
      busy={busy}
      error={error}
      queueLine={queueLine}
      estimate={estimate}
      estimateLoading={estimateLoading}
      estimateError={estimateError}
      onPromptChange={setPrompt}
      onSubmit={handleSubmit}
      onContinue={handleContinue}
      onCancel={handleCancel}
    />
  )
}

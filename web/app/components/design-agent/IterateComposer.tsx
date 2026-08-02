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
  /** When the prototype is locked the composer renders a disabled textarea and an
   *  "Unlock" button instead of the active form. Clicking Unlock fires `onUnlock`
   *  (wired to the resume path) which re-enables iteration. `unlockBusy` disables
   *  the button during the request; `unlockError` surfaces failure. */
  onUnlock?: () => void
  unlockBusy?: boolean
  unlockError?: string | null
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
  onUnlock,
  unlockBusy = false,
  unlockError = null,
}: IterateComposerViewProps) {
  if (isComplete) {
    // Locked state: disabled textarea and Unlock button instead of the active form.
    return (
      <div
        className="iterate-composer iterate-composer--locked"
        data-testid="iterate-composer-locked"
      >
        <textarea
          className="iterate-composer-input"
          data-testid="iterate-composer-input-locked"
          value=""
          placeholder="Prototype locked — unlock to make changes…"
          disabled
          aria-disabled="true"
          readOnly
        />
        <p className="iterate-composer-locked-note">{LOCKED_AFFORDANCE}</p>
        <div className="iterate-composer-actions">
          <button
            type="button"
            className="btn btn-accent"
            data-testid="iterate-composer-unlock"
            disabled={unlockBusy}
            onClick={() => onUnlock?.()}
          >
            {unlockBusy ? "Unlocking…" : "Unlock"}
          </button>
        </div>
        {unlockError && (
          <p
            className="iterate-composer-error error"
            role="alert"
            data-testid="iterate-composer-unlock-error"
          >
            {unlockError}
          </p>
        )}
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
              ? "Edit this task for Sprntly…"
              : "Describe a change for Sprntly to make…"
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
  /**
   * The iterate path intentionally skips the pre-flight cost-estimate
   * confirmation modal. The per-generation soft/hard spend caps remain the
   * guardrail, and the generate-path estimate is unchanged. The default
   * (`skipCostConfirm = false`) preserves the confirmation modal for any
   * non-iterate caller.
   */
  skipCostConfirm?: boolean
  /** When supplied, Submit delegates the iterate run to the host's shared runner
   *  (useIterateRun.runIterate) instead of posting inline. Only honoured together
   *  with skipCostConfirm. When absent the composer keeps its own POST + onIterated
   *  notify (back-compat for the launcher / public callers). Resolves `true` when
   *  the runner actually started a run, `false` when it was rejected (e.g. a
   *  second submit while one is already in flight) — Submit awaits this and only
   *  clears the local prompt / apply target when it resolves `true`. */
  runIterateExternal?: (
    instruction: string,
    appliedCommentId?: number | null,
  ) => Promise<boolean>
  /** When the host runner is running, Submit is disabled (the activity stream is
   *  the progress surface). */
  externalBusy?: boolean
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
  skipCostConfirm = false,
  runIterateExternal,
  externalBusy = false,
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
  // Local unlock state. When the prototype is locked (isComplete) the composer
  // shows an Unlock button; clicking it calls designAgentApi.resume + flips unlocked
  // so the active form renders before the host refetches. Effective lock = isComplete && !unlocked.
  const [unlocked, setUnlocked] = useState(false)
  const [unlockBusy, setUnlockBusy] = useState(false)
  const [unlockError, setUnlockError] = useState<string | null>(null)
  const locked = isComplete && !unlocked

  // If the prop flips back to locked (e.g. a fresh prototype id / a re-complete),
  // drop the local unlock so the box re-locks to match the real state.
  useEffect(() => {
    if (!isComplete) setUnlocked(false)
  }, [isComplete])

  // The Unlock action: resumes the prototype via the API and flips the local
  // unlocked flag so the active form renders immediately without waiting for a prop refetch.
  async function handleUnlock() {
    setUnlockBusy(true)
    setUnlockError(null)
    try {
      await designAgentApi.resume(prototypeId)
      setUnlocked(true)
      // Surface the unlock to the host so it can re-poll the record if it wants
      // the lock chrome elsewhere to follow (no-op when unused).
      onIterated?.()
    } catch (e) {
      setUnlockError(toMessage(e, "Could not unlock the prototype"))
    } finally {
      setUnlockBusy(false)
    }
  }

  // F10: when the Apply target changes (a comment's Apply was clicked), re-seed
  // the editable prompt + applied_comment_id from it.
  useEffect(() => {
    const next = initialComposerState(applyTarget)
    setPrompt(next.prompt)
    setAppliedCommentId(next.appliedCommentId)
  }, [applyTarget])

  const mode: "reprompt" | "apply" = appliedCommentId != null ? "apply" : "reprompt"

  // The shared iterate run, used by both the cost-confirm modal's Continue path
  // and the direct (skipCostConfirm) Submit path. On success it resets the
  // composer and hands off to the launcher's existing status/poll surface — the
  // composer does not poll itself.
  async function runIterateNow() {
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

  // Submit. When `skipCostConfirm` is set, Submit runs the iteration directly,
  // intentionally skipping the pre-flight cost-estimate confirmation modal; the
  // per-generation soft/hard spend caps remain the guardrail. Otherwise the
  // default path stands: fetch the estimate and open the confirmation modal —
  // Submit never calls iterate from here.
  async function handleSubmit() {
    if (locked) return // F14 defense in depth (the locked view has no Submit)
    if (!prompt.trim()) return
    // When the host supplies the shared runner, delegate to it — it POSTs,
    // polls to completion, drives the left-panel activity, and reloads the
    // canvas. The composer just clears its local prompt + apply target and
    // hands off; it does NOT poll or render progress here.
    if (skipCostConfirm && runIterateExternal) {
      const instruction = prompt
      const linkedComment = appliedCommentId
      // Await the shared runner's outcome before clearing anything: a
      // rejected submit (a run is already in flight) must leave the prompt +
      // apply target exactly as the user left them, so they can retry once
      // the current change finishes.
      const started = await runIterateExternal(instruction, linkedComment)
      if (started) {
        setPrompt("")
        setAppliedCommentId(null)
        onClearApply?.()
      }
      return
    }
    if (skipCostConfirm) {
      await runIterateNow()
      return
    }
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

  // Modal Continue: the AD14 path that calls iterate (only reached when the cost
  // modal is shown, i.e. `skipCostConfirm` is false).
  async function handleContinue() {
    await runIterateNow()
  }

  // Cancel: close the modal, make NO API call.
  function handleCancel() {
    setShowModal(false)
    setEstimate(null)
    setEstimateError(null)
  }

  if (locked) {
    return (
      <IterateComposerView
        prompt=""
        isComplete
        mode={mode}
        showModal={false}
        onUnlock={handleUnlock}
        unlockBusy={unlockBusy}
        unlockError={unlockError}
      />
    )
  }

  return (
    <IterateComposerView
      prompt={prompt}
      isComplete={false}
      mode={mode}
      showModal={showModal}
      busy={busy || externalBusy}
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

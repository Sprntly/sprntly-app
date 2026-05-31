"use client"

/**
 * P3-11 — Pre-flight cost estimate modal (AD14 / AD15).
 *
 * The trust gate before an iterate run: "this will cost ~$0.X, here's why."
 * Continue runs the iterate; Cancel closes WITHOUT any API call, so cancelling
 * provably costs nothing (and the estimate endpoint makes no Anthropic call
 * either — the estimate is a deterministic token-count + price calc).
 *
 * Testability split mirrors PrdPatchBanner / CompletionBar: the repo's vitest runs
 * in a `node` env with no jsdom / @testing-library, so the pure markup lives in
 * `CostEstimateModalView` (SSR-renderable via `renderToStaticMarkup`) and the I/O
 * lives in exported pure async helpers (`runContinue`, `runCancel`) that take their
 * deps as arguments. The container wires React state to those units.
 *
 * Iterate-helper injection (P3-14 seam): the iterate composer `designAgentApi.iterate`
 * is owned by P3-14, not this ticket. So `runContinue` and the container take the
 * iterate function as an INJECTED dependency rather than referencing a method that
 * does not exist yet. P3-14 mounts this modal and passes `designAgentApi.iterate` as
 * `iterate`. This keeps the AC9 guarantee testable (a spy stands in for the iterate
 * helper) without P3-11 depending on P3-14's surface.
 *
 * Per BUILD.md §6 this file adds NO CSS to the hot `globals.css`; component-scoped
 * class strings only.
 */

import { useEffect, useState } from "react"
import { designAgentApi, type IterateCostEstimate } from "../../lib/api"

/** The iterate composer's call signature (owned by P3-14). Injected so P3-11 does
 *  not reference a method that does not exist yet. */
export type IterateFn = (
  prototypeId: number,
  body: { prompt: string; applied_comment_id?: number | null },
) => Promise<unknown>

export type CostEstimateModalViewProps = {
  estimate: IterateCostEstimate | null
  loading: boolean
  errorMsg?: string | null
  busy?: boolean
  onContinue: () => void
  onCancel: () => void
}

export function formatUsd(n: number): string {
  return `$${n.toFixed(2)}`
}

/** Cached-vs-fresh framing string (AD14): the estimate prices the cache-READ path
 *  when a prior run's context is being re-used, so tell the user when that's the case. */
export function cacheFraming(estimate: IterateCostEstimate): string {
  return estimate.cached_input_tokens > 0
    ? "Reusing context from your last run (cached) — cheaper than a fresh build."
    : "First run today — no cached context to reuse yet."
}

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env vitest.
 *  Renders the dollar estimate, the cached-vs-fresh framing, an optional soft-cap
 *  warning, and Continue / Cancel. */
export function CostEstimateModalView({
  estimate,
  loading,
  errorMsg = null,
  busy = false,
  onContinue,
  onCancel,
}: CostEstimateModalViewProps) {
  return (
    <div
      className="cost-estimate-modal"
      data-testid="cost-estimate-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Estimated run cost"
    >
      {loading && (
        <p className="cost-estimate-loading" data-testid="cost-estimate-loading">
          Estimating run cost…
        </p>
      )}

      {errorMsg && (
        <p className="error" role="alert" data-testid="cost-estimate-error">
          {errorMsg}
        </p>
      )}

      {estimate && !loading && !errorMsg && (
        <>
          <p className="cost-estimate-amount">
            This run will cost about{" "}
            <strong data-testid="cost-estimate-amount">
              ~{formatUsd(estimate.est_cost_usd)}
            </strong>
          </p>
          <p className="cost-estimate-framing">{cacheFraming(estimate)}</p>
          {estimate.exceeds_soft_cap && (
            <p
              className="cost-estimate-warning"
              role="status"
              data-testid="cost-estimate-soft-cap-warning"
            >
              This run is larger than usual (~{formatUsd(estimate.est_cost_usd)}, above
              the {formatUsd(estimate.soft_cap_usd)} guide).
            </p>
          )}
        </>
      )}

      <div className="cost-estimate-actions">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          data-testid="cost-estimate-cancel"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-accent btn-sm"
          data-testid="cost-estimate-continue"
          disabled={busy || loading || !estimate || !!errorMsg}
          onClick={onContinue}
        >
          Continue
        </button>
      </div>
    </div>
  )
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

export type RunContinueArgs = {
  prototypeId: number
  prompt: string
  appliedCommentId?: number | null
}

/** Continue handler (AC9): the ONLY path that invokes the iterate helper. Kicks off
 *  the iterate run the modal was gating. `iterate` is injected (P3-14 owns the real
 *  `designAgentApi.iterate`); a spy stands in for it in tests. */
export async function runContinue(
  iterate: IterateFn,
  args: RunContinueArgs,
): Promise<void> {
  await iterate(args.prototypeId, {
    prompt: args.prompt,
    applied_comment_id: args.appliedCommentId ?? null,
  })
}

/** Cancel handler (AC9): closes the modal and makes NO API call. Cancelling an
 *  estimate provably costs nothing — the injected iterate helper is never reached. */
export function runCancel(onClose: () => void): void {
  onClose()
}

// ---- container --------------------------------------------------------------

export type CostEstimateModalProps = {
  prototypeId: number
  prompt: string
  appliedCommentId?: number | null
  /** The iterate composer (P3-14 passes `designAgentApi.iterate`). Injected so this
   *  ticket does not reference a method P3-14 has not landed yet. */
  iterate: IterateFn
  /** Called after a successful Continue (iterate kicked off). */
  onConfirmed?: () => void
  /** Called to close the modal (Cancel, or after a successful Continue). */
  onClose: () => void
}

/** Container: fetches the estimate on mount, owns loading/error/busy state, wires
 *  the Continue / Cancel helpers to the view. Delegates all markup to the pure view. */
export function CostEstimateModal({
  prototypeId,
  prompt,
  appliedCommentId,
  iterate,
  onConfirmed,
  onClose,
}: CostEstimateModalProps) {
  const [estimate, setEstimate] = useState<IterateCostEstimate | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    designAgentApi
      .estimateIterate(prototypeId, { prompt, applied_comment_id: appliedCommentId ?? null })
      .then((data) => {
        if (!cancelled) setEstimate(data)
      })
      .catch((e) => {
        if (!cancelled) setErrorMsg(e instanceof Error ? e.message : "Failed to estimate cost")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [prototypeId, prompt, appliedCommentId])

  async function handleContinue() {
    setBusy(true)
    setErrorMsg(null)
    try {
      await runContinue(iterate, { prototypeId, prompt, appliedCommentId })
      onConfirmed?.()
      onClose()
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Failed to start the run")
    } finally {
      setBusy(false)
    }
  }

  return (
    <CostEstimateModalView
      estimate={estimate}
      loading={loading}
      errorMsg={errorMsg}
      busy={busy}
      onContinue={handleContinue}
      onCancel={() => runCancel(onClose)}
    />
  )
}

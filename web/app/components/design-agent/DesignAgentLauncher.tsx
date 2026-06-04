"use client"

/**
 * F2 launcher — the "Generate Prototype" entry point that lives inside the
 * PRD's Design section (rendered by PrdSections' `prd-design` block). It owns
 * the drawer open/close state locally with `useState` (Path A): the
 * `'design-agent'` member in NavigationContext's drawer-kind union stays as
 * forward-compat for a future Cmd+K palette entry but is NOT driven from here.
 *
 * The `contentEditable={false}` wrapper is load-bearing. The Design section
 * renders inside the PRD's contentEditable region; without it the button is
 * swallowed by the editable focus and clicks misbehave.
 *
 * Testability split mirrors DesignAgentDrawer: the container owns `useState`,
 * the pure `DesignAgentLauncherView` holds the SSR-renderable markup, and the
 * drawer is injected via `renderDrawer` (defaulting to the real
 * `DesignAgentDrawer`). The default drawer wires `useNavigation`, so injecting
 * a stub keeps the launcher renderable under the repo's node-env vitest (no
 * NavigationContext provider, no @testing-library).
 */

import { useState, type ReactNode } from "react"
import { DesignAgentDrawer } from "./DesignAgentDrawer"
import { PostGenerationResult } from "./PostGenerationResult"
import { GenerationErrorBanner, reasonCopy } from "./GenerationErrorBanner"
import { CommentsPanel } from "./CommentsPanel"
import { IterateComposer } from "./IterateComposer"
import { ClarifyingQuestionSurface } from "./ClarifyingQuestionSurface"
import type { CommentRecord, PrototypeRecord } from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"

export type DesignAgentLauncherProps = {
  prdId: number
  figmaFileKey?: string | null
}

/** Props the launcher hands to whatever drawer it mounts. Mirrors
 *  DesignAgentDrawerProps so the default renderer and any test stub agree. */
export type LauncherDrawerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  prdId: number
  figmaFileKey?: string | null
  /** P2-12: drawer reports the terminal generation outcome here so the
   *  container can mount the post-generation result view. */
  onGenerated?: (result: DesignAgentGenResult) => void
}

/** P2-12: maps a generation outcome to launcher result state — the prototype
 *  on success, null on failure (the drawer's existing toast surfaces the error;
 *  AC5: no result view on failure). Pure → unit-testable without a DOM. */
export function resultFromGeneration(
  result: DesignAgentGenResult,
): PrototypeRecord | null {
  return result.ok ? result.prototype : null
}

/** P6-08 (Fix #11 visibility half): maps a generation outcome to launcher
 *  FAILURE state — the failure `message` on a failed outcome, null on success
 *  (which clears any prior banner). Single `{ message } | null` slot, so a second
 *  failure REPLACES the first via `setFailure` (no banner stacking — AC9). Pure →
 *  unit-testable without a DOM, mirroring `resultFromGeneration`. The raw
 *  `message` is mapped to human copy by `reasonCopy` at render time, never shown
 *  verbatim (Rule #24). */
export function failureFromGeneration(
  result: DesignAgentGenResult,
): { message: string } | null {
  return result.ok ? null : { message: result.message }
}

/** Default drawer renderer: the real, NavigationContext-wired DesignAgentDrawer. */
export const defaultRenderDrawer = (props: LauncherDrawerProps): ReactNode => (
  <DesignAgentDrawer {...props} />
)

/** P6-05 (#5): the pending-question identity used to detect a clarify re-pause
 *  across a refetch — the question text, or null when none is pending. A change
 *  (null→Q, Q→null, or Q1→Q2) signals the record advanced off the prior state. */
export function pendingKey(
  p: Pick<PrototypeRecord, "pending_question">,
): string | null {
  return p.pending_question?.question ?? null
}

/** Dependency seams for `pollUntilAdvanced` — injected in tests so the loop runs
 *  without real timers / network. Production defaults are the real poll helper,
 *  a 4s sleep, the wall clock, and the 6-minute cap. */
export type RefreshDeps = {
  runGeneration?: (args: { prototypeId: number }) => Promise<DesignAgentGenResult>
  sleep?: (ms: number) => Promise<void>
  now?: () => number
  deadlineMs?: number
}

/**
 * P6-05 (#5) — race-safe post-iterate/clarify re-poll. Resolves with the fresh
 * `PrototypeRecord` once the record advances OFF the pre-iterate checkpoint:
 * a NEW `bundle_url` (a new checkpoint built) OR a changed `pending_question`
 * (a clarify re-pause / a newly-asked question, including null→Q and Q1→Q2).
 * Returns null on failure or if the deadline passes without an advance.
 *
 * Why it does NOT trust the first get(): the iterate/clarify callbacks fire
 * immediately after kickoff, but the backend flips the prototype row to
 * `generating` only inside its bg task — so the first `runDesignAgentGeneration`
 * may still observe the PRE-iterate `ready` + OLD `bundle_url` (+ OLD
 * `pending_question`) and return it as a terminal `{ok, ready}`. Resolving on
 * that stale read would re-flow the old checkpoint (the exact #5 bug). So we
 * gate on the OBSERVED transition off the captured prev values and re-sample
 * (the backend may not have flipped yet) until it advances. `runDesignAgentGeneration`'s
 * own 4s/6min loop carries an in-progress `generating` run to its next `ready`;
 * this outer guard only adds the "wait for the transition off the OLD checkpoint"
 * gate the helper alone cannot provide.
 */
export async function pollUntilAdvanced(
  prototypeId: number,
  prevBundle: string | null,
  prevPending: string | null,
  deps: RefreshDeps = {},
): Promise<PrototypeRecord | null> {
  const runGeneration = deps.runGeneration ?? runDesignAgentGeneration
  const sleep =
    deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const now = deps.now ?? (() => Date.now())
  const deadline = now() + (deps.deadlineMs ?? 6 * 60 * 1000)
  while (now() < deadline) {
    const r = await runGeneration({ prototypeId })
    if (!r.ok) return null // surfaced via the existing failure path (#5 leaves failure handling to P6-08)
    const advancedBundle =
      r.prototype.bundle_url != null && r.prototype.bundle_url !== prevBundle
    const advancedPending = pendingKey(r.prototype) !== prevPending
    if (advancedBundle || advancedPending) return r.prototype
    await sleep(4000) // re-sample; the backend may not have flipped to generating yet
  }
  return null
}

type LauncherViewProps = DesignAgentLauncherProps & {
  open: boolean
  setOpen: (open: boolean) => void
  /** P2-12: the generated prototype to show post-generation. Null → no result
   *  view yet (the Generate button is the only chrome). Optional/defaulted so
   *  existing direct-view test calls keep typechecking. */
  result?: PrototypeRecord | null
  /** P2-12: handed to the drawer so a successful generation populates `result`. */
  onGenerated?: (result: DesignAgentGenResult) => void
  /** P6-08 (Fix #11): the last generation attempt's failure, or null. When set,
   *  the view renders `<GenerationErrorBanner/>` (replacing the old silent
   *  revert-to-Generate-button). Independent of `result`: a failed retry after a
   *  prior success shows the banner AND retains the prior result view (AC5).
   *  Optional/defaulted so existing direct-view test calls keep typechecking. */
  failure?: { message: string } | null
  /** P6-08: fired by the banner's Retry control — clears `failure` + re-opens the
   *  drawer (`setOpen(true)`). Does NOT auto-re-POST. Optional/defaulted. */
  onRetry?: () => void
  /** P3-14 (F10): the comment selected for Apply, lifted to the container so
   *  CommentsPanel's Apply action sets it and IterateComposer reads it. Optional
   *  so existing direct-view test calls keep typechecking. */
  applyTarget?: CommentRecord | null
  /** P3-14 (F10): setter for `applyTarget` (CommentsPanel onApply → set;
   *  IterateComposer onClearApply → clear). */
  setApplyTarget?: (comment: CommentRecord | null) => void
  /** P6-05 (#5): forwarded to IterateComposer — fired after a successful iterate
   *  so the container re-polls + refreshes `result`. Optional/defaulted. */
  onIterated?: () => void
  /** P6-05 (#5): forwarded to ClarifyingQuestionSurface — fired after a
   *  successful clarify answer so the container re-polls + refreshes `result`.
   *  Optional/defaulted (the surface already declares `onAnswered`; it just
   *  wasn't threaded from the launcher before). */
  onAnswered?: () => void
  /** Injected in tests so the view renders without NavigationContext. */
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}

/**
 * Pure, SSR-renderable view: the `contentEditable={false}` wrapper, the
 * "Generate Prototype" button, the (closed-by-default) drawer, and — once a
 * generation has succeeded — the editable `PostGenerationResult` chrome. The
 * result mounts INSIDE the same `contentEditable={false}` boundary so it never
 * interferes with the PRD body's `contentEditable` (PrdScreen antipattern guard).
 */
export function DesignAgentLauncherView({
  prdId,
  figmaFileKey,
  open,
  setOpen,
  result = null,
  onGenerated,
  failure = null,
  onRetry = () => {},
  applyTarget = null,
  setApplyTarget,
  onIterated,
  onAnswered,
  renderDrawer = defaultRenderDrawer,
}: LauncherViewProps) {
  return (
    <div className="design-agent-surface prd-design-launcher" contentEditable={false}>
      <button
        type="button"
        className="btn btn-accent"
        onClick={() => setOpen(true)}
      >
        Generate Prototype
      </button>
      {/* P6-08 (Fix #11): when the last generation FAILED, surface a persistent
          banner with mapped human copy + Retry — instead of the old silent
          revert to the bare button above. Mounted ABOVE the `result &&` blocks
          and INDEPENDENT of `result`: a failed retry after a prior success shows
          the banner alongside the still-good result view (AC5). `reasonCopy`
          maps the raw `message` so the raw backend `error` never reaches the DOM
          (Rule #24). */}
      {failure && (
        <GenerationErrorBanner
          reason={reasonCopy(failure.message)}
          onRetry={onRetry}
        />
      )}
      {/* `key` forces a clean remount per prototype id: PostGenerationResult
          (and the CompletionBar it mounts) seed state from props at mount only,
          so regenerating a second prototype in the same launcher instance must
          remount to avoid carrying the prior prototype's is_complete. */}
      {result && <PostGenerationResult key={result.id} prototype={result} />}
      {/* P3-14 (F10): signed-in CommentsPanel mount — the public mount lives in
          PublicTokenViewer (P3-03). Comments are addressed by the share token,
          so this mounts only once the prototype is shared. `onApply` enables the
          Apply→IterateComposer handoff (absent on the public mount → no Apply). */}
      {result && result.share_token && (
        <CommentsPanel
          key={`comments-${result.id}`}
          token={result.share_token}
          prototypeId={result.id}
          onApply={(comment) => setApplyTarget?.(comment)}
        />
      )}
      {/* P3-14 (F9/F10): the iterate trigger surface — re-prompt always available
          (when unlocked); Apply pre-fills from `applyTarget`. Mounted ONLY here
          (authed surface), never in the public route. */}
      {result && (
        <IterateComposer
          key={`iterate-${result.id}`}
          prototypeId={result.id}
          isComplete={result.is_complete ?? false}
          applyTarget={applyTarget}
          onClearApply={() => setApplyTarget?.(null)}
          onIterated={onIterated}
        />
      )}
      {/* P3-16 (F12): the clarifying-question answer surface — rendered ONLY when
          the agent has paused with a pending question and the prototype is not
          locked. The answer routes through the reused P3-14 iterate (no new
          method). Mounted ONLY here (authed surface), never in the public route
          (external viewers cannot answer/iterate). */}
      {result &&
        result.pending_question != null &&
        !(result.is_complete ?? false) && (
          <ClarifyingQuestionSurface
            key={`clarify-${result.id}`}
            prototype={result}
            onAnswered={onAnswered}
          />
        )}
      {renderDrawer({
        open,
        onOpenChange: setOpen,
        prdId,
        figmaFileKey,
        onGenerated,
      })}
    </div>
  )
}

/**
 * Public component. Owns the drawer open/close state locally and delegates
 * rendering to the pure view. `renderDrawer` is optional (defaults to the real
 * drawer) — production callers pass only `prdId` / `figmaFileKey`.
 */
export function DesignAgentLauncher({
  prdId,
  figmaFileKey,
  renderDrawer,
}: DesignAgentLauncherProps & {
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<PrototypeRecord | null>(null)
  // P6-08 (Fix #11): the last generation attempt's failure, or null. A non-null
  // value renders the persistent GenerationErrorBanner (replacing the old silent
  // revert). Kept INDEPENDENT of `result` so a failed retry after a prior success
  // shows the banner without wiping the previously-good prototype (AC5).
  const [failure, setFailure] = useState<{ message: string } | null>(null)
  // P3-14 (F10): lifted so CommentsPanel's Apply sets it and IterateComposer
  // reads it as its pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)

  // On a successful generation, mount the result view AND clear any prior failure
  // banner. On failure, STOP discarding it (the pre-P6-08 bug): set the single
  // `failure` slot so the banner surfaces the reason — `result` is left intact so
  // a previously-good prototype survives a failed retry (AC5). A second failure
  // REPLACES the slot (no stacking — AC9). `resultFromGeneration` still owns the
  // success-path mapping; `failureFromGeneration` owns the failure-path mapping.
  const handleGenerated = (outcome: DesignAgentGenResult) => {
    const next = resultFromGeneration(outcome)
    if (next) setResult(next)
    setFailure(failureFromGeneration(outcome))
  }

  // P6-08: the banner's Retry — clear the failure banner and re-open the drawer so
  // the user re-initiates a generation from the same surface. Deliberately does
  // NOT auto-re-POST (avoids a silent retry loop on a deterministically-failing
  // PRD); re-kicking is the user's explicit action inside the drawer.
  const handleRetry = () => {
    setFailure(null)
    setOpen(true)
  }

  // P6-05 (#5): after an iterate/clarify advances the SAME prototype id to a new
  // checkpoint, re-poll off the pre-iterate `bundle_url` / `pending_question`
  // and replace `result` with the refetched record. Race-safe: `pollUntilAdvanced`
  // never resolves on a first get() that still shows the pre-iterate checkpoint.
  // Surfacing the refetched record also flows a newly-minted `share_token` into
  // `result` (#14 facet), so the share-gated CommentsPanel mounts without a
  // manual re-mount — no extra code, just the live snapshot.
  const refreshResult = async () => {
    if (!result) return
    const fresh = await pollUntilAdvanced(
      result.id,
      result.bundle_url,
      pendingKey(result),
    )
    if (fresh) setResult(fresh)
  }

  return (
    <DesignAgentLauncherView
      prdId={prdId}
      figmaFileKey={figmaFileKey}
      open={open}
      setOpen={setOpen}
      result={result}
      onGenerated={handleGenerated}
      failure={failure}
      onRetry={handleRetry}
      applyTarget={applyTarget}
      setApplyTarget={setApplyTarget}
      onIterated={refreshResult}
      onAnswered={refreshResult}
      renderDrawer={renderDrawer}
    />
  )
}

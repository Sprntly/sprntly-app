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

import { useEffect, useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { prototypePath } from "../../lib/routes"
import { DesignAgentDrawer } from "./DesignAgentDrawer"
import { PostGenerationResult } from "./PostGenerationResult"
import { GenerationErrorBanner, reasonCopy } from "./GenerationErrorBanner"
import { CommentsPanel } from "./CommentsPanel"
import { IterateComposer } from "./IterateComposer"
import { ClarifyingQuestionSurface } from "./ClarifyingQuestionSurface"
import { PrototypePreviewCard } from "./PrototypePreviewCard"
import { designAgentApi, type CommentRecord, type PrototypeRecord } from "../../lib/api"
import {
  runDesignAgentGeneration,
  type DesignAgentGenResult,
} from "../../lib/runDesignAgentGeneration"

export type DesignAgentLauncherProps = {
  prdId: number
  figmaFileKey?: string | null
  /** PRD title, threaded from PrdScreen → PrdSections so the preview card + the
   *  canvas breadcrumb / left-column header can label the PRD. Optional so
   *  existing callers keep type-checking. The PRD content panel was removed from
   *  the canvas (live-only conversation thread); only the title survives. */
  prdTitle?: string | null
  /** When set from outside (e.g. notify-mode generation kicked off by
   *  ApproveModal), shows PrototypeGeneratingCard without requiring the
   *  launcher's own drawer flow to have kicked off. */
  externalGeneratingId?: number | null
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
  /** Fires immediately after kickoff (before polling) so the host can show the
   *  in-page "Generating prototype…" status card. */
  onKickoff?: (prototypeId: number) => void
}

/** Persistent in-page status card shown from kickoff until the terminal result
 *  mounts. Gives users a clear "still running" signal without requiring them to
 *  opt in to the toast notification or wait for the drawer to reopen. */
function PrototypeGeneratingCard() {
  return (
    <div className="da-prototype-generating">
      {/* Spinner */}
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
        className="da-spinner"
      >
        <circle cx="8" cy="8" r="6" stroke="var(--accent-alpha-28)" strokeWidth="2" />
        <path d="M8 2a6 6 0 0 1 6 6" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <div className="da-prototype-generating-title">Generating prototype…</div>
    </div>
  )
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

/**
 * P6-20 (#14) — share-success single-shot re-seed. A bare Share (no iterate)
 * changes NEITHER `bundle_url` NOR `pending_question`, so `pollUntilAdvanced`
 * would never resolve (it waits for an advance that never comes). But the share
 * endpoint sets `share_token` synchronously before its POST returns, so there is
 * no race to poll: a SINGLE `get()` of the same id returns the post-share record
 * whose `share_token` is now live. The launcher sets it as `result` so the
 * share-gated CommentsPanel mounts without a re-mount. Returns null when there is
 * no current prototype or the fetch fails — the local `ShareMenu` token already
 * shows the link, so a failed re-seed degrades silently (no spurious error, no
 * unhandled rejection from the fire-and-forget `onShared` call). Pure of React
 * (deps injected) → node-env testable, mirroring `pollUntilAdvanced`.
 */
export async function refreshShareTokenStep(
  prototypeId: number | null,
  api: Pick<typeof designAgentApi, "get"> = designAgentApi,
): Promise<PrototypeRecord | null> {
  if (prototypeId == null) return null
  try {
    const fresh = await api.get(prototypeId)
    return fresh ?? null
  } catch {
    return null
  }
}

// There is deliberately no mount-time fetch that auto-seeds the PRD-screen
// canvas: the post-gen canvas must NOT auto-render on the PRD screen. `result`
// is populated only by the in-launcher drawer/iterate/share flows. The existing
// ready prototype (if any) surfaces as the preview card, not an auto-mounted
// canvas — see the read-only `existing` lookup below.

type LauncherViewProps = DesignAgentLauncherProps & {
  open: boolean
  setOpen: (open: boolean) => void
  /** The PRD's existing ready prototype (resolved read-only via getByPrd), or
   *  null when none exists yet. Drives the preview card + the "View Prototype"
   *  skip-loading open. */
  existing?: PrototypeRecord | null
  /** PRD title for the preview card label. */
  prdTitle?: string | null
  /** Navigate to the in-tab canvas (`/prototype?prd=<id>`) for the existing
   *  prototype. */
  onOpenExisting?: () => void
  onDeleteExisting?: () => Promise<void>
  /** P2-12: the generated prototype to show post-generation. Null → no result
   *  view yet (the Generate button is the only chrome). Optional/defaulted so
   *  existing direct-view test calls keep typechecking. */
  result?: PrototypeRecord | null
  /** P2-12: handed to the drawer so a successful generation populates `result`. */
  onGenerated?: (result: DesignAgentGenResult) => void
  /** In-page status card: prototype_id being generated, null when idle. */
  generatingId?: number | null
  /** External override — see DesignAgentLauncherProps.externalGeneratingId. */
  externalGeneratingId?: number | null
  /** Fires immediately after kickoff so the container sets `generatingId`. */
  onKickoff?: (prototypeId: number) => void
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
  /** dedup: server comment ids lifted in the container, threaded to
   *  PostGenerationResult (→ PrototypeMarkLayer) so saved-pin cards already in
   *  the server list are suppressed. Optional so direct-view test calls keep
   *  typechecking. */
  serverCommentIds?: number[]
  /** dedup: setter passed to the mounted CommentsPanel as
   *  `onCommentsLoaded` so each successful list load republishes the ids. */
  setServerCommentIds?: (ids: number[]) => void
  /** P6-05 (#5): forwarded to IterateComposer — fired after a successful iterate
   *  so the container re-polls + refreshes `result`. Optional/defaulted. */
  onIterated?: () => void
  /** P6-05 (#5): forwarded to ClarifyingQuestionSurface — fired after a
   *  successful clarify answer so the container re-polls + refreshes `result`.
   *  Optional/defaulted (the surface already declares `onAnswered`; it just
   *  wasn't threaded from the launcher before). */
  onAnswered?: () => void
  /** P6-20 (#14): forwarded to PostGenerationResult → ShareMenu — fired after a
   *  successful Share so the container single-shot re-polls + refreshes `result`,
   *  flipping the share-gated CommentsPanel live with no re-mount. Optional/defaulted. */
  onShared?: (token: string | null) => void
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
  generatingId = null,
  externalGeneratingId = null,
  onKickoff,
  failure = null,
  onRetry = () => {},
  applyTarget = null,
  setApplyTarget,
  serverCommentIds = [],
  setServerCommentIds,
  onIterated,
  onAnswered,
  onShared,
  existing = null,
  prdTitle = null,
  onOpenExisting,
  onDeleteExisting,
  renderDrawer = defaultRenderDrawer,
}: LauncherViewProps) {
  return (
    <div className="design-agent-surface prd-design-launcher" contentEditable={false}>
      {/* The PRD Design section no longer renders a "Generate Prototype" button:
          the generation trigger lives in the "Approve & next step" modal (P7
          relocation, #143). The launcher's state / drawer / result wiring below
          is intentionally kept so the drawer/iterate/share flows still work, and
          the in-page generating status card still surfaces once a generation is
          kicked off from the Approve flow. */}
      {/* In-page generating status card — visible from kickoff until the
          terminal result mounts. Keeps the user informed without relying on
          the transient toast or the "Notify me" opt-in. */}
      {(generatingId !== null || externalGeneratingId !== null) && result === null && (
        <PrototypeGeneratingCard />
      )}
      {/* When the PRD already has a ready prototype (read-only getByPrd), show a
          preview card here. Clicking it navigates to the in-tab canvas
          (`/prototype?prd=<id>`). When none exists this renders nothing (the
          Design section stays empty). Suppressed while the launcher's own
          in-session `result` is mounted below to avoid a duplicate surface. */}
      {existing && !result && (
        <PrototypePreviewCard
          prototype={existing}
          prdTitle={prdTitle}
          onOpen={() => onOpenExisting?.()}
          onDelete={onDeleteExisting}
        />
      )}
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
      {/* P6-13 (UX-3): the signed-in CommentsPanel is now passed DOWN as
          PostGenerationResult's `comments` prop so a two-column `design-pane`
          grid can wrap viewer-left + comments-right in ONE box (the launcher
          cannot wrap them while they are separate siblings — the viewer lives
          inside PostGenerationResult). Only the LOCATION moves (sibling → prop):
          the launcher keeps ownership of the share-token gate, the `key`, the
          `token`/`prototypeId`, and the `onApply → setApplyTarget` wiring, all
          byte-identical. Comments are addressed by the share token, so the node
          is built only once the prototype is shared (`result.share_token`),
          else null → no comments cell. The public mount still lives in
          PublicTokenViewer (P3-03); `onApply` enables the Apply→IterateComposer
          handoff (absent on the public mount → no Apply). */}
      {/* Post-generation canvas: IterateComposer lives in PostGenerationResult's
          `iterate` slot (the left region of the 3-region canvas layout), and
          CommentsPanel in its `comments` slot (the right region). The
          PrototypeViewer + thin toolbar occupy the center region. PRD sections
          The left column is a live-only conversation thread; the PRD title is
          threaded for the breadcrumb / left-column header. */}
      {result && (
        <PostGenerationResult
          key={result.id}
          prototype={result}
          prdTitle={prdTitle}
          serverCommentIds={serverCommentIds}
          comments={
            result.share_token ? (
              <CommentsPanel
                key={`comments-${result.id}`}
                token={result.share_token}
                prototypeId={result.id}
                onApply={(comment) => setApplyTarget?.(comment)}
                onCommentsLoaded={setServerCommentIds}
              />
            ) : null
          }
          iterate={
            <IterateComposer
              key={`iterate-${result.id}`}
              prototypeId={result.id}
              isComplete={result.is_complete ?? false}
              applyTarget={applyTarget}
              onClearApply={() => setApplyTarget?.(null)}
              onIterated={onIterated}
            />
          }
          onShared={onShared}
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
        onKickoff,
      })}
    </div>
  )
}

/**
 * Navigates to the in-tab canvas (`/prototype?prd=<id>`) when the preview card is
 * opened. Reads `useRouter` from context, so it is mounted ONLY once a navigation
 * is requested (a non-null `prdId`): that keeps `DesignAgentLauncher` itself
 * renderable without a router context (its node-env tests render the bare
 * container, where no navigation is in flight). The push runs once per requested
 * PRD id (effect keyed on the id).
 */
function NavigateToCanvas({ prdId }: { prdId: number | null | undefined }) {
  const router = useRouter()
  useEffect(() => {
    router.push(prototypePath(prdId ?? undefined))
  }, [router, prdId])
  return null
}

/**
 * Public component. Owns the drawer open/close state locally and delegates
 * rendering to the pure view. `renderDrawer` is optional (defaults to the real
 * drawer) — production callers pass only `prdId` / `figmaFileKey`.
 */
export function DesignAgentLauncher({
  prdId,
  figmaFileKey,
  prdTitle = null,
  externalGeneratingId = null,
  renderDrawer,
}: DesignAgentLauncherProps & {
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<PrototypeRecord | null>(null)
  const [generatingId, setGeneratingId] = useState<number | null>(null)
  // The PRD's existing ready prototype (resolved read-only via getByPrd), or
  // null. Resolved once on mount; degrades to null when no ready prototype exists
  // (getByPrd swallows the 404 → null) so the card simply does not render and no
  // generation is kicked.
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  // The PRD id to navigate to the in-tab canvas for, set when the preview card is
  // opened (`/prototype?prd=<id>`). Null until the user opens the existing
  // prototype; the navigation runs declaratively via <NavigateToCanvas>.
  const [navPrdId, setNavPrdId] = useState<number | null>(null)
  // P6-08 (Fix #11): the last generation attempt's failure, or null. A non-null
  // value renders the persistent GenerationErrorBanner (replacing the old silent
  // revert). Kept INDEPENDENT of `result` so a failed retry after a prior success
  // shows the banner without wiping the previously-good prototype (AC5).
  const [failure, setFailure] = useState<{ message: string } | null>(null)
  // P3-14 (F10): lifted so CommentsPanel's Apply sets it and IterateComposer
  // reads it as its pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)

  // dedup: the canonical server comment ids from the mounted CommentsPanel.
  // Lifted here because the launcher owns BOTH the CommentsPanel (source) and the
  // PostGenerationResult (consumer, which threads them to the pin layer). A saved
  // pin whose comment is in this set has its local card suppressed (canvas dot
  // stays) so a saved comment renders exactly once. `setServerCommentIds` is a
  // stable setter → safe to pass straight as CommentsPanel's onCommentsLoaded.
  const [serverCommentIds, setServerCommentIds] = useState<number[]>([])

  // On a successful generation, mount the result view AND clear any prior failure
  // banner. On failure, STOP discarding it (the pre-P6-08 bug): set the single
  // `failure` slot so the banner surfaces the reason — `result` is left intact so
  // a previously-good prototype survives a failed retry (AC5). A second failure
  // REPLACES the slot (no stacking — AC9). `resultFromGeneration` still owns the
  // success-path mapping; `failureFromGeneration` owns the failure-path mapping.
  const handleGenerated = (outcome: DesignAgentGenResult) => {
    const next = resultFromGeneration(outcome)
    setGeneratingId(null)
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

  // P6-20 (#14): after a bare Share (no iterate), `bundle_url` / `pending_question`
  // do NOT change, so `pollUntilAdvanced` would never resolve. The share endpoint
  // sets `share_token` synchronously, so single-shot re-fetch the SAME id and
  // replace `result` → `result.share_token` goes live and the share-gated
  // CommentsPanel mounts with no re-mount. Distinct from `refreshResult`
  // (iterate/clarify), whose race-gate is left intact (AC5).
  const refreshShareToken = async () => {
    const fresh = await refreshShareTokenStep(result?.id ?? null, designAgentApi)
    if (fresh) setResult(fresh)
  }

  // There is deliberately no auto-mount effect seeding `result` from the PRD's
  // existing prototype: the post-generation 3-region canvas must not auto-render
  // on the PRD screen. `result` starts null and is set only by the in-launcher
  // drawer/iterate/share flows (handleGenerated / refreshResult /
  // refreshShareToken), so the Design section is empty on PRD load (an existing
  // prototype surfaces as the preview card, not an auto-mounted canvas).

  // Read-only existence check on mount. `getByPrd` hits
  // `GET /v1/design-agent/by-prd/{prd_id}` and swallows a 404 → null, so this
  // never kicks a generation and degrades to "no card / no View label"
  // gracefully. Only a genuinely-ready prototype with a bundle_url is adopted for
  // the preview card.
  useEffect(() => {
    let cancelled = false
    designAgentApi
      .getByPrd(prdId)
      .then((proto) => {
        if (cancelled) return
        if (proto && proto.status === "ready" && proto.bundle_url) {
          setExisting(proto)
        }
      })
      .catch(() => {
        /* degrade silently — no card, label stays Generate */
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  const deleteExisting = async () => {
    if (!existing) return
    await designAgentApi.delete(existing.id)
    setExisting(null)
  }

  // Open the existing prototype in the in-tab canvas (`/prototype?prd=<id>`). The
  // navigation runs declaratively via <NavigateToCanvas> (mounted in the returned
  // tree once a target id is set) rather than inline here: this container is
  // rendered without a router context in its node-env tests, so reading
  // `useRouter()` directly in the container would be unsafe. Mounting the
  // navigator only once a target is set keeps the container renderable in those
  // tests (where no navigation is in flight) while still pushing the route in the
  // app. The existing prototype shares this PRD, so navigate by `prdId`.
  const openExisting = () => {
    if (existing) setNavPrdId(prdId)
  }

  return (
    <>
      <DesignAgentLauncherView
        prdId={prdId}
        figmaFileKey={figmaFileKey}
        open={open}
        setOpen={setOpen}
        result={result}
        onGenerated={handleGenerated}
        generatingId={generatingId}
        externalGeneratingId={externalGeneratingId}
        onKickoff={setGeneratingId}
        failure={failure}
        onRetry={handleRetry}
        applyTarget={applyTarget}
        setApplyTarget={setApplyTarget}
        serverCommentIds={serverCommentIds}
        setServerCommentIds={setServerCommentIds}
        onIterated={refreshResult}
        onAnswered={refreshResult}
        onShared={refreshShareToken}
        existing={existing}
        prdTitle={prdTitle}
        onOpenExisting={openExisting}
        onDeleteExisting={deleteExisting}
        renderDrawer={renderDrawer}
      />
      {/* Once the preview card is opened, navigate to the in-tab canvas
          (`/prototype?prd=<id>`). Mounted only while a target id is set — see
          NavigateToCanvas. */}
      {navPrdId != null && <NavigateToCanvas prdId={navPrdId} />}
    </>
  )
}

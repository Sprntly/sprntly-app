"use client"

/**
 * P2-12 — post-generation result surface for the SIGNED-IN app.
 *
 * After the launcher's drawer reports a successful generation
 * (`{ ok: true, prototype }`), the launcher mounts this inside its existing
 * `contentEditable={false}` boundary. It mounts the EDITABLE flavour of the
 * P2-10 chrome — `CompletionBar` (Mark Complete / Resume / Download / Copy) +
 * `ShareMenu` (Private / Public / Passcode + copy link) — distinct from the
 * public `/p/<token>` viewer, which mounts `CompletionBar editable={false}`.
 *
 * Both P2-10 components are reused UNMODIFIED (no prop-shape change — AC6).
 *
 * Testability split mirrors `CompletionBar` / `DesignAgentDrawer`: the pure
 * markup lives in `PostGenerationResultView` (SSR-renderable via
 * `renderToStaticMarkup` under the repo's node-env vitest — no jsdom /
 * @testing-library), and the container (`PostGenerationResult`) owns the local
 * `is_complete` copy so the view reflects `CompletionBar.onStateChange` lock
 * changes without a page reload (AC4).
 *
 * Per BUILD.md §6 this adds NO CSS to the hot `globals.css`; it reuses repo
 * class names (`btn`) + the `completion-bar` / `share-menu` classNames P2-10
 * introduced.
 */

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react"
import { CompletionBar } from "./CompletionBar"
import { useHandoffActions, STALE_MESSAGE } from "./handoff-actions"
import { ShareMenu, type ShareMode } from "./ShareMenu"
import { PrototypeViewer, type Platform } from "./PrototypeViewer"
// ManualEditOverlay import dropped —
// its trigger is no longer mounted on the canvas (the component file is kept).
// The left column is now a LIVE-ONLY agent-conversation thread (named turns +
// composer); the PRD panel was removed entirely (live-only, no PRD context dump).
// the clarifying-question answer
// surface, mounted in the LEFT sidebar near the composer when the iterate run
// returns a `pending_question` (see the launcher's original conditional mount).
import { ClarifyingQuestionSurface } from "./ClarifyingQuestionSurface"
// the live agent-flow activity stream
// (the user request → working steps → done/question/error transcript) shown in
// the LEFT panel while/after an iterate runs.
import { IterateActivityStream } from "./IterateActivityStream"
// C1 Slice B — the mark-and-comment view, extracted from this file: the stage
// overlay + pin layer (CENTER) + the pin-comment rows (RIGHT). See module header.
import { MarkOverlay, PinLayer, PrototypeMarkLayer } from "./PrototypeMarkLayer"
// C2b — the shared mark-and-comment pin engine, extracted from this container so
// the public viewer drives the SAME implementation. The create-fn is injected
// per surface (signed-in: withAuthRetry(createComment); public: createCommentByToken).
import { usePinMarking } from "./usePinMarking"
// Bundle-proxy view-grant flow: the authed iframe now loads from the same-origin
// proxy, so the app must mint the `da_view_grant` cookie (bearer-authed POST)
// BEFORE setting the iframe `src`. The hook gates `bundle_url` until the grant
// exists and re-mints ONCE (bounded) on a later asset 401. The public
// `/p/<token>` viewer does NOT use this surface, so it is untouched.
import { useViewGrant } from "./useViewGrant"
import type { PendingQuestion } from "../../lib/api"
import {
  IconMessage,
  IconClose,
  IconDocument,
  IconFullscreen,
  IconChevronLeft,
  IconChevronDown,
  IconShare,
  IconMore,
  IconPin,
  IconCopy,
  IconUndo,
} from "../shared/app-icons"
// subtle breadcrumb at the top of the
// canvas ("PRDs / {PRD title} / Design"). Clicking a crumb closes the canvas and
// returns to the PRD (reuses onDone / closeCanvas). Pure leaf → SSR-renderable.
function DaBreadcrumb({
  prdTitle,
  onDone,
}: {
  prdTitle: string | null
  onDone?: () => void
}) {
  // The crumb is interactive only when there is somewhere to go back to (onDone).
  const Crumb = ({ label }: { label: string }) =>
    onDone ? (
      <button
        type="button"
        className="da-breadcrumb-link"
        onClick={() => onDone()}
      >
        {label}
      </button>
    ) : (
      <span className="da-breadcrumb-link">{label}</span>
    )
  return (
    <nav className="da-breadcrumb" data-testid="da-breadcrumb" aria-label="Breadcrumb">
      <Crumb label="PRDs" />
      <span className="da-breadcrumb-sep" aria-hidden="true">/</span>
      <Crumb label={prdTitle || "PRD"} />
      <span className="da-breadcrumb-sep" aria-hidden="true">/</span>
      <span className="da-breadcrumb-cur" aria-current="page">Design</span>
    </nav>
  )
}
import {
  designAgentApi,
  withAuthRetry,
  type CommentRecord,
  type PrototypeRecord,
} from "../../lib/api"

// a pin-anchored comment created via
// the mark-and-comment flow. `xPct`/`yPct` are the pin's position over the canvas
// stage (0–100, relative to the stage box) — persisted via `pin_x_pct`/`pin_y_pct`
// on the comment create, driving the durable pin position.
// `saved` flips true once the authed create endpoint confirms; `error` surfaces a
// failed create while the optimistic pin/row stays visible so nothing is lost.
export type PinComment = {
  n: number
  xPct: number
  yPct: number
  draft: string
  body: string
  saved: boolean
  busy: boolean
  error: string | null
  // author + timestamp captured at
  // create time so the saved row can show WHO + WHEN + an avatar (David's
  // `.proto-comment-au` / `.proto-comment-time` / `.pc-av`). The authed create
  // attributes the author server-side ("demo"); we mirror the returned record's
  // author/created_at onto the pin so the optimistic row shows real identity.
  author?: string | null
  createdAt?: string | null
  // a saved pin comment can be
  // Applied (pre-fill composer + resolve) or Ignored (resolve only). `resolved`
  // moves the row to the muted/collapsed state (David's `.resolved`).
  resolved?: boolean
  // stable JSX anchor resolved at the click point inside the bundle iframe;
  // null when the iframe is cross-origin or the click hit no anchored element.
  // Typed anchor object (anchor-id or xpath) replaces the old string field.
  anchor: { type: 'anchor-id' | 'xpath'; value: string } | null
  /** click position within the anchor element (0–100). Null for DB-loaded pins. */
  xPctInEl: number | null
  yPctInEl: number | null
  /** friendly label shown in the UI: e.g. '"Schedule Demo" button' */
  elementFriendly: string | null
  /** technical element context sent to the agent */
  elementTechnical: string | null
}

export type PostGenerationResultProps = {
  prototype: PrototypeRecord
  /** P6-13 (UX-3): optional comments node placed in the right cell of the
   *  two-column `design-pane` grid beside the viewer. The signed-in launcher
   *  passes its `<CommentsPanel>` here; the public `/p/<token>` viewer does NOT
   *  use this component (it composes its own chrome) → it passes nothing and the
   *  comments column is omitted. Null-by-default keeps the public shape intact. */
  comments?: ReactNode
  /** the iterate/change-request column node
   *  (the launcher's `<IterateComposer>`). Placed in the LEFT region of the
   *  3-region canvas. Optional → when absent (e.g. the public viewer) the left
   *  region is omitted and the canvas degrades to canvas + comments. */
  iterate?: ReactNode
  /** P6-20 (#14): forwarded to `<ShareMenu>` — fired after a successful Share so
   *  the launcher re-polls and `result.share_token` goes live (flipping the
   *  share-gated comments column on without a re-mount). Optional/defaulted so the
   *  public-viewer composition and existing direct calls keep type-checking. */
  onShared?: (token: string | null) => void
  /** the PRD title — KEPT (used by the breadcrumb, the in-tab title bar, and the
   *  left-column header). The PRD content panel itself was removed; only the title
   *  label survives. */
  prdTitle?: string | null
  /** the signed-in user's display name, used to label user turns in the live
   *  conversation thread ("{userName} · 2m ago"). Falls back to "You" when null.
   *  Sourced upstream from `content.userName`. */
  userName?: string | null
  /** the control-bar "Done" affordance — closes
   *  the full-screen canvas back to the PRD (ApproveModal.closeCanvas). */
  onDone?: () => void
  /** Apply a pin comment — pre-fill
   *  the LEFT IterateComposer with a pin-context edit instruction. Threaded from
   *  ApproveModal (sets `applyTarget`, the same seam CommentsPanel's Apply uses).
   *  The synthetic CommentRecord carries the composed instruction as its `body`.
   *  Kept for back-compat; superseded by `onPinIterate` when that is supplied. */
  onPinApply?: (comment: CommentRecord) => void
  /** Apply a pin comment by running it
   *  through the canvas's SHARED iterate runner IMMEDIATELY (pin-context string +
   *  comment body as the instruction) instead of pre-filling the composer. When
   *  supplied it takes precedence over `onPinApply`. */
  onPinIterate?: (instruction: string, appliedCommentId?: number | null) => void
  /** the live agent-flow activity for
   *  the LEFT panel — the user request, working steps, completion / clarifying
   *  question / error — driven by the shared runner (useIterateRun). */
  iterateActivity?: import("./useIterateRun").ActivityEvent[]
  /** true while an iterate is running. */
  iterateRunning?: boolean
  /** a run-level error (also appended
   *  to the activity stream). */
  iterateError?: string | null
  /** the agent's clarifying question
   *  when the run paused — surfaced INLINE in the left-panel flow. */
  iteratePendingQuestion?: import("../../lib/api").PendingQuestion | null
  /** answer the clarifying question →
   *  continues the iterate via the shared runner. */
  onAnswerQuestion?: (answer: string) => void | Promise<void>
  /** bumped on each completed iterate
   *  to force the center iframe to reload the rebuilt bundle (cache-bust). */
  bundleReloadNonce?: number
  /** Called when Mark Complete or Resume fires so the parent can merge the new
   *  `is_complete` value into its own copy of the record without a round-trip.
   *  Optional — existing callers that omit it keep type-checking. */
  onStateChange?: (state: { isComplete: boolean }) => void
  /** When true, the full-screen prototype view is open on mount; defaults false
   *  so existing consumers are unaffected. */
  defaultFullscreen?: boolean
  /** Optional notification callback — fired whenever the fullscreen state
   *  toggles. Receives the new open value. The parent can use this to sync
   *  external state (e.g. a URL query param) without taking control of the
   *  internal `fullscreenOpen` state. */
  onFullscreenChange?: (open: boolean) => void
  /** When true (the in-tab /prototype route), the top breadcrumb is suppressed — the back affordance lives in the app chrome-strip title instead. Absent (launcher/overlay) → the breadcrumb renders. */
  hideBreadcrumb?: boolean
  /** True only for the in-tab /prototype editor. Switches the control bar to the
   *  state-driven handoff buttons (Mark Complete / Export / Undo) and drops the
   *  "..." Actions popover + Done button. Launcher/public keep the classic bar. */
  isInTab?: boolean
  /** Navigate back to the previous page (in-tab title-bar back button). Absent on launcher/public paths. */
  onBack?: () => void
}

export type PostGenerationResultViewProps = {
  prototypeId: number
  isComplete: boolean
  shareMode: ShareMode
  shareToken: string | null
  /** The GATED proxy bundle url: null until the view-grant cookie has been
   *  minted (the container's useViewGrant gates it). When null the viewer cell
   *  doesn't render — the iframe `src` is never set without a credential. */
  bundleUrl: string | null
  /** A terminal view-grant error (initial mint failed or the bounded re-mint was
   *  exhausted). When set the canvas surfaces a non-blocking error affordance. */
  bundleGrantError?: string | null
  /** Called when an authed iframe asset load 401s (grant missing/expired) so the
   *  container can re-mint ONCE (bounded). Absent on the public surface. */
  onBundleAssetError?: () => void
  /** Bumped on a successful re-mint so the iframe is forced to reload the
   *  now-re-authorized bundle. Folded into the viewer remount key. */
  bundleGrantReloadKey?: number
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
  /** P6-13 (UX-3): comments node for the right cell of the `design-pane` grid.
   *  When absent, the viewer renders full-width (no comments cell, no grid). */
  comments?: ReactNode
  /** iterate/change-request column node for the
   *  LEFT region of the 3-region canvas. When absent, the left region is omitted. */
  iterate?: ReactNode
  /** P6-20 (#14): forwarded to `<ShareMenu onShared>` so a successful Share
   *  re-polls the launcher result. Optional/defaulted. */
  onShared?: (token: string | null) => void
  /** P6-16 (UX-6): full-screen overlay open state + open/close handlers, owned by
   *  the `PostGenerationResult` container (client-only `useState`). Threaded so the
   *  pure view stays SSR-renderable: the always-shown trigger calls
   *  `onOpenFullscreen`, the overlay Close calls `onCloseFullscreen`, and
   *  `fullscreenOpen` decides whether the overlay renders (and, by the
   *  selector-collision guard, whether the inline viewer stays mounted).
   *  Optional/defaulted → existing direct view calls and the public composition
   *  keep type-checking. */
  fullscreenOpen?: boolean
  onOpenFullscreen?: () => void
  onCloseFullscreen?: () => void
  /** PRD title for the breadcrumb / in-tab title bar / left-column header
   *  + the control-bar Done affordance. The PRD content panel was removed. */
  prdTitle?: string | null
  /** signed-in user's display name for user-turn labels in the live thread. */
  userName?: string | null
  onDone?: () => void
  /** collapsible-panel + control-bar state, owned
   *  by the container and threaded into the SSR-renderable pure view (matching the
   *  `fullscreenOpen` threading pattern). LEFT sidebar (PRD + iterate) is OPEN by
   *  default; RIGHT comments sidebar is COLLAPSED by default; the Desktop/Mobile
   *  toggle lifted out of PrototypeViewer lives here. */
  leftOpen?: boolean
  onToggleLeft?: () => void
  commentsOpen?: boolean
  onToggleComments?: () => void
  platform?: Platform
  onPlatformChange?: (platform: Platform) => void
  /** mark-and-comment pin flow state,
   *  owned by the container and threaded into the SSR-renderable view (same
   *  pattern as `fullscreenOpen`). `markMode` toggles the crosshair overlay; the
   *  overlay click reports stage-relative x/y via `onStageClick`; `pins` render
   *  the numbered teardrops + the right-sidebar comment rows; the row handlers
   *  edit/submit/remove a pin's comment. */
  markMode?: boolean
  onToggleMark?: () => void
  onStageClick?: (xPct: number, yPct: number, viewportX: number, viewportY: number, anchor: { type: 'anchor-id' | 'xpath'; value: string } | null) => void
  pins?: PinComment[]
  onPinDraftChange?: (n: number, value: string) => void
  onPinSubmit?: (n: number) => void
  onPinRemove?: (n: number) => void
  /** Apply a saved pin comment
   *  (pre-fill composer w/ pin context + resolve) / Ignore (resolve only). */
  onPinApply?: (n: number) => void
  onPinIgnore?: (n: number) => void
  /** Resolve a saved pin comment from the consolidated `.comment-resolve-btn`
   *  header control (resolve-only semantic, like Ignore). */
  onPinResolve?: (n: number) => void
  /** the clarifying-question
   *  surface node (the container's <ClarifyingQuestionSurface>). Mounted in the
   *  LEFT sidebar just above the IterateComposer; when null nothing renders. */
  clarifying?: ReactNode
  /** the live agent-flow activity for
   *  the LEFT panel, plus the run-paused clarifying answer surface. */
  iterateActivity?: import("./useIterateRun").ActivityEvent[]
  iterateRunning?: boolean
  iteratePendingQuestion?: import("../../lib/api").PendingQuestion | null
  onAnswerQuestion?: (answer: string) => void | Promise<void>
  /** pin Apply → immediate iterate. */
  onPinIterate?: (instruction: string, appliedCommentId?: number | null) => void
  /** cache-bust nonce → forces the
   *  iframe to reload the rebuilt bundle on each completed iterate. */
  bundleReloadNonce?: number
  /** element-anchored computed positions for pins that have a `resolvedAnchorId`.
   *  Keyed by pin.n; when present overrides the static xPct/yPct so pins track
   *  the DOM element they were placed on across scroll and resize events. */
  computedPinPositions?: Record<number, { xPct: number; yPct: number }>
  /** Ref forwarded from the container so the activity-scroll useEffect (which
   *  can only live in a client component) can scrollIntoView the activity section
   *  when the first SSE event arrives. Threaded through the pure view without a
   *  hook so the view stays SSR-renderable. */
  leftPanelRef?: RefObject<HTMLDivElement | null>
  /** When true (the in-tab /prototype route), the top breadcrumb is suppressed — the back affordance lives in the app chrome-strip title instead. Absent (launcher/overlay) → the breadcrumb renders. */
  hideBreadcrumb?: boolean
  /** True only for the in-tab /prototype editor. Switches the control bar to the
   *  state-driven handoff buttons (Mark Complete / Export / Undo) and drops the
   *  "..." Actions popover + Done button. Launcher/public keep the classic bar. */
  isInTab?: boolean
  /** Navigate back to the previous page (in-tab title-bar back button). Absent on launcher/public paths. */
  onBack?: () => void
}

/**
 * P6-05 (#5) — guarded re-seed decision for the local `isComplete` copy.
 *
 * After an iterate/clarify advances the SAME prototype id to a new checkpoint,
 * the launcher refetches and hands a fresh `prototype` prop down. We want the
 * iframe + "View prototype" href to follow the new `bundle_url` (those read the
 * prop directly, so they update for free), and we want the local `isComplete`
 * copy to track a genuine checkpoint advance — WITHOUT clobbering a user's local
 * Mark-Complete (`onStateChange`), which mutates `isComplete` independently of
 * the prop.
 *
 * Rule: re-seed ONLY when `bundle_url` actually changed AND the new prop's
 * `is_complete` differs from the last PROP-DERIVED baseline (tracked in refs, so
 * a user's local toggle between prop changes is never the baseline). A bundle
 * change whose prop `is_complete` equals the baseline advances the baseline but
 * leaves the local copy alone. No bundle change → no-op.
 *
 * Pure (returns the next baseline + whether to call setIsComplete) so the
 * sequence is unit-testable without a DOM (the repo's vitest env is `node`).
 */
export type ReseedBaseline = { bundle: string | null; complete: boolean }

export function reseedStep(
  baseline: ReseedBaseline,
  nextBundle: string | null,
  nextComplete: boolean,
): { baseline: ReseedBaseline; setComplete: boolean | null } {
  if (nextBundle !== baseline.bundle) {
    const advanced = { bundle: nextBundle, complete: nextComplete }
    return {
      baseline: advanced,
      setComplete: nextComplete !== baseline.complete ? nextComplete : null,
    }
  }
  return { baseline, setComplete: null }
}

/**
 * Resolve the "View prototype" href: the built bundle if present, else the
 * public `/p/<slug>/<token>` link once the prototype has been shared. Returns
 * null when neither is available yet (nothing to link to → the affordance hides).
 */
export function resolveViewHref(
  bundleUrl: string | null,
  shareToken: string | null,
  // INTENTIONAL slug exposure (intentional, reviewed): companies.slug is the cosmetic /p/<slug>/<token> segment — the one surface overriding the "slug is internal" convention.
  companySlug: string,
): string | null {
  if (bundleUrl) return bundleUrl
  if (shareToken) return `/p/${companySlug}/${shareToken}`
  return null
}

/**
 * Derive the viewer iframe `src`. Reads the live bundle path directly (never a
 * captured/cached base) so that when an iterate rebuild lands at a NEW bundle
 * path, the src follows it onto the new build. The reload nonce appends a
 * cache-bust query for the same-path edge — a rebuild that overwrites the bundle
 * at the existing path — and is omitted on the clean first load (keeps the SSR
 * output stable until a rebuild has run). Returns null when no bundle exists yet.
 * Pure → unit-testable without a DOM (the repo's vitest env is `node`).
 */
export function viewerSrc(
  bundleUrl: string | null,
  reloadNonce: number,
): string | null {
  if (!bundleUrl) return bundleUrl
  if (reloadNonce > 0) {
    return `${bundleUrl}${bundleUrl.includes("?") ? "&" : "?"}v=${reloadNonce}`
  }
  return bundleUrl
}

/**
 * Derive the viewer remount `key`. The key changes whenever the bundle path
 * advances to a new build OR a same-path rebuild bumps the reload nonce, so React
 * mounts a fresh iframe at the current bundle in both cases. Keying on the path
 * (not the nonce alone) is the fix for a build swap whose nonce did not move in
 * lockstep with the refetch that delivers the new path: the new path by itself
 * now forces the fresh mount, so the canvas never sticks on the prior build.
 * Pure → unit-testable without a DOM.
 */
export function viewerRemountKey(
  bundleUrl: string | null,
  reloadNonce: number,
): string {
  return `viewer-${bundleUrl ?? "none"}-${reloadNonce}`
}

/**
 * a tiny click-outside-dismiss popover used by
 * the compact control bar (Share + Actions). Models David's `.pct-export-menu`
 * dropdown: a trigger button + an absolutely-positioned panel that closes on an
 * outside click or Escape. Self-contained (own `useState`/`useEffect`) so it
 * keeps the bar markup flat; the bar itself stays SSR-renderable because this is
 * a leaf client component (the bar renders it only inside the client container).
 */
function DaPopover({
  trigger,
  align = "right",
  children,
  testId,
}: {
  trigger: (open: boolean) => ReactNode
  align?: "left" | "right"
  children: ReactNode
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDocClick)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  return (
    <div className="da-popover" ref={rootRef} data-testid={testId}>
      <span onClick={() => setOpen((v) => !v)}>{trigger(open)}</span>
      {open && (
        <div className={`da-popover-panel da-popover-panel--${align}`} role="menu">
          {children}
        </div>
      )}
    </div>
  )
}

/**
 * the COMPACT top control bar (≈54px), modelled
 * on David's `.proto-canvas-top` toolbar. A single horizontal row, vertically
 * centred:
 *   LEFT cluster  — the Desktop/Mobile platform toggle (segmented control,
 *     lifted out of PrototypeViewer; this bar runs the viewer CONTROLLED).
 *   RIGHT cluster — compact `.da-ctl-icon` tools, in order:
 *     • comments-toggle (always toggles the right sidebar — Problem 2),
 *     • Share        → DROPDOWN popover wrapping the existing <ShareMenu>
 *                      (Private/Public/Passcode radios + copy-link + passcode
 *                      live INSIDE the popover, NOT expanded inline),
 *     • Actions (⋯)  → DROPDOWN popover wrapping the existing <CompletionBar>
 *                      (Mark Complete / Export to Claude Code / Download .md /
 *                      Copy) so the handoff stack never bloats the bar,
 *     • Fullscreen   → reuses the existing onOpenFullscreen trigger,
 *     • Done         → closes the canvas back to the PRD (onDone).
 * Forest-green tokens only; no coral. The full CompletionBar + full ShareMenu
 * are NEVER rendered directly in the bar row — only inside their popovers.
 */
/**
 * In-tab handoff buttons cluster (Mark Complete / Export / Copy / Undo).
 * Extracted into its own component so DaControlBar itself stays hook-free and
 * can be called as a plain function in node-env vitest without a React renderer.
 * All hooks (useHandoffActions, useState) live HERE, not in DaControlBar.
 *
 * The stale-handoff banner is rendered here (after the action buttons) and
 * positioned BELOW the control bar via `position: absolute` + the parent
 * `.da-controlbar` having `position: relative` (set in design-agent.css).
 */
function InTabHandoffCluster({
  prototypeId,
  isComplete,
  onStateChange,
}: {
  prototypeId: number
  isComplete: boolean
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
}) {
  const { busy, markComplete, resume, download, copy } = useHandoffActions({
    prototypeId,
    onStateChange,
  })
  const [stale, setStale] = useState(false)
  return (
    <>
      {!isComplete ? (
        <button
          type="button"
          className="btn btn-accent da-ctl-done"
          data-testid="da-mark-complete"
          disabled={busy}
          onClick={async () => { await markComplete(); setStale(false) }}
        >
          Mark Complete
        </button>
      ) : (
        <>
          <button
            type="button"
            className="btn da-ctl-export"
            data-testid="da-export"
            disabled={busy}
            onClick={() => download()}
          >
            Export
          </button>
          <button
            type="button"
            className="da-ctl-icon"
            data-testid="da-copy"
            title="Copy markdown"
            aria-label="Copy markdown"
            disabled={busy}
            onClick={() => copy()}
          >
            <IconCopy size={16} />
          </button>
          <button
            type="button"
            className="btn da-ctl-undo"
            data-testid="da-undo"
            disabled={busy}
            onClick={async () => {
              const r = await resume()
              if (r) setStale(!!r.handoffs_flagged_stale)
            }}
          >
            <IconUndo size={16} /> Undo
          </button>
        </>
      )}
      {stale && (
        <div className="da-stale-row" data-testid="da-stale-row">
          <div className="stale-banner" data-testid="stale-banner-intab">
            {STALE_MESSAGE}
          </div>
        </div>
      )}
    </>
  )
}

export function DaControlBar({
  prototypeId,
  isComplete,
  onStateChange,
  shareMode,
  shareToken,
  onShared,
  platform,
  onPlatformChange,
  commentsOpen,
  onToggleComments,
  markMode,
  onToggleMark,
  canOpen,
  onOpenFullscreen,
  onDone,
  isInTab,
  onBack,
  prdTitle,
}: {
  prototypeId: number
  isComplete: boolean
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
  shareMode: ShareMode
  shareToken: string | null
  onShared?: (token: string | null) => void
  platform: Platform
  onPlatformChange?: (platform: Platform) => void
  commentsOpen: boolean
  onToggleComments?: () => void
  /** mark-and-comment tool state. */
  markMode: boolean
  onToggleMark?: () => void
  canOpen: boolean
  onOpenFullscreen?: () => void
  onDone?: () => void
  /** True only for the in-tab /prototype editor. When set, replaces the
   *  "..." Actions popover + Done with state-driven handoff buttons. */
  isInTab?: boolean
  /** Navigate back to the previous page (in-tab title-bar back button). */
  onBack?: () => void
  /** PRD title shown in the in-tab title bar next to the back button. */
  prdTitle?: string | null
}) {
  // NOTE: DaControlBar is intentionally hook-free so it can be called as a
  // plain function in node-env vitest (the test suite calls it directly to
  // walk the returned element tree). All hook usage for the in-tab path lives
  // in <InTabHandoffCluster> above.
  return (
    <div className={`da-controlbar${isInTab ? " da-controlbar--titlebar" : ""}`} data-testid="da-controlbar">
      {/* LEFT cluster — compact Desktop/Mobile segmented control, + back button when in-tab. */}
      <div className="da-controlbar-l">
        {isInTab && (
          <button
            type="button"
            className="da-ctl-back"
            data-testid="da-titlebar-back"
            title="Back"
            aria-label="Back"
            onClick={() => onBack?.()}
          >
            <IconChevronLeft size={16} />
          </button>
        )}
        {isInTab && (
          <span className="da-titlebar-title" title={prdTitle ?? undefined} data-testid="da-titlebar-title">
            {prdTitle ?? "Untitled prototype"}
          </span>
        )}
        <div
          className="platform-toggle da-controlbar-platform"
          role="group"
          aria-label="Preview platform"
        >
          <button
            type="button"
            className={platform === "desktop" ? "active" : ""}
            aria-pressed={platform === "desktop"}
            onClick={() => onPlatformChange?.("desktop")}
          >
            Desktop
          </button>
          <button
            type="button"
            className={platform === "mobile" ? "active" : ""}
            aria-pressed={platform === "mobile"}
            onClick={() => onPlatformChange?.("mobile")}
          >
            Mobile
          </button>
        </div>
      </div>

      {/* RIGHT cluster — compact icon/button tools. */}
      <div className="da-controlbar-r">
        {/* Mark & comment tool
            (David's `#markToggle`, `ti-pin`). Enters mark mode → crosshair +
            brand ring on the stage; clicking the prototype drops a numbered pin
            and opens a comment composer. `.on` reflects active mark mode. */}
        <button
          type="button"
          className={`da-ctl-icon${markMode ? " on" : ""}`}
          aria-pressed={markMode}
          data-testid="da-mark-toggle"
          title="Mark & comment"
          disabled={!canOpen}
          onClick={() => onToggleMark?.()}
        >
          <IconPin size={15} />
          {!isInTab && <span className="da-ctl-label">Mark</span>}
        </button>

        {/* comments-toggle — ALWAYS toggles the right sidebar (Problem 2). */}
        <button
          type="button"
          className={`da-ctl-icon${commentsOpen ? " on" : ""}`}
          aria-pressed={commentsOpen}
          data-testid="da-comments-toggle"
          title="Comments"
          onClick={() => onToggleComments?.()}
        >
          <IconMessage size={16} />
          {!isInTab && <span className="da-ctl-label">Comments</span>}
        </button>

        {/* Share — compact button opening a DROPDOWN with the visibility options
            (the full ShareMenu is rendered INSIDE the popover, never inline). */}
        <DaPopover
          align="right"
          testId="da-share-popover"
          trigger={(open) => (
            <button
              type="button"
              className={`da-ctl-icon${open ? " on" : ""}`}
              title="Share"
              data-testid="da-share-toggle"
            >
              <IconShare size={15} />
              {!isInTab && <span className="da-ctl-label">Share</span>}
              {!isInTab && <IconChevronDown size={13} />}
            </button>
          )}
        >
          {/* the restyled ShareMenu renders its
              own `.share-title` ("Share prototype") + clean panel, so the generic
              `.da-popover-title` is dropped here to avoid a duplicate heading. */}
          <ShareMenu
            prototypeId={prototypeId}
            initialMode={shareMode}
            initialToken={shareToken}
            onShared={onShared}
          />
        </DaPopover>

        {/* Actions / handoff cluster — conditional on isInTab.
            isInTab=TRUE  → <InTabHandoffCluster> (hook-owning child component)
                            with state-driven Mark Complete / Export / Copy / Undo
                            + its own stale-banner below the cluster.
            isInTab=FALSE → classic "..." Actions popover + Done (launcher path). */}
        {isInTab ? (
          <InTabHandoffCluster
            prototypeId={prototypeId}
            isComplete={isComplete}
            onStateChange={onStateChange}
          />
        ) : (
          <>
            {/* Actions overflow (⋯) — Mark Complete / Export / Download / Copy, kept
                reachable but compact (the full CompletionBar lives in the popover). */}
            <DaPopover
              align="right"
              testId="da-actions-popover"
              trigger={(open) => (
                <button
                  type="button"
                  className={`da-ctl-icon da-ctl-icon--square${open ? " on" : ""}`}
                  title="Actions"
                  aria-label="Actions"
                  data-testid="da-actions-toggle"
                >
                  <IconMore size={16} />
                </button>
              )}
            >
              <div className="da-popover-title">Handoff</div>
              <CompletionBar
                prototypeId={prototypeId}
                isComplete={isComplete}
                onStateChange={onStateChange}
              />
            </DaPopover>

            {/* Done — closes the canvas back to the PRD. */}
            {onDone && (
              <button
                type="button"
                className="btn btn-accent da-ctl-done"
                data-testid="da-control-done"
                onClick={() => onDone()}
              >
                Done
              </button>
            )}
          </>
        )}

        {/* Fullscreen — reuses the existing open-fullscreen trigger. */}
        <button
          type="button"
          className="da-ctl-icon da-ctl-icon--square proto-fullscreen-trigger"
          title={canOpen ? "View full screen" : "Prototype building…"}
          aria-label="View full screen"
          data-testid="proto-fullscreen-trigger"
          disabled={!canOpen}
          onClick={() => onOpenFullscreen?.()}
        >
          <IconFullscreen size={15} />
        </button>
      </div>
    </div>
  )
}

/**
 * the INLINE clarifying-answer surface
 * for the left-panel flow. When the shared iterate runner pauses on a
 * `pending_question`, this renders RIGHT IN THE ACTIVITY STREAM (not as a
 * detached surface): the question is already shown as an agent message above; this
 * is the answer affordance (choice buttons when the question carries `choices`,
 * else a free-text box). Answering routes a continuation iterate via the runner
 * (onAnswer → useIterateRun.answerQuestion). Local input state only → a leaf
 * client component (the file is already "use client").
 */
function friendlyChoiceLabel(choice: string): string {
  return choice.replace(/\s*\([^)]{1,50}\)\s*$/, '').trim() || choice
}

function InlineClarifyAnswer({
  question,
  busy,
  onAnswer,
}: {
  question: PendingQuestion
  busy: boolean
  onAnswer: (answer: string) => void | Promise<void>
}) {
  const [answer, setAnswer] = useState("")
  const choices = question.choices ?? null
  const hasChoices = !!choices && choices.length > 0
  return (
    <div
      className="da-activity-answer"
      data-testid="da-activity-answer"
      role="region"
      aria-label="Answer the Design Agent"
    >
      {question.context && (
        <p className="da-activity-answer-context">{question.context}</p>
      )}
      {hasChoices ? (
        <div className="da-activity-answer-choices">
          {choices!.map((choice, i) => (
            <button
              key={`${i}-${choice}`}
              type="button"
              className="da-activity-choice-btn"
              data-testid="da-activity-answer-choice"
              disabled={busy}
              onClick={() => void onAnswer(choice)}
            >
              {friendlyChoiceLabel(choice)}
            </button>
          ))}
        </div>
      ) : (
        <form
          className="da-activity-answer-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!answer.trim()) return
            void onAnswer(answer)
            setAnswer("")
          }}
        >
          <textarea
            className="da-activity-answer-input"
            data-testid="da-activity-answer-input"
            value={answer}
            placeholder="Answer the Design Agent…"
            onChange={(e) => setAnswer(e.target.value)}
          />
          <div className="da-activity-answer-actions">
            <button
              type="submit"
              className="btn btn-accent"
              data-testid="da-activity-answer-submit"
              disabled={busy || !answer.trim()}
            >
              Submit
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

function FullscreenOverlay({
  bundleUrl,
  isComplete,
  onCloseFullscreen,
  onAssetError,
}: {
  bundleUrl: string
  isComplete: boolean
  onCloseFullscreen?: () => void
  onAssetError?: () => void
}) {
  const fullscreenRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (fullscreenRef.current) {
      fullscreenRef.current.requestFullscreen().catch(() => {})
    }
  }, [])

  useEffect(() => {
    const handler = () => {
      if (!document.fullscreenElement) onCloseFullscreen?.()
    }
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [onCloseFullscreen])

  return (
    <div
      ref={fullscreenRef}
      className="proto-fullscreen"
      role="dialog"
      aria-modal="true"
      aria-label="Prototype full screen"
      data-testid="proto-fullscreen"
    >
      <button
        type="button"
        className="proto-fullscreen-close"
        aria-label="Close full screen"
        data-testid="proto-fullscreen-close"
        onClick={() => { document.exitFullscreen().catch(() => {}); onCloseFullscreen?.() }}
      >
        ×
      </button>
      <div className="proto-fullscreen-body">
        <PrototypeViewer bundleUrl={bundleUrl} isComplete={isComplete} onAssetError={onAssetError} />
      </div>
    </div>
  )
}

/**
 * Client-only wrapper for the `.da-left-activity` panel. Owns the scroll
 * sentinel ref so the pure `PostGenerationResultView` stays SSR-renderable
 * (no hooks in the pure view itself). Scrolls the sentinel into view whenever
 * `iterateActivity` grows so the latest step is always visible.
 */
function ActivityPanel({
  iterateActivity,
  iterateRunning,
  iteratePendingQuestion,
  onAnswerQuestion,
  userName,
}: {
  iterateActivity: import("./useIterateRun").ActivityEvent[]
  iterateRunning: boolean
  iteratePendingQuestion: import("../../lib/api").PendingQuestion | null
  onAnswerQuestion?: (answer: string) => void | Promise<void>
  userName?: string | null
}) {
  const activityEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    activityEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [iterateActivity])
  return (
    <div className="da-left-activity" data-testid="da-canvas-activity">
      <IterateActivityStream
        activity={iterateActivity}
        running={iterateRunning}
        userName={userName}
      />
      {iteratePendingQuestion && onAnswerQuestion && (
        <InlineClarifyAnswer
          question={iteratePendingQuestion}
          busy={iterateRunning}
          onAnswer={onAnswerQuestion}
        />
      )}
      <div ref={activityEndRef} />
    </div>
  )
}

/** Pure presentational view — no I/O of its own → SSR-renderable in node-env
 *  vitest. The container threads live `isComplete` + the `onStateChange`
 *  handler into it. */
export function PostGenerationResultView({
  prototypeId,
  isComplete,
  shareMode,
  shareToken,
  bundleUrl,
  bundleGrantError = null,
  onBundleAssetError,
  bundleGrantReloadKey = 0,
  onStateChange,
  comments,
  iterate,
  onShared,
  fullscreenOpen = false,
  onOpenFullscreen,
  onCloseFullscreen,
  prdTitle,
  userName,
  onDone,
  leftOpen = true,
  onToggleLeft,
  commentsOpen = false,
  onToggleComments,
  platform = "desktop",
  onPlatformChange,
  markMode = false,
  onToggleMark,
  onStageClick,
  pins = [],
  onPinDraftChange,
  onPinSubmit,
  onPinRemove,
  onPinApply,
  onPinIgnore,
  onPinResolve,
  clarifying,
  iterateActivity = [],
  iterateRunning = false,
  iteratePendingQuestion = null,
  onAnswerQuestion,
  onPinIterate,
  bundleReloadNonce = 0,
  computedPinPositions = {},
  leftPanelRef,
  hideBreadcrumb,
  isInTab,
  onBack,
}: PostGenerationResultViewProps) {
  // cache-bust the iframe src so a
  // rebuilt bundle reloads even when the backend overwrites it at the SAME url.
  // Only appends when the nonce has advanced (keeps the initial load url clean +
  // SSR output stable when no iterate has run). Preserves any existing query.
  const reloadBundleUrl = viewerSrc(bundleUrl, bundleReloadNonce)
  // P6-16 (UX-6): the primary View affordance is ALWAYS rendered (never a hidden
  // / dead link — the #6 bug). It is gated only on a built bundle existing:
  // enabled "View full screen" when `bundleUrl` is present, otherwise a DISABLED
  // "Prototype building…" control — never a removed element. `resolveViewHref`
  // (below) is KEPT but no longer consumed here; its null-return no longer hides
  // the control. The real shared URL stays reachable via ShareMenu, which sources
  // the company slug from `useCompany().activeCompany` for the /p/<slug>/<token>
  // link; `resolveViewHref` now takes that same slug for its (test-only) parity.
  const canOpen = bundleUrl != null
  // P4-10 — the EDITABLE viewer, rendered only when a built bundle exists. This
  // surface only renders inside (app)/AuthGate, so it is internal by
  // construction; passing the real numeric `prototypeId` into the overlay IS the
  // internal mount that makes manual-edit reachable. The overlay
  // reaches the same-origin iframe (`da-prototype-iframe`) for click→select. The
  // public `/p/<token>` mount keeps passing no `prototypeId` → the overlay
  // renders nothing (AC10 preserved, untouched here). Extracted into a const so
  // P6-13's two-column `design-pane` grid can place it in the main cell without
  // duplicating the block — the `bundleUrl &&` guard is preserved exactly as
  // P6-05 left it (the viewer cell only renders when a bundle exists; the
  // comments cell is independent and mounts on share regardless of bundle state).
  // P6-16 (UX-6) selector-collision guard (AC3b): the full-screen overlay mounts
  // its OWN <PrototypeViewer> → a SECOND `da-prototype-iframe`. ManualEditOverlay
  // reaches the editable iframe via a GLOBAL
  // `document.querySelector("iframe.da-prototype-iframe")` (ManualEditOverlay.tsx
  // `defaultGetPrototypeDoc`), which takes the FIRST match — so two such iframes
  // could let manual-edit bind to the wrong one. We unmount the inline viewer (its
  // iframe AND its ManualEditOverlay editor) whenever the overlay is open, so at
  // most ONE `da-prototype-iframe` exists at any instant and it is always the
  // active edit target. The overlay viewer is view-only (no `chrome` → no second
  // editor). The live selector behaviour is tester-verified (AC8) — the node-env
  // unit cannot exercise the real global query.
  // the CENTER full-area canvas. The
  // Desktop/Mobile toggle is now LIFTED into the top control bar, so the viewer
  // runs CONTROLLED (`platform` from props, `onPlatformChange` reports clicks) and
  // hides its own in-frame toggle (`hideToggle`). The stage class still tracks
  // `platform` so the canvas width still switches. The viewer fills the full
  // center region (David's `.proto-frame-full`) via the `.da-canvas-stage` wrap.
  // the "Edit" button (ManualEditOverlay
  // trigger) is NO LONGER rendered on the canvas — the `chrome` slot is left empty.
  // The ManualEditOverlay component file is kept intact; we just don't mount its
  // trigger here. Mark-and-comment (CHANGE 3) is the canvas annotation path now.
  const viewer = bundleUrl && !fullscreenOpen ? (
    <PrototypeViewer
      // cache-busted url so a completed
      // iterate reloads the rebuilt bundle (the iframe src changes → reload). The
      // `key` follows BOTH the bundle path and the nonce, so React mounts a fresh
      // iframe when the build advances to a new path AND when a same-path rebuild
      // bumps the nonce — the canvas never reuses a frame stuck on a prior build.
      // The remount key folds in the grant reload key so a bounded re-mint
      // forces a fresh iframe load of the now-re-authorized bundle.
      key={`${viewerRemountKey(bundleUrl, bundleReloadNonce)}-g${bundleGrantReloadKey}`}
      bundleUrl={reloadBundleUrl ?? bundleUrl}
      isComplete={isComplete}
      platform={platform}
      onPlatformChange={onPlatformChange}
      hideToggle
      onAssetError={onBundleAssetError}
    />
  ) : null

  // the TOP control bar is now a COMPACT single
  // row (≈54px) — see <DaControlBar> below. The full CompletionBar + full ShareMenu
  // are NO LONGER rendered inline; they are consolidated into compact dropdown
  // popovers (Actions / Share) inside the bar, so it never bloats to ~180px.
  const controlBar = (
    <DaControlBar
      prototypeId={prototypeId}
      isComplete={isComplete}
      onStateChange={onStateChange}
      shareMode={shareMode}
      shareToken={shareToken}
      onShared={onShared}
      platform={platform}
      onPlatformChange={onPlatformChange}
      commentsOpen={commentsOpen}
      onToggleComments={onToggleComments}
      markMode={markMode}
      onToggleMark={onToggleMark}
      canOpen={canOpen}
      onOpenFullscreen={onOpenFullscreen}
      onDone={onDone}
      isInTab={isInTab}
      onBack={onBack}
      prdTitle={prdTitle}
    />
  )

  return (
    <div className="design-agent-surface design-agent-result" data-testid="post-generation-result">
      {/* David's `.proto-ready` post-gen layout —
          a TOP control bar + a 3-section body:
            LEFT  = collapsible sidebar (OPEN by default): PRD content (read-only)
                    at top + the iterate/reprompt composer pinned at the bottom.
            CENTER= the prototype canvas filling the FULL area (`.proto-frame-full`
                    analogue: `.da-canvas-stage` wraps the controlled PrototypeViewer).
            RIGHT = collapsible comments sidebar (COLLAPSED by default), toggled
                    from the control bar's comments tool.
          Excluded per spec: Code/Preview/Spec tabs + version stepper. The
          collapse/expand model + control-bar affordances live in design-agent.css. */}
      {/* breadcrumb row at the very top
          of the canvas — "PRDs / {PRD title} / Design". The PRDs / PRD crumbs close
          the canvas (onDone → ApproveModal.closeCanvas / launcher close) and return
          to the PRD screen. */}
      {hideBreadcrumb ? null : (
        <DaBreadcrumb prdTitle={prdTitle ?? null} onDone={onDone} />
      )}
      {controlBar}
      <div
        className="da-ready"
        data-testid="da-ready"
        data-left-open={leftOpen ? "true" : "false"}
        data-comments-open={commentsOpen ? "true" : "false"}
      >
        {/* LEFT collapsible sidebar — PRD (top, scrollable) + iterate (bottom). */}
        <aside
          ref={leftPanelRef}
          className={`da-left${leftOpen ? "" : " collapsed"}${iterateRunning ? " is-running" : ""}`}
          data-testid="da-left"
        >
          <div className="da-left-top">
            <span className="da-left-title">
              {isInTab ? "Prototype" : (prdTitle || "PRD")}
            </span>
            {isInTab && (
              <span className="da-left-badge">DESIGN AGENT</span>
            )}
            {iterateRunning && (
              <span className="da-left-running-pill" aria-live="polite">
                <span className="da-activity-spinner" aria-hidden="true" />
                Working…
              </span>
            )}
            <button
              type="button"
              className="da-left-handle"
              data-testid="da-left-collapse"
              title="Collapse"
              aria-label="Collapse conversation panel"
              onClick={() => onToggleLeft?.()}
            >
              <IconChevronLeft size={15} />
            </button>
          </div>
          {/* LIVE-ONLY agent-conversation thread — named turns (author + relative
              timestamp) for the user's requests, the "agent working" steps, the
              completion summary, clarifying questions, and errors. No PRD panel,
              no persistence: a refresh starts the thread empty. The thread is the
              scrollable region; the composer below stays pinned at the bottom.
              When a run pauses on a clarifying question the INLINE answer surface
              renders right here in the stream and continues the iterate. */}
          <div className="da-left-scroll" data-testid="da-left-thread">
            {(iterateActivity.length > 0 || iteratePendingQuestion) && (
              <ActivityPanel
                iterateActivity={iterateActivity}
                iterateRunning={iterateRunning}
                iteratePendingQuestion={iteratePendingQuestion}
                onAnswerQuestion={onAnswerQuestion}
                userName={userName}
              />
            )}
          </div>
          {/* the prop-driven
              clarifying surface (from a prototype row that already carried a
              `pending_question` BEFORE this session's run). Suppressed while the
              runner is driving its own inline question to avoid a double surface. */}
          {clarifying && !iteratePendingQuestion && (
            <div className="da-left-clarify" data-testid="da-canvas-clarify">
              {clarifying}
            </div>
          )}
          {iterate && (
            <div className="da-left-compose" data-testid="da-canvas-iterate">
              {iterate}
            </div>
          )}
        </aside>
        {/* Collapsed LEFT handle — reopens the sidebar (mirrors David's
            `.proto-chat-handle` that appears when the chat is hidden). */}
        {!leftOpen && (
          <button
            type="button"
            className="da-left-reopen"
            data-testid="da-left-expand"
            title="Open conversation panel"
            aria-label="Open conversation panel"
            onClick={() => onToggleLeft?.()}
          >
            <IconDocument size={16} />
          </button>
        )}

        {/* CENTER full-area canvas. 
            the stage wraps the viewer + a transparent mark overlay + the pin
            layer. `.marking` (David's class) adds the crosshair cursor + brand
            outline ring when mark mode is on. */}
        <div
          className={`da-stage${markMode ? " marking" : ""}`}
          data-testid="da-canvas-center"
        >
          {viewer}
          {/* View-grant failure (initial mint failed, or the bounded re-mint was
              exhausted — the grant was likely revoked mid-session). Non-blocking
              banner over the stage; a refresh re-runs the mint. */}
          {bundleGrantError && !viewer && (
            <div className="da-grant-error" role="alert" data-testid="da-grant-error">
              {bundleGrantError}
            </div>
          )}
          {/* IFRAME NUANCE (critical): the prototype is an <iframe>, so clicks
              inside it can't be captured directly. The <MarkOverlay> sits ABOVE
              the iframe; it is click-inert normally (pointer-events:none via CSS)
              and click-active ONLY in mark mode (`.da-mark-overlay.active`,
              pointer-events:auto + crosshair). Its click hit-tests the iframe →
              stage-relative x/y + resolved anchor → onStageClick (handleStageClick
              captures xPctInEl/yPctInEl/anchor). */}
          {viewer && (
            <MarkOverlay markMode={markMode} onStageClick={onStageClick} />
          )}
          {/* Pin layer — numbered teardrops positioned absolutely over the canvas.
              `placed` triggers David's `pinDrop` animation. Always rendered above
              the overlay so pins stay visible after mark mode exits. */}
          {viewer && (
            <PinLayer pins={pins} computedPinPositions={computedPinPositions} />
          )}
        </div>

        {/* RIGHT collapsible comments sidebar — COLLAPSED by default; width is
            driven by `.da-right.open` (control-bar comments-toggle). The shell
            now ALWAYS renders so the control-bar comments-toggle can reveal it
            regardless of share state. When a `comments` node exists (shared /
            `comments` node present) it renders <CommentsPanel> inside; when NOT
            shared it shows a small empty state pointing at the Share dropdown. */}
        <aside
          className={`da-right${commentsOpen ? " open" : ""}`}
          data-testid="da-canvas-comments"
          aria-hidden={commentsOpen ? "false" : "true"}
        >
          <div className="da-right-top">
            <IconMessage size={15} />
            <span className="da-right-title">Comments</span>
            {pins.filter((p) => p.saved && !p.resolved).length > 0 && (
              <span className="comments-count-badge">
                {pins.filter((p) => p.saved && !p.resolved).length}
              </span>
            )}
            <button
              type="button"
              className="da-right-close"
              title="Hide comments"
              aria-label="Hide comments"
              onClick={() => onToggleComments?.()}
            >
              <IconClose size={14} />
            </button>
          </div>
          <div className="da-right-body">
            {/* the mark-and-comment pin
                rows (C1 Slice B — extracted to <PrototypeMarkLayer>). Each pin
                dropped on the canvas appears here with its number + a composer
                (auto-focused) to type the comment. Submit wires to the authed
                create endpoint (api.createComment) via onPinSubmit; the row stays
                optimistic until confirmed. This is the CREATE path; the existing
                CommentsPanel below stays the resolve/list surface for shared
                prototypes. The resolve control now reuses the shared
                `.comment-resolve-btn` (Part 2 consolidation). */}
            <PrototypeMarkLayer
              pins={pins}
              editorMode
              canResolve
              onPinDraftChange={onPinDraftChange}
              onSubmitComment={onPinSubmit}
              onPinRemove={onPinRemove}
              onPinApply={onPinApply}
              onPinIgnore={onPinIgnore}
              onPinResolve={onPinResolve ?? onPinIgnore}
            />
            {comments ? (
              comments
            ) : (
              pins.length === 0 && (
                <div className="da-right-empty" data-testid="da-comments-empty">
                  <p>Use <strong>Mark</strong> to pin a comment on the prototype.</p>
                  <p className="da-right-empty-hint">
                    Share via <strong>Share</strong> in the toolbar to collect
                    comments from others too.
                  </p>
                </div>
              )
            )}
          </div>
        </aside>
      </div>
      {/* The full-screen overlay reuses the SAME device frame (P6-12
          `<PrototypeViewer>`) at viewport scale — not a bare iframe (keeps the
          browser-frame chrome + Desktop/Mobile toggle + the P6-17 sandbox). It is
          view-only (no `chrome` → no second ManualEditOverlay editor). Mounted
          only while open AND a bundle exists; the inline viewer is unmounted while
          it is open (selector-collision guard above). */}
      {fullscreenOpen && bundleUrl && (
        <FullscreenOverlay
          bundleUrl={bundleUrl}
          isComplete={isComplete}
          onCloseFullscreen={onCloseFullscreen}
          onAssetError={onBundleAssetError}
        />
      )}
    </div>
  )
}

/**
 * Public component. Owns the local `is_complete` copy so the result view (and
 * any completion-dependent chrome) reflects Mark Complete / Resume without a
 * reload (AC4). Defends against older / partial rows that don't surface the
 * P2-06 columns by defaulting `is_complete`→false, `share_mode`→"private",
 * `share_token`→null (AC9).
 */
export function PostGenerationResult({
  prototype,
  comments,
  iterate,
  onShared,
  prdTitle,
  userName,
  onDone,
  onPinApply,
  onPinIterate,
  iterateActivity,
  iterateRunning,
  // iterateError is surfaced inside the activity stream (an `error` event), so it
  // is intentionally not destructured/used directly here.
  iteratePendingQuestion,
  onAnswerQuestion,
  bundleReloadNonce,
  onStateChange,
  defaultFullscreen,
  onFullscreenChange,
  hideBreadcrumb,
  isInTab,
  onBack,
}: PostGenerationResultProps) {
  const [isComplete, setIsComplete] = useState<boolean>(
    prototype.is_complete ?? false,
  )

  // Bundle-proxy view-grant: mint `da_view_grant` (bearer-authed POST) before the
  // authed iframe loads the same-origin proxy bundle. `grantedBundleUrl` is null
  // until the grant exists, so the iframe `src` is never set without a credential
  // the asset GETs can carry. `notifyAssetError` re-mints ONCE on an asset 401
  // (bounded — see useViewGrant). The bundle url is opaque here; we never parse it.
  const grant = useViewGrant(prototype.id, prototype.bundle_url)

  // P6-16 (UX-6): client-only open state for the full-screen overlay. Owned here
  // (the stateful container) and threaded into the SSR-renderable pure view,
  // matching the existing `onStateChange` threading pattern.
  const [fullscreenOpen, setFullscreenOpen] = useState<boolean>(defaultFullscreen ?? false)

  // collapsible-panel + control-bar state, owned
  // by the container (same threading pattern as `fullscreenOpen`). LEFT sidebar
  // (PRD + iterate) OPEN by default; RIGHT comments sidebar COLLAPSED by default;
  // the Desktop/Mobile toggle lifted out of PrototypeViewer lives here too.
  const [leftOpen, setLeftOpen] = useState<boolean>(true)
  const [commentsOpen, setCommentsOpen] = useState<boolean>(false)
  const [platform, setPlatform] = useState<Platform>("desktop")

  // mark-and-comment pin flow — now driven by the shared usePinMarking hook (C2b)
  // so the public viewer runs the SAME implementation. The signed-in create-fn is
  // injected here: createComment(prototype.id) wrapped in withAuthRetry. The
  // surface side effects (open the comments sidebar on enter-mark / pin-drop) +
  // the Apply runner / pre-fill seam are injected too — they are the ONLY things
  // that differ from the public surface.
  const pin = usePinMarking({
    onCreate: (payload) =>
      withAuthRetry(() => designAgentApi.createComment(prototype.id, payload)),
    onEnterMarkMode: () => setCommentsOpen(true),
    onPinDropped: () => setCommentsOpen(true),
    onPinIterate,
    onPinApply,
  })
  const leftPanelRef = useRef<HTMLDivElement>(null)

  // Escape closes the full-screen
  // overlay (in addition to the visible × close button). Bound only while open.
  useEffect(() => {
    if (!fullscreenOpen) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setFullscreenOpen(false)
        onFullscreenChange?.(false)
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [fullscreenOpen, onFullscreenChange])

  // Scroll to the activity section when the FIRST SSE event arrives so the
  // user sees the stream without having to scroll. Only fires on the transition
  // from 0 → 1 events (length === 1). The leftPanelRef is attached to the
  // `.da-left` aside via the pure view; the activityEl query finds `.da-left-activity`
  // within it, matching the ActivityPanel mount point.
  useEffect(() => {
    if ((iterateActivity?.length ?? 0) === 1) {
      const activityEl = leftPanelRef.current?.querySelector('.da-left-activity')
      activityEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [iterateActivity?.length])


  // P6-05 (#5): when the launcher refetches after an iterate/clarify and hands a
  // fresh prop down (same id, new `bundle_url`), re-seed the local `isComplete`
  // ONLY on a genuine checkpoint advance whose prop value differs from the last
  // prop-derived baseline — never clobbering a user's local Mark-Complete. The
  // iframe `src` + "View prototype" href read `bundle_url` straight from the prop
  // in the view, so they refresh automatically; only this local copy needs care.
  const baselineRef = useRef<ReseedBaseline>({
    bundle: prototype.bundle_url,
    complete: prototype.is_complete ?? false,
  })
  useEffect(() => {
    const next = prototype.is_complete ?? false
    const { baseline, setComplete } = reseedStep(
      baselineRef.current,
      prototype.bundle_url,
      next,
    )
    baselineRef.current = baseline
    if (setComplete !== null) setIsComplete(setComplete)
  }, [prototype.bundle_url, prototype.is_complete])

  return (
    <PostGenerationResultView
      prototypeId={prototype.id}
      isComplete={isComplete}
      shareMode={prototype.share_mode ?? "private"}
      shareToken={prototype.share_token ?? null}
      // GATED bundle url — null until the view-grant cookie is minted, so the
      // authed iframe never loads the proxy bundle without a credential its asset
      // GETs can carry. (The opaque proxy url is loaded verbatim; never parsed.)
      bundleUrl={grant.grantedBundleUrl}
      bundleGrantError={grant.error}
      onBundleAssetError={grant.notifyAssetError}
      bundleGrantReloadKey={grant.reloadKey}
      onStateChange={(state) => {
        setIsComplete(state.isComplete)
        onStateChange?.(state)
      }}
      comments={comments}
      iterate={iterate}
      onShared={onShared}
      fullscreenOpen={fullscreenOpen}
      onOpenFullscreen={() => { setFullscreenOpen(true); onFullscreenChange?.(true) }}
      onCloseFullscreen={() => { setFullscreenOpen(false); onFullscreenChange?.(false) }}
      prdTitle={prdTitle}
      userName={userName}
      onDone={onDone}
      leftOpen={leftOpen}
      onToggleLeft={() => setLeftOpen((v) => !v)}
      commentsOpen={commentsOpen}
      onToggleComments={() => setCommentsOpen((v) => !v)}
      platform={platform}
      onPlatformChange={(p) => setPlatform(p)}
      markMode={pin.markMode}
      onToggleMark={pin.toggleMark}
      onStageClick={pin.handleStageClick}
      pins={pin.pins}
      onPinDraftChange={pin.handlePinDraftChange}
      onPinSubmit={pin.handlePinSubmit}
      onPinRemove={pin.handlePinRemove}
      onPinApply={pin.handlePinApply}
      onPinIgnore={pin.handlePinIgnore}
      // the consolidated resolve control on a saved pin row resolves it WITHOUT
      // pre-filling the composer — same semantic as Ignore.
      onPinResolve={pin.handlePinIgnore}
      // mount the clarifying-
      // question surface. It self-gates on `prototype.pending_question` (renders
      // null when none/locked), so it's safe to always pass. When the launcher's
      // refetch (onIterated → ApproveModal.refreshCanvas) advances the prototype
      // to a pending-question checkpoint, this prop updates and the surface shows;
      // answering routes a NEW iterate (continues the loop) via the reused
      // designAgentApi.iterate.
      clarifying={<ClarifyingQuestionSurface prototype={prototype} />}
      // the live agent-flow activity
      // + inline clarifying answer + pin-Apply immediate-iterate + the iframe
      // reload nonce, all sourced from the shared runner threaded by ApproveModal.
      iterateActivity={iterateActivity}
      iterateRunning={iterateRunning}
      iteratePendingQuestion={iteratePendingQuestion}
      onAnswerQuestion={onAnswerQuestion}
      onPinIterate={onPinIterate}
      bundleReloadNonce={bundleReloadNonce}
      computedPinPositions={pin.computedPinPositions}
      leftPanelRef={leftPanelRef}
      hideBreadcrumb={hideBreadcrumb}
      isInTab={isInTab}
      onBack={onBack}
    />
  )
}

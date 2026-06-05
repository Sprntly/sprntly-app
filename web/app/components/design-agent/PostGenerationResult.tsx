"use client"

/**
 * P2-12 — post-generation result surface for the SIGNED-IN app.
 *
 * After the F2 launcher's drawer reports a successful generation
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

import { useEffect, useRef, useState, type ReactNode } from "react"
import { CompletionBar } from "./CompletionBar"
import { ShareMenu, type ShareMode } from "./ShareMenu"
import { PrototypeViewer, type Platform } from "./PrototypeViewer"
// UX-EXPLORE (throwaway — REVERT, CHANGE 2): ManualEditOverlay import dropped —
// its trigger is no longer mounted on the canvas (the component file is kept).
// UX-EXPLORE (throwaway — REVERT, CHANGE A): PrdSections no longer dumped in the
// left sidebar — replaced by a CONDENSED context panel built from the PRD's
// title + meta + the prd-tldr (Problem/Fix/Impact) block. PrdSections import is
// kept only for the optional "View full PRD" expander.
import { PrdSections } from "../shared/PrdSections"
// UX-EXPLORE (throwaway — REVERT, CHANGE B/C): reuse the comment identity helpers
// (avatar + relative time) so pin-comment rows render WHO + WHEN like David's.
import { CommentAvatar, shortRelativeTime } from "./CommentsPanel"
// UX-EXPLORE (throwaway — REVERT, CHANGE E): the F12 clarifying-question answer
// surface, mounted in the LEFT sidebar near the composer when the iterate run
// returns a `pending_question` (see the launcher's original conditional mount).
import { ClarifyingQuestionSurface } from "./ClarifyingQuestionSurface"
// UX-EXPLORE (throwaway — REVERT, CHANGE A): the live agent-flow activity stream
// (the user request → working steps → done/question/error transcript) shown in
// the LEFT panel while/after an iterate runs.
import { IterateActivityStream } from "./IterateActivityStream"
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
} from "../shared/app-icons"
// UX-EXPLORE (throwaway — REVERT, CHANGE 2): subtle breadcrumb at the top of the
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
import { designAgentApi, type CommentRecord, type PrototypeRecord } from "../../lib/api"
import type { PrdSection } from "../../types/content"

// UX-EXPLORE (throwaway — REVERT, CHANGE 3): a pin-anchored comment created via
// the mark-and-comment flow. `xPct`/`yPct` are the pin's position over the canvas
// stage (0–100, relative to the stage box) — UI-only, NOT persisted (the backend
// CommentCreate schema has no position field; see api.createComment note + RETURN).
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
  // UX-EXPLORE (throwaway — REVERT, CHANGE B): author + timestamp captured at
  // create time so the saved row can show WHO + WHEN + an avatar (David's
  // `.proto-comment-au` / `.proto-comment-time` / `.pc-av`). The authed create
  // attributes the author server-side ("demo"); we mirror the returned record's
  // author/created_at onto the pin so the optimistic row shows real identity.
  author?: string | null
  createdAt?: string | null
  // UX-EXPLORE (throwaway — REVERT, CHANGE C): a saved pin comment can be
  // Applied (pre-fill composer + resolve) or Ignored (resolve only). `resolved`
  // moves the row to the muted/collapsed state (David's `.resolved`).
  resolved?: boolean
}

export type PostGenerationResultProps = {
  prototype: PrototypeRecord
  /** P6-13 (UX-3): optional comments node placed in the right cell of the
   *  two-column `design-pane` grid beside the viewer. The signed-in launcher
   *  passes its `<CommentsPanel>` here; the public `/p/<token>` viewer does NOT
   *  use this component (it composes its own chrome) → it passes nothing and the
   *  comments column is omitted. Null-by-default keeps the public shape intact. */
  comments?: ReactNode
  /** UX-EXPLORE (throwaway — REVERT): the iterate/change-request column node
   *  (the launcher's `<IterateComposer>`). Placed in the LEFT region of the
   *  3-region canvas. Optional → when absent (e.g. the public viewer) the left
   *  region is omitted and the canvas degrades to canvas + comments. */
  iterate?: ReactNode
  /** P6-20 (#14): forwarded to `<ShareMenu>` — fired after a successful Share so
   *  the launcher re-polls and `result.share_token` goes live (flipping the
   *  share-gated comments column on without a re-mount). Optional/defaulted so the
   *  public-viewer composition and existing direct calls keep type-checking. */
  onShared?: (token: string | null) => void
  /** UX-EXPLORE (throwaway — REVERT): the PRD's parsed semantic sections, threaded
   *  from ApproveModal (which reads them off `useContent().content.prd`). Rendered
   *  read-only at the TOP of the new LEFT sidebar (above the iterate composer). */
  prdSections?: PrdSection[]
  /** UX-EXPLORE (throwaway — REVERT): the PRD title for the left-sidebar header. */
  prdTitle?: string | null
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): the PRD's one-line meta/description
   *  (PrdContent.metaLine) — rendered as the subtitle in the condensed context panel. */
  prdMetaLine?: string | null
  /** UX-EXPLORE (throwaway — REVERT): the PRD's DB id, threaded to PrdSections so
   *  the read-only render does NOT mount a second DesignAgentLauncher (prd-design
   *  block) — passing undefined makes that block fall back to its inert empty
   *  state inside the sidebar. (We pass undefined deliberately.) */
  /** UX-EXPLORE (throwaway — REVERT): the control-bar "Done" affordance — closes
   *  the full-screen canvas back to the PRD (ApproveModal.closeCanvas). */
  onDone?: () => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE C/D): Apply a pin comment — pre-fill
   *  the LEFT IterateComposer with a pin-context edit instruction. Threaded from
   *  ApproveModal (sets `applyTarget`, the same seam CommentsPanel's Apply uses).
   *  The synthetic CommentRecord carries the composed instruction as its `body`.
   *  Kept for back-compat; superseded by `onPinIterate` when that is supplied. */
  onPinApply?: (comment: CommentRecord) => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE B): Apply a pin comment by running it
   *  through the canvas's SHARED iterate runner IMMEDIATELY (pin-context string +
   *  comment body as the instruction) instead of pre-filling the composer. When
   *  supplied it takes precedence over `onPinApply`. */
  onPinIterate?: (instruction: string, appliedCommentId?: number | null) => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): the live agent-flow activity for
   *  the LEFT panel — the user request, working steps, completion / clarifying
   *  question / error — driven by the shared runner (useIterateRun). */
  iterateActivity?: import("./useIterateRun").ActivityEvent[]
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): true while an iterate is running. */
  iterateRunning?: boolean
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): a run-level error (also appended
   *  to the activity stream). */
  iterateError?: string | null
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): the agent's clarifying question
   *  when the run paused — surfaced INLINE in the left-panel flow. */
  iteratePendingQuestion?: import("../../lib/api").PendingQuestion | null
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): answer the clarifying question →
   *  continues the iterate via the shared runner. */
  onAnswerQuestion?: (answer: string) => void | Promise<void>
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): bumped on each completed iterate
   *  to force the center iframe to reload the rebuilt bundle (cache-bust). */
  bundleReloadNonce?: number
}

export type PostGenerationResultViewProps = {
  prototypeId: number
  isComplete: boolean
  shareMode: ShareMode
  shareToken: string | null
  bundleUrl: string | null
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
  /** P6-13 (UX-3): comments node for the right cell of the `design-pane` grid.
   *  When absent, the viewer renders full-width (no comments cell, no grid). */
  comments?: ReactNode
  /** UX-EXPLORE (throwaway — REVERT): iterate/change-request column node for the
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
  /** UX-EXPLORE (throwaway — REVERT): PRD content for the LEFT sidebar (read-only)
   *  + the control-bar Done affordance. See PostGenerationResultProps. */
  prdSections?: PrdSection[]
  prdTitle?: string | null
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): PRD one-line meta for the condensed panel. */
  prdMetaLine?: string | null
  onDone?: () => void
  /** UX-EXPLORE (throwaway — REVERT): collapsible-panel + control-bar state, owned
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
  /** UX-EXPLORE (throwaway — REVERT, CHANGE 3): mark-and-comment pin flow state,
   *  owned by the container and threaded into the SSR-renderable view (same
   *  pattern as `fullscreenOpen`). `markMode` toggles the crosshair overlay; the
   *  overlay click reports stage-relative x/y via `onStageClick`; `pins` render
   *  the numbered teardrops + the right-sidebar comment rows; the row handlers
   *  edit/submit/remove a pin's comment. */
  markMode?: boolean
  onToggleMark?: () => void
  onStageClick?: (xPct: number, yPct: number) => void
  pins?: PinComment[]
  onPinDraftChange?: (n: number, value: string) => void
  onPinSubmit?: (n: number) => void
  onPinRemove?: (n: number) => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE C/D): Apply a saved pin comment
   *  (pre-fill composer w/ pin context + resolve) / Ignore (resolve only). */
  onPinApply?: (n: number) => void
  onPinIgnore?: (n: number) => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE E): the F12 clarifying-question
   *  surface node (the container's <ClarifyingQuestionSurface>). Mounted in the
   *  LEFT sidebar just above the IterateComposer; when null nothing renders. */
  clarifying?: ReactNode
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): the live agent-flow activity for
   *  the LEFT panel, plus the run-paused clarifying answer surface. */
  iterateActivity?: import("./useIterateRun").ActivityEvent[]
  iterateRunning?: boolean
  iteratePendingQuestion?: import("../../lib/api").PendingQuestion | null
  onAnswerQuestion?: (answer: string) => void | Promise<void>
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): pin Apply → immediate iterate. */
  onPinIterate?: (instruction: string, appliedCommentId?: number | null) => void
  /** UX-EXPLORE (throwaway — REVERT, CHANGE A): cache-bust nonce → forces the
   *  iframe to reload the rebuilt bundle on each completed iterate. */
  bundleReloadNonce?: number
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
 * public `/p/<token>` link once the prototype has been shared. Returns null
 * when neither is available yet (nothing to link to → the affordance hides).
 */
export function resolveViewHref(
  bundleUrl: string | null,
  shareToken: string | null,
): string | null {
  if (bundleUrl) return bundleUrl
  if (shareToken) return `/p/${shareToken}`
  return null
}

/**
 * UX-EXPLORE (throwaway — REVERT, CHANGE A): the condensed PRD context model.
 * David's left panel is LIGHTWEIGHT context, not the full document. We pull the
 * TL;DR triptych (Problem / Fix / Impact) from the `prd-tldr` block if present;
 * the long body sections (Context / Requirements / AC / etc.) are dropped from
 * the default view and tucked behind a "View full PRD" expander. Pure → unit-
 * testable without a DOM.
 */
export type CondensedPrd = {
  problem: string | null
  fix: string | null
  impact: string | null
  hasFullBody: boolean
}

export function condensePrd(sections: PrdSection[] | undefined): CondensedPrd {
  const empty: CondensedPrd = { problem: null, fix: null, impact: null, hasFullBody: false }
  if (!sections || sections.length === 0) return empty
  const tldr = sections.find((s) => s.type === "prd-tldr") as
    | { type: "prd-tldr"; problem: string; fix: string; impact: string }
    | undefined
  // "Full body" = anything beyond the tldr / a leading title-ish heading worth
  // putting behind the expander.
  const bodyCount = sections.filter((s) => s.type !== "prd-tldr").length
  if (tldr) {
    return {
      problem: tldr.problem || null,
      fix: tldr.fix || null,
      impact: tldr.impact || null,
      hasFullBody: bodyCount > 0,
    }
  }
  return { ...empty, hasFullBody: sections.length > 0 }
}

/**
 * UX-EXPLORE (throwaway — REVERT, CHANGE A): the condensed left-sidebar context
 * panel — PRD title + one-line meta + Problem/Fix/Impact cards (David's
 * `.proto-ctx-panel` `.pcx` style: small uppercase header + short body). The
 * long PRD body is dropped to a "View full PRD" expander (kept cheap — a native
 * <details>) so the panel stays a LIGHT context view, not the full doc.
 */
function CondensedPrdPanel({
  title,
  metaLine,
  sections,
}: {
  title: string | null
  metaLine: string | null
  sections: PrdSection[] | undefined
}) {
  const c = condensePrd(sections)
  const cards: { label: string; body: string }[] = []
  if (c.problem) cards.push({ label: "Problem", body: c.problem })
  if (c.fix) cards.push({ label: "Fix", body: c.fix })
  if (c.impact) cards.push({ label: "Impact", body: c.impact })

  return (
    <div className="proto-ctx-panel" data-testid="da-prd-condensed">
      {metaLine && <p className="proto-ctx-meta">{metaLine}</p>}
      {cards.length > 0 ? (
        <div className="proto-ctx-cards">
          {cards.map((card) => (
            <div className="pcx" key={card.label} data-testid={`da-prd-pcx-${card.label.toLowerCase()}`}>
              <div className="pcx-label">{card.label}</div>
              <div className="pcx-body">{card.body}</div>
            </div>
          ))}
        </div>
      ) : (
        <p className="da-left-prd-empty">No summary available for this PRD.</p>
      )}
      {/* Long body behind a cheap native expander so the panel stays light. */}
      {c.hasFullBody && sections && (
        <details className="proto-ctx-full" data-testid="da-prd-full">
          <summary className="proto-ctx-full-summary">View full PRD</summary>
          <div className="proto-ctx-full-body">
            <PrdSections sections={sections} />
          </div>
        </details>
      )}
    </div>
  )
}

/**
 * UX-EXPLORE (throwaway — REVERT): a tiny click-outside-dismiss popover used by
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
 * UX-EXPLORE (throwaway — REVERT): the COMPACT top control bar (≈54px), modelled
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
  /** UX-EXPLORE (throwaway — REVERT, CHANGE 3): mark-and-comment tool state. */
  markMode: boolean
  onToggleMark?: () => void
  canOpen: boolean
  onOpenFullscreen?: () => void
  onDone?: () => void
}) {
  return (
    <div className="da-controlbar" data-testid="da-controlbar">
      {/* LEFT cluster — compact Desktop/Mobile segmented control. */}
      <div className="da-controlbar-l">
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
        {/* UX-EXPLORE (throwaway — REVERT, CHANGE 3): Mark & comment tool
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
          <span className="da-ctl-label">Mark</span>
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
          <span className="da-ctl-label">Comments</span>
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
              <span className="da-ctl-label">Share</span>
              <IconChevronDown size={13} />
            </button>
          )}
        >
          {/* UX-EXPLORE (throwaway — REVERT): the restyled ShareMenu renders its
              own `.share-title` ("Share prototype") + clean panel, so the generic
              `.da-popover-title` is dropped here to avoid a duplicate heading. */}
          <ShareMenu
            prototypeId={prototypeId}
            initialMode={shareMode}
            initialToken={shareToken}
            onShared={onShared}
          />
        </DaPopover>

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
      </div>
    </div>
  )
}

/**
 * UX-EXPLORE (throwaway — REVERT, CHANGE A): the INLINE clarifying-answer surface
 * for the left-panel flow. When the shared iterate runner pauses on a
 * `pending_question`, this renders RIGHT IN THE ACTIVITY STREAM (not as a
 * detached surface): the question is already shown as an agent message above; this
 * is the answer affordance (choice buttons when the question carries `choices`,
 * else a free-text box). Answering routes a continuation iterate via the runner
 * (onAnswer → useIterateRun.answerQuestion). Local input state only → a leaf
 * client component (the file is already "use client").
 */
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
              className="btn btn-accent"
              data-testid="da-activity-answer-choice"
              disabled={busy}
              onClick={() => void onAnswer(choice)}
            >
              {choice}
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

/** Pure presentational view — no I/O of its own → SSR-renderable in node-env
 *  vitest. The container threads live `isComplete` + the `onStateChange`
 *  handler into it. */
export function PostGenerationResultView({
  prototypeId,
  isComplete,
  shareMode,
  shareToken,
  bundleUrl,
  onStateChange,
  comments,
  iterate,
  onShared,
  fullscreenOpen = false,
  onOpenFullscreen,
  onCloseFullscreen,
  prdSections,
  prdTitle,
  prdMetaLine,
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
  clarifying,
  iterateActivity = [],
  iterateRunning = false,
  iteratePendingQuestion = null,
  onAnswerQuestion,
  onPinIterate,
  bundleReloadNonce = 0,
}: PostGenerationResultViewProps) {
  // UX-EXPLORE (throwaway — REVERT, CHANGE A): cache-bust the iframe src so a
  // rebuilt bundle reloads even when the backend overwrites it at the SAME url.
  // Only appends when the nonce has advanced (keeps the initial load url clean +
  // SSR output stable when no iterate has run). Preserves any existing query.
  const reloadBundleUrl =
    bundleUrl && bundleReloadNonce > 0
      ? `${bundleUrl}${bundleUrl.includes("?") ? "&" : "?"}v=${bundleReloadNonce}`
      : bundleUrl
  // P6-16 (UX-6): the primary View affordance is ALWAYS rendered (never a hidden
  // / dead link — the #6 bug). It is gated only on a built bundle existing:
  // enabled "View full screen" when `bundleUrl` is present, otherwise a DISABLED
  // "Prototype building…" control — never a removed element. `resolveViewHref`
  // (below) is KEPT byte-for-byte but no longer consumed here; its null-return no
  // longer hides the control. The real shared URL stays reachable via ShareMenu.
  const canOpen = bundleUrl != null
  // P4-10 — the EDITABLE viewer, rendered only when a built bundle exists. This
  // surface only renders inside (app)/AuthGate, so it is internal by
  // construction; passing the real numeric `prototypeId` into the overlay IS the
  // internal mount that makes F13 manual-edit reachable (AD13). The overlay
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
  // UX-EXPLORE (throwaway — REVERT): the CENTER full-area canvas. The
  // Desktop/Mobile toggle is now LIFTED into the top control bar, so the viewer
  // runs CONTROLLED (`platform` from props, `onPlatformChange` reports clicks) and
  // hides its own in-frame toggle (`hideToggle`). The stage class still tracks
  // `platform` so the canvas width still switches. The viewer fills the full
  // center region (David's `.proto-frame-full`) via the `.da-canvas-stage` wrap.
  // UX-EXPLORE (throwaway — REVERT, CHANGE 2): the "Edit" button (ManualEditOverlay
  // trigger) is NO LONGER rendered on the canvas — the `chrome` slot is left empty.
  // The ManualEditOverlay component file is kept intact; we just don't mount its
  // trigger here. Mark-and-comment (CHANGE 3) is the canvas annotation path now.
  const viewer = bundleUrl && !fullscreenOpen ? (
    <PrototypeViewer
      // UX-EXPLORE (throwaway — REVERT, CHANGE A): cache-busted url so a completed
      // iterate reloads the rebuilt bundle (the iframe src changes → reload). The
      // `key` ALSO follows the nonce so React remounts the iframe even if the
      // browser would otherwise treat the new query as a same-document nav.
      key={`viewer-${bundleReloadNonce}`}
      bundleUrl={reloadBundleUrl ?? bundleUrl}
      isComplete={isComplete}
      platform={platform}
      onPlatformChange={onPlatformChange}
      hideToggle
    />
  ) : null

  // UX-EXPLORE (throwaway — REVERT): the TOP control bar is now a COMPACT single
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
    />
  )

  return (
    <div className="design-agent-surface design-agent-result" data-testid="post-generation-result">
      {/* UX-EXPLORE (throwaway — REVERT): David's `.proto-ready` post-gen layout —
          a TOP control bar + a 3-section body:
            LEFT  = collapsible sidebar (OPEN by default): PRD content (read-only)
                    at top + the iterate/reprompt composer pinned at the bottom.
            CENTER= the prototype canvas filling the FULL area (`.proto-frame-full`
                    analogue: `.da-canvas-stage` wraps the controlled PrototypeViewer).
            RIGHT = collapsible comments sidebar (COLLAPSED by default), toggled
                    from the control bar's comments tool.
          Excluded per spec: Code/Preview/Spec tabs + version stepper. The
          collapse/expand model + control-bar affordances live in design-agent.css. */}
      {/* UX-EXPLORE (throwaway — REVERT, CHANGE 2): breadcrumb row at the very top
          of the canvas — "PRDs / {PRD title} / Design". The PRDs / PRD crumbs close
          the canvas (onDone → ApproveModal.closeCanvas / launcher close) and return
          to the PRD screen. */}
      <DaBreadcrumb prdTitle={prdTitle ?? null} onDone={onDone} />
      {controlBar}
      <div
        className="da-ready"
        data-testid="da-ready"
        data-left-open={leftOpen ? "true" : "false"}
        data-comments-open={commentsOpen ? "true" : "false"}
      >
        {/* LEFT collapsible sidebar — PRD (top, scrollable) + iterate (bottom). */}
        <aside
          className={`da-left${leftOpen ? "" : " collapsed"}`}
          data-testid="da-left"
        >
          <div className="da-left-top">
            <span className="da-left-title">
              {prdTitle || "PRD"}
            </span>
            <button
              type="button"
              className="da-left-handle"
              data-testid="da-left-collapse"
              title="Collapse"
              aria-label="Collapse PRD panel"
              onClick={() => onToggleLeft?.()}
            >
              <IconChevronLeft size={15} />
            </button>
          </div>
          <div className="da-left-scroll" data-testid="da-left-prd">
            {/* UX-EXPLORE (throwaway — REVERT, CHANGE A): CONDENSED context — PRD
                meta + the TL;DR (Problem/Fix/Impact) cards (David's `.pcx`), with
                the long body tucked behind a "View full PRD" expander. NOT the
                full document dump. */}
            {prdSections && prdSections.length > 0 ? (
              <CondensedPrdPanel
                title={prdTitle ?? null}
                metaLine={prdMetaLine ?? null}
                sections={prdSections}
              />
            ) : (
              <p className="da-left-prd-empty">PRD content unavailable.</p>
            )}
          </div>
          {/* UX-EXPLORE (throwaway — REVERT, CHANGE A): the LIVE agent-flow
              activity stream — the user's request, the "agent working" steps, and
              the completion / clarifying question / error — rendered IN the left
              flow (David's `.proto-msg` chat style). Driven by the shared runner's
              poll (cosmetic steps; SSE-ready seam in useIterateRun). When a run
              pauses on a clarifying question, the INLINE answer surface renders
              right here in the stream (not detached) and continues the iterate. */}
          {(iterateActivity.length > 0 || iteratePendingQuestion) && (
            <div className="da-left-activity" data-testid="da-canvas-activity">
              <IterateActivityStream
                activity={iterateActivity}
                running={iterateRunning}
              />
              {iteratePendingQuestion && onAnswerQuestion && (
                <InlineClarifyAnswer
                  question={iteratePendingQuestion}
                  busy={iterateRunning}
                  onAnswer={onAnswerQuestion}
                />
              )}
            </div>
          )}
          {/* UX-EXPLORE (throwaway — REVERT, CHANGE E): the prop-driven F12
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
            title="Open PRD panel"
            aria-label="Open PRD panel"
            onClick={() => onToggleLeft?.()}
          >
            <IconDocument size={16} />
          </button>
        )}

        {/* CENTER full-area canvas. UX-EXPLORE (throwaway — REVERT, CHANGE 3):
            the stage wraps the viewer + a transparent mark overlay + the pin
            layer. `.marking` (David's class) adds the crosshair cursor + brand
            outline ring when mark mode is on. */}
        <div
          className={`da-stage${markMode ? " marking" : ""}`}
          data-testid="da-canvas-center"
        >
          {viewer}
          {/* IFRAME NUANCE (critical): the prototype is an <iframe>, so clicks
              inside it can't be captured directly. This transparent overlay sits
              ABOVE the iframe; it is click-inert normally (pointer-events:none via
              CSS) and click-active ONLY in mark mode (`.da-mark-overlay.active`,
              pointer-events:auto + crosshair). On click we hit-test the overlay's
              own rect → x/y percentages → drop a pin there. */}
          {viewer && (
            <div
              className={`da-mark-overlay${markMode ? " active" : ""}`}
              data-testid="da-mark-overlay"
              aria-hidden={markMode ? "false" : "true"}
              onClick={(e) => {
                if (!markMode) return
                const rect = e.currentTarget.getBoundingClientRect()
                const xPct = ((e.clientX - rect.left) / rect.width) * 100
                const yPct = ((e.clientY - rect.top) / rect.height) * 100
                onStageClick?.(
                  Math.max(0, Math.min(100, xPct)),
                  Math.max(0, Math.min(100, yPct)),
                )
              }}
            />
          )}
          {/* Pin layer — numbered teardrops positioned absolutely over the canvas.
              `placed` triggers David's `pinDrop` animation. Always rendered above
              the overlay so pins stay visible after mark mode exits. */}
          {viewer && pins.length > 0 && (
            <div className="da-pin-layer" data-testid="da-pin-layer" aria-hidden="true">
              {pins.map((pin) => (
                <span
                  key={pin.n}
                  className="pc-pin placed"
                  data-testid={`da-pin-${pin.n}`}
                  style={{ left: `${pin.xPct}%`, top: `${pin.yPct}%` }}
                >
                  <span className="pc-pin-num">{pin.n}</span>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* RIGHT collapsible comments sidebar — COLLAPSED by default; width is
            driven by `.da-right.open` (control-bar comments-toggle). UX-EXPLORE
            (throwaway — REVERT, Problem 2): the shell now ALWAYS renders so the
            control-bar comments-toggle can reveal it regardless of share state.
            When a `comments` node exists (shared / `comments` node present) it
            renders <CommentsPanel> inside; when NOT shared it shows a small empty
            state pointing at the Share dropdown. */}
        <aside
          className={`da-right${commentsOpen ? " open" : ""}`}
          data-testid="da-canvas-comments"
          aria-hidden={commentsOpen ? "false" : "true"}
        >
          <div className="da-right-top">
            <IconMessage size={15} />
            <span className="da-right-title">Comments</span>
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
            {/* UX-EXPLORE (throwaway — REVERT, CHANGE 3): the mark-and-comment pin
                rows. Each pin dropped on the canvas appears here with its number +
                a composer (auto-focused) to type the comment. Submit wires to the
                authed create endpoint (api.createComment); the row stays optimistic
                until confirmed. This is the CREATE path; the existing CommentsPanel
                below stays the resolve/list surface for shared prototypes. */}
            {pins.length > 0 && (
              <ul className="proto-comment-list" data-testid="da-pin-comments">
                {pins.map((pin) => (
                  <li
                    key={pin.n}
                    className={`proto-comment${pin.saved ? " saved" : ""}${pin.resolved ? " resolved" : ""}`}
                    data-testid={`da-pin-comment-${pin.n}`}
                    data-status={pin.resolved ? "resolved" : pin.saved ? "open" : "draft"}
                  >
                    <span className="proto-comment-pin">{pin.n}</span>
                    <div className="proto-comment-main">
                      {pin.saved ? (
                        <>
                          {/* UX-EXPLORE (throwaway — REVERT, CHANGE B): author +
                              avatar + relative time on the saved pin comment. */}
                          <div className="proto-comment-au-row">
                            <CommentAvatar author={pin.author ?? "demo"} />
                            <span className="proto-comment-au">{pin.author ?? "demo"}</span>
                            <time
                              className="proto-comment-time"
                              dateTime={pin.createdAt ?? undefined}
                              title={pin.createdAt ?? undefined}
                            >
                              {shortRelativeTime(pin.createdAt)}
                            </time>
                          </div>
                          <p className="proto-comment-body">{pin.body}</p>
                          {/* UX-EXPLORE (throwaway — REVERT, CHANGE C): Apply /
                              Ignore on a saved, unresolved pin comment. Apply
                              pre-fills the composer with the pin context (CHANGE D)
                              + marks resolved; Ignore marks resolved only. */}
                          {!pin.resolved && (
                            <div className="proto-comment-actions">
                              <button
                                type="button"
                                className="btn btn-accent"
                                data-testid={`da-pin-apply-${pin.n}`}
                                onClick={() => onPinApply?.(pin.n)}
                              >
                                Apply
                              </button>
                              <button
                                type="button"
                                className="btn"
                                data-testid={`da-pin-ignore-${pin.n}`}
                                onClick={() => onPinIgnore?.(pin.n)}
                              >
                                Ignore
                              </button>
                            </div>
                          )}
                          {pin.resolved && (
                            <p className="proto-comment-resolved-note">Resolved</p>
                          )}
                        </>
                      ) : (
                        <form
                          className="proto-comment-form"
                          onSubmit={(e) => {
                            e.preventDefault()
                            onPinSubmit?.(pin.n)
                          }}
                        >
                          <textarea
                            className="proto-comment-input"
                            data-testid={`da-pin-input-${pin.n}`}
                            value={pin.draft}
                            placeholder="Add a comment…"
                            autoFocus
                            onChange={(e) =>
                              onPinDraftChange?.(pin.n, e.target.value)
                            }
                          />
                          <div className="proto-comment-actions">
                            <button
                              type="submit"
                              className="btn btn-accent"
                              data-testid={`da-pin-submit-${pin.n}`}
                              disabled={pin.busy || !pin.draft.trim()}
                            >
                              Comment
                            </button>
                            <button
                              type="button"
                              className="btn"
                              data-testid={`da-pin-cancel-${pin.n}`}
                              onClick={() => onPinRemove?.(pin.n)}
                            >
                              Cancel
                            </button>
                          </div>
                        </form>
                      )}
                      {pin.error && (
                        <p className="proto-comment-error error">{pin.error}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
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
        <div
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
            onClick={() => onCloseFullscreen?.()}
          >
            ×
          </button>
          <div className="proto-fullscreen-body">
            <PrototypeViewer bundleUrl={bundleUrl} isComplete={isComplete} />
          </div>
        </div>
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
  prdSections,
  prdTitle,
  prdMetaLine,
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
}: PostGenerationResultProps) {
  const [isComplete, setIsComplete] = useState<boolean>(
    prototype.is_complete ?? false,
  )

  // P6-16 (UX-6): client-only open state for the full-screen overlay. Owned here
  // (the stateful container) and threaded into the SSR-renderable pure view,
  // matching the existing `onStateChange` threading pattern.
  const [fullscreenOpen, setFullscreenOpen] = useState<boolean>(false)

  // UX-EXPLORE (throwaway — REVERT): collapsible-panel + control-bar state, owned
  // by the container (same threading pattern as `fullscreenOpen`). LEFT sidebar
  // (PRD + iterate) OPEN by default; RIGHT comments sidebar COLLAPSED by default;
  // the Desktop/Mobile toggle lifted out of PrototypeViewer lives here too.
  const [leftOpen, setLeftOpen] = useState<boolean>(true)
  const [commentsOpen, setCommentsOpen] = useState<boolean>(false)
  const [platform, setPlatform] = useState<Platform>("desktop")

  // UX-EXPLORE (throwaway — REVERT, CHANGE 3): mark-and-comment pin flow state.
  // `markMode` toggles the crosshair overlay; `pins` holds the dropped pins +
  // their (optimistic) comment drafts. Entering mark mode force-opens the right
  // comments sidebar (David's behaviour) so the new comment row is visible.
  const [markMode, setMarkMode] = useState<boolean>(false)
  const [pins, setPins] = useState<PinComment[]>([])
  const pinCounter = useRef<number>(0)

  // UX-EXPLORE (throwaway — REVERT, CHANGE 4): Escape closes the full-screen
  // overlay (in addition to the visible × close button). Bound only while open.
  useEffect(() => {
    if (!fullscreenOpen) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreenOpen(false)
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [fullscreenOpen])

  function toggleMark() {
    setMarkMode((on) => {
      const next = !on
      if (next) setCommentsOpen(true) // mark mode reveals the comments sidebar
      return next
    })
  }

  // Drop a numbered pin at the clicked stage location + open its comment composer.
  function handleStageClick(xPct: number, yPct: number) {
    pinCounter.current += 1
    const n = pinCounter.current
    setPins((prev) => [
      ...prev,
      { n, xPct, yPct, draft: "", body: "", saved: false, busy: false, error: null },
    ])
    setCommentsOpen(true)
    setMarkMode(false) // David exits mark mode per pin
  }

  function handlePinDraftChange(n: number, value: string) {
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, draft: value } : p)))
  }

  function handlePinRemove(n: number) {
    setPins((prev) => prev.filter((p) => p.n !== n))
  }

  // Submit a pin's comment to the AUTHED create endpoint. The anchor_id carries a
  // synthetic pin marker (`pin-<n>`) — the iframe click cannot resolve a real
  // data-anchor-id across the sandbox boundary, and the backend has no x/y field,
  // so the pin's on-canvas position is NOT persisted (UI-only). The comment BODY
  // is persisted. The row stays optimistic until the create resolves.
  async function handlePinSubmit(n: number) {
    const pin = pins.find((p) => p.n === n)
    if (!pin || !pin.draft.trim()) return
    setPins((prev) =>
      prev.map((p) => (p.n === n ? { ...p, busy: true, error: null } : p)),
    )
    try {
      // UX-EXPLORE (throwaway — REVERT, CHANGE B): the authed create returns the
      // CommentRecord with the server-attributed author + created_at — mirror them
      // onto the pin so the saved row shows real identity + a relative timestamp.
      const created = await designAgentApi.createComment(prototype.id, {
        anchor_id: `pin-${n}`,
        body: pin.draft.trim(),
      })
      setPins((prev) =>
        prev.map((p) =>
          p.n === n
            ? {
                ...p,
                body: p.draft.trim(),
                saved: true,
                busy: false,
                error: null,
                author: created?.author ?? "demo",
                createdAt: created?.created_at ?? new Date().toISOString(),
              }
            : p,
        ),
      )
    } catch (e) {
      // Keep the optimistic pin + draft so nothing is lost; surface the error.
      setPins((prev) =>
        prev.map((p) =>
          p.n === n
            ? {
                ...p,
                busy: false,
                error: e instanceof Error ? e.message : "Could not save comment",
              }
            : p,
        ),
      )
    }
  }

  // UX-EXPLORE (throwaway — REVERT, CHANGE D): describe WHERE a pin sits on the
  // canvas so the agent knows where the comment applies. The backend doesn't
  // persist x/y (UI-only — see api.createComment note), so we compose it from the
  // LOCAL pin state: pin number + a human region hint + the raw x/y %.
  function pinRegionHint(xPct: number, yPct: number): string {
    const v = yPct < 33 ? "top" : yPct < 66 ? "middle" : "bottom"
    const h = xPct < 33 ? "left" : xPct < 66 ? "centre" : "right"
    return `${v} ${h}`
  }

  // UX-EXPLORE (throwaway — REVERT, CHANGE C/D): Apply a saved pin comment —
  // pre-fill the LEFT IterateComposer (via the SAME applyTarget seam CommentsPanel
  // uses; ApproveModal threads `onPinApply → setApplyTarget`) with an instruction
  // that includes the pin number + on-canvas position + the comment text, THEN
  // mark the pin resolved. The synthetic CommentRecord's `body` is the composed
  // instruction; `id` is negative so it never collides with a real comment id and
  // is harmless if forwarded as applied_comment_id (the backend treats unknown ids
  // as "no linked comment").
  function handlePinApply(n: number) {
    const pin = pins.find((p) => p.n === n)
    if (!pin || !pin.saved) return
    const region = pinRegionHint(pin.xPct, pin.yPct)
    const instruction = `Re: pin #${pin.n} (near the ${region} of the prototype, at ~${Math.round(
      pin.xPct,
    )}%,${Math.round(pin.yPct)}%): ${pin.body}`
    // UX-EXPLORE (throwaway — REVERT, CHANGE B): pin Apply now runs the iterate
    // IMMEDIATELY through the shared runner (pin context + body as the
    // instruction) when `onPinIterate` is supplied — same fixed path as the
    // composer + comment Apply. Falls back to the old applyTarget pre-fill
    // (`onPinApply`) only when no runner is wired. The agent decides
    // applicability; the client fabricates no change. Then mark the pin resolved.
    if (onPinIterate) {
      onPinIterate(instruction, null)
    } else {
      const synthetic: CommentRecord = {
        id: -pin.n,
        anchor_id: `pin-${pin.n}`,
        body: instruction,
        author: pin.author ?? "demo",
        status: "open",
        created_at: pin.createdAt ?? new Date().toISOString(),
        resolved_at: null,
      }
      onPinApply?.(synthetic)
    }
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, resolved: true } : p)))
  }

  // UX-EXPLORE (throwaway — REVERT, CHANGE C): Ignore — mark the pin resolved
  // WITHOUT pre-filling the composer.
  function handlePinIgnore(n: number) {
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, resolved: true } : p)))
  }

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
      bundleUrl={prototype.bundle_url}
      onStateChange={(state) => setIsComplete(state.isComplete)}
      comments={comments}
      iterate={iterate}
      onShared={onShared}
      fullscreenOpen={fullscreenOpen}
      onOpenFullscreen={() => setFullscreenOpen(true)}
      onCloseFullscreen={() => setFullscreenOpen(false)}
      prdSections={prdSections}
      prdTitle={prdTitle}
      prdMetaLine={prdMetaLine}
      onDone={onDone}
      leftOpen={leftOpen}
      onToggleLeft={() => setLeftOpen((v) => !v)}
      commentsOpen={commentsOpen}
      onToggleComments={() => setCommentsOpen((v) => !v)}
      platform={platform}
      onPlatformChange={(p) => setPlatform(p)}
      markMode={markMode}
      onToggleMark={toggleMark}
      onStageClick={handleStageClick}
      pins={pins}
      onPinDraftChange={handlePinDraftChange}
      onPinSubmit={handlePinSubmit}
      onPinRemove={handlePinRemove}
      onPinApply={handlePinApply}
      onPinIgnore={handlePinIgnore}
      // UX-EXPLORE (throwaway — REVERT, CHANGE E): mount the F12 clarifying-
      // question surface. It self-gates on `prototype.pending_question` (renders
      // null when none/locked), so it's safe to always pass. When the launcher's
      // refetch (onIterated → ApproveModal.refreshCanvas) advances the prototype
      // to a pending-question checkpoint, this prop updates and the surface shows;
      // answering routes a NEW iterate (continues the loop) via the reused
      // designAgentApi.iterate.
      clarifying={<ClarifyingQuestionSurface prototype={prototype} />}
      // UX-EXPLORE (throwaway — REVERT, CHANGE A/B): the live agent-flow activity
      // + inline clarifying answer + pin-Apply immediate-iterate + the iframe
      // reload nonce, all sourced from the shared runner threaded by ApproveModal.
      iterateActivity={iterateActivity}
      iterateRunning={iterateRunning}
      iteratePendingQuestion={iteratePendingQuestion}
      onAnswerQuestion={onAnswerQuestion}
      onPinIterate={onPinIterate}
      bundleReloadNonce={bundleReloadNonce}
    />
  )
}

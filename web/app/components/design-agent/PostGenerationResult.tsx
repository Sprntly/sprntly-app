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
import { PrototypeViewer } from "./PrototypeViewer"
import { ManualEditOverlay } from "./ManualEditOverlay"
import type { PrototypeRecord } from "../../lib/api"

export type PostGenerationResultProps = {
  prototype: PrototypeRecord
  /** P6-13 (UX-3): optional comments node placed in the right cell of the
   *  two-column `design-pane` grid beside the viewer. The signed-in launcher
   *  passes its `<CommentsPanel>` here; the public `/p/<token>` viewer does NOT
   *  use this component (it composes its own chrome) → it passes nothing and the
   *  comments column is omitted. Null-by-default keeps the public shape intact. */
  comments?: ReactNode
  /** P6-20 (#14): forwarded to `<ShareMenu>` — fired after a successful Share so
   *  the launcher re-polls and `result.share_token` goes live (flipping the
   *  share-gated comments column on without a re-mount). Optional/defaulted so the
   *  public-viewer composition and existing direct calls keep type-checking. */
  onShared?: (token: string | null) => void
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
  onShared,
  fullscreenOpen = false,
  onOpenFullscreen,
  onCloseFullscreen,
}: PostGenerationResultViewProps) {
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
  const viewer = bundleUrl && !fullscreenOpen ? (
    <PrototypeViewer
      bundleUrl={bundleUrl}
      isComplete={isComplete}
      chrome={
        <ManualEditOverlay prototypeId={prototypeId} isComplete={isComplete} />
      }
    />
  ) : null
  return (
    <div className="design-agent-surface design-agent-result" data-testid="post-generation-result">
      <CompletionBar
        prototypeId={prototypeId}
        isComplete={isComplete}
        editable
        onStateChange={onStateChange}
      />
      <ShareMenu
        prototypeId={prototypeId}
        initialMode={shareMode}
        initialToken={shareToken}
        onShared={onShared}
      />
      {/* P6-13 (UX-3): two-column pane — viewer left (main cell), comments right
          (320px cell). CompletionBar + ShareMenu stay above as full-width chrome;
          the "View prototype" link stays below. When no `comments` node is
          supplied (e.g. the prototype isn't shared yet), the viewer renders
          full-width with no grid (degrades to a single column). The 1fr/320px
          split + the ≤1080px collapse are CSS (design-agent.css). */}
      {comments ? (
        <div className="design-pane">
          <div className="design-pane-main">{viewer}</div>
          <div className="design-pane-aside">{comments}</div>
        </div>
      ) : (
        viewer
      )}
      {/* P6-16 (UX-6): the always-shown primary View affordance. Never hidden —
          enabled when a bundle exists (opens the full-screen overlay), DISABLED
          with an explanatory label while the prototype is still building. This
          replaces the old `{viewHref && <a target="_blank">}` dead-end (silently
          hidden when no bundle/token; a chrome-less raw new tab otherwise). */}
      <button
        type="button"
        className="btn proto-fullscreen-trigger"
        data-testid="view-fullscreen-trigger"
        disabled={!canOpen}
        onClick={() => onOpenFullscreen?.()}
      >
        {canOpen ? "View full screen" : "Prototype building…"}
      </button>
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
export function PostGenerationResult({ prototype, comments, onShared }: PostGenerationResultProps) {
  const [isComplete, setIsComplete] = useState<boolean>(
    prototype.is_complete ?? false,
  )

  // P6-16 (UX-6): client-only open state for the full-screen overlay. Owned here
  // (the stateful container) and threaded into the SSR-renderable pure view,
  // matching the existing `onStateChange` threading pattern.
  const [fullscreenOpen, setFullscreenOpen] = useState<boolean>(false)

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
      onShared={onShared}
      fullscreenOpen={fullscreenOpen}
      onOpenFullscreen={() => setFullscreenOpen(true)}
      onCloseFullscreen={() => setFullscreenOpen(false)}
    />
  )
}

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

import { useState } from "react"
import { CompletionBar } from "./CompletionBar"
import { ShareMenu, type ShareMode } from "./ShareMenu"
import { PrototypeViewer } from "./PrototypeViewer"
import { ManualEditOverlay } from "./ManualEditOverlay"
import type { PrototypeRecord } from "../../lib/api"

export type PostGenerationResultProps = {
  prototype: PrototypeRecord
}

export type PostGenerationResultViewProps = {
  prototypeId: number
  isComplete: boolean
  shareMode: ShareMode
  shareToken: string | null
  bundleUrl: string | null
  onStateChange?: (state: { isComplete: boolean; staleHandoff: boolean }) => void
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
}: PostGenerationResultViewProps) {
  const viewHref = resolveViewHref(bundleUrl, shareToken)
  return (
    <div className="design-agent-result" data-testid="post-generation-result">
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
      />
      {/* P4-10 — embed the EDITABLE viewer when a built bundle exists. This
          surface only renders inside (app)/AuthGate, so it is internal by
          construction; passing the real numeric `prototypeId` into the overlay
          IS the internal mount that makes F13 manual-edit reachable (AD13). The
          overlay reaches the same-origin iframe (`da-prototype-iframe`) for
          click→select. The public `/p/<token>` mount keeps passing no
          `prototypeId` → the overlay renders nothing (AC10 preserved, untouched
          here). The link-out below is kept as a full-screen "open in new tab"
          affordance. */}
      {bundleUrl && (
        <PrototypeViewer
          bundleUrl={bundleUrl}
          isComplete={isComplete}
          chrome={
            <ManualEditOverlay prototypeId={prototypeId} isComplete={isComplete} />
          }
        />
      )}
      {viewHref && (
        <a
          className="btn"
          href={viewHref}
          data-testid="view-prototype-link"
          target="_blank"
          rel="noreferrer"
        >
          View prototype
        </a>
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
export function PostGenerationResult({ prototype }: PostGenerationResultProps) {
  const [isComplete, setIsComplete] = useState<boolean>(
    prototype.is_complete ?? false,
  )

  return (
    <PostGenerationResultView
      prototypeId={prototype.id}
      isComplete={isComplete}
      shareMode={prototype.share_mode ?? "private"}
      shareToken={prototype.share_token ?? null}
      bundleUrl={prototype.bundle_url}
      onStateChange={(state) => setIsComplete(state.isComplete)}
    />
  )
}

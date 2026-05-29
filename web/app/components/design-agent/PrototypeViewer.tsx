"use client"
// Reusable public-viewer primitive (P2-05). First component in the public
// `/p/<token>` surface; intentionally extracted so P2-10 (CompletionBar +
// ShareMenu) and P3 (comments panel) mount their chrome here without
// re-implementing the iframe shell. No providers, no auth — it renders a
// bundle URL and an inert chrome slot, nothing else.
import type { ReactNode } from "react"

type Props = {
  bundleUrl: string
  isComplete: boolean
  /** P2-10 passes <CompletionBar/> + <ShareMenu/>; P3 will pass <CommentsPanel/>. */
  chrome?: ReactNode
}

export function PrototypeViewer({ bundleUrl, isComplete, chrome }: Props) {
  return (
    <div
      className="da-prototype-viewer"
      // Exposed for chrome (P2-10) and tests; not load-bearing for layout.
      data-complete={isComplete ? "true" : "false"}
    >
      {/* The chrome slot is ALWAYS rendered (even when `chrome` is undefined) so
          the testid stays queryable and P2-10/P3 have a stable mount point — an
          empty div is the no-op state (AC9). */}
      <div className="da-prototype-chrome" data-testid="prototype-chrome">
        {chrome}
      </div>
      <iframe
        src={bundleUrl}
        title="Generated prototype"
        // sandbox is EXACTLY scripts (the React bundle runs) + same-origin (it
        // fetches its own static assets). No allow-forms / allow-popups /
        // allow-top-navigation: the prototype is a self-contained static SPA
        // and must not be able to navigate the parent or post off-bundle.
        sandbox="allow-scripts allow-same-origin"
        className="da-prototype-iframe"
      />
    </div>
  )
}

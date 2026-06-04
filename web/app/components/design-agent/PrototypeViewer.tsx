"use client"
// Reusable prototype-viewer primitive (P2-05). Mounted by BOTH the signed-in
// PostGenerationResult and the public `/p/<token>` PublicTokenViewer; chrome
// (CompletionBar / ShareMenu / CommentsPanel / ManualEditOverlay) mounts in the
// always-rendered `da-prototype-chrome` slot without re-implementing the shell.
//
// P6-12 (UX-2) — wraps the bare iframe in the v3-mockup browser-frame device
// chrome (three traffic-light dots + a cosmetic URL bar) plus a Desktop/Mobile
// platform toggle that swaps the stage class (and only the class — the single
// iframe is never re-mounted, so the bundle stays loaded and ManualEditOverlay
// keeps its selection). The outer `.da-prototype-viewer` already supplies the
// bordered/rounded browser-frame surface; the inner `.proto-frame` adds only the
// 580px dominant height + head/stage layout (see design-agent.css). The
// `da-prototype-chrome` + `da-prototype-iframe` classNames are LOCKED —
// ManualEditOverlay reaches the iframe by selector and tests query the slot.
import { useState, type ReactNode } from "react"

export type Platform = "desktop" | "mobile"

/** The stage wrapper class for a platform. The toggle swaps THIS class (not the
 *  iframe), which the CSS uses to constrain width + apply the phone bezel.
 *  Exported as a pure unit so the desktop↔mobile mapping is assertable in the
 *  repo's node-env vitest (no DOM to drive a real click). */
export function stageClass(platform: Platform): string {
  return `proto-stage ${platform}`
}

/** The static URL-bar fallback. The bar is cosmetic, non-navigable chrome (no
 *  href); a later ticket can plumb a real `{company}/{feature}` slug via the
 *  optional `urlSlug` prop without another signature change. */
export const DEFAULT_URL_SLUG = "sprntly.com/preview"

type Props = {
  bundleUrl: string
  isComplete: boolean
  /** CompletionBar / ShareMenu (signed-in) or read-only chrome (public). */
  chrome?: ReactNode
  /** Cosmetic URL-bar text — `sprntly.com/{company}/{feature}`. NOT a link.
   *  Optional: the static default covers both call sites with no signature
   *  change (see ticket URL-slug Decision). */
  urlSlug?: string
  /** Test seam only — node-env vitest can't drive a real toggle click, so the
   *  initial platform is injectable to assert both rendered branches. Production
   *  call sites never set it; defaults to "desktop". */
  initialPlatform?: Platform
}

export function PrototypeViewer({
  bundleUrl,
  isComplete,
  chrome,
  urlSlug,
  initialPlatform = "desktop",
}: Props) {
  const [platform, setPlatform] = useState<Platform>(initialPlatform)
  const slug = urlSlug ?? DEFAULT_URL_SLUG
  return (
    <div
      className="da-prototype-viewer"
      // Exposed for chrome (P2-10) and tests; not load-bearing for layout.
      data-complete={isComplete ? "true" : "false"}
    >
      <div className="proto-frame">
        {/* browser-frame head: traffic lights + cosmetic URL bar + toggle */}
        <div className="proto-frame-head">
          <span className="proto-dot r" />
          <span className="proto-dot y" />
          <span className="proto-dot g" />
          <span className="proto-url" data-testid="proto-url">
            {slug}
          </span>
          <div
            className="platform-toggle"
            role="group"
            aria-label="Preview platform"
          >
            <button
              type="button"
              className={platform === "desktop" ? "active" : ""}
              aria-pressed={platform === "desktop"}
              onClick={() => setPlatform("desktop")}
            >
              Desktop
            </button>
            <button
              type="button"
              className={platform === "mobile" ? "active" : ""}
              aria-pressed={platform === "mobile"}
              onClick={() => setPlatform("mobile")}
            >
              Mobile
            </button>
          </div>
        </div>
        {/* The chrome slot is ALWAYS rendered (even when `chrome` is undefined)
            so the testid stays queryable and the overlay has a stable mount
            point — an empty div is the no-op state. */}
        <div className="da-prototype-chrome" data-testid="prototype-chrome">
          {chrome}
        </div>
        {/* Single iframe; the toggle swaps the stage class only (no re-mount). */}
        <div className={stageClass(platform)} data-testid="proto-stage">
          <iframe
            src={bundleUrl}
            title="Generated prototype"
            // sandbox is EXACTLY scripts (the React bundle runs) + same-origin
            // (it fetches its own static assets). Owned by P6-17 (which adds
            // allow-forms) — carried byte-identical here.
            sandbox="allow-scripts allow-same-origin"
            className="da-prototype-iframe"
          />
        </div>
      </div>
    </div>
  )
}

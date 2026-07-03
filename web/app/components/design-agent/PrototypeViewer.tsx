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
  /** UX-EXPLORE (throwaway — REVERT): controlled-platform seam. When BOTH are
   *  supplied the viewer becomes controlled — it reads `platform` from the prop
   *  and reports clicks via `onPlatformChange` instead of owning local state.
   *  PostGenerationResult uses this to LIFT the Desktop/Mobile toggle up into the
   *  new top control bar (the toggle then renders in the control bar, not here).
   *  Uncontrolled call sites (the public viewer, fullscreen overlay) pass neither
   *  and keep the in-frame toggle + local state exactly as before. */
  platform?: Platform
  onPlatformChange?: (platform: Platform) => void
  /** UX-EXPLORE (throwaway — REVERT): when true the in-frame Desktop/Mobile
   *  toggle is NOT rendered (it has been lifted into the control bar). The stage
   *  class still tracks `platform`, so canvas width still switches. */
  hideToggle?: boolean
  /** Per-device toggle visibility, derived by the caller from the prototype's
   *  `target_platform` (mirrors DaControlBar). When a SINGLE device applies
   *  (`showDesktop` XOR `showMobile`) the in-frame toggle group is suppressed —
   *  there is nothing to toggle to. Default both true so every existing caller
   *  (public viewer, direct view) keeps rendering both buttons unchanged. The
   *  stage class still tracks `platform`, so the canvas width stays correct. */
  showDesktop?: boolean
  showMobile?: boolean
  /** C2a: optional control group rendered in the browser-frame head, to the
   *  right of the platform toggle (e.g. the public viewer's Mark + Comment
   *  buttons). Purely additive — when undefined nothing extra renders, so the
   *  signed-in PostGenerationResult call site (which passes no headControls) is
   *  byte-for-byte unchanged. */
  headControls?: ReactNode
  /** When true, the COSMETIC browser-frame decoration — the three traffic-light
   *  dots + the non-navigable URL bar — is NOT rendered, so the iframe sits
   *  edge-to-edge. Set ONLY on the signed-in non-fullscreen editor preview, where
   *  the Desktop/Mobile toggle is already lifted into the top control bar. The
   *  public `/p/<token>` viewer and the fullscreen overlay leave this unset and
   *  keep the full chrome. The FUNCTIONAL toggle (and any `headControls`) is NOT
   *  suppressed by this flag — only the decoration is — so a call site that keeps
   *  its in-frame toggle still renders it. */
  hideChrome?: boolean
  /** Bundle-proxy view-grant: called when the authed bundle iframe fails to load
   *  (e.g. an asset GET 401s because the `da_view_grant` cookie expired or was
   *  revoked). The authed container wires this to a BOUNDED single re-mint
   *  (useViewGrant). Absent on the public `/p/<token>` surface — that path is
   *  token-in-URL and never mints a grant. The browser fires the iframe `error`
   *  event on a failed top-document (index.html) load; per-subresource 401s on a
   *  same-origin bundle are tester-verified in the browser lane. */
  onAssetError?: () => void
  /** Bundle-readiness: called on the iframe `onLoad`. A 404-bodied document
   *  fires `load` (not `error`), so the authed container probes the real status
   *  here to detect a briefly-unavailable bundle and cover it with a loading
   *  state instead of the raw 404 body. Absent on the public `/p/<token>` surface
   *  — that path is unchanged (no probe, no onLoad handler). */
  onBundleLoad?: () => void
  /** C2b: optional overlay rendered INSIDE `.proto-stage` (which is
   *  position:relative), layered OVER the iframe. The public viewer passes the
   *  mark overlay + pin layer here so marking renders on top of the prototype.
   *  Purely additive — undefined renders nothing, so the signed-in
   *  PostGenerationResult call site (which keeps its own `.da-stage` overlay and
   *  passes no stageOverlay) is byte-for-byte unchanged. */
  stageOverlay?: ReactNode
  /** Opt-in load mask (Glitch A): while set, a NEUTRAL surface-colored cover is
   *  rendered over the iframe until its FIRST `load` event fires, so the black
   *  initial-paint / grant-mint gap is never shown to the user. The cover is keyed
   *  to THIS viewer instance's mount, so a genuine reload (a new `key` remounts the
   *  viewer) re-shows it until the fresh bundle paints. Undefined/false on the
   *  public `/p/<token>` surface and every other caller → nothing extra renders, so
   *  those paths are byte-for-byte unchanged. */
  maskUntilLoaded?: boolean
}

export function PrototypeViewer({
  bundleUrl,
  isComplete,
  chrome,
  urlSlug,
  initialPlatform = "desktop",
  platform: platformProp,
  onPlatformChange,
  hideToggle = false,
  showDesktop = true,
  showMobile = true,
  headControls,
  stageOverlay,
  onAssetError,
  onBundleLoad,
  hideChrome = false,
  maskUntilLoaded = false,
}: Props) {
  // Glitch A: mask the iframe with a neutral surface cover until it has painted
  // (first `load`). Local to this instance, so a genuine reload (new `key`
  // remounts the viewer) resets it and re-masks the fresh bundle's dark
  // pre-paint. A passive re-render never resets it.
  const [loaded, setLoaded] = useState(false)
  const handleLoad = () => {
    setLoaded(true)
    onBundleLoad?.()
  }
  // UX-EXPLORE (throwaway — REVERT): controlled when a `platform` prop is given;
  // otherwise own the state locally as before. Either way `platform` below is the
  // effective value the stage class reads, so the single iframe is never
  // re-mounted on a switch.
  const [platformState, setPlatformState] = useState<Platform>(initialPlatform)
  const controlled = platformProp != null
  const platform = controlled ? platformProp : platformState
  const setPlatform = (p: Platform) => {
    if (!controlled) setPlatformState(p)
    onPlatformChange?.(p)
  }
  const slug = urlSlug ?? DEFAULT_URL_SLUG
  // The in-frame toggle renders only when it's not lifted out (`hideToggle`) AND
  // both devices apply. A single-device prototype (showDesktop XOR showMobile)
  // has nothing to toggle to, so the group is suppressed — mirroring DaControlBar.
  const showToggle = !hideToggle && showDesktop && showMobile
  return (
    <div
      className="da-prototype-viewer"
      // Exposed for chrome (P2-10) and tests; not load-bearing for layout.
      data-complete={isComplete ? "true" : "false"}
    >
      <div className="proto-frame">
        {/* browser-frame head: cosmetic decoration (traffic lights + URL bar) +
            the functional toggle + headControls. The head wrapper renders only
            when it has SOMETHING to show — so when `hideChrome` suppresses the
            decoration AND the toggle has been lifted out (`hideToggle`), the head
            disappears entirely and the iframe sits edge-to-edge. The toggle and
            headControls are NOT gated by `hideChrome` — only the decoration is —
            so a caller that keeps its in-frame toggle still renders it. */}
        {(!hideChrome || showToggle || headControls) && (
          <div className="proto-frame-head">
            {/* Cosmetic, non-navigable decoration — suppressed for the signed-in
                edge-to-edge editor preview via `hideChrome`. */}
            {!hideChrome && (
              <>
                <span className="proto-dot r" />
                <span className="proto-dot y" />
                <span className="proto-dot g" />
                <span className="proto-url" data-testid="proto-url">
                  {slug}
                </span>
              </>
            )}
            {/* The toggle is hidden when lifted
                into the control bar (`hideToggle`); otherwise it renders in-frame
                exactly as before for the public viewer + fullscreen overlay. */}
            {showToggle && (
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
            )}
            {/* C2a: right-aligned head control group (public viewer's Mark +
                Comment buttons). The `.proto-url` flex:1 pushes this to the right
                edge alongside the toggle. Renders nothing when omitted. */}
            {headControls && (
              <div className="proto-head-controls">{headControls}</div>
            )}
          </div>
        )}
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
            // (it fetches its own static assets) + forms (so a form-centric
            // prototype can submit its own in-bundle, JS-handled form — without
            // allow-forms a sandboxed iframe blocks form submission entirely).
            // allow-popups / allow-top-navigation / allow-top-navigation-by-user-activation
            // remain DELIBERATELY omitted: the prototype is untrusted model-generated
            // code and must never open new windows or navigate the parent host away.
            sandbox="allow-scripts allow-same-origin allow-forms"
            className="da-prototype-iframe"
            // View-grant: a failed top-document load (e.g. index.html 401 after the
            // grant lapsed) fires `error`; the authed container re-mints ONCE
            // (bounded). No-op when no handler is wired (public surface).
            onError={onAssetError ? () => onAssetError() : undefined}
            // A 404-bodied document fires `load`, not `error`; the authed
            // container probes the real status here to cover a briefly-
            // unavailable bundle. No-op when no handler is wired (public surface).
            // When the load mask is opted in we also need the first `load` to
            // lift the cover, so wire `handleLoad` whenever EITHER concern applies.
            onLoad={
              maskUntilLoaded || onBundleLoad ? handleLoad : undefined
            }
          />
          {/* Glitch A: neutral surface cover over the iframe until it paints.
              Opt-in (signed-in editor + fullscreen) — the public surface omits
              `maskUntilLoaded` so it renders nothing here. */}
          {maskUntilLoaded && !loaded && (
            <div
              className="da-viewer-placeholder"
              data-testid="da-viewer-placeholder"
              aria-hidden="true"
            />
          )}
          {/* C2b: optional marking overlay, layered over the iframe inside the
              position:relative stage. Undefined → nothing (signed-in path). */}
          {stageOverlay}
        </div>
      </div>
    </div>
  )
}

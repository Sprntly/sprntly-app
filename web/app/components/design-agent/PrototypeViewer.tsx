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
import { useEffect, useState, type ReactNode } from "react"

export type Platform = "desktop" | "mobile"

/** Load-mask fallback deadline: a stalled bundle (hung signed-URL fetch, dead
 *  asset host) may never fire `load`, and the neutral cover must not stay up
 *  forever. Bundles are static SPAs that typically paint in well under 2s;
 *  8s covers a slow cold signed-URL fetch while capping the worst-case blank
 *  cover. The timeout lifts the COVER only — it never synthesizes a load
 *  signal (`onBundleLoad` stays a real load-event callback). */
const MASK_TIMEOUT_MS = 8000

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
   *  — that path is unchanged (no probe, no onLoad handler). May return a
   *  Promise that resolves once the readiness decision is in — when it does,
   *  the load mask stays up until then instead of clearing on the raw `load`
   *  event, closing the gap where a freshly-rebuilt bundle's raw 404/401 body
   *  could otherwise be briefly exposed. A synchronous (non-thenable) return
   *  keeps today's behavior unchanged. */
  onBundleLoad?: () => void | Promise<void>
  /** C2b: optional overlay rendered INSIDE `.proto-stage` (which is
   *  position:relative), layered OVER the iframe. The public viewer passes the
   *  mark overlay + pin layer here so marking renders on top of the prototype.
   *  Purely additive — undefined renders nothing, so the signed-in
   *  PostGenerationResult call site (which keeps its own `.da-stage` overlay and
   *  passes no stageOverlay) is byte-for-byte unchanged. */
  stageOverlay?: ReactNode
  /** Opt-in load mask (Glitch A): while set, a NEUTRAL surface-colored cover is
   *  rendered over the iframe until its FIRST `load` event fires — or until the
   *  MASK_TIMEOUT_MS fallback lifts it for a bundle that never loads — so the
   *  black/white initial-paint / grant-mint gap is never shown to the user. The
   *  cover is keyed to THIS viewer instance's mount, so a genuine reload (a new
   *  `key` remounts the viewer) re-shows it until the fresh bundle paints. Both
   *  production mounts opt in (the signed-in editor's PostGenerationResult and
   *  the public/passcode PublicPrototypeChrome); undefined/false — the default
   *  for any direct mount that doesn't opt in — renders nothing extra, keeping
   *  that path byte-for-byte unchanged. */
  maskUntilLoaded?: boolean
  /** Iterate-aware label for the load mask: true only while a genuine
   *  iterate/apply is running, so the mask reads "Applying changes…";
   *  false/undefined (a passive reload — the manual "Refresh preview"
   *  button, or a brand-new prototype's first load) reads the neutral
   *  "Loading…". Mirrors PostGenerationResult's existing bundle-not-ready
   *  label logic verbatim, threaded one level down so both covers show
   *  identical copy for the identical situation. Optional; undefined →
   *  "Loading…", so every other call site (public viewer, fullscreen
   *  overlay, any direct mount) is unaffected. */
  iterateRunning?: boolean
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
  iterateRunning,
}: Props) {
  // Glitch A: mask the iframe with a neutral surface cover until it has painted
  // (first `load`). Local to this instance, so a genuine reload (new `key`
  // remounts the viewer) resets it and re-masks the fresh bundle's dark
  // pre-paint. A passive re-render never resets it.
  const [loaded, setLoaded] = useState(false)
  const handleLoad = () => {
    // A readiness-aware caller (useViewGrant's notifyBundleLoaded) returns a
    // promise that resolves only once the async proxy-status preflight has
    // decided ready / not-ready / unauthorized. Keep the mask up until THEN —
    // closing the race where a freshly-rebuilt bundle's raw 404 body (or a
    // lapsed-grant 401 body) would otherwise be exposed for the gap between
    // this synchronous `load` event and that async decision. A caller with no
    // onBundleLoad, or one that returns a plain (non-thenable) value — the
    // public `/p/<token>` surface, which passes neither — keeps the ORIGINAL
    // synchronous clear below, byte-identical to today.
    const readiness = onBundleLoad?.()
    if (readiness && typeof (readiness as Promise<void>).then === "function") {
      void (readiness as Promise<void>).then(() => setLoaded(true))
    } else {
      setLoaded(true)
    }
  }
  // Timeout fallback: while masking and still unloaded, lift the cover after
  // MASK_TIMEOUT_MS so a stalled bundle can never leave it up forever. Lifting
  // reuses the same `loaded` state the `load` handler sets (no second flag) but
  // deliberately does NOT call `onBundleLoad` — that stays a load-event signal.
  // No-op for callers without `maskUntilLoaded`; cleared on unmount / on load.
  useEffect(() => {
    if (!maskUntilLoaded || loaded) return
    const t = setTimeout(() => setLoaded(true), MASK_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [maskUntilLoaded, loaded])
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
          {/* Glitch A (reskinned): scrim + spinner + iterate-aware label cover
              over the iframe until it paints AND (when a readiness-aware
              onBundleLoad is wired) the readiness decision is in — or until
              the MASK_TIMEOUT_MS fallback lifts it. Opt-in — both production
              mounts (signed-in editor + public/passcode chrome) set
              `maskUntilLoaded`; the default renders nothing here. */}
          {maskUntilLoaded && !loaded && (
            <div
              className="da-viewer-placeholder"
              data-testid="da-viewer-placeholder"
              role="status"
              aria-live="polite"
            >
              <span className="da-spinner da-bundle-loading-spinner" aria-hidden="true" />
              <span className="da-bundle-loading-label">
                {iterateRunning ? "Applying changes…" : "Loading…"}
              </span>
            </div>
          )}
          {/* C2b: optional marking overlay, layered over the iframe inside the
              position:relative stage. Undefined → nothing (signed-in path). */}
          {stageOverlay}
        </div>
      </div>
    </div>
  )
}

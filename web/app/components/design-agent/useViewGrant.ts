"use client"

// Bundle-proxy view-grant flow (Option B — same-origin serving).
//
// PROBLEM this solves: the authed prototype bundle is now served through the
// app-origin proxy (`/_da-bundle/...`). The iframe's asset subresource GETs
// (index.html, assets/*.js, fonts) can send ONLY cookies — never an
// `Authorization: Bearer` header — so a short-lived, HttpOnly, path-scoped
// `da_view_grant` cookie is the credential. It is minted by a single
// bearer-authed POST (`designAgentApi.viewGrant`, which rides the bearer via the
// shared api.ts request path + `credentials: 'include'`), and the browser then
// attaches it automatically to the same-site iframe asset GETs.
//
// SEQUENCE (authed surface ONLY): mint the grant BEFORE the iframe `src` is set.
// This hook gates the authed `bundleUrl` — it returns `null` until the grant
// POST resolves, then exposes the (opaque) proxy bundle URL the viewer loads
// verbatim (the hook never parses it). The public `/p/<token>` path does NOT use
// this hook — it is token-in-URL and never mints a grant.
//
// BOUNDED RE-MINT: the grant TTL is short, so a long-lived viewing
// session can outlive its grant — the next asset GET then 401s. On such a
// failure the caller invokes `notifyAssetError()`, which re-mints the grant
// EXACTLY ONCE and forces a fresh iframe load (via a bumped `reloadKey`). If the
// asset still fails after that single re-mint (e.g. the grant was revoked
// mid-session — the workspace flipped the prototype private, or ownership
// changed), the hook surfaces an error and does NOT retry again. RETRY CAP = 1.
// No infinite mint↔401 loop.
import { useCallback, useEffect, useRef, useState } from "react"
import { designAgentApi } from "../../lib/api"

/** The single re-mint cap. One re-mint after the initial mint, then
 *  the hook surfaces an error rather than looping. The cap bounds a SINGLE
 *  mint→preflight→re-mint cycle; it is intentionally re-armed (the attempt counter
 *  is reset) when a NEW lapse is detected — e.g. the tab regains focus after the
 *  grant TTL elapsed — so the viewer can recover for its whole lifetime without a
 *  manual reload, while still never looping within one lapse. */
export const VIEW_GRANT_REMINT_CAP = 1

/** How often to proactively re-mint the grant while a bundle is being viewed.
 *  Sits comfortably under the backend grant TTL (currently 600s) so the cookie is
 *  refreshed BEFORE it can expire — an idle-but-open viewer never reaches the 401
 *  even without a visibility/focus event to trigger recovery.
 *
 *  Defaults to 5 minutes. Overridable at build time via
 *  `NEXT_PUBLIC_DA_GRANT_REFRESH_MS` (a positive integer of milliseconds) so the
 *  cadence can be tuned per environment without a code change — unset/invalid
 *  falls back to the 5-minute default, so production is unchanged. */
export const GRANT_REFRESH_INTERVAL_MS = (() => {
  const override = Number(process.env.NEXT_PUBLIC_DA_GRANT_REFRESH_MS)
  return Number.isFinite(override) && override > 0 ? override : 5 * 60 * 1000
})()

export type ViewGrantState = {
  /** The opaque proxy bundle URL to load into the authed iframe, or null while
   *  the grant has not yet been minted (or has been lost without a bundle). The
   *  caller MUST NOT set the iframe `src` until this is non-null. */
  grantedBundleUrl: string | null
  /** A user-facing error once minting fails terminally (initial mint failed, or
   *  the bounded re-mint was exhausted). Null while healthy / pending. */
  error: string | null
  /** True while a mint (initial or re-mint) is in flight. */
  pending: boolean
  /** Bumped on a successful (re)mint so the caller can force a fresh iframe load
   *  after a re-mint (use it in the iframe React `key` or as a cache-bust nonce).
   *  Starts at 0 for the first grant — the caller leaves the clean first load
   *  untouched and only reacts to increments. */
  reloadKey: number
  /** Called by the viewer when an asset/iframe load 401s (grant missing/expired).
   *  Re-mints ONCE (bounded by VIEW_GRANT_REMINT_CAP); a second failure surfaces
   *  an error instead of re-minting again. No-op once the cap is hit. */
  notifyAssetError: () => void
  /** Manual recovery for the TERMINAL error state (initial mint failed, or the
   *  bounded re-mint was exhausted) — wired to the caller's own "Refresh
   *  preview" action. Resets the re-mint budget and retries the mint via the
   *  SAME path `notifyAssetError` uses (no duplicated mint-dispatch logic).
   *  No-op while healthy (`error === null`) — an explicit refresh click on an
   *  already-working viewer must not trigger a spurious extra mint/reload; the
   *  caller's own bundle-reload-nonce cascade already covers that case. */
  retryAfterError: () => void
  /** True while the bundle is briefly unavailable (a 404 through the proxy), so
   *  the caller can cover the iframe with a neutral loading state instead of the
   *  raw 404 body. Cleared automatically once a re-probe sees the bundle ready. */
  notReady: boolean
  /** Called by the viewer on the iframe `onLoad`. A 404-bodied document fires
   *  `load`, not `error`, so this credentialed probe inspects the real status:
   *  a 401 routes through the bounded re-mint; a 404 sets `notReady` + starts a
   *  bounded readiness retry that reloads the iframe once the bundle is ready;
   *  an ok clears `notReady`. Returns a Promise that resolves once the
   *  readiness decision has been made (handleReadiness has run for this
   *  call's preflight result) — not merely once the fetch is issued. The
   *  caller (the viewer) awaits this before clearing its own load mask,
   *  closing the gap where the mask cleared on the raw `onLoad` while the
   *  readiness cover hadn't yet had a chance to activate for the same
   *  reload. */
  notifyBundleLoaded: () => Promise<void>
}

/** Pure decision step for `notifyAssetError` — extracted so the bounded-re-mint
 *  cap is unit-testable in node-env vitest (no DOM). Given how many re-mints have
 *  already happened, decide whether to re-mint again or surface a terminal error.
 *  `attempts` is the count of re-mints ALREADY performed (0 = none yet). */
export function shouldRemint(attempts: number): {
  remint: boolean
  surfaceError: boolean
} {
  if (attempts < VIEW_GRANT_REMINT_CAP) return { remint: true, surfaceError: false }
  return { remint: false, surfaceError: true }
}

/** How long to wait between bundle-readiness re-probes, and how many to attempt
 *  before giving up. The bundle proxy can briefly 404 a freshly-staged build
 *  (the prod incident); a manual reload always recovered, so a bounded poll
 *  recovers it automatically. ~20 attempts × 1200ms ≈ 24s — comfortably past a
 *  transient staging gap, but it CANNOT loop forever. */
export const BUNDLE_READY_RETRY_MS = 1200
export const BUNDLE_READY_RETRY_CAP = 20

/** Pure decision step for the iframe-load readiness probe — extracted so the
 *  branch is unit-testable in node-env vitest (no DOM). Maps a `preflightBundle`
 *  result to the action the hook should take:
 *   - "unauthorized" → the grant lapsed; route through the bounded re-mint path.
 *   - "notready"     → the bundle is briefly 404ing; show the loading state and
 *                      keep re-probing until it is ready.
 *   - "ok"           → the bundle loaded cleanly; clear any loading state. */
export function readinessAction(
  status: "ok" | "unauthorized" | "notready",
): "remint" | "retry" | "clear" {
  if (status === "unauthorized") return "remint"
  if (status === "notready") return "retry"
  return "clear"
}

/**
 * Probe the granted bundle's top document (index.html) with a credentialed GET.
 *
 * WHY: a 401-bodied index.html is a SUCCESSFUL load to the browser — it renders
 * the JSON error body (`{"detail":"grant required"}`) and fires the iframe `load`
 * event, NOT `error`. So the iframe `onError` handler can't detect a lapsed or
 * withheld `da_view_grant` (the real prod incident). This explicit preflight can:
 * it inspects the HTTP status the iframe load itself would hide. Same-origin +
 * `credentials: "include"` so the host-only path-scoped grant cookie attaches,
 * exactly as the iframe asset GETs do.
 *
 * Returns "unauthorized" on a 401 (the case the bounded re-mint must handle);
 * "notready" on a 404 (the bundle is briefly unavailable through the proxy — a
 * not-yet-staged / transiently-missing build; the iframe would paint the raw
 * `{"detail":"Not found"}` 404 body, which fires `load` not `error`); "ok"
 * otherwise — including a network/transient failure, which would also fail the
 * real iframe load and fire `onError`, so it stays on that path. Only
 * "unauthorized" ever drives the grant re-mint; "notready" drives the bounded
 * readiness retry instead, so neither burns the other's budget.
 */
export async function preflightBundle(
  bundleUrl: string,
  fetchImpl?: typeof fetch,
): Promise<"ok" | "unauthorized" | "notready"> {
  const doFetch = fetchImpl ?? fetch
  try {
    const res = await doFetch(bundleUrl, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    })
    if (res.status === 401) return "unauthorized"
    if (res.status === 404) return "notready"
    return "ok"
  } catch {
    return "ok"
  }
}

/**
 * Mint a `da_view_grant` for the authed bundle iframe, gating `bundleUrl` until
 * the grant exists, with a bounded single re-mint on a later asset 401.
 *
 * @param prototypeId  the prototype whose bundle the iframe will load
 * @param bundleUrl    the opaque proxy bundle URL (null until the row is ready);
 *                     loaded verbatim once the grant is minted — never parsed.
 */
export function useViewGrant(
  prototypeId: number,
  bundleUrl: string | null,
  checkpointId: number | null = null,
): ViewGrantState {
  const [grantedBundleUrl, setGrantedBundleUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  // True while the bundle is briefly 404ing through the proxy, so the caller can
  // cover the iframe with a neutral loading state instead of the raw 404 body.
  const [notReady, setNotReady] = useState(false)

  // Count of re-mints already performed for the CURRENT bundle (reset whenever a
  // fresh bundle url arrives — a new build / checkpoint gets a fresh budget).
  const remintAttemptsRef = useRef(0)
  // Guards against overlapping mints (StrictMode double-invoke, rapid calls).
  const mintingRef = useRef(false)
  // The pending readiness-retry timer + a guard against overlapping retry loops
  // (mirrors mintingRef). Cleared on unmount and whenever the bundle url changes.
  const readyRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const readyRetryingRef = useRef(false)

  const mint = useCallback(
    async (url: string, isRemint: boolean, forceReload = true) => {
      if (mintingRef.current) return
      mintingRef.current = true
      setPending(true)
      try {
        // Option A: mint via the app-origin /_da-bundle/ view-grant path derived
        // from the bundle URL (…/bundle/<asset>[?v=] → …/view-grant), so the grant
        // cookie is first-party to the serving (app) origin — not the API origin.
        await designAgentApi.viewGrant(url.replace(/\/bundle\/.*$/, "/view-grant"))
        setError(null)
        // Expose the bundle url only AFTER the grant cookie is set, so the iframe
        // asset GETs carry it on the very first request.
        setGrantedBundleUrl(url)
        // Bump the reload key on a re-mint so the caller can force a fresh load of
        // the now-re-authorized iframe. The initial mint leaves it at 0 (clean
        // first load — no cache-bust needed). A PROACTIVE refresh (forceReload =
        // false) renews the cookie silently and leaves the key untouched so it does
        // NOT interrupt an active viewing session with an iframe reload.
        if (isRemint && forceReload) setReloadKey((k) => k + 1)
      } catch {
        // Mint failed (network / 401 / 404 / 429). For the initial mint and a
        // reactive recovery re-mint (forceReload = true), withhold the bundle url
        // and surface an error — we don't yet have (or have lost) a usable grant,
        // so loading the iframe would only 401 on assets.
        //
        // A PROACTIVE silent renewal (forceReload = false) is different: it runs
        // on a timer while a still-valid grant is already exposed, refreshing the
        // cookie BEFORE it can expire. A transient failure there must NOT tear
        // down a healthy viewer — the current grant is almost certainly still
        // valid, and a real lapse is independently caught by the preflight /
        // visibility recovery. So a failed proactive renewal is swallowed; the
        // next interval (or a genuine 401) handles it.
        if (forceReload) {
          setGrantedBundleUrl(null)
          setError("Couldn't load the prototype. Please refresh and try again.")
        }
      } finally {
        setPending(false)
        mintingRef.current = false
      }
    },
    [prototypeId],
  )

  // Initial mint (and re-mint when a fresh bundle url OR a new checkpoint
  // arrives). A checkpoint advance means the bundle CONTENT changed even when
  // the url string didn't (the common in-place-overwrite iterate case) — the
  // grant bound to the OLD checkpoint is already semantically stale even
  // though it's still within its TTL, so treat a checkpoint change exactly
  // like a bundle url change: reset the re-mint budget and mint fresh.
  useEffect(() => {
    if (!bundleUrl) {
      // No bundle yet (still generating) — nothing to grant; clear any stale grant.
      setGrantedBundleUrl(null)
      setError(null)
      remintAttemptsRef.current = 0
      return
    }
    remintAttemptsRef.current = 0
    void mint(bundleUrl, false)
  }, [bundleUrl, checkpointId, mint])

  const notifyAssetError = useCallback(() => {
    if (!bundleUrl) return
    const { remint, surfaceError } = shouldRemint(remintAttemptsRef.current)
    if (remint) {
      remintAttemptsRef.current += 1
      void mint(bundleUrl, true)
    } else if (surfaceError) {
      setGrantedBundleUrl(null)
      setError("Couldn't load the prototype. Please refresh and try again.")
    }
  }, [bundleUrl, mint])

  // Manual recovery for the state notifyAssetError's own cap has exhausted (or
  // the very first mint failed). Mirrors recoverIfLapsed's reset-then-remint
  // shape (below) but guards on `error` rather than preflighting
  // `grantedBundleUrl` — in the terminal state grantedBundleUrl is ALREADY
  // null, so there is nothing to preflight; the caller (an explicit "Refresh
  // preview" click) already knows the grant is broken, so we skip straight to
  // a fresh, single bounded remint. No-op while healthy — an explicit refresh
  // on an already-working viewer must not remint spuriously.
  const retryAfterError = useCallback(() => {
    if (error === null) return
    remintAttemptsRef.current = 0
    notifyAssetError()
  }, [error, notifyAssetError])

  // Cancel a pending readiness-retry loop (on unmount / bundle change).
  const clearReadyRetry = useCallback(() => {
    if (readyRetryTimerRef.current !== null) {
      clearTimeout(readyRetryTimerRef.current)
      readyRetryTimerRef.current = null
    }
    readyRetryingRef.current = false
  }, [])

  // BUNDLE-READINESS RECOVERY (shared): given a preflight `status` for the granted
  // bundle, route it. Reused by BOTH the iframe `onLoad` probe (notifyBundleLoaded)
  // AND the post-(re)mint preflight effect, so a transient-404 is closed on the
  // first load / right after a (re)mint with NO second retry mechanism:
  //   - unauthorized (401) → the grant lapsed; hand off to the bounded re-mint.
  //   - notready (404)     → set the loading state and start a BOUNDED retry loop
  //                          that re-probes until the bundle is ready, then bumps
  //                          reloadKey to force a fresh iframe load of it.
  //   - ok                 → clear the loading state and any pending retry.
  // The retry is bounded (BUNDLE_READY_RETRY_CAP attempts) so it can never loop
  // forever; if the cap is hit while still 404ing, the loading state persists and
  // a manual reload remains the escape.
  const handleReadiness = useCallback(
    (status: "ok" | "unauthorized" | "notready") => {
      if (!grantedBundleUrl) return
      const action = readinessAction(status)
      if (action === "remint") {
        clearReadyRetry()
        // Mask the iframe for the duration of the 401 recovery — the same
        // treatment the "notready" branch below already gives a briefly-404ing
        // bundle. Withholding the mask here (the pre-fix `setNotReady(false)`)
        // let the raw 401 body flash visible during the recovery window.
        setNotReady(true)
        notifyAssetError()
        return
      }
      if (action === "clear") {
        clearReadyRetry()
        setNotReady(false)
        return
      }
      // notready → cover the iframe and start a single bounded retry loop (guard
      // against overlapping loops from repeated probes — onLoad or post-mint).
      setNotReady(true)
      if (readyRetryingRef.current) return
      readyRetryingRef.current = true
      let attempts = 0
      const tick = () => {
        readyRetryTimerRef.current = setTimeout(() => {
          attempts += 1
          void preflightBundle(grantedBundleUrl).then((s) => {
            if (s !== "notready") {
              // Ready (or a grant lapse) — stop polling. An ok bumps reloadKey to
              // force a fresh iframe load of the now-ready bundle; a 401 hands off
              // to the bounded re-mint (which forces its own reload).
              readyRetryingRef.current = false
              readyRetryTimerRef.current = null
              setNotReady(false)
              if (s === "unauthorized") notifyAssetError()
              else setReloadKey((k) => k + 1)
              return
            }
            if (attempts >= BUNDLE_READY_RETRY_CAP) {
              // Cap hit while still 404ing — leave the loading state up; a manual
              // reload remains the escape. No user-hostile error.
              readyRetryingRef.current = false
              readyRetryTimerRef.current = null
              return
            }
            tick()
          })
        }, BUNDLE_READY_RETRY_MS)
      }
      tick()
    },
    [grantedBundleUrl, notifyAssetError, clearReadyRetry],
  )

  // The iframe `onLoad` fires even for a 404-bodied document (the proxy briefly
  // returns `{"detail":"Not found"}` for a not-yet-staged build — the prod
  // incident), so a clean `load` event does NOT mean the real bundle painted.
  // Probe the real status the load event hides and route it via handleReadiness.
  const notifyBundleLoaded = useCallback(async () => {
    if (!grantedBundleUrl) return
    const status = await preflightBundle(grantedBundleUrl)
    handleReadiness(status)
  }, [grantedBundleUrl, handleReadiness])

  // Clear a pending readiness retry on unmount and whenever the bundle changes,
  // so a stale loop can't reload a frame that has moved on to a new build.
  useEffect(() => clearReadyRetry, [grantedBundleUrl, bundleUrl, clearReadyRetry])

  // Recover from a grant that lapsed WHILE the bundle was already exposed (the
  // real prod gap): after the initial mint→preflight cycle settles, nothing was
  // re-checking the grant, so once the short TTL elapsed the next iframe asset GET
  // 401'd with no re-mint until a full manual page reload. This re-runs the
  // credentialed preflight on demand and, if the grant has lapsed, RESETS the
  // re-mint budget (a fresh lapse deserves a fresh single cycle — the cap bounds
  // one cycle, it is not a lifetime kill-switch) and re-mints + forces a reload.
  const recoverIfLapsed = useCallback(() => {
    if (!grantedBundleUrl) return
    void preflightBundle(grantedBundleUrl).then((status) => {
      if (status !== "unauthorized") return
      // Fresh lapse → re-arm the bounded cycle and re-mint once.
      remintAttemptsRef.current = 0
      notifyAssetError()
    })
  }, [grantedBundleUrl, notifyAssetError])

  // RECOVER-ON-VISIBILITY: a backgrounded tab can sit past the grant TTL; when it
  // comes back to the foreground (visibilitychange → visible, or window focus),
  // re-check the grant and recover if it lapsed — the user never sees the raw
  // 401 body, the iframe just silently reloads with a fresh grant. The grant
  // cookie is HttpOnly, so JS can't read it; the preflight GET is the only way to
  // observe the 401, exactly as the post-mint preflight does.
  useEffect(() => {
    if (!grantedBundleUrl) return
    const onVisible = () => {
      if (document.visibilityState === "visible") recoverIfLapsed()
    }
    document.addEventListener("visibilitychange", onVisible)
    window.addEventListener("focus", recoverIfLapsed)
    return () => {
      document.removeEventListener("visibilitychange", onVisible)
      window.removeEventListener("focus", recoverIfLapsed)
    }
  }, [grantedBundleUrl, recoverIfLapsed])

  // PROACTIVE REFRESH: re-mint on an interval under the grant TTL so the cookie is
  // refreshed before it can expire. This covers the idle-but-foreground viewer
  // (no visibility/focus event ever fires), so they never reach the 401 at all.
  // It re-mints unconditionally (cheap, bounded by the interval) rather than
  // waiting for a preflight to fail; the bounded post-mint preflight still guards
  // against a revoked grant. mintingRef inside mint() coalesces any overlap.
  useEffect(() => {
    if (!grantedBundleUrl || !bundleUrl) return
    const id = setInterval(() => {
      // Renew the cookie silently — no reloadKey bump, so the live iframe is not
      // reloaded out from under the user (forceReload = false).
      void mint(bundleUrl, true, false)
    }, GRANT_REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [grantedBundleUrl, bundleUrl, mint])

  // Preflight the granted bundle after each (re)mint and route it through the
  // SAME readiness path the iframe onLoad probe uses (handleReadiness). A
  // 401-bodied index.html LOADS in the iframe (fires `load`, not `error` — see
  // preflightBundle), so the iframe onError can't detect a lapsed/withheld grant;
  // this credentialed GET does. A 401 routes through the bounded re-mint
  // (notifyAssetError / remintAttemptsRef, cap = 1 — NO parallel counter); a 404
  // ("notready") — a freshly-staged build the proxy is briefly 404ing right after
  // a (re)mint, e.g. on first load / post-completion — now drives the SAME bounded
  // readiness retry + loading state instead of being dropped, closing the
  // transient-404 flash with NO new per-load latency (the preflight already ran).
  // Keyed on the granted url + reloadKey so it re-runs after a re-mint bumps the
  // key; the re-mint / retry cycles are each bounded, so there is no loop.
  useEffect(() => {
    if (!grantedBundleUrl) return
    let active = true
    void preflightBundle(grantedBundleUrl).then((status) => {
      if (active) handleReadiness(status)
    })
    return () => {
      active = false
    }
  }, [grantedBundleUrl, reloadKey, handleReadiness])

  return {
    grantedBundleUrl,
    error,
    pending,
    reloadKey,
    notifyAssetError,
    retryAfterError,
    notReady,
    notifyBundleLoaded,
  }
}

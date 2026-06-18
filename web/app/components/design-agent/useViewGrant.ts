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
// BOUNDED RE-MINT (plan §16-1): the grant TTL is short, so a long-lived viewing
// session can outlive its grant — the next asset GET then 401s. On such a
// failure the caller invokes `notifyAssetError()`, which re-mints the grant
// EXACTLY ONCE and forces a fresh iframe load (via a bumped `reloadKey`). If the
// asset still fails after that single re-mint (e.g. the grant was revoked
// mid-session — the workspace flipped the prototype private, or ownership
// changed), the hook surfaces an error and does NOT retry again. RETRY CAP = 1.
// No infinite mint↔401 loop.
import { useCallback, useEffect, useRef, useState } from "react"
import { designAgentApi } from "../../lib/api"

/** The single re-mint cap (plan §16-1). One re-mint after the initial mint, then
 *  the hook surfaces an error rather than looping. The cap bounds a SINGLE
 *  mint→preflight→re-mint cycle; it is intentionally re-armed (the attempt counter
 *  is reset) when a NEW lapse is detected — e.g. the tab regains focus after the
 *  grant TTL elapsed — so the viewer can recover for its whole lifetime without a
 *  manual reload, while still never looping within one lapse. */
export const VIEW_GRANT_REMINT_CAP = 1

/** How often to proactively re-mint the grant while a bundle is being viewed.
 *  Sits comfortably under the backend grant TTL (currently 600s) so the cookie is
 *  refreshed BEFORE it can expire — an idle-but-open viewer never reaches the 401
 *  even without a visibility/focus event to trigger recovery. */
export const GRANT_REFRESH_INTERVAL_MS = 5 * 60 * 1000

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
 * Returns "unauthorized" ONLY on a 401 (the case the bounded re-mint must handle);
 * "ok" otherwise — including a network/transient failure, which would also fail
 * the real iframe load and fire `onError`, so it stays on that path and never
 * burns the bounded re-mint budget on a transient.
 */
export async function preflightBundle(
  bundleUrl: string,
  fetchImpl?: typeof fetch,
): Promise<"ok" | "unauthorized"> {
  const doFetch = fetchImpl ?? fetch
  try {
    const res = await doFetch(bundleUrl, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    })
    return res.status === 401 ? "unauthorized" : "ok"
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
): ViewGrantState {
  const [grantedBundleUrl, setGrantedBundleUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  // Count of re-mints already performed for the CURRENT bundle (reset whenever a
  // fresh bundle url arrives — a new build / checkpoint gets a fresh budget).
  const remintAttemptsRef = useRef(0)
  // Guards against overlapping mints (StrictMode double-invoke, rapid calls).
  const mintingRef = useRef(false)

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
        // Mint failed (network / 401 / 404 / 429). Withhold the bundle url and
        // surface an error; do not load an iframe that will only 401 on assets.
        setGrantedBundleUrl(null)
        setError("Couldn't load the prototype. Please refresh and try again.")
      } finally {
        setPending(false)
        mintingRef.current = false
      }
    },
    [prototypeId],
  )

  // Initial mint (and re-mint when a fresh bundle url arrives). A new bundle url
  // means a new build/checkpoint, so reset the re-mint budget and mint fresh.
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
  }, [bundleUrl, mint])

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

  // Preflight the granted bundle after each (re)mint. A 401-bodied index.html
  // LOADS in the iframe (fires `load`, not `error` — see preflightBundle), so the
  // iframe onError can't detect a lapsed/withheld grant; this credentialed GET
  // does, and routes a 401 through the SAME bounded re-mint path (notifyAssetError
  // / remintAttemptsRef, cap = 1 — NO parallel counter). Keyed on the granted url
  // + reloadKey so it re-runs after a re-mint bumps the key; once the cap is hit
  // notifyAssetError nulls grantedBundleUrl and this guard returns — so the
  // preflight→re-mint cycle is bounded too (no preflight↔mint loop).
  useEffect(() => {
    if (!grantedBundleUrl) return
    let active = true
    void preflightBundle(grantedBundleUrl).then((status) => {
      if (active && status === "unauthorized") notifyAssetError()
    })
    return () => {
      active = false
    }
  }, [grantedBundleUrl, reloadKey, notifyAssetError])

  return { grantedBundleUrl, error, pending, reloadKey, notifyAssetError }
}

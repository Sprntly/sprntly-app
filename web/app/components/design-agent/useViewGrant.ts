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
 *  the hook surfaces an error rather than looping. */
export const VIEW_GRANT_REMINT_CAP = 1

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
    async (url: string, isRemint: boolean) => {
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
        // Bump the reload key on a re-mint so the caller can force a fresh load
        // of the now-re-authorized iframe. The initial mint leaves it at 0 (clean
        // first load — no cache-bust needed).
        if (isRemint) setReloadKey((k) => k + 1)
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

  return { grantedBundleUrl, error, pending, reloadKey, notifyAssetError }
}

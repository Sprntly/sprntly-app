"use client"
// Client viewer for the public /p/<token> route (P2-05). Co-located with the
// page exactly like web/app/(app)/onboarding/[step]/OnboardingStep.tsx — the
// server shell (page.tsx) handles static export; this owns the runtime
// behaviour. Reads the real token from the LIVE URL (window.location.pathname,
// client-side) — NOT useParams(), which under output:"export" returns the
// prerendered "_" sentinel (see shareTokenFromPathname). Resolves it against the
// public backend resolver, and branches: public → iframe; passcode → gate;
// missing/private/not-ready/404 → notFound().
//
// The resolver + branch logic are split into pure functions (resolveToken,
// nextViewerState) so they are unit-testable in the node-env vitest run, which
// has no DOM/router — the same split convention as DesignAgentDrawer's
// runGenerateFlow. Relative imports (not `@/…`) match the codebase + vitest.
import { useEffect, useState } from "react"
import { notFound } from "next/navigation"
import { API_URL } from "../lib/api"
import { PasscodeGate } from "./PasscodeGate"
import { PublicPrototypeChrome } from "./PublicPrototypeChrome"
import { resolveToken, type ResolvedView } from "./resolveToken"
import { shareTokenFromLocation } from "./shareTokenFromPathname"

export type { ResolvedView }

// ─── PWA head tags (mobile installability) ──────────────────────────────────
//
// The app is a static export, so the per-prototype manifest (name/start_url/
// scope distinct per share link) is API-served and linked here CLIENT-SIDE,
// only after a token resolves to a READY view — a loading/passcode-gated/404
// state must never expose a manifest link. Chromium-based mobile browsers
// re-evaluate installability on late-injected manifests and need no service worker (none is
// registered — no offline; the bundle rides short-lived signed-URL proxying).
// Tags are keyed by `data-da-pwa` so application is idempotent: apply removes
// any prior instance first (token change → the href is replaced), and the
// effect cleanup removes them on unmount. The manifest link is a PLAIN tag (no
// crossorigin attribute): no credentials ride the fetch — the token is in the
// URL — and the API's CORS middleware covers the app origin.
const PWA_TAG_MARKER = "data-da-pwa"

export function removePwaHeadTags(): void {
  document.head
    .querySelectorAll(`[${PWA_TAG_MARKER}]`)
    .forEach((el) => el.remove())
}

export function applyPwaHeadTags(token: string): void {
  removePwaHeadTags()
  const tags: Array<[tag: string, attrs: Record<string, string>]> = [
    [
      "link",
      {
        rel: "manifest",
        href: `${API_URL}/v1/design-agent/by-token/${encodeURIComponent(token)}/manifest.webmanifest`,
      },
    ],
    ["meta", { name: "theme-color", content: "#f6f7f6" }],
    // iOS gets add-to-home-screen basics via the apple tags; full manifest
    // semantics belong to Chromium-based mobile browsers — a stated
    // limitation, not a defect.
    ["link", { rel: "apple-touch-icon", href: "/pwa/prototype-icon-192.png" }],
    ["meta", { name: "apple-mobile-web-app-capable", content: "yes" }],
  ]
  for (const [tag, attrs] of tags) {
    const el = document.createElement(tag)
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
    el.setAttribute(PWA_TAG_MARKER, "")
    document.head.appendChild(el)
  }
}

export type ViewerState =
  | { kind: "loading" }
  | { kind: "notfound" }
  | { kind: "error" }
  | { kind: "passcode" }
  | { kind: "ready"; bundleUrl: string; isComplete: boolean; targetPlatform: string }

// Pure reducer over a resolver outcome → the terminal viewer state. Passcode
// mode arrives with bundle_url === null (the bundle is withheld until POST
// /passcode succeeds), so it maps to the gate; any other missing bundle_url is
// treated as not-found rather than rendering an empty iframe.
export function nextViewerState(
  view: ResolvedView | null,
): Extract<ViewerState, { kind: "notfound" | "passcode" | "ready" }> {
  if (!view) return { kind: "notfound" }
  if (view.share_mode === "passcode" && !view.bundle_url) return { kind: "passcode" }
  if (!view.bundle_url) return { kind: "notfound" }
  return {
    kind: "ready",
    bundleUrl: view.bundle_url,
    isComplete: view.is_complete,
    targetPlatform: view.target_platform,
  }
}

export function PublicTokenViewer() {
  // The real share token comes from the live URL, not useParams() — under
  // output:"export" the route is prerendered under the "_" sentinel, so
  // useParams() returns "_". `undefined` = not yet read on the client (stay in
  // loading); `null` = read but no real token (sentinel/malformed → notFound()).
  const [token, setToken] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    setToken(shareTokenFromLocation())
  }, [])
  const [state, setState] = useState<ViewerState>({ kind: "loading" })

  useEffect(() => {
    if (token === undefined) return // not yet read from the URL → stay loading
    if (!token) {
      setState({ kind: "notfound" })
      return
    }
    let active = true
    resolveToken(token)
      .then((view) => {
        if (active) setState(nextViewerState(view))
      })
      .catch(() => {
        if (active) setState({ kind: "error" })
      })
    return () => {
      active = false
    }
  }, [token])

  // Inject the PWA head tags only once the token has resolved to a READY view
  // (public, or passcode after a successful verify). The effect re-runs on a
  // token change (apply replaces the prior tag set) and its cleanup removes the
  // tags on unmount / when the state leaves "ready".
  useEffect(() => {
    if (state.kind !== "ready" || !token) return
    applyPwaHeadTags(token)
    return removePwaHeadTags
  }, [state.kind, token])

  // notFound() during render is the supported client-component pattern (it
  // throws into the nearest not-found boundary).
  if (state.kind === "notfound") notFound()
  if (state.kind === "loading") {
    return (
      <div className="design-agent-surface da-public-loading">Loading prototype…</div>
    )
  }
  if (state.kind === "error") {
    return (
      <div className="design-agent-surface da-public-error">
        Could not load this prototype. Please try again.
      </div>
    )
  }
  if (state.kind === "passcode") return <PasscodeGate token={token as string} />
  return (
    <PublicPrototypeChrome
      token={token as string}
      bundleUrl={state.bundleUrl}
      isComplete={state.isComplete}
      targetPlatform={state.targetPlatform}
    />
  )
}

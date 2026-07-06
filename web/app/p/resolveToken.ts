// Shared token-resolver for the public `/p` routes. Extracted from
// PublicTokenViewer so the legacy `/p/<token>` redirect can resolve a token to
// its `company_slug` WITHOUT importing a `"use client"` viewer component. Pure,
// node-env testable (no DOM/router) — same split convention as the rest of the
// public viewer. Relative imports (not `@/…`) match the codebase + vitest.
import { API_URL } from "../lib/api"

export type ResolvedView = {
  share_mode: "public" | "passcode"
  requires_passcode: boolean
  bundle_url: string | null
  is_complete: boolean
  // Cosmetic segment of the canonical /p/<slug>/<token> URL. The backend
  // resolver returns it on the by-token response; default to "" defensively so
  // a missing field never crashes the redirect / canonicalize path.
  company_slug: string
  // "desktop" | "mobile" | "both" — drives the public viewer's single-device
  // toggle gate + device badge. Defaults to "both" when the backend omits it
  // (pre-deploy backend or a malformed body) so the viewer degrades to showing
  // the toggle rather than throwing.
  target_platform: string
}

// Returns null for a 404 — the caller maps that to notFound(). A non-404 non-OK
// status is a real backend error and throws (surfaced as the error state).
export async function resolveToken(
  token: string,
  fetchImpl?: typeof fetch,
): Promise<ResolvedView | null> {
  const doFetch = fetchImpl ?? fetch
  const res = await doFetch(
    `${API_URL}/v1/design-agent/by-token/${encodeURIComponent(token)}`,
    // never stale: a Resume Iteration can re-publish a new bundle URL behind the
    // same token.
    { cache: "no-store" },
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`resolver failed: ${res.status}`)
  const body = (await res.json()) as Partial<ResolvedView>
  return {
    share_mode: body.share_mode as ResolvedView["share_mode"],
    requires_passcode: body.requires_passcode as boolean,
    bundle_url: body.bundle_url ?? null,
    is_complete: body.is_complete as boolean,
    company_slug: body.company_slug ?? "",
    target_platform: body.target_platform ?? "both",
  }
}

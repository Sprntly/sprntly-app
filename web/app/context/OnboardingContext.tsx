"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useAuth } from "../lib/auth"
import {
  fetchUserProfile,
  fetchWorkspaceForUser,
} from "../lib/onboarding/store"
import type { UserProfile, WorkspaceCompany } from "../lib/onboarding/types"
import type { AnalyzeWebsiteResponse } from "../lib/api"

type OnboardingCtx = {
  /**
   * True only on the FIRST load (no profile/workspace fetched yet). Step
   * screens gate their "Loading…" shell + kickoff effects on this flag, so it
   * must NOT flip back to true on a background re-fetch (e.g. a Supabase token
   * refresh when a backgrounded tab refocuses) — doing so discarded in-memory
   * step progress ("restart"). See `refreshing` for background re-fetches.
   */
  loading: boolean
  /**
   * True while a NON-initial refresh is in flight (data already loaded once).
   * Distinct from `loading` so refocus-triggered refreshes update data in the
   * background without dropping step screens to the loading shell.
   */
  refreshing: boolean
  profile: UserProfile | null
  workspace: WorkspaceCompany | null
  refresh: () => Promise<void>
  setWorkspace: (w: WorkspaceCompany | null) => void
  /**
   * Website-analysis result, stashed by the blocking `/onboarding/analyzing`
   * interstitial that runs between the Company and Metrics pages, and read by
   * later steps (success metrics, business context). `null` while pending /
   * never run / on failure; may carry `ok: false` when analysis degraded — in
   * every such case downstream pages fall back to manual entry.
   */
  websiteAnalysis: AnalyzeWebsiteResponse | null
  setWebsiteAnalysis: (a: AnalyzeWebsiteResponse | null) => void
}

const Ctx = createContext<OnboardingCtx | null>(null)

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [workspace, setWorkspace] = useState<WorkspaceCompany | null>(null)
  const [websiteAnalysis, setWebsiteAnalysis] =
    useState<AnalyzeWebsiteResponse | null>(null)

  // Tracks whether the FIRST authed load has completed, so subsequent refreshes
  // (e.g. a Supabase token refresh on tab refocus) re-fetch in the background
  // WITHOUT flipping `loading` — which would drop onboarding step screens back
  // to the "Loading…" shell and discard their in-memory progress ("restart").
  // A ref (not state) so refresh's identity never changes when it's set.
  const hasLoadedRef = useRef(false)

  // Refresh keyed on a STABLE identity (who is logged in) rather than the whole
  // `auth` object: AuthProvider rebuilds a new auth state object on every auth
  // event (incl. TOKEN_REFRESHED / SIGNED_IN on refocus), so keying on `auth`
  // directly re-fired refresh even when the same user was still logged in.
  const authKind = auth.kind
  const authUserId = auth.kind === "authed" ? auth.user.id : null
  const refresh = useCallback(async () => {
    if (authKind !== "authed" || !authUserId) {
      // A genuine sign-out / unauthenticated state: reset onboarding data and
      // allow the next authed load to show the initial loading shell again.
      hasLoadedRef.current = false
      setProfile(null)
      setWorkspace(null)
      setLoading(false)
      setRefreshing(false)
      return
    }
    // First authed load → block on the loading shell; later refreshes update in
    // the background so step screens never regress.
    if (hasLoadedRef.current) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }
    try {
      const [p, w] = await Promise.all([
        fetchUserProfile(authUserId),
        fetchWorkspaceForUser(authUserId),
      ])
      setProfile(p)
      setWorkspace(w)
      hasLoadedRef.current = true
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
    // Keyed on the stable identity (kind + user id) — NOT the whole auth object.
  }, [authKind, authUserId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo(
    () => ({
      loading,
      refreshing,
      profile,
      workspace,
      refresh,
      setWorkspace,
      websiteAnalysis,
      setWebsiteAnalysis,
    }),
    [loading, refreshing, profile, workspace, refresh, websiteAnalysis],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useOnboarding() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useOnboarding must be used within OnboardingProvider")
  return ctx
}

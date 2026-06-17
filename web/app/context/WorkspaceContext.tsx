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
import { fetchUserProfile, fetchWorkspaceForUser } from "../lib/onboarding/store"
import type { UserProfile, WorkspaceCompany } from "../lib/onboarding/types"
import { isSupabaseConfigured } from "../lib/supabase/client"

type WorkspaceCtx = {
  /**
   * True only on the FIRST authed load (no profile/workspace fetched yet).
   * Consumers (AuthGate, CompanyContext seeding, screen shells) gate their
   * "Loading…" state on this, so it must NOT flip back to true on a background
   * re-fetch — e.g. a Supabase TOKEN_REFRESHED / SIGNED_IN fired when a
   * backgrounded tab refocuses. Doing so flashed the whole app to a loading
   * shell and churned CompanyContext/activeCompany on every refocus. See
   * `refreshing` for non-initial re-fetches. Mirrors OnboardingContext.
   */
  loading: boolean
  /**
   * True while a NON-initial refresh is in flight (data already loaded once).
   * Distinct from `loading` so refocus-triggered refreshes update data in the
   * background without dropping consumers to the loading state.
   */
  refreshing: boolean
  profile: UserProfile | null
  workspace: WorkspaceCompany | null
  refresh: () => Promise<void>
}

const Ctx = createContext<WorkspaceCtx | null>(null)

export function profileDisplayName(
  profile: UserProfile | null,
  fallbackEmail?: string | null,
): string | null {
  if (!profile) return null
  const full = [profile.first_name, profile.last_name]
    .map((s) => s?.trim())
    .filter(Boolean)
    .join(" ")
  if (full) return full
  if (fallbackEmail) {
    const local = fallbackEmail.split("@")[0]
    if (local) return local
  }
  return null
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [workspace, setWorkspace] = useState<WorkspaceCompany | null>(null)

  // Tracks whether the FIRST authed load has completed, so subsequent refreshes
  // (e.g. a Supabase token refresh on tab refocus) re-fetch in the background
  // WITHOUT flipping `loading` — which would flash the whole app to a loading
  // shell and churn CompanyContext/activeCompany. A ref (not state) so refresh's
  // identity never changes when it's set. Mirrors OnboardingContext.
  const hasLoadedRef = useRef(false)

  // Refresh keyed on a STABLE identity (who is logged in) rather than the whole
  // `auth` object: AuthProvider rebuilds a new auth state object on every auth
  // event (incl. TOKEN_REFRESHED / SIGNED_IN on refocus), so keying on `auth`
  // directly re-fired refresh even when the same user was still logged in.
  const authKind = auth.kind
  const authUserId = auth.kind === "authed" ? auth.user.id : null
  const refresh = useCallback(async () => {
    if (authKind !== "authed" || !authUserId || !isSupabaseConfigured()) {
      // A genuine sign-out / unauthenticated state: reset workspace data and
      // allow the next authed load to show the initial loading state again.
      hasLoadedRef.current = false
      setProfile(null)
      setWorkspace(null)
      setLoading(false)
      setRefreshing(false)
      return
    }
    // First authed load → block on `loading`; later refreshes update in the
    // background so consumers never regress to the loading shell.
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
    () => ({ loading, refreshing, profile, workspace, refresh }),
    [loading, refreshing, profile, workspace, refresh],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useWorkspace(): WorkspaceCtx {
  const ctx = useContext(Ctx)
  if (!ctx) {
    throw new Error("useWorkspace must be used within WorkspaceProvider")
  }
  return ctx
}

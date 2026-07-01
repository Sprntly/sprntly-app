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
import {
  getPendingAnalysis,
  resumeWebsiteAnalysis,
  runWebsiteAnalysis,
} from "../lib/onboarding/runWebsiteAnalysis"
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
   * Website-analysis result, and read by later steps (success metrics, business
   * context). `null` while pending / never run / on failure; may carry
   * `ok: false` when analysis degraded — in every such case downstream pages
   * fall back to manual entry.
   *
   * The analysis is kicked off in the BACKGROUND from the business-info step
   * (via `startWebsiteAnalysis`) — there is no longer a blocking interstitial.
   * Because the job runs SERVER-SIDE (POST returns a job_id) and this provider
   * outlives every onboarding step navigation, the poll survives the user
   * moving forward through the flow; `websiteAnalysis` fills in whenever the
   * job finishes (typically before the business-context step reads it).
   */
  websiteAnalysis: AnalyzeWebsiteResponse | null
  setWebsiteAnalysis: (a: AnalyzeWebsiteResponse | null) => void
  /**
   * Fire-and-forget the onboarding website analysis for this workspace, then
   * stash the result on context when it lands. Safe to call more than once —
   * it runs at most once per provider lifetime (a remount / refresh re-attaches
   * to the persisted job instead of re-POSTing). A `null` website is a no-op.
   */
  startWebsiteAnalysis: (website: string | null, workspaceId: string) => void
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

  // Guards the background website analysis to at most one run per provider
  // lifetime, so a re-render / a second `startWebsiteAnalysis` call / the
  // auto-resume effect below can never double-fire the job. The provider wraps
  // the whole `/onboarding/*` tree, so this survives step-to-step navigation —
  // which is exactly why the analysis no longer needs a dedicated screen.
  const analysisStartedRef = useRef(false)

  // Kick off (or re-attach to) the server-side website analysis in the
  // background, stashing the result on context when it lands. Fire-and-forget:
  // callers navigate on immediately and never await this. Never rejects (the
  // underlying run/resume resolve `{ result: null }` on any failure).
  const startWebsiteAnalysis = useCallback(
    (website: string | null, workspaceId: string) => {
      if (analysisStartedRef.current) return
      if (!website || !workspaceId) return
      analysisStartedRef.current = true
      // The workspace id keys both the persistence scope and the localStorage
      // company segment, so a remount re-attaches unambiguously.
      const company = workspaceId
      const pending = getPendingAnalysis(company, workspaceId)
      const run = pending
        ? resumeWebsiteAnalysis(Number(pending.id), company, workspaceId)
        : runWebsiteAnalysis(website, company, workspaceId)
      void run.then(({ result }) => {
        // Stash even when ok:false — downstream reads it and falls back to
        // manual entry gracefully. null (error/timeout) leaves it untouched.
        if (result) setWebsiteAnalysis(result)
      })
    },
    [],
  )

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

  // Re-attach to an analysis that was kicked off before a refresh / remount.
  // `startWebsiteAnalysis` persists the job_id per workspace, so if the user
  // reloads mid-flow we resume polling the still-running job here rather than
  // orphaning it — the result still lands on context for the later step to
  // read. No-op when nothing is pending (the guard also prevents a duplicate
  // run when `startWebsiteAnalysis` already fired this session).
  const workspaceId = workspace?.id ?? null
  useEffect(() => {
    if (!workspaceId) return
    if (analysisStartedRef.current) return
    const pending = getPendingAnalysis(workspaceId, workspaceId)
    if (!pending) return
    analysisStartedRef.current = true
    void resumeWebsiteAnalysis(
      Number(pending.id),
      workspaceId,
      workspaceId,
    ).then(({ result }) => {
      if (result) setWebsiteAnalysis(result)
    })
  }, [workspaceId])

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
      startWebsiteAnalysis,
    }),
    [
      loading,
      refreshing,
      profile,
      workspace,
      refresh,
      websiteAnalysis,
      startWebsiteAnalysis,
    ],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useOnboarding() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useOnboarding must be used within OnboardingProvider")
  return ctx
}

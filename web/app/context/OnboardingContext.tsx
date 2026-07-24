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
import {
  getPendingContextImport,
  rememberContextImport,
  resumeContextImport,
} from "../lib/onboarding/runContextImport"
import { applyImportedContext } from "../lib/onboarding/applyImportedContext"
import type { UserProfile, WorkspaceCompany } from "../lib/onboarding/types"
import type { AnalyzeWebsiteResponse, LlmContextFields } from "../lib/api"

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
  /**
   * State of the background LLM extraction kicked off by the import-context
   * step. "idle" when nothing was uploaded, "running" while the job is in
   * flight, "done" once its fields have been merged onto the workspace, and
   * "failed" when it errored or timed out (the deterministic parse applied at
   * upload time still stands in that case).
   *
   * Read by the steps the import prefills so they can say "we filled this in
   * from your file" rather than leaving the user guessing why a form they
   * never touched has values in it.
   */
  contextImport: ContextImportState
  /**
   * Fire-and-forget the background extraction poll for a just-uploaded context
   * file, applying whatever it returns onto the workspace as a prefill.
   *
   * Lives on the PROVIDER, not the import step, for the same reason
   * `startWebsiteAnalysis` does: the provider wraps the whole `/onboarding/*`
   * tree, so the poll survives the user advancing to connectors and the fields
   * land in time for the metrics and product steps. Safe to call more than
   * once — it runs at most one job per provider lifetime.
   */
  startContextImport: (jobId: number, workspaceId: string) => void
}

/** @see OnboardingCtx.contextImport */
export type ContextImportState = "idle" | "running" | "done" | "failed"

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

  // ---- Background LLM context extraction (the import-context step) ----------
  const [contextImport, setContextImport] = useState<ContextImportState>("idle")
  // One extraction per provider lifetime, so a re-render, a second
  // `startContextImport` call, and the re-attach effect below can't double-poll.
  const contextImportStartedRef = useRef(false)
  // The LATEST workspace, for the async apply below. The poll resolves tens of
  // seconds after it started — by then the user has worked through connectors
  // and may have edited fields, so applying against the workspace captured at
  // kickoff would write from a stale snapshot and could resurrect a value they
  // deliberately cleared. A ref keeps the callback identity stable too.
  const workspaceRef = useRef<WorkspaceCompany | null>(null)
  useEffect(() => {
    workspaceRef.current = workspace
  }, [workspace])

  const applyImported = useCallback(async (fields: LlmContextFields) => {
    const current = workspaceRef.current
    if (!current) return
    try {
      const next = await applyImportedContext(current, fields)
      // Identity is the "nothing to write" signal — see applyImportedContext.
      if (next !== current) setWorkspace(next)
      setContextImport("done")
    } catch {
      // The extraction succeeded but the write didn't. Report it rather than
      // claiming a prefill that never landed; the steps still work by hand.
      setContextImport("failed")
    }
  }, [])

  // Poll a just-kicked-off extraction and merge what it finds. Fire-and-forget:
  // the import step navigates on immediately and never awaits this.
  const startContextImport = useCallback(
    (jobId: number, workspaceId: string) => {
      if (contextImportStartedRef.current) return
      if (!jobId || !workspaceId) return
      contextImportStartedRef.current = true
      setContextImport("running")
      // Persist before polling so a reload mid-connectors re-attaches instead
      // of orphaning the job.
      rememberContextImport(workspaceId, workspaceId, jobId)
      void resumeContextImport(jobId, workspaceId, workspaceId).then(
        ({ result }) => {
          if (result?.fields) void applyImported(result.fields)
          else setContextImport("failed")
        },
      )
    },
    [applyImported],
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

  // Same re-attach, for the context extraction: a reload while the user is on
  // connectors would otherwise orphan a job still running server-side and cost
  // them the prefill it was about to produce.
  useEffect(() => {
    if (!workspaceId) return
    if (contextImportStartedRef.current) return
    const pending = getPendingContextImport(workspaceId, workspaceId)
    if (!pending) return
    startContextImport(Number(pending.id), workspaceId)
  }, [workspaceId, startContextImport])

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
      contextImport,
      startContextImport,
    }),
    [
      loading,
      refreshing,
      profile,
      workspace,
      refresh,
      websiteAnalysis,
      startWebsiteAnalysis,
      contextImport,
      startContextImport,
    ],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useOnboarding() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useOnboarding must be used within OnboardingProvider")
  return ctx
}

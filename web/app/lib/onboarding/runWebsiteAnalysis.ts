// Blur/remount-safe onboarding website-analysis flow.
//
// POST /v1/onboarding/analyze-website is fire-and-forget: it returns a job_id
// and the analysis keeps running server-side. We persist the active job_id per
// workspace (jobResume) and poll the status endpoint with the shared
// visibility-aware `pollUntil`, so the analysis survives a backgrounded tab
// (setTimeout throttled to ~1/min) AND a remount (the awaiting closure is gone,
// but the persisted id lets us re-attach instead of re-POSTing). Mirrors
// runAskGeneration — same poll/jobResume primitives.
//
// Onboarding must never trap the user: a terminal `error`, a wall-clock budget
// exhaustion, or any transport failure all resolve to `{ result: null }`, and
// the Analyzing screen forwards regardless. A `ready` status yields the
// AnalyzeWebsiteResponse the metrics page consumes via setWebsiteAnalysis.

import { onboardingApi } from "../api"
import type {
  AnalyzeWebsiteResponse,
  AnalyzeWebsiteStatusResponse,
} from "../api"
import { pollUntil } from "../poll"
import {
  clearPendingJob,
  getPendingJob,
  setPendingJob,
  type PendingJob,
} from "../jobResume"

// Wall-clock budget; Date.now()-measured inside pollUntil so a throttled
// background tab still measures elapsed time correctly. Generous (90s) so a
// briefly-backgrounded tab is NOT abandoned mid-analysis — it replaces the old
// 12s hard-forward wall, which would drop a still-running analysis the moment
// the tab lost focus.
const MAX_MS = 90 * 1000
const POLL_INTERVAL_MS = 1500

const KIND = "website-analysis" as const

/** Stable per-workspace scope for an onboarding website-analysis job. One
 *  workspace runs at most one analysis at a time, so the persisted job_id is
 *  unambiguous on remount. */
export function analysisScope(workspaceId: string): string {
  return `ws:${workspaceId}`
}

/** localStorage-persisted pending analysis job for a workspace, or null. */
export function getPendingAnalysis(
  company: string,
  workspaceId: string,
): PendingJob | null {
  return getPendingJob(KIND, company, analysisScope(workspaceId))
}

/** The terminal outcome the Analyzing screen forwards on. `result` is the
 *  AnalyzeWebsiteResponse on success (status 'ready'); null on error / timeout
 *  so the metrics page falls back to manual entry. */
export type AnalysisOutcome = { result: AnalyzeWebsiteResponse | null }

/**
 * Poll an already-kicked-off analysis job by id until terminal, then resolve.
 * Shared by `runWebsiteAnalysis` (POSTs first) and `resumeWebsiteAnalysis`
 * (re-attaches to a persisted id on remount). Clears the persisted marker on
 * every terminal exit. NEVER rejects — onboarding must always complete, so a
 * backend error / timeout resolves to `{ result: null }`.
 */
async function pollAnalysisToResult(
  jobId: number,
  company: string,
  workspaceId: string,
  isCancelled?: () => boolean,
): Promise<AnalysisOutcome> {
  const scope = analysisScope(workspaceId)
  let final: AnalyzeWebsiteStatusResponse
  try {
    final = await pollUntil<AnalyzeWebsiteStatusResponse>({
      fetchStatus: () => onboardingApi.analyzeWebsiteStatus(jobId),
      isDone: (v) => v.status !== "generating",
      maxMs: MAX_MS,
      intervalMs: POLL_INTERVAL_MS,
      isCancelled,
    })
  } catch {
    // Transport failure while polling → leave the marker so a later remount can
    // re-attach to the (possibly still-running) job, and forward with no result.
    return { result: null }
  }
  clearPendingJob(KIND, company, scope)
  // 'ready' → the analysis dict; 'error' or a budget-exhausted 'generating' →
  // null (forward to manual entry; never trap the user).
  return { result: final.status === "ready" ? final.result : null }
}

/**
 * Run one onboarding website analysis end-to-end: POST to get a job_id, persist
 * it for the workspace, then poll until terminal. Resolves with the
 * AnalyzeWebsiteResponse on success, or `{ result: null }` on degrade / failure
 * / timeout. NEVER rejects.
 */
export async function runWebsiteAnalysis(
  url: string,
  company: string,
  workspaceId: string,
  isCancelled?: () => boolean,
): Promise<AnalysisOutcome> {
  let start: { job_id: number }
  try {
    start = await onboardingApi.analyzeWebsite(url)
  } catch {
    // A POST failure → nothing to poll; forward with manual fallback.
    return { result: null }
  }
  setPendingJob(KIND, company, analysisScope(workspaceId), start.job_id)
  return pollAnalysisToResult(start.job_id, company, workspaceId, isCancelled)
}

/**
 * Re-attach to an analysis whose POST already happened (id persisted via
 * setPendingJob) — used on mount so a background-finished analysis resumes
 * instead of being orphaned (and so a remount doesn't re-POST a duplicate run).
 * Does NOT re-POST.
 */
export async function resumeWebsiteAnalysis(
  jobId: number,
  company: string,
  workspaceId: string,
  isCancelled?: () => boolean,
): Promise<AnalysisOutcome> {
  return pollAnalysisToResult(jobId, company, workspaceId, isCancelled)
}

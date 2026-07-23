// Blur/remount-safe polling for the onboarding context-import extraction.
//
// POST /v1/connectors/llm-context/import returns the deterministic heading
// parse immediately AND a `job_id` for a background LLM pass over the same
// file. That pass is what makes an arbitrary context document usable — the
// heading walk only understands files our own prompt produced.
//
// The job runs while the user works through the connectors step, which is
// exactly why connectors sits directly after the import in the flow. We persist
// the job_id per workspace (jobResume) and poll with the shared
// visibility-aware `pollUntil`, so the extraction survives a backgrounded tab
// AND a remount — a reload mid-connectors re-attaches to the running job
// instead of losing the prefill. Mirrors runWebsiteAnalysis exactly.
//
// Onboarding must never trap the user: a terminal `error`, an exhausted
// wall-clock budget, or any transport failure all resolve to `{ result: null }`.
// The user keeps whatever the deterministic parse already gave them and types
// the rest, which is the same place they would have been without the import.

import { llmContextApi } from "../api"
import type { LlmContextImportResponse, LlmContextJobStatus } from "../api"
import { pollUntil } from "../poll"
import {
  clearPendingJob,
  getPendingJob,
  setPendingJob,
  type PendingJob,
} from "../jobResume"

// One LLM pass over a context document. Generous relative to the ~10-30s the
// call takes, because the user is on the connectors step and nothing is
// blocked on it — a briefly-backgrounded tab must not abandon a live job.
const MAX_MS = 180 * 1000
const POLL_INTERVAL_MS = 2000

const KIND = "llm-context-import" as const

/** Stable per-workspace scope. A workspace runs at most one import at a time,
 *  so the persisted job_id is unambiguous on remount. */
export function contextImportScope(workspaceId: string): string {
  return `ws:${workspaceId}`
}

/** localStorage-persisted pending extraction job for a workspace, or null. */
export function getPendingContextImport(
  company: string,
  workspaceId: string,
): PendingJob | null {
  return getPendingJob(KIND, company, contextImportScope(workspaceId))
}

/** Record the job the upload just kicked off, so a remount re-attaches. */
export function rememberContextImport(
  company: string,
  workspaceId: string,
  jobId: number,
): void {
  setPendingJob(KIND, company, contextImportScope(workspaceId), jobId)
}

/** Terminal outcome. `result` carries the merged extraction on success, null on
 *  error / timeout — in which case the deterministic parse already applied at
 *  upload time stands on its own. */
export type ContextImportOutcome = { result: LlmContextImportResponse | null }

/**
 * Poll an already-kicked-off extraction job until terminal, then resolve.
 * Clears the persisted marker on every terminal exit. NEVER rejects.
 */
export async function resumeContextImport(
  jobId: number,
  company: string,
  workspaceId: string,
  isCancelled?: () => boolean,
): Promise<ContextImportOutcome> {
  let final: LlmContextJobStatus
  try {
    final = await pollUntil<LlmContextJobStatus>({
      fetchStatus: () => llmContextApi.importStatus(jobId),
      isDone: (v) => v.status !== "generating",
      maxMs: MAX_MS,
      intervalMs: POLL_INTERVAL_MS,
      isCancelled,
    })
  } catch {
    // Transport failure while polling → leave the marker so a later remount can
    // re-attach to the (possibly still-running) job.
    return { result: null }
  }
  clearPendingJob(KIND, company, contextImportScope(workspaceId))
  return { result: final.status === "ready" ? final.result : null }
}

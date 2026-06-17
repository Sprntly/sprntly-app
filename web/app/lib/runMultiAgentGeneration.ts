import {
  multiAgentApi,
  type MultiAgentMode,
  type MultiAgentRunStatus,
  type MultiAgentDocsResponse,
} from "./api"
import { sleepUntilNextPoll } from "./poll"
import { clearPendingJob, insightScope, setPendingJob } from "./jobResume"

export type MultiAgentResult =
  | { ok: true; runId: string; status: MultiAgentRunStatus; docs: MultiAgentDocsResponse }
  | { ok: false; message: string }

// Max 12 minutes for aggressive mode — 7 agents.
const MAX_MS = 12 * 60 * 1000

/**
 * Poll an already-kicked-off multi-agent run by id until terminal, then fetch
 * docs. Shared by `runMultiAgentGeneration` (calls generate first) and
 * `resumeMultiAgentGeneration` (re-enters against a persisted run_id on
 * remount). Clears the persisted pending-job marker on every terminal exit.
 */
async function pollMultiAgentToResult(
  runId: string,
  scope: string | null,
): Promise<MultiAgentResult> {
  try {
    const startedAt = Date.now()
    let status = await multiAgentApi.getStatus(runId)
    while (status.status === "generating" && Date.now() - startedAt < MAX_MS) {
      // Visibility-aware sleep: a backgrounded tab throttles setTimeout to
      // ~1/min, which stalls polling though the server-side run finishes.
      // Refocusing wakes immediately and re-reads the real status.
      await sleepUntilNextPoll(4000)
      status = await multiAgentApi.getStatus(runId)
    }

    if (status.status === "generating") {
      if (scope) clearPendingJob("multi-agent", "_", scope)
      return { ok: false, message: "Timed out waiting for multi-agent generation" }
    }

    const docs = await multiAgentApi.getDocs(runId)
    if (scope) clearPendingJob("multi-agent", "_", scope)
    return { ok: true, runId, status, docs }
  } catch (err) {
    if (scope) clearPendingJob("multi-agent", "_", scope)
    const msg = err instanceof Error ? err.message : "Multi-agent generation failed"
    return { ok: false, message: msg }
  }
}

/**
 * Kick off multi-agent generation and poll until all docs are ready.
 *
 * Multi-Agent Mode runs 7 agents concurrently in 3 phases:
 *   Phase 1: PRD + Evidence (concurrent)
 *   Phase 2: User Stories + Technical Design + QA Test Cases + Risk Analysis (concurrent)
 *   Phase 3: Traceability Matrix (needs Phase 2 outputs)
 *
 * Aggressive Analysis Mode additionally ingests ClickUp task context
 * (comments, attachments, linked tasks) for deeper grounding.
 */
export async function runMultiAgentGeneration(
  briefId: number,
  insightIndex: number,
  mode: MultiAgentMode = "aggressive",
): Promise<MultiAgentResult> {
  let start
  try {
    start = await multiAgentApi.generate(briefId, insightIndex, mode)
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Multi-agent generation failed"
    return { ok: false, message: msg }
  }
  const scope = insightScope(briefId, insightIndex)
  setPendingJob("multi-agent", "_", scope, start.run_id)
  return pollMultiAgentToResult(start.run_id, scope)
}

/**
 * Re-enter polling for a multi-agent run whose generation was already kicked
 * off (run_id persisted via `setPendingJob`) — used on remount so a
 * background-finished run resumes instead of being orphaned. Does NOT call
 * generate again.
 */
export async function resumeMultiAgentGeneration(
  runId: string,
  briefId: number,
  insightIndex: number,
): Promise<MultiAgentResult> {
  const scope = insightScope(briefId, insightIndex)
  return pollMultiAgentToResult(runId, scope)
}

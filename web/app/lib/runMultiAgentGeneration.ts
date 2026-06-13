import {
  multiAgentApi,
  type MultiAgentMode,
  type MultiAgentRunStatus,
  type MultiAgentDocsResponse,
} from "./api"

export type MultiAgentResult =
  | { ok: true; runId: string; status: MultiAgentRunStatus; docs: MultiAgentDocsResponse }
  | { ok: false; message: string }

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
  try {
    const start = await multiAgentApi.generate(briefId, insightIndex, mode)
    const runId = start.run_id

    // Poll until complete (max 12 minutes for aggressive mode — 7 agents)
    const MAX_MS = 12 * 60 * 1000
    const startedAt = Date.now()
    let status: MultiAgentRunStatus

    do {
      await new Promise((r) => setTimeout(r, 4000))
      status = await multiAgentApi.getStatus(runId)
    } while (
      status.status === "generating" &&
      Date.now() - startedAt < MAX_MS
    )

    if (status.status === "generating") {
      return { ok: false, message: "Timed out waiting for multi-agent generation" }
    }

    // Fetch full docs
    const docs = await multiAgentApi.getDocs(runId)

    return { ok: true, runId, status, docs }
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Multi-agent generation failed"
    return { ok: false, message: msg }
  }
}

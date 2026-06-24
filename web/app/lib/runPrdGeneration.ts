import { prdApi } from "./api"
import { markdownToPrdState } from "./prd-adapter"
import { sleepUntilNextPoll } from "./poll"
import { clearPendingJob, insightScope, setPendingJob } from "./jobResume"
import type { DetailState, PrdState } from "../types/content"

export type PrdGenResult = { ok: true; prd: PrdState } | { ok: false; message: string }

const MAX_MS = 6 * 60 * 1000

/**
 * Poll an already-kicked-off PRD by id until terminal, then map to the result
 * shape. Shared by `runPrdGeneration` (which calls generate first) and
 * `resumePrdGeneration` (which re-enters against a persisted id on remount).
 * Clears the persisted pending-job marker on every terminal exit (ready /
 * failed / timeout) so the resume only fires while a job is genuinely running.
 */
async function pollPrdToResult(
  prdId: number,
  scope: string | null,
): Promise<PrdGenResult> {
  let prd = await prdApi.get(prdId)
  const startedAt = Date.now()
  while (prd.status === "generating" && Date.now() - startedAt < MAX_MS) {
    // Visibility-aware sleep: a backgrounded tab throttles setTimeout to ~1/min,
    // which stalls polling though the server-side PRD job finishes. Refocusing
    // wakes immediately and re-reads the real status.
    await sleepUntilNextPoll(4000)
    prd = await prdApi.get(prdId)
  }
  if (scope) clearPendingJob("prd", "_", scope)
  if (prd.status === "failed") {
    return { ok: false, message: prd.error || "PRD generation failed on the backend" }
  }
  if (prd.status !== "ready") {
    return { ok: false, message: "Timed out waiting for PRD" }
  }
  // Carry the PRD's DB id (and the — for now always-undefined — Figma file
  // key) onto the returned PrdState so the F2 "Generate Prototype" flow can
  // call designAgentApi.generate({ prd_id }). `prd.id` is read from the
  // freshly-fetched ready record. figma_file_key stays undefined here —
  // sourcing it from the user's Figma connector is out of scope for P1-13
  // (handled by the drawer's sourceDetectedLabel as "No Figma source
  // connected").
  return {
    ok: true,
    prd: {
      ...markdownToPrdState(prd.payload_md),
      prd_id: prd.id,
      figma_file_key: undefined,
      llmPart: prd.llm_part,
    },
  }
}

/** Polls until PRD is ready (same contract as DetailScreen). Persists the
 *  active prd_id so a remount can resume via `resumePrdGeneration`. */
export async function runPrdGeneration(meta: DetailState["meta"]): Promise<PrdGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const start = await prdApi.generate(meta.briefId, meta.insightIndex)
  // briefId is globally unique, so the insight scope alone is unambiguous
  // across companies — the "_" company token keeps the key shape uniform.
  const scope = insightScope(meta.briefId, meta.insightIndex)
  setPendingJob("prd", "_", scope, start.prd_id)
  return pollPrdToResult(start.prd_id, scope)
}

/**
 * Re-enter polling for a PRD whose generation was already kicked off (its
 * prd_id was persisted via `setPendingJob`) — used on screen/tab remount so a
 * background-finished job resumes in the UI instead of being orphaned. Does NOT
 * call generate again. `meta` is used only to clear the right persisted marker.
 */
export async function resumePrdGeneration(
  prdId: number,
  meta: DetailState["meta"],
): Promise<PrdGenResult> {
  const scope = meta ? insightScope(meta.briefId, meta.insightIndex) : null
  return pollPrdToResult(prdId, scope)
}

export type PrdLoadResult = { ok: true; prd: PrdState } | { ok: false; message: string }

/**
 * Fetch an already-generated PRD by id and map it to PrdState — no generation.
 * Lets the brief card's "View PRD" surface an existing PRD in the right rail
 * (the same content-panel card as Evidence) instead of navigating to a page.
 */
export async function loadPrdById(prdId: number): Promise<PrdLoadResult> {
  const prd = await prdApi.get(prdId)
  if (prd.status === "failed") {
    return { ok: false, message: prd.error || "PRD failed on the backend" }
  }
  if (prd.status !== "ready") {
    return { ok: false, message: "PRD isn't ready yet" }
  }
  return {
    ok: true,
    prd: {
      ...markdownToPrdState(prd.payload_md),
      prd_id: prd.id,
      figma_file_key: undefined,
      llmPart: prd.llm_part,
    },
  }
}

/**
 * Fetch the most-recent PRD for a company and map it to PrdState — no
 * generation. Used right after a multi-agent run (which creates the PRD record)
 * to land it in the right-rail card without re-generating: the run we just
 * finished is the latest PRD.
 */
export async function loadLatestPrd(dataset: string): Promise<PrdLoadResult> {
  const prd = await prdApi.latest(dataset)
  if (!prd || prd.status === "failed") {
    return { ok: false, message: prd?.error || "No PRD available yet" }
  }
  if (prd.status !== "ready") {
    return { ok: false, message: "PRD isn't ready yet" }
  }
  return {
    ok: true,
    prd: {
      ...markdownToPrdState(prd.payload_md),
      prd_id: prd.id,
      figma_file_key: undefined,
      llmPart: prd.llm_part,
    },
  }
}

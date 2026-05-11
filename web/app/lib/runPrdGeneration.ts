import { prdApi } from "./api"
import { markdownToPrdState } from "./prd-adapter"
import type { DetailState, PrdState } from "../types/content"

export type PrdGenResult = { ok: true; prd: PrdState } | { ok: false; message: string }

/** Polls until PRD is ready (same contract as DetailScreen). */
export async function runPrdGeneration(meta: DetailState["meta"]): Promise<PrdGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const start = await prdApi.generate(meta.briefId, meta.insightIndex)
  let prd = await prdApi.get(start.prd_id)
  const startedAt = Date.now()
  const MAX_MS = 6 * 60 * 1000
  while (prd.status === "generating" && Date.now() - startedAt < MAX_MS) {
    await new Promise((r) => setTimeout(r, 4000))
    prd = await prdApi.get(start.prd_id)
  }
  if (prd.status === "failed") {
    return { ok: false, message: prd.error || "PRD generation failed on the backend" }
  }
  if (prd.status !== "ready") {
    return { ok: false, message: "Timed out waiting for PRD" }
  }
  return { ok: true, prd: markdownToPrdState(prd.payload_md) }
}

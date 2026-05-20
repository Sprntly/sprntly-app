import { prdV2Api } from "./api"
import { markdownToPrdV2State } from "./prd-v2-adapter"
import type { DetailState, PrdState } from "../types/content"
import type { PrdGenResult } from "./runPrdGeneration"

/** Polls until the v2 PRD is ready, then parses the markdown with the v2
 *  PRD adapter (semantic blocks + standard markdown). Same shape and same
 *  backoff cadence as runPrdGeneration so the caller (PrdScreen) can
 *  dispatch either runner behind the format toggle without branching on
 *  the return shape. */
export async function runPrdV2Generation(
  meta: DetailState["meta"],
): Promise<PrdGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this PRD from the brief first." }
  }
  const start = await prdV2Api.generate(meta.briefId, meta.insightIndex)
  let prd = await prdV2Api.get(start.prd_id)
  const startedAt = Date.now()
  const MAX_MS = 6 * 60 * 1000
  while (prd.status === "generating" && Date.now() - startedAt < MAX_MS) {
    await new Promise((r) => setTimeout(r, 4000))
    prd = await prdV2Api.get(start.prd_id)
  }
  if (prd.status === "failed") {
    return {
      ok: false,
      message: prd.error || "v2 PRD generation failed on the backend",
    }
  }
  if (prd.status !== "ready") {
    return { ok: false, message: "Timed out waiting for v2 PRD" }
  }
  const parsed: PrdState = markdownToPrdV2State(prd.payload_md)
  return { ok: true, prd: parsed }
}

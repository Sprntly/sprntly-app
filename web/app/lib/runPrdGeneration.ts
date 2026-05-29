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
  // Carry the PRD's DB id (and the — for now always-undefined — Figma file
  // key) onto the returned PrdState so the F2 "Generate Prototype" flow can
  // call designAgentApi.generate({ prd_id }). `prd.id` and `start.prd_id` are
  // the same value; `prd.id` is read from the freshly-fetched ready record.
  // figma_file_key stays undefined here — sourcing it from the user's Figma
  // connector is out of scope for P1-13 (handled by the drawer's
  // sourceDetectedLabel as "No Figma source connected").
  return {
    ok: true,
    prd: {
      ...markdownToPrdState(prd.payload_md),
      prd_id: prd.id,
      figma_file_key: undefined,
    },
  }
}

import { evidenceApi } from "./api"
import { markdownToPrdState } from "./prd-adapter"
import type { DetailState, PrdState } from "../types/content"

export type EvidenceGenResult =
  | { ok: true; evidence: PrdState }
  | { ok: false; message: string }

/** Polls until the Evidence Page is ready, then returns the parsed PrdState
 *  (same markdown adapter, since the doc is markdown + chart blocks). */
export async function runEvidenceGeneration(
  meta: DetailState["meta"],
): Promise<EvidenceGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const start = await evidenceApi.generate(meta.briefId, meta.insightIndex)
  let doc = await evidenceApi.get(start.evidence_id)
  const startedAt = Date.now()
  const MAX_MS = 6 * 60 * 1000
  while (doc.status === "generating" && Date.now() - startedAt < MAX_MS) {
    await new Promise((r) => setTimeout(r, 4000))
    doc = await evidenceApi.get(start.evidence_id)
  }
  if (doc.status === "failed") {
    return {
      ok: false,
      message: doc.error || "Evidence generation failed on the backend",
    }
  }
  if (doc.status !== "ready") {
    return { ok: false, message: "Timed out waiting for Evidence" }
  }
  return { ok: true, evidence: markdownToPrdState(doc.payload_md) }
}

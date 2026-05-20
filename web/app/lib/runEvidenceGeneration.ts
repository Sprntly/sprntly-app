import { evidenceApi } from "./api"
import { markdownToEvidenceState } from "./evidence-adapter"
import type { DetailState, PrdState } from "../types/content"

export type EvidenceGenResult =
  | { ok: true; evidence: PrdState }
  | { ok: false; message: string }

/** Polls until the Evidence Page is ready, then parses the markdown with
 *  the evidence adapter (typed semantic blocks + standard markdown). */
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
    return { ok: false, message: "Timed out waiting for evidence" }
  }
  return { ok: true, evidence: markdownToEvidenceState(doc.payload_md) }
}

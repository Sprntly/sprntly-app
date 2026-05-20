import { evidenceV2Api } from "./api"
import { markdownToEvidenceV2State } from "./evidence-v2-adapter"
import type { DetailState, PrdState } from "../types/content"

export type EvidenceV2GenResult =
  | { ok: true; evidence: PrdState }
  | { ok: false; message: string }

/** Polls until the v2 Evidence Page is ready, then parses the markdown
 *  with the v2 adapter (semantic blocks + standard markdown). */
export async function runEvidenceV2Generation(
  meta: DetailState["meta"],
): Promise<EvidenceV2GenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const start = await evidenceV2Api.generate(meta.briefId, meta.insightIndex)
  let doc = await evidenceV2Api.get(start.evidence_id)
  const startedAt = Date.now()
  const MAX_MS = 6 * 60 * 1000
  while (doc.status === "generating" && Date.now() - startedAt < MAX_MS) {
    await new Promise((r) => setTimeout(r, 4000))
    doc = await evidenceV2Api.get(start.evidence_id)
  }
  if (doc.status === "failed") {
    return {
      ok: false,
      message: doc.error || "v2 evidence generation failed on the backend",
    }
  }
  if (doc.status !== "ready") {
    return { ok: false, message: "Timed out waiting for v2 evidence" }
  }
  return { ok: true, evidence: markdownToEvidenceV2State(doc.payload_md) }
}

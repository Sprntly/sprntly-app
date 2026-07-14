import { evidenceApi } from "./api"
import { markdownToEvidenceState } from "./evidence-adapter"
import { sleepUntilNextPoll } from "./poll"
import { clearPendingJob, insightScope, setPendingJob } from "./jobResume"
import type { DetailState, PrdContent } from "../types/content"

export type EvidenceGenResult =
  | { ok: true; evidence: PrdContent }
  | { ok: false; message: string }

const MAX_MS = 6 * 60 * 1000

/**
 * Poll an already-kicked-off evidence doc by id until terminal. Shared by
 * `runEvidenceGeneration` (calls generate first) and `resumeEvidenceGeneration`
 * (re-enters against a persisted id on remount). Clears the persisted
 * pending-job marker on every terminal exit.
 */
async function pollEvidenceToResult(
  evidenceId: number,
  scope: string | null,
): Promise<EvidenceGenResult> {
  let doc = await evidenceApi.get(evidenceId)
  const startedAt = Date.now()
  while (doc.status === "generating" && Date.now() - startedAt < MAX_MS) {
    // Visibility-aware sleep: a backgrounded tab throttles setTimeout to ~1/min,
    // which stalls polling though the server-side evidence job finishes.
    // Refocusing wakes immediately and re-reads the real status.
    await sleepUntilNextPoll(4000)
    doc = await evidenceApi.get(evidenceId)
  }
  if (scope) clearPendingJob("evidence", "_", scope)
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

/** Polls until the Evidence Page is ready, then parses the markdown with
 *  the evidence adapter (typed semantic blocks + standard markdown). Persists
 *  the active evidence_id so a remount can resume via
 *  `resumeEvidenceGeneration`.
 *
 *  Read-first: ready evidence for the insight is returned directly (one GET,
 *  no generate POST) — generation only starts when nothing exists yet.
 *  `force: true` skips the read AND the backend's dedup/failed-row check —
 *  the explicit retry after a failed run. */
export async function runEvidenceGeneration(
  meta: DetailState["meta"],
  opts?: { force?: boolean },
): Promise<EvidenceGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const force = opts?.force ?? false
  if (!force) {
    const existing = await loadEvidenceByInsight(meta.briefId, meta.insightIndex)
    if (existing) return { ok: true, evidence: existing }
  }
  const start = await evidenceApi.generate(meta.briefId, meta.insightIndex, force)
  // A prior run failed and the backend won't silently re-run it — surface the
  // error so the panel offers the explicit Retry (which sends force=true).
  if (start.status === "failed") {
    return {
      ok: false,
      message: start.error || "Evidence generation failed on the backend",
    }
  }
  const scope = insightScope(meta.briefId, meta.insightIndex)
  setPendingJob("evidence", "_", scope, start.evidence_id)
  return pollEvidenceToResult(start.evidence_id, scope)
}

/**
 * Re-enter polling for an evidence doc whose generation was already kicked off
 * (id persisted via `setPendingJob`) — used on remount so a background-finished
 * job resumes instead of being orphaned. Does NOT call generate again.
 */
export async function resumeEvidenceGeneration(
  evidenceId: number,
  meta: DetailState["meta"],
): Promise<EvidenceGenResult> {
  const scope = meta ? insightScope(meta.briefId, meta.insightIndex) : null
  return pollEvidenceToResult(evidenceId, scope)
}

/** Read-only sibling of runEvidenceGeneration: fetch the EXISTING evidence for a
 *  brief insight (no generation) and parse it for the panel. Returns null when
 *  no ready evidence exists yet. Used to populate the Evidence tab for the
 *  insight whose PRD is being viewed/generated. */
export async function loadEvidenceByInsight(
  briefId: number,
  insightIndex: number,
): Promise<PrdContent | null> {
  const rec = await evidenceApi.byInsight(briefId, insightIndex)
  if (!rec || rec.status !== "ready" || !rec.payload_md) return null
  return markdownToEvidenceState(rec.payload_md)
}

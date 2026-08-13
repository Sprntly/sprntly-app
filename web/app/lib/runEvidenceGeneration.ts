import { evidenceApi } from "./api"
import { markdownToEvidenceState } from "./evidence-adapter"
import { sleepUntilNextPoll } from "./poll"
import { clearPendingJob, insightScope, setPendingJob } from "./jobResume"
import { subscribeToGenerationStream } from "./streamGeneration"
import { throttlePartial } from "./runPrdGeneration"
import type { DetailState, PrdContent } from "../types/content"

export type EvidenceGenResult =
  | {
      ok: true
      evidence: PrdContent
      /** The evidences row id — what the artifact chat-summary endpoint keys on. */
      evidenceId: number
      /** True when the read-first path returned an ALREADY-ready doc (one GET,
       *  nothing generated). Callers that react to a fresh generation (the chat
       *  summary) must skip when set — a reopen is not a build. */
      existing?: true
    }
  | { ok: false; message: string }

/** Optional live-preview callback: the accumulating evidence HTML as it streams. */
export type OnEvidencePartial = (html: string) => void

const MAX_MS = 6 * 60 * 1000

/** Signals the poll loop that the stream saw its terminal `done` frame, so the
 *  next status read happens immediately instead of waiting out the 4s tick. */
type DoneSignal = { fired: boolean; promise: Promise<void> }

/**
 * Poll an already-kicked-off evidence doc by id until terminal. Shared by
 * `runEvidenceGeneration` (calls generate first) and `resumeEvidenceGeneration`
 * (re-enters against a persisted id on remount). Clears the persisted
 * pending-job marker on every terminal exit.
 *
 * `onPartial`, when given, opens an SSE token stream alongside the poll and
 * forwards the accumulating evidence HTML (throttled) for a live preview —
 * mirrors runPrdGeneration's pollPrdToResult. The poll stays the authoritative
 * source of the finished doc; the stream only feeds the preview and is always
 * torn down before returning. The stream's `done` frame also wakes the poll so
 * `ready` is picked up right away.
 */
async function pollEvidenceToResult(
  evidenceId: number,
  scope: string | null,
  onPartial?: OnEvidencePartial,
): Promise<EvidenceGenResult> {
  let wakeDone: (() => void) | null = null
  const done: DoneSignal = {
    fired: false,
    promise: new Promise<void>((resolve) => {
      wakeDone = () => {
        done.fired = true
        resolve()
      }
    }),
  }
  const throttled = onPartial ? throttlePartial(onPartial) : null
  const stopStream = throttled
    ? subscribeToGenerationStream((t) => evidenceApi.streamUrl(evidenceId, t), {
        onDelta: (full) => throttled.push(full),
        onDone: () => wakeDone?.(),
      })
    : () => {}
  try {
    return await _pollEvidenceLoop(evidenceId, scope, done)
  } finally {
    throttled?.cancel()
    stopStream()
  }
}

async function _pollEvidenceLoop(
  evidenceId: number,
  scope: string | null,
  done?: DoneSignal,
): Promise<EvidenceGenResult> {
  let doc = await evidenceApi.get(evidenceId)
  const startedAt = Date.now()
  let doneConsumed = false
  while (doc.status === "generating" && Date.now() - startedAt < MAX_MS) {
    // Visibility-aware sleep: a backgrounded tab throttles setTimeout to ~1/min,
    // which stalls polling though the server-side evidence job finishes.
    // Refocusing wakes immediately and re-reads the real status. The stream's
    // `done` frame also wakes the sleep (consumed after one use — a status read
    // lagging the frame falls back to plain ticks instead of a hot loop).
    if (done && !doneConsumed) {
      await Promise.race([sleepUntilNextPoll(4000), done.promise])
      if (done.fired) doneConsumed = true
    } else {
      await sleepUntilNextPoll(4000)
    }
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
  return {
    ok: true,
    evidence: { ...markdownToEvidenceState(doc.payload_md), question: doc.question },
    evidenceId,
  }
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
  onPartial?: OnEvidencePartial,
): Promise<EvidenceGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const force = opts?.force ?? false
  if (!force) {
    // Inlined (rather than loadEvidenceByInsight) because the caller-facing
    // result needs the row id + the `existing` marker, and the read-only
    // sibling deliberately returns only parsed content.
    const rec = await evidenceApi.byInsight(meta.briefId, meta.insightIndex)
    if (rec && rec.status === "ready" && rec.payload_md) {
      return {
        ok: true,
        evidence: { ...markdownToEvidenceState(rec.payload_md), question: rec.question },
        evidenceId: rec.id,
        existing: true,
      }
    }
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
  return pollEvidenceToResult(start.evidence_id, scope, onPartial)
}

/**
 * Re-enter polling for an evidence doc whose generation was already kicked off
 * (id persisted via `setPendingJob`) — used on remount so a background-finished
 * job resumes instead of being orphaned. Does NOT call generate again.
 */
export async function resumeEvidenceGeneration(
  evidenceId: number,
  meta: DetailState["meta"],
  onPartial?: OnEvidencePartial,
): Promise<EvidenceGenResult> {
  const scope = meta ? insightScope(meta.briefId, meta.insightIndex) : null
  return pollEvidenceToResult(evidenceId, scope, onPartial)
}

/** Read-only sibling of runEvidenceGeneration: fetch the EXISTING evidence for a
 *  brief insight (no generation) and parse it for the panel. Returns null when
 *  no ready evidence exists yet. Used to populate the Evidence tab for the
 *  insight whose PRD is being viewed/generated. */
export async function loadEvidenceByInsight(
  briefId: number,
  insightIndex: number,
): Promise<(PrdContent & { evidenceId: number }) | null> {
  const rec = await evidenceApi.byInsight(briefId, insightIndex)
  if (!rec || rec.status !== "ready" || !rec.payload_md) return null
  // `evidenceId` rides along so a chat tab can name the document it has open
  // when it asks a question (POST /v1/ask `evidence_id` — see ChatScreen's
  // submit path). Purely additive: every existing consumer reads this value
  // as a plain PrdContent and never notices the extra key.
  return { ...markdownToEvidenceState(rec.payload_md), question: rec.question, evidenceId: rec.id }
}

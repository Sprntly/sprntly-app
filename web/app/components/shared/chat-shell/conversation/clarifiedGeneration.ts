"use client"

// The clarify/sufficiency-wizard's "run generation" path, shared by every chat
// surface. Both the tab-scoped surface (ChatScreen) and the single-conversation
// surface (useProjectConversation) used to carry a full copy of this sequence,
// and the two drifting apart is exactly how the card's "generate now / answers
// submitted" affordance ends up behaving differently in a project than in the
// main chat.
//
// EXTRACT-IN-PLACE, NOT A REWRITE. Everything surface-specific stays a seam the
// caller injects (its state writes, its conversation binding, its toast/summary
// policy). What lives here is only the invariant the two must keep in lockstep:
//   • the 4000-char task trim (a 422 lands AFTER the ack is on screen otherwise),
//   • the identical "Generating a PRD…" acknowledgment copy,
//   • the SYNCHRONOUS seed order (seed ack turn + spinner, then persist), and
//   • the async generate→bind→resume→dispatch control flow.
//
// RENDER-COMMIT ORDERING IS LOAD-BEARING and preserved by construction:
// `runClarifiedGeneration` is a plain synchronous function. It calls
// `seedAckTurn` and `openPanel` back-to-back in the SAME call stack the caller
// invoked it from — no await, no effect, no microtask in between — so React
// batches both surfaces' state writes into the one render commit they always
// landed on (the ack turn + the panel spinner appear together). The only awaits
// sit inside the fire-and-forget async block, at the exact points the inline
// copies awaited (generateFromTask, resumePrdGeneration), so the later commit
// boundaries are unchanged too. Callers that flip the clarify card to its
// resolved record (markClarifyResolved) must still do so BEFORE calling this —
// that ordering lives at the call site, not here.

import { type AskResponse } from "../../../../lib/api"
import { resumePrdGeneration, type PrdGenResult } from "../../../../lib/runPrdGeneration"

/** The backend's `task` cap. The combined task (original command + the user's
 *  echoed clarify answers) can outgrow it; trimming the tail beats a 422 after
 *  the "Generating a PRD…" ack is already on screen. */
export const CLARIFIED_GENERATION_TASK_MAX = 4000

/** Trim the combined clarify task to the backend cap (losing the last answer's
 *  tail beats losing the whole generation). */
export function trimClarifiedTask(rawTask: string): string {
  return rawTask.length > CLARIFIED_GENERATION_TASK_MAX
    ? `${rawTask.slice(0, CLARIFIED_GENERATION_TASK_MAX - 1)}…`
    : rawTask
}

/** The acknowledgment both surfaces seed onto the command turn the instant
 *  generation starts. Byte-for-byte identical copy on purpose — it's what
 *  `PRD_ACK_ANSWER_RE` keys off to keep the ack out of the next PRD's grounding. */
export function clarifiedGenerationAck(): AskResponse {
  return {
    answer:
      "Generating a PRD for that — it'll open in the panel on the right when ready. Use the View PRD button in this chat to reopen the panel anytime.",
    sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
  } as AskResponse
}

/** The subset of `PrdStartResponse` this flow reads. */
export type ClarifiedGenerationStart = {
  prd_id: number
  title?: string | null
  project_id?: number | null
}

type PrdReadyResult = Extract<PrdGenResult, { ok: true }>

/** The surface-specific seams. Each caller supplies its exact current closures;
 *  where a surface does nothing today (e.g. the project surface posts no summary
 *  and shows no toast), it simply omits or no-ops that seam. */
export type ClarifiedGenerationSeams = {
  /** The surface's turn-id generator. */
  newId: () => string
  /** SYNCHRONOUS: append the ack turn to the thread, clear the pending clarify,
   *  set prdGenerating. Runs in the caller's call stack (one render commit with
   *  `openPanel`). */
  seedAckTurn: (id: string, userMessage: string, ack: AskResponse) => void
  /** SYNCHRONOUS: open the panel + show its spinner (setContent + panel open),
   *  batched into the same commit as `seedAckTurn`. */
  openPanel: () => void
  /** Persist the optimistic user/ack turn on the surface's conversation. */
  pushPendingConversation: (id: string, userMessage: string) => void
  /** Finalize the ack turn's reply on the surface's rail. */
  finalizeAck: (id: string, ack: AskResponse) => void
  /** The streaming Part-A HTML sink. */
  onPartial: (html: string) => void
  /** Synchronous read of the conversation id to hand the backend at generate time. */
  resolveKnownConvId: () => number | null
  /** Kick the backend generation (each surface binds its own `prdApi`). */
  generateFromTask: (
    task: string,
    sourceDocs: { name: string; content: string }[] | undefined,
    knownConvId: number | null,
  ) => Promise<ClarifiedGenerationStart>
  /** Generation kicked: bind conv↔prd and stamp prdId (+ title, where kept). */
  onStarted: (start: ClarifiedGenerationStart, knownConvId: number | null) => void
  /** Generation resolved OK. */
  onSuccess: (start: ClarifiedGenerationStart, result: PrdReadyResult) => void
  /** Generation resolved not-ok (backend message). */
  onFailure: (message: string) => void
  /** Generation threw. */
  onError: (error: unknown) => void
}

/**
 * Run PRD generation for a clarify-resolved task. Synchronous by design: it
 * seeds the ack turn + panel spinner on the current commit, then kicks the
 * network work in a fire-and-forget async block. See the file header on why the
 * commit ordering is preserved.
 */
export function runClarifiedGeneration(
  rawTask: string,
  sourceDocs: { name: string; content: string }[] | undefined,
  userMessage: string,
  seams: ClarifiedGenerationSeams,
): void {
  const task = trimClarifiedTask(rawTask)
  const id = seams.newId()
  const ack = clarifiedGenerationAck()
  // ── One render commit: the ack turn appears and the panel spinner opens. ──
  seams.seedAckTurn(id, userMessage, ack)
  seams.openPanel()
  seams.pushPendingConversation(id, userMessage)
  seams.finalizeAck(id, ack)
  // ── Network AFTER the render (same await points as the inline copies). ──
  void (async () => {
    try {
      const knownConvId = seams.resolveKnownConvId()
      const start = await seams.generateFromTask(task, sourceDocs, knownConvId)
      seams.onStarted(start, knownConvId)
      const result = await resumePrdGeneration(start.prd_id, undefined, seams.onPartial)
      if (result.ok) {
        seams.onSuccess(start, result)
      } else {
        seams.onFailure(result.message)
      }
    } catch (e) {
      seams.onError(e)
    }
  })()
}

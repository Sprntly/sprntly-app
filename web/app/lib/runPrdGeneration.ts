import { prdApi } from "./api"
import { markdownToPrdState } from "./prd-adapter"
import { sleepUntilNextPoll } from "./poll"
import { clearPendingJob, insightScope, setPendingJob } from "./jobResume"
import { subscribeToGenerationStream } from "./streamGeneration"
import type { DetailState, PrdState } from "../types/content"

export type PrdGenResult = { ok: true; prd: PrdState } | { ok: false; message: string }

/** Optional live-preview callback: the accumulating Part A HTML as it streams. */
export type OnPrdPartial = (html: string) => void

const MAX_MS = 6 * 60 * 1000

/**
 * Leading+trailing throttle for the live-preview callback: the first delta
 * renders immediately, then at most one update per `intervalMs`, always
 * ending on the latest html (trailing edge). Deltas arrive far faster than an
 * iframe should re-render, so this is what keeps the preview from thrashing.
 * `cancel()` drops any pending trailing update — called on stream teardown so
 * a late timer can't resurrect a stale preview after the real PRD landed.
 * Exported for tests.
 */
export function throttlePartial(
  fn: OnPrdPartial,
  intervalMs = 400,
): { push: OnPrdPartial; cancel: () => void } {
  let last = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let latest = ""
  const fire = () => {
    last = Date.now()
    timer = null
    fn(latest)
  }
  return {
    push: (html) => {
      latest = html
      if (timer) return
      const wait = intervalMs - (Date.now() - last)
      if (wait <= 0) fire()
      else timer = setTimeout(fire, wait)
    },
    cancel: () => {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
    },
  }
}

/** Signals the poll loop that the stream saw its terminal `done` frame, so the
 *  next status read happens immediately instead of waiting out the 4s tick. */
type DoneSignal = { fired: boolean; promise: Promise<void> }

/**
 * Poll an already-kicked-off PRD by id until terminal, then map to the result
 * shape. Shared by `runPrdGeneration` (which calls generate first) and
 * `resumePrdGeneration` (which re-enters against a persisted id on remount).
 * Clears the persisted pending-job marker on every terminal exit (ready /
 * failed / timeout) so the resume only fires while a job is genuinely running.
 *
 * `onPartial`, when given, opens an SSE token stream alongside the poll and
 * forwards the accumulating Part A HTML (throttled) for a live preview. The
 * poll stays the authoritative source of the finished PRD; the stream only
 * feeds the preview and is always torn down before returning. The stream's
 * `done` frame also wakes the poll so `ready` is picked up right away.
 */
async function pollPrdToResult(
  prdId: number,
  scope: string | null,
  onPartial?: OnPrdPartial,
): Promise<PrdGenResult> {
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
    ? subscribeToGenerationStream((t) => prdApi.streamUrl(prdId, t), {
        onDelta: (full) => throttled.push(full),
        onDone: () => wakeDone?.(),
      })
    : () => {}
  try {
    return await _pollPrdLoop(prdId, scope, done)
  } finally {
    throttled?.cancel()
    stopStream()
  }
}

async function _pollPrdLoop(
  prdId: number,
  scope: string | null,
  done?: DoneSignal,
): Promise<PrdGenResult> {
  let prd = await prdApi.get(prdId)
  const startedAt = Date.now()
  let doneConsumed = false
  while (prd.status === "generating" && Date.now() - startedAt < MAX_MS) {
    // Visibility-aware sleep: a backgrounded tab throttles setTimeout to ~1/min,
    // which stalls polling though the server-side PRD job finishes. Refocusing
    // wakes immediately and re-reads the real status. The stream's `done` frame
    // also wakes the sleep (consumed after one use — a status read lagging the
    // frame falls back to plain ticks instead of a hot loop).
    if (done && !doneConsumed) {
      await Promise.race([sleepUntilNextPoll(4000), done.promise])
      if (done.fired) doneConsumed = true
    } else {
      await sleepUntilNextPoll(4000)
    }
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
      briefId: prd.brief_id,
      insightIndex: prd.insight_index,
      source: prd.source,
    },
  }
}

/** Polls until PRD is ready (same contract as DetailScreen). Persists the
 *  active prd_id so a remount can resume via `resumePrdGeneration`. */
export async function runPrdGeneration(
  meta: DetailState["meta"],
  onPartial?: OnPrdPartial,
): Promise<PrdGenResult> {
  if (!meta) {
    return { ok: false, message: "Open this evidence from the brief first." }
  }
  const start = await prdApi.generate(meta.briefId, meta.insightIndex)
  // briefId is globally unique, so the insight scope alone is unambiguous
  // across companies — the "_" company token keeps the key shape uniform.
  const scope = insightScope(meta.briefId, meta.insightIndex)
  setPendingJob("prd", "_", scope, start.prd_id)
  return pollPrdToResult(start.prd_id, scope, onPartial)
}

/**
 * Kick off + poll PRD generation for an IDEATION item (a theme ranked ≥ 4 that
 * isn't in the brief's top-3, so it has no insight_index). Mirrors
 * `runPrdGeneration` but starts from an ideation_item_id — the backend
 * synthesizes the insight and anchors the PRD to the company's current brief.
 * Polling, pending-job persistence, and the result shape are identical, so the
 * content panel renders an ideation PRD exactly like a brief PRD.
 */
export async function runPrdGenerationFromIdeation(
  ideationItemId: string,
  onPartial?: OnPrdPartial,
): Promise<PrdGenResult> {
  const start = await prdApi.generateFromIdeation(ideationItemId)
  // Scope the pending-job marker by the ideation item — ideation PRDs share a
  // sentinel insight_index, so the item id is the unambiguous resume key.
  const scope = `ideation:${ideationItemId}`
  setPendingJob("prd", "_", scope, start.prd_id)
  return pollPrdToResult(start.prd_id, scope, onPartial)
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
  onPartial?: OnPrdPartial,
): Promise<PrdGenResult> {
  const scope = meta ? insightScope(meta.briefId, meta.insightIndex) : null
  return pollPrdToResult(prdId, scope, onPartial)
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
      briefId: prd.brief_id,
      insightIndex: prd.insight_index,
      source: prd.source,
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
      briefId: prd.brief_id,
      insightIndex: prd.insight_index,
      source: prd.source,
    },
  }
}

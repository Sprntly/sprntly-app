// Blur/remount-safe chat Ask flow.
//
// POST /v1/ask is fire-and-forget: it returns an `ask_id` and the answer keeps
// generating server-side. We persist the active ask_id per chat tab (jobResume)
// and poll the status endpoint with the shared visibility-aware `pollUntil`, so
// the answer survives a backgrounded tab (setTimeout throttled to ~1/min) AND a
// remount (the awaiting closure is gone, but the persisted id lets us re-attach
// instead of re-asking). Mirrors runEvidenceGeneration / runPrdGeneration.

import { askApi, ApiError } from "./api"
import type { AskResponse, AskStatusResponse } from "./api"
import { pollUntil } from "./poll"
import { clearPendingJob, getPendingJob, setPendingJob, type PendingJob } from "./jobResume"
import { throttlePartial } from "./runPrdGeneration"
import { subscribeToGenerationStream } from "./streamGeneration"
import { providerNoticeFromAsk, type ProviderNotice } from "./providerLimitNotice"

/** Live-preview callback: the accumulating answer markdown as it streams.
 *  Progressive display only — the poll's final payload stays authoritative. */
export type OnAskPartial = (markdown: string) => void

/**
 * Fired when the SSE preview channel drops AFTER it had already delivered at
 * least one delta, while the poll is still running.
 *
 * This is a DISPLAY signal, never an error: the poll remains authoritative and
 * still resolves the finished answer. Its only job is to let the waiting surface
 * say "the live preview dropped out, the answer is still generating" instead of
 * freezing a half-sentence under a blinking cursor with no explanation — which
 * is what a dropped EventSource looked like before.
 *
 * The "at least one delta" gate matters: most skills publish nothing on this
 * channel, so `onerror` fires immediately on those runs. Firing then would put a
 * "the preview dropped out" note on every non-streaming answer, which is exactly
 * the kind of claim-without-a-signal this surface exists to avoid.
 */
export type OnAskStreamDrop = () => void

// Answer deltas re-render a markdown bubble (much cheaper than the PRD's
// iframe, but still a full remark parse) — cap preview updates to ~7/s.
const PARTIAL_THROTTLE_MS = 150

// Wall-clock budget. Date.now()-measured inside pollUntil so a throttled
// background tab still times out correctly. 12 min (not the evidence/PRD
// pollers' 6): the public-feedback report legitimately runs ~8 minutes
// (a web-search capture sweep plus a document-scale synthesis) on the same
// ask job as every other chat answer.
const MAX_MS = 12 * 60 * 1000
const POLL_INTERVAL_MS = 1500

// A dropped/blipped connection (a dev-server reload, a moment offline, a reset
// keep-alive socket) surfaces as a NON-ApiError throw from `fetch` — the browser's
// "Failed to fetch" TypeError. The Ask job lives server-side and its status
// endpoint is idempotent to read, so a single failed request must NEVER collapse
// the whole ask into an error bubble: retry transport failures a few times before
// giving up. This matters most for multi-file / large-context asks, which take
// longer to generate and therefore poll many more times — each poll another
// chance to hit a transient blip. A real HTTP error (ApiError: a 4xx/5xx like the
// 404 tenant gate or a 422 validation) is deterministic, so it propagates at once.
const TRANSIENT_RETRIES = 4
const TRANSIENT_BACKOFF_MS = 400

async function withTransientRetry<T>(fn: () => Promise<T>): Promise<T> {
  let lastErr: unknown
  for (let attempt = 0; attempt <= TRANSIENT_RETRIES; attempt++) {
    try {
      return await fn()
    } catch (e) {
      // Deterministic HTTP failure, or retries exhausted → surface it.
      if (e instanceof ApiError || attempt === TRANSIENT_RETRIES) throw e
      lastErr = e
      await new Promise((r) => setTimeout(r, TRANSIENT_BACKOFF_MS * (attempt + 1)))
    }
  }
  throw lastErr
}

/** Stable per-tab scope for a chat Ask job. The tab id (a uuid) is unique per
 *  conversation tab, so a persisted ask_id is unambiguous on remount. */
export function askScope(tabId: string): string {
  return `t:${tabId}`
}

/** localStorage-persisted pending Ask id for a tab, or null. */
export function getPendingAsk(company: string, tabId: string): PendingJob | null {
  return getPendingJob("ask", company, askScope(tabId))
}

class AskFailedError extends Error {
  /** The typed provider refusal behind this failure, when there was one.
   *
   *  Carried on the ERROR rather than returned alongside it because every
   *  caller already has a `catch` and none has a second channel — and the one
   *  thing that must not happen is the reason being available server-side and
   *  invisible to the person who made the request. Undefined for an ordinary
   *  failure, so existing handling is untouched. */
  providerNotice?: ProviderNotice
}

/**
 * The 12-minute wall-clock budget expired while the job was still `generating`.
 *
 * NOT a failure: the server job may yet finish, which is why the persisted
 * ask_id is deliberately left in place so a reload re-attaches. Split out from
 * the generic failure so the chat can render the honest "still running on our
 * side — reload and it will pick up where it left off" state instead of the
 * red "that answer didn't come through" bubble.
 */
export class AskTimeoutError extends AskFailedError {}

/**
 * Thrown when the poll is cancelled mid-flight because the chat UI went away
 * (ChatScreen unmounted — the user navigated to another screen). Unlike a
 * failure, this is NOT surfaced as an error: the pending ask_id is deliberately
 * LEFT in place so the mount-time resume effect re-attaches and populates the
 * answer when the user returns. Callers must swallow it (no error state / toast).
 */
export class AskCancelledError extends Error {}

/**
 * Thrown when the user explicitly STOPS an ask (the composer's Stop button).
 * Unlike AskCancelledError (a silent UI-unmount that LEAVES the pending id so a
 * remount resumes), a stop is deliberate: the persisted ask_id is CLEARED so
 * the ask is not resumed, and the backend job is cancelled separately by the
 * caller. Also thrown when the poll observes a job that reached the `cancelled`
 * terminal state. Callers swallow it (no error bubble/toast) — the stopped turn
 * is rendered by the component instead.
 */
export class AskStoppedError extends Error {}

function toAskResponse(status: AskStatusResponse): AskResponse {
  // Drop the job envelope (status/error); keep the answer body + any extra
  // qa_agent fields (e.g. _skill) the renderer reads.
  const { status: _s, error: _e, ...rest } = status
  return rest as unknown as AskResponse
}

/**
 * Poll an already-kicked-off Ask job by id until terminal, then return the
 * answer. Shared by `runAskGeneration` (POSTs first) and `resumeAskGeneration`
 * (re-attaches to a persisted id on remount). Clears the persisted pending-job
 * marker on every terminal exit. Throws on backend error / timeout so the
 * caller's existing error UX (`runTabAsk.onError`) renders the failure.
 *
 * ONE exception to the "clear on exit" rule: if the poll was CANCELLED because
 * the chat UI unmounted (`isCancelled` flipped mid-flight), the marker is left
 * intact and `AskCancelledError` is thrown. That is what lets a background
 * completion survive navigating away — the persisted id stays put so the
 * mount-time resume effect re-fetches the (server-retained) answer on return
 * instead of the answer being silently dropped by a no-op state write.
 */
async function pollAskToResult(
  askId: number,
  company: string,
  tabId: string,
  isCancelled?: () => boolean,
  isStopped?: () => boolean,
  onPartial?: OnAskPartial,
  onStreamDrop?: OnAskStreamDrop,
): Promise<AskResponse> {
  const scope = askScope(tabId)
  // `onPartial` opens an SSE token stream ALONGSIDE the poll and forwards the
  // accumulating answer markdown (throttled) for a live word-by-word preview.
  // The poll stays the authoritative source of the finished answer; any stream
  // failure (transport drop, multi-worker box, non-streamable skill path that
  // publishes nothing) just means no preview — never an error. Always torn
  // down before returning, so a late frame can't touch a settled turn.
  const throttled = onPartial ? throttlePartial(onPartial, PARTIAL_THROTTLE_MS) : null
  // A preview that was RUNNING and then died is the one stream event worth
  // telling the user about (see OnAskStreamDrop). One that never produced a
  // delta is indistinguishable from a skill that simply doesn't stream, so it
  // stays invisible.
  let sawDelta = false
  // The stream's `done` frame is the earliest signal that the job has finished
  // writing its row, and it lands well before the next poll tick would. Waking
  // the poll on it removes up to a full interval of dead time between an answer
  // being ready and being shown.
  //
  // It WAKES the poll rather than resolving it: SSE frames are display-only and
  // the polled row stays authoritative, so the loop still re-reads status and
  // decides for itself. Deliberately not wired to `onError` — a dropped stream
  // says nothing about whether the job finished, and the ordinary interval
  // already covers that case.
  let wakePoll: (() => void) | null = null
  const stopStream = throttled
    ? subscribeToGenerationStream((t) => askApi.streamUrl(askId, t), {
        onDelta: (full) => {
          sawDelta = true
          throttled.push(full)
        },
        onDone: () => {
          wakePoll?.()
        },
        onError: () => {
          if (sawDelta) onStreamDrop?.()
        },
      })
    : () => {}
  try {
    return await _pollAskLoop(
      askId, company, scope, isCancelled, isStopped,
      // Registered per sleep; cleared on wake so a frame arriving between
      // sleeps never parks a stale callback.
      (wake) => {
        wakePoll = wake
        return () => {
          wakePoll = null
        }
      },
    )
  } finally {
    throttled?.cancel()
    stopStream()
  }
}

/**
 * Console trace of what a question routed to. Debug aid, not product UI.
 *
 * Until this existed there was NO way — for a user or an engineer — to see
 * which skill answered a plain (no-slash) question: the composer only ever
 * shows a chip when you type the trigger yourself, so automatic selection was
 * completely opaque. That is why a router bug survived unnoticed; you cannot
 * report "it picked the wrong skill" when nothing tells you what it picked.
 *
 * `routed_skill` is null when the router deliberately chose no skill, which is
 * a real outcome and is logged as such rather than skipped — "answered
 * directly" is exactly the case worth seeing when a skill was expected.
 */
function logAskRoute(askId: number, s: AskStatusResponse): void {
  const skill = s.routed_skill ?? null
  // eslint-disable-next-line no-console
  console.info(
    `[ask:route] #${askId} ${s.status} → ${skill ?? "(no skill — answered directly)"}`,
    { skill, action: s.routed_skill_action ?? null, status: s.status },
  )
}

async function _pollAskLoop(
  askId: number,
  company: string,
  scope: string,
  isCancelled?: () => boolean,
  isStopped?: () => boolean,
  wakeOn?: (wake: () => void) => (() => void) | void,
): Promise<AskResponse> {
  // Log the routing decision ONCE, the first poll that reveals it — the column
  // is populated the moment `route()` returns, so this fires while the answer
  // is still generating rather than after it lands. Repeating it every poll
  // would bury the one line that matters under a dozen identical ones.
  let loggedRoute = false
  const final = await pollUntil<AskStatusResponse>({
    // A single transient "Failed to fetch" during polling must not kill an ask
    // whose server-side job is still running fine — retry the status read.
    fetchStatus: () =>
      withTransientRetry(() => askApi.get(askId)).then((s) => {
        // Terminal states always log, even if the route never appeared, so a
        // failed or skill-less ask still produces exactly one trace line.
        if (!loggedRoute && (s.routed_skill != null || s.status !== "generating")) {
          loggedRoute = true
          logAskRoute(askId, s)
        }
        return s
      }),
    isDone: (v) => v.status !== "generating",
    maxMs: MAX_MS,
    intervalMs: POLL_INTERVAL_MS,
    // Either signal stops the local poll; the two are disambiguated below.
    isCancelled: () => Boolean(isCancelled?.() || isStopped?.()),
    // Absent on the no-preview paths (AIBar, resume without onPartial), which
    // keep the plain interval exactly as before.
    wakeOn,
  })
  // Explicit user Stop → the ask is deliberately abandoned: CLEAR the marker so
  // a remount does not resume it, and surface AskStoppedError (swallowed by the
  // caller — the stopped turn is rendered directly, not as an error).
  if (isStopped?.()) {
    clearPendingJob("ask", company, scope)
    throw new AskStoppedError("Ask stopped by the user")
  }
  // Unmounted mid-poll → do NOT clear the marker; a remount re-attaches by id.
  if (isCancelled?.()) throw new AskCancelledError("Ask poll cancelled (UI unmounted)")
  // Wall-clock timeout while the job is still generating: the server job may
  // yet finish, so LEAVE the marker in place — a reload/remount re-attaches by
  // id and picks the answer up (this is what the timeout message promises).
  // Clearing here used to orphan every answer that outlived the budget.
  if (final.status === "generating") {
    throw new AskTimeoutError("Timed out waiting for the answer")
  }
  clearPendingJob("ask", company, scope)
  if (final.status === "ready") return toAskResponse(final)
  // The job was cancelled server-side (a Stop from this or another tab/device
  // landed and the poll observed the terminal state) — treat as a stop, not a
  // failure, so no error bubble is shown.
  if (final.status === "cancelled") {
    throw new AskStoppedError("Ask was stopped")
  }
  if (final.status === "error") {
    // A PROVIDER refusal (out of credits, rate limited, overloaded) gets the
    // server's own user-safe sentence as the error message AND the typed
    // notice attached, so the surface can both render the bubble and raise a
    // toast the user cannot miss. `final.error` is a stringified exception —
    // never the right thing to put in front of a person.
    const notice = providerNoticeFromAsk(final)
    const failure = new AskFailedError(
      notice?.message || final.error || "Ask failed on the backend",
    )
    if (notice) failure.providerNotice = notice
    throw failure
  }
  // Unreachable: generating (timeout) throws above; ready/cancelled/error all
  // returned or threw.
  throw new AskFailedError("Ask ended in an unexpected state")
}

/**
 * Run one chat Ask end-to-end: POST to get an ask_id, persist it for the tab,
 * then poll until the answer is ready. Returns the `AskResponse` the chat
 * renderer expects (same shape as the old synchronous `askApi.ask`).
 */
export async function runAskGeneration(
  question: string,
  company: string,
  tabId: string,
  opts?: {
    conversation_id?: number
    pinned_skill?: string
    prd_id?: number
    /** Individual project chat: folds the project's memory into this turn
     *  server-side. Passed straight through to `askApi.start` — see its own
     *  doc for the membership-gate contract. */
    project_id?: number
    /** Standalone-artifact grounding — the open evidence report / ticket set,
     *  mutually exclusive with prd_id (the tab has one primary artifact). */
    evidence_id?: number
    ticket_set_id?: number
    /** Individual-project-chat send identity — the idempotency key the
     *  server persists this turn's user side under. Passed straight through
     *  to `askApi.start`. */
    client_message_id?: string
    isCancelled?: () => boolean
    isStopped?: () => boolean
    onPartial?: OnAskPartial
    onStreamDrop?: OnAskStreamDrop
  },
): Promise<AskResponse> {
  // A POST failure (4xx/5xx) propagates as-is so the route's error detail
  // (e.g. validation / 404 tenant gate) renders unchanged via runTabAsk.onError.
  // A transient transport failure ("Failed to fetch") is retried first — the
  // kick-off must not fail on a momentary blip while the backend is healthy.
  const start = await withTransientRetry(() => askApi.start(question, company, opts))
  setPendingJob("ask", company, askScope(tabId), start.ask_id)
  // A cache hit comes back immediately-`ready` — there is no generation to
  // stream, so don't open an EventSource that would only ever see silence.
  const onPartial = start.status === "generating" ? opts?.onPartial : undefined
  return pollAskToResult(
    start.ask_id, company, tabId, opts?.isCancelled, opts?.isStopped, onPartial, opts?.onStreamDrop,
  )
}

/**
 * Re-attach to an Ask whose POST already happened (id persisted via
 * setPendingJob) — used on tab mount so a background-finished answer resumes
 * instead of being orphaned. Does NOT re-POST.
 */
export async function resumeAskGeneration(
  askId: number,
  company: string,
  tabId: string,
  isCancelled?: () => boolean,
  isStopped?: () => boolean,
  onPartial?: OnAskPartial,
  onStreamDrop?: OnAskStreamDrop,
): Promise<AskResponse> {
  // A resume re-attaches mid-generation: the stream's replay frame catches the
  // preview up with everything emitted before this mount, then live deltas.
  return pollAskToResult(askId, company, tabId, isCancelled, isStopped, onPartial, onStreamDrop)
}

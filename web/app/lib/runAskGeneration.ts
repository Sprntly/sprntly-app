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

// Wall-clock budget; matches the evidence/PRD pollers. Date.now()-measured
// inside pollUntil so a throttled background tab still times out correctly.
const MAX_MS = 6 * 60 * 1000
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

class AskFailedError extends Error {}

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
): Promise<AskResponse> {
  const scope = askScope(tabId)
  const final = await pollUntil<AskStatusResponse>({
    // A single transient "Failed to fetch" during polling must not kill an ask
    // whose server-side job is still running fine — retry the status read.
    fetchStatus: () => withTransientRetry(() => askApi.get(askId)),
    isDone: (v) => v.status !== "generating",
    maxMs: MAX_MS,
    intervalMs: POLL_INTERVAL_MS,
    // Either signal stops the local poll; the two are disambiguated below.
    isCancelled: () => Boolean(isCancelled?.() || isStopped?.()),
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
  clearPendingJob("ask", company, scope)
  if (final.status === "ready") return toAskResponse(final)
  // The job was cancelled server-side (a Stop from this or another tab/device
  // landed and the poll observed the terminal state) — treat as a stop, not a
  // failure, so no error bubble is shown.
  if (final.status === "cancelled") {
    throw new AskStoppedError("Ask was stopped")
  }
  if (final.status === "error") {
    throw new AskFailedError(final.error || "Ask failed on the backend")
  }
  // Loop exited still 'generating' → wall-clock timeout (the server job may
  // still finish; a later remount re-attaches via the persisted id if any).
  throw new AskFailedError("Timed out waiting for the answer")
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
    isCancelled?: () => boolean
    isStopped?: () => boolean
  },
): Promise<AskResponse> {
  // A POST failure (4xx/5xx) propagates as-is so the route's error detail
  // (e.g. validation / 404 tenant gate) renders unchanged via runTabAsk.onError.
  // A transient transport failure ("Failed to fetch") is retried first — the
  // kick-off must not fail on a momentary blip while the backend is healthy.
  const start = await withTransientRetry(() => askApi.start(question, company, opts))
  setPendingJob("ask", company, askScope(tabId), start.ask_id)
  return pollAskToResult(start.ask_id, company, tabId, opts?.isCancelled, opts?.isStopped)
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
): Promise<AskResponse> {
  return pollAskToResult(askId, company, tabId, isCancelled, isStopped)
}

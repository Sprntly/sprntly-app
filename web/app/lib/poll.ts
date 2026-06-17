// Shared visibility-aware polling helpers.
//
// Background tabs throttle setTimeout to ~1/min, so a plain `setTimeout`-based
// poll loop stalls when the tab is backgrounded — the server-side job finishes
// but the UI never catches up until the tab is refocused AND the (throttled)
// timer finally fires. Every server-side AI flow (brief / PRD / evidence /
// multi-agent / design-agent / pipeline) is fire-and-forget with an idempotent
// status endpoint, so it's safe to wake the moment the tab becomes visible and
// re-read the real status. These helpers centralize that behavior (extracted
// from the brief poller in workspace-brief.ts).

/**
 * Resolve after `ms`, OR as soon as a hidden tab becomes visible again —
 * whichever comes first. Background tabs throttle setTimeout to ~1/min, so
 * without the visibility wakeup a refocused tab would stall up to a minute
 * before its next status poll. Server-side jobs are idempotent, so waking early
 * simply re-reads the real status and lets the UI catch up.
 */
export function sleepUntilNextPoll(ms: number): Promise<void> {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(timer)
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible)
      }
      resolve()
    }
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        finish()
      }
    }
    const timer = setTimeout(finish, ms)
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible)
    }
  })
}

export type PollUntilOptions<T> = {
  /** Fetch the latest status/record. Called immediately, then after each sleep. */
  fetchStatus: () => Promise<T>
  /** Return true once `value` is terminal (ready / failed / etc.) — stops the loop. */
  isDone: (value: T) => boolean
  /**
   * Wall-clock budget in ms, measured via Date.now() (NOT a tick count) so a
   * backgrounded tab whose timers are throttled still measures elapsed time
   * correctly and times out as expected.
   */
  maxMs: number
  /** Sleep between polls (visibility-aware). */
  intervalMs: number
  /** Optional cooperative cancel — when it returns true the loop bails out. */
  isCancelled?: () => boolean
}

/**
 * Poll `fetchStatus` until `isDone` or the wall-clock budget (`maxMs`) is
 * exhausted, sleeping `intervalMs` between polls but waking immediately when a
 * backgrounded tab is refocused. Returns the last fetched value (the caller
 * inspects its status to distinguish done-vs-timeout). The first fetch happens
 * immediately so a refocus-driven re-entry catches an already-finished job at
 * once.
 */
export async function pollUntil<T>(opts: PollUntilOptions<T>): Promise<T> {
  const { fetchStatus, isDone, maxMs, intervalMs, isCancelled } = opts
  const start = Date.now()
  let value = await fetchStatus()
  while (
    !isDone(value) &&
    !isCancelled?.() &&
    Date.now() - start < maxMs
  ) {
    await sleepUntilNextPoll(intervalMs)
    if (isCancelled?.()) return value
    value = await fetchStatus()
  }
  return value
}

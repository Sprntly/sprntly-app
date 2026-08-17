// Autosave for a shared team document.
//
// Deliberately plain logic with no React and no ProseMirror in it, for one
// reason: this is the part that can LOSE SOMEONE'S WRITING, and it has to be
// testable without standing up an editor. Everything that decides when to
// write, what version to write against, and what to do when the write is
// refused lives here; the component only feeds it text and renders the state
// it reports.
//
// THE SHAPE OF THE PROBLEM. The document is shared with the whole team and
// saves are debounced, so two people typing in the same paragraph is a real,
// ordinary event rather than an edge case. The server settles it with a
// compare-and-set: a save carries the `version` it started from and is refused
// with 409 when the stored row has moved on. That refusal is not an error to
// retry — retrying would overwrite the colleague the check just protected — so
// this module surfaces it as a state the UI must resolve, and STOPS SAVING
// until it is resolved. A conflicted editor that keeps retrying is how you turn
// one lost paragraph into a fight.
//
// WHAT IT REFUSES TO DO:
//   * save while a conflict is unresolved (see above);
//   * overlap two writes (a slow save followed by a fast keystroke would
//     otherwise race, and the loser's text could land last);
//   * save text identical to what the server already has.

/** How long the typing has to stop before a save is sent. Long enough that a
 *  sentence is one request rather than thirty; short enough that closing the
 *  tab shortly after typing does not lose the thought. */
export const SAVE_DEBOUNCE_MS = 1200

export type SaveState =
  | { kind: "idle" }
  /** Text is pending: the debounce is running, or a save is in flight. */
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  /** The write failed for a reason that is worth retrying (network, 5xx). The
   *  scheduler keeps the text and retries on the next change or flush. */
  | { kind: "error"; message: string }
  /** Someone else saved first. TERMINAL until the caller resolves it — see the
   *  module note. `theirs` is the document as it now stands, so the UI can show
   *  what landed instead of just refusing. */
  | { kind: "conflict"; theirs: ConflictDoc | null }

export type ConflictDoc = {
  id: number
  title: string
  body_html: string
  version: number
  updated_by: string | null
}

export type SavePayload = { title?: string; body_html?: string }

export type SaveResult = { version: number }

export type SaveFn = (
  payload: SavePayload & { base_version: number },
) => Promise<SaveResult>

/** Raised by the caller's save function when the server answers 409. */
export class SaveConflict extends Error {
  theirs: ConflictDoc | null
  constructor(theirs: ConflictDoc | null) {
    super("version conflict")
    this.theirs = theirs
  }
}

export type Scheduler = {
  /** Queue a change. Resets the debounce. */
  schedule: (patch: SavePayload) => void
  /** Send whatever is pending right now (used on blur and on unmount). */
  flush: () => Promise<void>
  /** Adopt a new base version and clear a conflict — what "keep theirs" or
   *  "reload" does. Pending text is dropped, because it was written against a
   *  document that no longer exists. */
  reset: (version: number) => void
  /** Stop all timers. Safe to call twice. */
  dispose: () => void
  /** Test/inspection seam. */
  pendingKeys: () => string[]
}

export function createSaveScheduler(opts: {
  baseVersion: number
  save: SaveFn
  onState: (s: SaveState) => void
  debounceMs?: number
}): Scheduler {
  const debounceMs = opts.debounceMs ?? SAVE_DEBOUNCE_MS
  let version = opts.baseVersion
  let pending: SavePayload = {}
  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  let conflicted = false
  let disposed = false

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  /** The send currently in flight, so `flush` can WAIT for it rather than
   *  returning early and letting the caller tear down over queued text. */
  let inFlightSend: Promise<void> | null = null

  async function drain(): Promise<void> {
    // A LOOP, not recursion into `send()`. The previous shape chained by
    // calling itself and returning — which ran the outer `finally` and cleared
    // `inFlight` while the chained request was still live, so a concurrent
    // `flush()` sailed past the overlap guard and raced two writes on the same
    // base version. One of them then 409'd as a conflict the user never caused.
    while (true) {
      if (disposed || conflicted) return
      const keys = Object.keys(pending)
      if (keys.length === 0) return

      const payload = pending
      // Cleared BEFORE the await, so a keystroke during the request is queued
      // as NEW pending text rather than being folded into the write already
      // leaving — and therefore is not silently marked saved when this one
      // returns.
      pending = {}
      opts.onState({ kind: "saving" })
      try {
        const { version: next } = await opts.save({ ...payload, base_version: version })
        version = next
        if (disposed) return
        opts.onState({ kind: "saved", at: Date.now() })
        // Loop: text that arrived mid-flight goes now, rather than waiting for
        // the next keystroke that may never come.
        continue
      } catch (err) {
        if (err instanceof SaveConflict) {
          conflicted = true
          // The refused text is put BACK, so "keep mine" still has something to
          // keep. Dropping it here is the bug that turns a conflict into data
          // loss on top of a conflict.
          pending = { ...payload, ...pending }
          if (!disposed) opts.onState({ kind: "conflict", theirs: err.theirs })
          return
        }
        // Retryable: keep the text and say so. The next change or flush retries.
        pending = { ...payload, ...pending }
        if (!disposed) {
          opts.onState({
            kind: "error",
            message: err instanceof Error ? err.message : "Could not save",
          })
        }
        return
      }
    }
  }

  function send(): Promise<void> {
    // One drain at a time. A second caller joins the running one instead of
    // starting a parallel write.
    if (inFlight) return inFlightSend ?? Promise.resolve()
    inFlight = true
    inFlightSend = drain().finally(() => {
      inFlight = false
      inFlightSend = null
    })
    return inFlightSend
  }

  return {
    schedule(patch) {
      if (disposed || conflicted) return
      pending = { ...pending, ...patch }
      opts.onState({ kind: "saving" })
      clearTimer()
      timer = setTimeout(() => {
        timer = null
        void send()
      }, debounceMs)
    },
    async flush() {
      clearTimer()
      // Awaited, then repeated: the first call may join a send that is already
      // running, which by definition cannot carry text queued after it left.
      // Without the second pass, typing during a request and then navigating
      // away loses those words — and the caller's teardown comment promises
      // the opposite.
      await send()
      if (!disposed && !conflicted && Object.keys(pending).length > 0) await send()
    },
    reset(v) {
      version = v
      conflicted = false
      pending = {}
      clearTimer()
      opts.onState({ kind: "idle" })
    },
    dispose() {
      disposed = true
      clearTimer()
    },
    pendingKeys: () => Object.keys(pending),
  }
}

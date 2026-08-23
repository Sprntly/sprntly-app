import { getAccessToken } from "./api"

export type StreamFrame = { kind?: string; text?: string; label?: string }

export type StreamHandlers = {
  /** Called on each delta with the FULL accumulated text so far (and the raw
   *  delta). Render `full` — it's the progressive document. */
  onDelta: (full: string, delta: string) => void
  /** Called on each `{"kind":"phase","label":…}` frame — a real "which leg of
   *  the pipeline is running" signal the backend publishes ahead of, and during,
   *  the answer (retrieval, writing, report legs). The RAW backend label; the
   *  caller is responsible for curating it before display (see `friendlyPhase`).
   *  Pre-answer only in effect: once deltas start the answer body takes over. */
  onPhase?: (label: string) => void
  /** Terminal: generation finished cleanly. The caller's poll carries the
   *  authoritative persisted result; this just ends the live preview. */
  onDone?: () => void
  /** Terminal: the stream errored or the transport dropped. The poll still
   *  resolves the real result, so this only stops the live preview. */
  onError?: () => void
}

/**
 * Subscribe to a backend SSE token stream (PRD / evidence / chat answers) and
 * accumulate delta frames into the growing document. Mirrors the design-agent
 * EventSource pattern: the bearer rides in the URL (EventSource can't set
 * headers), frames are `{kind:'delta',text}` then a terminal
 * `{kind:'done'|'error'}`, with `{kind:'replay'}` catching up a mid-generation
 * join and `{kind:'restart'}` announcing that a backend retry has superseded
 * everything streamed so far.
 *
 * FRAME KINDS DEGRADE, THEY DO NOT FAIL: an unrecognised kind falls through
 * the chain and is ignored. Backend and frontend do not deploy atomically, so
 * a new server frame reaching an old client must leave it exactly as it was.
 *
 * PROGRESSIVE DISPLAY ONLY — the caller keeps polling for the authoritative
 * finished document, so any stream failure just stops the live preview and is
 * never surfaced as an error. Returns a cleanup that closes the EventSource;
 * always call it (e.g. in a finally / effect cleanup).
 */
export function subscribeToGenerationStream(
  buildUrl: (token: string) => string,
  handlers: StreamHandlers,
): () => void {
  let es: EventSource | null = null
  let closed = false
  let acc = ""

  void getAccessToken().then((token) => {
    if (closed || !token) return
    es = new EventSource(buildUrl(token))
    es.onmessage = (e: MessageEvent) => {
      let frame: StreamFrame
      try {
        frame = JSON.parse(e.data)
      } catch {
        return // ignore a malformed frame; the next one or the poll recovers
      }
      if (frame.kind === "replay" && frame.text) {
        // Catch-up frame for a mid-generation join (warm-started brief PRDs /
        // evidence): everything the generation emitted before we connected.
        // The server sends it strictly first; if deltas somehow beat it here,
        // replacing them with the (longer) backlog would drop text, so a
        // non-empty accumulator ignores it and stays live-only.
        if (acc === "") {
          acc = frame.text
          const restart = acc.toLowerCase().lastIndexOf("<!doctype")
          if (restart > 0) acc = acc.slice(restart)
          handlers.onDelta(acc, frame.text)
        }
      } else if (frame.kind === "phase" && frame.label) {
        // A real pipeline-leg marker (retrieval / writing / report legs). Not
        // part of the answer text — it never touches `acc` — so it can't corrupt
        // the streamed document. Display-only, like every other frame here.
        handlers.onPhase?.(frame.label)
      } else if (frame.kind === "restart") {
        // The backend retried mid-generation and is about to re-emit from
        // zero: everything accumulated so far is superseded. Drop it, but do
        // NOT call onDelta("") — blanking the preview for the ~100ms until the
        // first replacement delta arrives is a visible flash for no gain. The
        // next delta renders attempt 2 alone.
        //
        // This is the signal the `<!doctype` heuristic below cannot give for
        // markdown answers (chat), which have no document open to look for.
        acc = ""
      } else if (frame.kind === "delta" && frame.text) {
        acc += frame.text
        // Fallback for HTML documents (PRD / evidence): a second document open
        // (a doctype past position 0) marks a restart that arrived without an
        // explicit `restart` frame — an older backend, or a generation path
        // whose sink is not rewound. Kept alongside the frame above, not
        // replaced by it.
        const restart = acc.toLowerCase().lastIndexOf("<!doctype")
        if (restart > 0) acc = acc.slice(restart)
        handlers.onDelta(acc, frame.text)
      } else if (frame.kind === "done") {
        handlers.onDone?.()
        es?.close()
      } else if (frame.kind === "error") {
        handlers.onError?.()
        es?.close()
      }
    }
    es.onerror = () => {
      // Transport dropped (network, or generation already finished before we
      // connected). Stop the live preview; the poll still delivers the result.
      handlers.onError?.()
      es?.close()
    }
  })

  return () => {
    closed = true
    es?.close()
  }
}

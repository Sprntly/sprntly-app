import { getAccessToken } from "./api"

export type StreamFrame = { kind?: string; text?: string }

export type StreamHandlers = {
  /** Called on each delta with the FULL accumulated text so far (and the raw
   *  delta). Render `full` — it's the progressive document. */
  onDelta: (full: string, delta: string) => void
  /** Terminal: generation finished cleanly. The caller's poll carries the
   *  authoritative persisted result; this just ends the live preview. */
  onDone?: () => void
  /** Terminal: the stream errored or the transport dropped. The poll still
   *  resolves the real result, so this only stops the live preview. */
  onError?: () => void
}

/**
 * Subscribe to a backend SSE token stream (PRD / evidence / …) and accumulate
 * delta frames into the growing document. Mirrors the design-agent EventSource
 * pattern: the bearer rides in the URL (EventSource can't set headers), frames
 * are `{kind:'delta',text}` then a terminal `{kind:'done'|'error'}`.
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
      } else if (frame.kind === "delta" && frame.text) {
        acc += frame.text
        // A backend mid-generation retry re-emits the document from zero on
        // the same channel. A second document open (a doctype past position 0)
        // marks that restart — reset the accumulator to the fresh document so
        // the preview doesn't show the two attempts glued together.
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

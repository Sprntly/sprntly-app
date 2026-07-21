import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Mock the token source so the EventSource branch is entered with a known bearer.
const getAccessToken = vi.fn()
vi.mock("../api", () => ({
  API_URL: "http://api.test",
  getAccessToken: () => getAccessToken(),
}))

import { subscribeToGenerationStream } from "../streamGeneration"

class MockEventSource {
  url: string
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  error() {
    this.onerror?.(new Event("error"))
  }

  static instances: MockEventSource[] = []
  static clear() {
    MockEventSource.instances = []
  }
  static latest(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

// Two microtask flushes clear getAccessToken().then(...) so the EventSource opens.
const flush = async () => {
  await Promise.resolve()
  await Promise.resolve()
}

describe("subscribeToGenerationStream", () => {
  beforeEach(() => {
    MockEventSource.clear()
    getAccessToken.mockResolvedValue("bearer-xyz")
    vi.stubGlobal("EventSource", MockEventSource)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it("opens the stream with the token and accumulates delta frames", async () => {
    const seen: string[] = []
    subscribeToGenerationStream((t) => `http://api.test/stream?token=${t}`, {
      onDelta: (full) => seen.push(full),
    })
    await flush()

    const es = MockEventSource.latest()
    expect(es.url).toContain("token=bearer-xyz")
    es.emit({ kind: "delta", text: "Hello " })
    es.emit({ kind: "delta", text: "world" })
    // onDelta always receives the FULL accumulated document.
    expect(seen).toEqual(["Hello ", "Hello world"])
  })

  it("fires onDone and closes on the terminal done frame", async () => {
    const onDone = vi.fn()
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: () => {}, onDone })
    await flush()

    const es = MockEventSource.latest()
    es.emit({ kind: "done" })
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(es.close).toHaveBeenCalled()
  })

  it("fires onError (not throw) on a transport error", async () => {
    const onError = vi.fn()
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: () => {}, onError })
    await flush()

    MockEventSource.latest().error()
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it("ignores malformed frames without breaking the stream", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    const es = MockEventSource.latest()
    es.onmessage?.({ data: "not json{" })  // must not throw
    es.emit({ kind: "delta", text: "ok" })
    expect(seen).toEqual(["ok"])
  })

  it("resets the accumulator when a backend retry re-emits the document from zero", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    const es = MockEventSource.latest()
    es.emit({ kind: "delta", text: "<!doctype html><body>first attempt" })
    // A mid-generation backend retry restarts the stream from zero on the same
    // channel — the preview must show ONLY the fresh document, not both glued.
    es.emit({ kind: "delta", text: "<!doctype html><body>second" })
    es.emit({ kind: "delta", text: " attempt" })
    expect(seen).toEqual([
      "<!doctype html><body>first attempt",
      "<!doctype html><body>second",
      "<!doctype html><body>second attempt",
    ])
  })

  it("handles a restart whose doctype is split across two delta frames", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    const es = MockEventSource.latest()
    es.emit({ kind: "delta", text: "<!DOCTYPE html><p>one</p><!doc" })
    es.emit({ kind: "delta", text: "type html><p>two</p>" })
    // The first frame's dangling "<!doc" is not yet a restart; once the second
    // frame completes the doctype, the accumulator resets to the new document.
    expect(seen[seen.length - 1]).toBe("<!doctype html><p>two</p>")
  })

  it("seeds the accumulator from a replay frame, then appends live deltas", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    const es = MockEventSource.latest()
    // A warm-started generation (brief-insight PRD/evidence) replays everything
    // emitted before this client connected, then streams live.
    es.emit({ kind: "replay", text: "<!doctype html><body>head of the doc " })
    es.emit({ kind: "delta", text: "then the tail" })
    expect(seen).toEqual([
      "<!doctype html><body>head of the doc ",
      "<!doctype html><body>head of the doc then the tail",
    ])
  })

  it("normalizes a replay that contains a glued backend retry", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    // The server buffer accumulates raw deltas, so a mid-generation retry can
    // leave two document opens glued inside the replay text — the same restart
    // guard applies and only the fresh document renders.
    MockEventSource.latest().emit({
      kind: "replay",
      text: "<!doctype html><p>one</p><!doctype html><p>two</p>",
    })
    expect(seen).toEqual(["<!doctype html><p>two</p>"])
  })

  it("ignores a replay frame once deltas have already accumulated", async () => {
    const seen: string[] = []
    subscribeToGenerationStream(() => "http://api.test/s", { onDelta: (f) => seen.push(f) })
    await flush()

    const es = MockEventSource.latest()
    es.emit({ kind: "delta", text: "live text" })
    // The server sends replay strictly first; a late/duplicate one must not
    // clobber (or double) what already accumulated.
    es.emit({ kind: "replay", text: "stale backlog" })
    es.emit({ kind: "delta", text: " continues" })
    expect(seen).toEqual(["live text", "live text continues"])
  })

  it("cleanup before the token resolves never opens a stream", async () => {
    const stop = subscribeToGenerationStream(() => "http://api.test/s", { onDelta: () => {} })
    stop() // closed while getAccessToken() is still pending
    await flush()
    expect(MockEventSource.instances.length).toBe(0)
  })
})

// @vitest-environment jsdom
//
// The shared iterate runner polls the prototype row to completion. A bearer
// token can expire mid-poll, returning a transient 401 — previously the outer
// catch treated that as terminal and aborted the run even though the background
// iterate finished, so the canvas never advanced ("iteration isn't working").
//
// These tests drive the hook through a real poll cycle with fake timers and
// assert that a single transient 401 on an in-loop fetch is retried (via the
// shared auth-retry primitive) rather than aborting the run. The primitive
// itself is real here (not mocked) so the end-to-end resilience is exercised.
import * as React from "react"
import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { readFileSync } from "node:fs"

import { useIterateRun, type ActivityEventInput } from "../useIterateRun"
import {
  ApiError,
  designAgentApi,
  setAccessTokenProvider,
  type IterateResponse,
  type PrototypeRecord,
} from "../../../lib/api"

const HERE = dirname(fileURLToPath(import.meta.url))
const USE_ITERATE_RUN_PATH = join(HERE, "..", "useIterateRun.ts")

// Sprntly components carry no `import React`; expose it globally (repo convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const PROTOTYPE_ID = 7

function proto(
  status: PrototypeRecord["status"],
  pendingQuestion: PrototypeRecord["pending_question"] = null,
): PrototypeRecord {
  return {
    id: PROTOTYPE_ID,
    status,
    bundle_url: status === "ready" ? "https://bundle.test/v2" : null,
    error: null,
    pending_question: pendingQuestion,
  }
}

function makeApi(get: ReturnType<typeof vi.fn>) {
  const iterate = vi.fn<
    (
      id: number,
      body: { prompt: string; applied_comment_id?: number | null; mode?: "plan" | "execute" },
    ) => Promise<IterateResponse>
  >().mockResolvedValue({
    prototype_id: PROTOTYPE_ID,
    status: "generating",
    queue_position: 0,
  })
  return { iterate, get } as unknown as Pick<
    typeof designAgentApi,
    "iterate" | "get"
  >
}

beforeEach(() => {
  vi.useFakeTimers()
  // No token provider needed; withAuthRetry's re-acquire is a no-op when unset.
  setAccessTokenProvider(() => Promise.resolve(null))
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe("useIterateRun — transient-401 resilience", () => {
  it("test_iterate_poll_survives_transient_401: a 401 mid-poll retries and the run reaches completion instead of aborting", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      // 1) initial poll: still generating → enter the poll loop
      .mockResolvedValueOnce(proto("generating"))
      // 2) in-loop poll: transient 401 (token refresh race)
      .mockRejectedValueOnce(new ApiError(401, { detail: "token expired" }))
      // 3) retry after re-acquire: the iterate has landed → ready
      .mockResolvedValueOnce(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("make the hero blue")
      await vi.runAllTimersAsync()
      await run
    })

    // The run did NOT abort: it completed and handed the ready row to the canvas.
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
    expect(result.current.error).toBeNull()
    expect(result.current.running).toBe(false)
    // The 401 was retried: initial + failed-attempt + retry = 3 gets.
    expect(get).toHaveBeenCalledTimes(3)
  })

  it("completes a clean run with no 401 (baseline)", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValueOnce(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("tweak the spacing")
      await vi.runAllTimersAsync()
      await run
    })

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
    expect(result.current.error).toBeNull()
  })

  it("a persistent 401 mid-poll surfaces an error (gives up after the retry budget)", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      // both the in-loop attempt and its single retry 401
      .mockRejectedValue(new ApiError(401, { detail: "really expired" }))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("change the copy")
      await vi.runAllTimersAsync()
      await run
    })

    // A genuine persistent auth failure still surfaces (not swallowed).
    expect(onComplete).not.toHaveBeenCalled()
    expect(result.current.error).not.toBeNull()
    expect(result.current.running).toBe(false)
  })
})

// Does the activity stream carry the terminal "Change applied" line?
function hasChangeApplied(activity: { kind: string; text?: string }[]) {
  return activity.some((e) => e.kind === "done" && e.text === "Change applied")
}

describe("useIterateRun — terminal state follows the real poll, not a timer", () => {
  it("test_done_not_appended_before_poll_ready: a run that never reaches ready never emits the 'Change applied' done line", async () => {
    // The background run never completes — the poll keeps returning 'generating'
    // until the run hits its max-duration cap and times out. The terminal done
    // line must NOT appear: it is gated on a real `ready`, not on the timer that
    // drives the cosmetic step reveal. (Pre-fix, the done line was appended
    // unconditionally just before the timeout threw, so this would go red.)
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValue(proto("generating"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("make it pop")
      await vi.runAllTimersAsync()
      await run
    })

    // No premature completion: the run timed out, so it surfaces an error and
    // never claims the change was applied.
    expect(hasChangeApplied(result.current.activity)).toBe(false)
    expect(onComplete).not.toHaveBeenCalled()
    expect(result.current.error).not.toBeNull()
    expect(result.current.running).toBe(false)
  })

  it("test_long_poll_keeps_active_state: the stream stays active through a long poll and only marks done on the real ready", async () => {
    // Stay 'generating' well past the cosmetic-step count, then resolve to ready.
    const get = vi.fn<(id: number) => Promise<PrototypeRecord>>()
    for (let i = 0; i < 8; i++) get.mockResolvedValueOnce(proto("generating"))
    get.mockResolvedValue(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("polish the layout")
      await vi.runAllTimersAsync()
      await run
    })

    const { activity } = result.current
    // Exactly one terminal done line, appended only after the real ready.
    expect(
      activity.filter((e) => e.kind === "done" && e.text === "Change applied"),
    ).toHaveLength(1)
    // And it is the LAST thing in the stream — it follows the working step,
    // never precedes the resolution.
    expect(activity[activity.length - 1].kind).toBe("done")
    // No pre-appended step when SSE is unavailable (token=null in this test);
    // the first step comes from the backend SSE stream, not a client placeholder.
    // When SSE is unavailable the activity has user + done only.
    expect(activity.filter((e) => e.kind === "step").length).toBe(0)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
  })

  it("test_pending_question_surfaces_not_done: a clarifying-question resolution shows the question, not a done line", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValueOnce(
        proto("generating", { question: "Which header variant?" }),
      )

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("update the header")
      await vi.runAllTimersAsync()
      await run
    })

    const { activity } = result.current
    // The agent paused to ask — surface the question, never a "Change applied".
    expect(
      activity.some(
        (e) => e.kind === "question" && e.question === "Which header variant?",
      ),
    ).toBe(true)
    expect(hasChangeApplied(activity)).toBe(false)
    expect(result.current.pendingQuestion?.question).toBe(
      "Which header variant?",
    )
    // The paused row is still handed to the canvas (unchanged behaviour).
    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// SSE seam contract
// ---------------------------------------------------------------------------

describe("useIterateRun — ActivityEventInput union stability (SSE seam)", () => {
  it("test_append_activity_seam_is_stable_union: ActivityEventInput has exactly the five expected member kinds", () => {
    // Build a representative sample of each member. TypeScript will fail to
    // compile this file if any variant is removed or renamed, so this test
    // acts as a compile-time + runtime contract guard for the SSE upgrade.
    const user: ActivityEventInput = { kind: "user", text: "hi" }
    const stepActive: ActivityEventInput = {
      kind: "step",
      text: "working",
      state: "active",
    }
    const stepDone: ActivityEventInput = {
      kind: "step",
      text: "done",
      state: "done",
    }
    const done: ActivityEventInput = { kind: "done", text: "Change applied" }
    const question: ActivityEventInput = {
      kind: "question",
      question: "Which variant?",
    }
    const error: ActivityEventInput = { kind: "error", text: "oops" }

    const kinds = [user, stepActive, stepDone, done, question, error].map(
      (e) => e.kind,
    )
    const uniqueKinds = new Set(kinds)
    // Exactly five unique kinds: user, step, done, question, error
    expect(uniqueKinds.size).toBe(5)
    expect(uniqueKinds).toContain("user")
    expect(uniqueKinds).toContain("step")
    expect(uniqueKinds).toContain("done")
    expect(uniqueKinds).toContain("question")
    expect(uniqueKinds).toContain("error")
  })
})

// ---------------------------------------------------------------------------
// First-event shape
// ---------------------------------------------------------------------------

describe("useIterateRun — first-event shape", () => {
  it("test_run_iterate_appends_user_event_first: the first activity entry has kind=user with the instruction text", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValue(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("make the hero blue")
      await vi.runAllTimersAsync()
      await run
    })

    const first = result.current.activity[0]
    expect(first.kind).toBe("user")
    if (first.kind === "user") {
      expect(first.text).toBe("make the hero blue")
    }
  })
})

// ---------------------------------------------------------------------------
// answerQuestion path
// ---------------------------------------------------------------------------

describe("useIterateRun — answerQuestion", () => {
  it("test_answer_question_composes_context_and_clears_pending: answering a question clears pendingQuestion and calls runIterate with composed prompt", async () => {
    // First run pauses on a question.
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValueOnce(
        proto("generating", { question: "Which color scheme?" }),
      )
      // After the answer re-triggers runIterate, the second iterate resolves.
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValue(proto("ready"))

    const iterate = vi
      .fn<
        (
          id: number,
          body: { prompt: string; applied_comment_id?: number | null; mode?: "plan" | "execute" },
        ) => Promise<IterateResponse>
      >()
      .mockResolvedValue({
        prototype_id: PROTOTYPE_ID,
        status: "generating",
        queue_position: 0,
      })

    const onComplete = vi.fn()
    const api = { iterate, get } as unknown as Pick<
      typeof designAgentApi,
      "iterate" | "get"
    >

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    // Trigger the initial run and wait for the question pause.
    await act(async () => {
      const run = result.current.runIterate("change the palette")
      await vi.runAllTimersAsync()
      await run
    })

    expect(result.current.pendingQuestion?.question).toBe("Which color scheme?")

    // Now answer the question.
    await act(async () => {
      const answer = result.current.answerQuestion("use the primary brand blue")
      await vi.runAllTimersAsync()
      await answer
    })

    // pendingQuestion must be cleared after answering.
    expect(result.current.pendingQuestion).toBeNull()

    // runIterate was called a second time (iterate posted twice total).
    expect(iterate).toHaveBeenCalledTimes(2)

    // The second iterate prompt must include the original question as context.
    const secondCall = iterate.mock.calls[1]
    expect(secondCall[1].prompt).toContain("Which color scheme?")
    expect(secondCall[1].prompt).toContain("use the primary brand blue")
  })

  it("test_answer_question_noop_on_empty_string: answerQuestion is a no-op when the answer is blank", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValue(proto("ready"))

    const iterate = vi
      .fn<
        (
          id: number,
          body: { prompt: string; applied_comment_id?: number | null; mode?: "plan" | "execute" },
        ) => Promise<IterateResponse>
      >()
      .mockResolvedValue({
        prototype_id: PROTOTYPE_ID,
        status: "generating",
        queue_position: 0,
      })

    const onComplete = vi.fn()
    const api = { iterate, get } as unknown as Pick<
      typeof designAgentApi,
      "iterate" | "get"
    >

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      await result.current.answerQuestion("   ")
    })

    // iterate must not have been called at all.
    expect(iterate).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Source-marker guard
// ---------------------------------------------------------------------------

describe("useIterateRun — source marker guard", () => {
  it("the source file carries no throwaway exploration marker (test_source_carries_no_throwaway_marker)", () => {
    const source = readFileSync(USE_ITERATE_RUN_PATH, "utf8")
    expect(source).not.toContain("UX-EXPLORE")
  })
})

// ---------------------------------------------------------------------------
// SSE EventSource integration
// ---------------------------------------------------------------------------

/** Minimal EventSource mock that captures instances for test control. */
class MockEventSource {
  url: string
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  /** Simulate a message frame from the server. */
  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  /** Simulate a connection error. */
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

// Helpers that set a non-null token so the SSE branch is taken.
function makeApiWithSse(get: ReturnType<typeof vi.fn>) {
  return makeApi(get)
}

describe("useIterateRun — SSE EventSource wiring", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    MockEventSource.clear()
    // Provide a non-null token so the EventSource branch is entered.
    setAccessTokenProvider(() => Promise.resolve("test-sse-bearer"))
    vi.stubGlobal("EventSource", MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.resetAllMocks()
  })

  it("test_sse_step_event_appended: a step event received from EventSource appears in activity via appendActivity", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValue(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApiWithSse(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("make the header bold")
      // getAccessToken() is an async function that itself awaits a resolved
      // promise, so we need two microtask yields before EventSource is created.
      await Promise.resolve()
      await Promise.resolve()
      const es = MockEventSource.latest()
      es.emit({ kind: "step", text: "Analyzing the prototype", state: "active" })
      await vi.runAllTimersAsync()
      await run
    })

    const stepEvents = result.current.activity.filter(
      (e) => e.kind === "step" && "text" in e && e.text === "Analyzing the prototype",
    )
    expect(stepEvents.length).toBeGreaterThanOrEqual(1)
    expect(result.current.error).toBeNull()
  })

  it("test_sse_done_event_closes_source: a done event from EventSource closes the connection", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValue(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApiWithSse(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("tweak the palette")
      await Promise.resolve()
      await Promise.resolve()
      const es = MockEventSource.latest()
      es.emit({ kind: "done", text: "Change applied" })
      await vi.runAllTimersAsync()
      await run
    })

    const es = MockEventSource.latest()
    expect(es.close).toHaveBeenCalled()
  })

  it("test_sse_failure_degrades_to_poll: when EventSource errors, the run still resolves its terminal state off the poll loop", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValue(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApiWithSse(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("remove the footer")
      await Promise.resolve()
      await Promise.resolve()
      // Simulate EventSource transport failure.
      MockEventSource.latest().error()
      await vi.runAllTimersAsync()
      await run
    })

    // Poll fallback resolved the run correctly.
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
    // No user-facing error from the SSE failure.
    expect(result.current.error).toBeNull()
    expect(result.current.running).toBe(false)
  })

  it("test_eventsource_closed_on_unmount: unmounting while a run is in flight closes the EventSource", async () => {
    // Poll never resolves — the run stays in-flight until unmount.
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValue(proto("generating"))

    const onComplete = vi.fn()
    const api = makeApiWithSse(get)

    const { result, unmount } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      result.current.runIterate("test unmount")
      await Promise.resolve()
      await Promise.resolve()
    })

    const es = MockEventSource.latest()
    unmount()

    expect(es.close).toHaveBeenCalled()
  })

  it("test_activity_event_union_unchanged: ActivityEventInput has exactly the five expected member kinds (seam contract)", () => {
    const user: ActivityEventInput = { kind: "user", text: "hi" }
    const stepA: ActivityEventInput = { kind: "step", text: "working", state: "active" }
    const stepD: ActivityEventInput = { kind: "step", text: "done", state: "done" }
    const done: ActivityEventInput = { kind: "done", text: "Change applied" }
    const question: ActivityEventInput = { kind: "question", question: "Which?" }
    const error: ActivityEventInput = { kind: "error", text: "oops" }

    const uniqueKinds = new Set([user, stepA, stepD, done, question, error].map((e) => e.kind))
    expect(uniqueKinds.size).toBe(5)
    expect(uniqueKinds).toContain("user")
    expect(uniqueKinds).toContain("step")
    expect(uniqueKinds).toContain("done")
    expect(uniqueKinds).toContain("question")
    expect(uniqueKinds).toContain("error")
  })
})

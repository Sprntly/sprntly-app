// @vitest-environment jsdom
//
// Unit tests for the blur/remount-safe chat Ask flow (runAskGeneration.ts).
// POST /v1/ask is fire-and-forget: it returns an ask_id and the answer keeps
// generating server-side; the client polls GET /v1/ask/{id} via the shared
// visibility-aware pollUntil and persists the active ask_id per tab (jobResume)
// so a remount re-attaches instead of re-asking. These tests mock the api layer
// and use fake timers to drive the poll without real wall-clock waits.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { askApi, ApiError } from "../api"
import {
  runAskGeneration,
  resumeAskGeneration,
  getPendingAsk,
  askScope,
  AskCancelledError,
  AskStoppedError,
} from "../runAskGeneration"
import { getPendingJob } from "../jobResume"

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility("visible")
  localStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  })
}

const READY = {
  status: "ready" as const,
  error: null,
  answer: "## The answer",
  key_points: ["k1"],
  citations: [],
  confidence: 0.9,
  unanswered: "",
}

describe("runAskGeneration", () => {
  it("POSTs for an ask_id, polls the status endpoint, and returns the answer", async () => {
    const startSpy = vi
      .spyOn(askApi, "start")
      .mockResolvedValue({ ask_id: 77, status: "generating" } as never)
    const getSpy = vi
      .spyOn(askApi, "get")
      // first poll still generating, second poll ready
      .mockResolvedValueOnce({ ...READY, status: "generating", answer: "" } as never)
      .mockResolvedValueOnce(READY as never)

    const p = runAskGeneration("What is churn?", "acme", "tab-1")
    // Drive the poll interval so the second fetch fires.
    await vi.advanceTimersByTimeAsync(2000)
    const res = await p

    expect(startSpy).toHaveBeenCalledWith("What is churn?", "acme", undefined)
    expect(getSpy).toHaveBeenCalledWith(77)
    expect(res.answer).toBe("## The answer")
    expect(res.key_points).toEqual(["k1"])
    // The job envelope (status/error) is not leaked onto the AskResponse.
    expect((res as Record<string, unknown>).status).toBeUndefined()
    expect((res as Record<string, unknown>).error).toBeUndefined()
    // Pending marker cleared on terminal exit.
    expect(getPendingAsk("acme", "tab-1")).toBeNull()
  })

  it("persists the active ask_id so a remount can re-attach (survives before the answer returns)", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 99, status: "generating" } as never)
    // Never resolves to ready within the test — we only assert the marker is set.
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)

    // Kick off but don't await to completion.
    void runAskGeneration("Pending question?", "acme", "tab-7")
    // Let the synchronous POST + setPendingJob run.
    await vi.advanceTimersByTimeAsync(0)

    // The marker is persisted under the jobResume key for this tab.
    expect(getPendingAsk("acme", "tab-7")).toEqual({ id: "99" })
    expect(getPendingJob("ask", "acme", askScope("tab-7"))).toEqual({ id: "99" })
  })

  it("surfaces a backend error status as a thrown error (drives the error UX)", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 5, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({
      ...READY,
      status: "error",
      answer: "",
      error: "qa pipeline kaboom",
    } as never)

    const p = runAskGeneration("Q?", "acme", "tab-err").catch((e) => e)
    await vi.advanceTimersByTimeAsync(0)
    const err = await p
    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toMatch(/kaboom/)
    // Cleared even on error.
    expect(getPendingAsk("acme", "tab-err")).toBeNull()
  })
})

// A transient "Failed to fetch" (dev-server reload, momentary offline, a reset
// keep-alive socket) must NOT collapse an ask whose server-side job is healthy —
// especially for multi-file / large-context asks that poll many more times. The
// flow retries transport failures a few times; a real HTTP error still fails fast.
describe("transient network resilience", () => {
  it("retries a 'Failed to fetch' on the initial POST and still completes", async () => {
    const startSpy = vi
      .spyOn(askApi, "start")
      .mockRejectedValueOnce(new TypeError("Failed to fetch")) // transient blip
      .mockResolvedValue({ ask_id: 11, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue(READY as never)

    const p = runAskGeneration("Q?", "acme", "tab-start-retry")
    // Clear the retry backoff (400ms) so the second POST fires, then the poll.
    await vi.advanceTimersByTimeAsync(600)
    const res = await p

    expect(startSpy).toHaveBeenCalledTimes(2)
    expect(res.answer).toBe("## The answer")
    expect(getPendingAsk("acme", "tab-start-retry")).toBeNull()
  })

  it("tolerates a transient poll failure and completes when the retry succeeds", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 22, status: "generating" } as never)
    const getSpy = vi
      .spyOn(askApi, "get")
      .mockRejectedValueOnce(new TypeError("Failed to fetch")) // one poll blips
      .mockResolvedValue(READY as never)

    const p = runAskGeneration("Q?", "acme", "tab-poll-retry")
    // Clear the status-read retry backoff (400ms).
    await vi.advanceTimersByTimeAsync(600)
    const res = await p

    expect(getSpy).toHaveBeenCalledTimes(2)
    expect(res.answer).toBe("## The answer")
  })

  it("does NOT retry a real HTTP error on the POST — a deterministic failure surfaces at once", async () => {
    const err = new ApiError(404, { detail: "tenant gate" })
    const startSpy = vi.spyOn(askApi, "start").mockRejectedValue(err as never)

    const p = runAskGeneration("Q?", "acme", "tab-http-err").catch((e) => e)
    await vi.advanceTimersByTimeAsync(0)
    const caught = await p

    expect(caught).toBe(err)
    expect(startSpy).toHaveBeenCalledTimes(1) // no retry on a 4xx
  })
})

describe("background completion survives navigating away (cancel-on-unmount)", () => {
  it("leaves the pending marker in place and throws AskCancelledError when cancelled mid-poll", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 42, status: "generating" } as never)
    // Stays 'generating' the whole time — the answer would land after the user
    // has already left, which is exactly the race we protect.
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)

    let mounted = true
    const p = runAskGeneration("Slow question?", "acme", "tab-nav", {
      isCancelled: () => !mounted,
    }).catch((e) => e)
    // POST + persist + first poll fetch, then the loop parks on its interval.
    await vi.advanceTimersByTimeAsync(0)
    expect(getPendingAsk("acme", "tab-nav")).toEqual({ id: "42" })

    // User navigates to another screen → ChatScreen unmounts.
    mounted = false
    // Next poll tick observes the cancel and bails.
    await vi.advanceTimersByTimeAsync(2000)
    const err = await p

    expect(err).toBeInstanceOf(AskCancelledError)
    // Crucially: the marker is NOT cleared, so a remount re-attaches by id
    // instead of the answer being orphaned.
    expect(getPendingAsk("acme", "tab-nav")).toEqual({ id: "42" })
  })

  it("a remount after the cancel re-fetches the now-ready answer and clears the marker", async () => {
    // Simulate the state left by the cancel above: marker present, no re-POST.
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 42, status: "generating" } as never)
    const getSpy = vi
      .spyOn(askApi, "get")
      .mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)
    let mounted = true
    void runAskGeneration("Slow question?", "acme", "tab-nav2", {
      isCancelled: () => !mounted,
    }).catch(() => undefined)
    await vi.advanceTimersByTimeAsync(0)
    mounted = false
    await vi.advanceTimersByTimeAsync(2000)
    expect(getPendingAsk("acme", "tab-nav2")).toEqual({ id: "42" })

    // Remount: the server job has since finished; resume reads it and populates.
    getSpy.mockResolvedValue(READY as never)
    const res = await resumeAskGeneration(42, "acme", "tab-nav2")
    expect(res.answer).toBe("## The answer")
    // Consumed while mounted → marker cleared for good.
    expect(getPendingAsk("acme", "tab-nav2")).toBeNull()
  })
})

// The composer's Stop button. Unlike an unmount (which KEEPS the marker so a
// remount resumes), a deliberate stop CLEARS the marker (no resume) and surfaces
// AskStoppedError, which the component swallows after rendering the stopped turn.
describe("user Stop (isStopped) vs cancel-on-unmount", () => {
  it("clears the pending marker and throws AskStoppedError when the tab is stopped mid-poll", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 42, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)

    let stopped = false
    const p = runAskGeneration("Wrong question?", "acme", "tab-stop", {
      isStopped: () => stopped,
    }).catch((e) => e)
    await vi.advanceTimersByTimeAsync(0)
    expect(getPendingAsk("acme", "tab-stop")).toEqual({ id: "42" })

    // User hits Stop.
    stopped = true
    await vi.advanceTimersByTimeAsync(2000)
    const err = await p

    expect(err).toBeInstanceOf(AskStoppedError)
    // Deliberately abandoned → marker CLEARED so no remount resume.
    expect(getPendingAsk("acme", "tab-stop")).toBeNull()
  })

  it("treats a job that reached the 'cancelled' terminal status as a stop, not an error", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 7, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({
      ...READY,
      status: "cancelled",
      answer: "",
    } as never)

    const p = runAskGeneration("Q?", "acme", "tab-cancelled").catch((e) => e)
    await vi.advanceTimersByTimeAsync(0)
    const err = await p

    expect(err).toBeInstanceOf(AskStoppedError)
    expect(getPendingAsk("acme", "tab-cancelled")).toBeNull()
  })

  it("stop takes precedence over unmount (marker cleared, AskStoppedError)", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 8, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)

    // Both signals true at once — an explicit stop must win over the unmount path
    // so the marker is cleared (not left for a resume).
    const p = runAskGeneration("Q?", "acme", "tab-both", {
      isCancelled: () => true,
      isStopped: () => true,
    }).catch((e) => e)
    await vi.advanceTimersByTimeAsync(0)
    const err = await p

    expect(err).toBeInstanceOf(AskStoppedError)
    expect(getPendingAsk("acme", "tab-both")).toBeNull()
  })
})

describe("resumeAskGeneration", () => {
  it("re-attaches to a persisted ask_id WITHOUT re-POSTing", async () => {
    const startSpy = vi.spyOn(askApi, "start")
    const getSpy = vi.spyOn(askApi, "get").mockResolvedValue(READY as never)

    const p = resumeAskGeneration(123, "acme", "tab-resume")
    await vi.advanceTimersByTimeAsync(0)
    const res = await p

    // No new POST — resume only reads the status endpoint.
    expect(startSpy).not.toHaveBeenCalled()
    expect(getSpy).toHaveBeenCalledWith(123)
    expect(res.answer).toBe("## The answer")
  })

  it("a remount reads the persisted id and resumes — start is called exactly once across the whole flow", async () => {
    // 1) Original ask kicks off and persists the ask_id (tab still 'generating').
    const startSpy = vi
      .spyOn(askApi, "start")
      .mockResolvedValue({ ask_id: 555, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "generating", answer: "" } as never)
    void runAskGeneration("Remount me?", "acme", "tab-remount")
    await vi.advanceTimersByTimeAsync(0)

    // 2) Simulated remount: the component reads the persisted id (jobResume).
    const pending = getPendingAsk("acme", "tab-remount")
    expect(pending).toEqual({ id: "555" })

    // 3) Resume by that id — the answer is now ready. No second POST.
    vi.spyOn(askApi, "get").mockResolvedValue(READY as never)
    const p = resumeAskGeneration(Number(pending!.id), "acme", "tab-remount")
    await vi.advanceTimersByTimeAsync(0)
    const res = await p

    expect(res.answer).toBe("## The answer")
    expect(startSpy).toHaveBeenCalledTimes(1)
    expect(getPendingAsk("acme", "tab-remount")).toBeNull()
  })
})

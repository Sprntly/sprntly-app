// @vitest-environment jsdom
//
// Unit tests for the blur/remount-safe chat Ask flow (runAskGeneration.ts).
// POST /v1/ask is fire-and-forget: it returns an ask_id and the answer keeps
// generating server-side; the client polls GET /v1/ask/{id} via the shared
// visibility-aware pollUntil and persists the active ask_id per tab (jobResume)
// so a remount re-attaches instead of re-asking. These tests mock the api layer
// and use fake timers to drive the poll without real wall-clock waits.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { askApi } from "../api"
import {
  runAskGeneration,
  resumeAskGeneration,
  getPendingAsk,
  askScope,
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

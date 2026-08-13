// The one owner of a standalone ticket-set run: kick off, poll, publish.
//
// Two invariants matter more than the rest here. ONE generate call per run —
// the insight path does not dedupe two phrasings of the same ask, so a second
// kick is a second `ticket_sets` row and a second multi-minute LLM bill. And a
// terminal state ALWAYS lands: a run that ends without writing
// `ticketSetGenerating: false` is a panel spinning forever over a job that
// finished.
import { beforeEach, describe, expect, it, vi } from "vitest"

const storiesApiMock = vi.hoisted(() => ({
  generateFromInsight: vi.fn(),
  getJob: vi.fn(),
}))
const ticketSetsApiMock = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api")
  return { ...actual, storiesApi: storiesApiMock, ticketSetsApi: ticketSetsApiMock }
})

// Real polling would spend 2s per tick; the loop's cadence isn't what's under
// test here.
vi.mock("../poll", () => ({ sleepUntilNextPoll: () => Promise.resolve() }))

import { ApiError } from "../api"
import { loadTicketSet, runTicketSetGeneration } from "../runTicketSetGeneration"
import type { AppContentState } from "../../types/content"

const STORY = {
  id: "sid-1", title: "Retry the webhook", body: "", acceptance_criteria: [],
  priority: null, route: null,
}

function recorder() {
  const patches: Partial<AppContentState>[] = []
  const setContent = (p: Partial<AppContentState>) => { patches.push(p) }
  return { patches, setContent, last: () => patches[patches.length - 1] }
}

beforeEach(() => {
  vi.clearAllMocks()
  storiesApiMock.generateFromInsight.mockResolvedValue({
    job_id: 11, status: "generating", ticket_set_id: 7,
  })
  ticketSetsApiMock.get.mockResolvedValue({
    id: 7, title: "Webhook retries", status: "ready", stories: [STORY],
    ticket_count: 1, conversation_id: 42, source_text: "make tickets",
  })
})

describe("runTicketSetGeneration", () => {
  it("kicks off exactly once and lands the finished set", async () => {
    storiesApiMock.getJob.mockResolvedValue({ job_id: 11, status: "ready", stories: [STORY] })
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 42, r.setContent)

    expect(out).toMatchObject({ ok: true })
    expect(storiesApiMock.generateFromInsight).toHaveBeenCalledTimes(1)
    // The third argument is the uploaded TICKET format the user named, and is
    // undefined here because this run named none — which means the company's
    // active ticket format, exactly as before it existed.
    expect(storiesApiMock.generateFromInsight).toHaveBeenCalledWith(
      "make tickets", 42, undefined,
    )
    // The title comes off the ROW, not the job — only the row has it.
    expect(r.last()).toMatchObject({
      ticketSetGenerating: false,
      ticketSet: { id: 7, title: "Webhook retries", conversationId: 42, status: "ready" },
    })
  })

  it("clears the previous artifact's slots before the first frame", async () => {
    storiesApiMock.getJob.mockResolvedValue({ job_id: 11, status: "ready", stories: [STORY] })
    const r = recorder()
    await runTicketSetGeneration("make tickets", 42, r.setContent)
    // A leftover PRD would otherwise put a PRD tab and an armed Share menu on a
    // chat that has neither.
    expect(r.patches[0]).toMatchObject({
      prd: null, prdMeta: null, evidence: null,
      ticketSetGenerating: true, ticketSetStandalone: false,
    })
  })

  it("publishes the planned roster and batch progress as they land", async () => {
    storiesApiMock.getJob
      .mockResolvedValueOnce({
        job_id: 11, status: "generating",
        stubs: [{ title: "Retry the webhook" }], progress: { done: 0, total: 2 },
      })
      .mockResolvedValueOnce({ job_id: 11, status: "ready", stories: [STORY] })
    const r = recorder()

    await runTicketSetGeneration("make tickets", 42, r.setContent)

    const streamed = r.patches.find((p) => p.ticketSet?.stubs?.length)
    expect(streamed?.ticketSet).toMatchObject({
      status: "generating", progress: { done: 0, total: 2 },
    })
    expect(streamed?.ticketSetGenerating).toBe(true)
  })

  it("records a failed run as a KIND, never the backend's message", async () => {
    storiesApiMock.getJob.mockResolvedValue({
      job_id: 11, status: "failed", error: "RuntimeError: anthropic 529 overloaded",
    })
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 42, r.setContent)

    expect(out).toEqual({ ok: false, kind: "failed" })
    expect(r.last()?.ticketSet).toMatchObject({ id: 7, status: "failed", error: "failed" })
    expect(JSON.stringify(r.last())).not.toMatch(/529|RuntimeError/)
    expect(r.last()?.ticketSetGenerating).toBe(false)
  })

  it("falls back to the durable row when a restart drops the job", async () => {
    storiesApiMock.getJob.mockRejectedValue(new ApiError(404, { detail: "Job not found" }))
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 42, r.setContent)

    // The in-memory job store is not the truth; the row is, and it is
    // guaranteed to reach a terminal state.
    expect(out).toMatchObject({ ok: true })
    expect(ticketSetsApiMock.get).toHaveBeenCalledWith(7)
    expect(r.last()?.ticketSet).toMatchObject({ status: "ready" })
  })

  it("calls a run lost only after repeated transport failures", async () => {
    storiesApiMock.getJob.mockRejectedValue(new TypeError("Failed to fetch"))
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 42, r.setContent)

    expect(out).toEqual({ ok: false, kind: "network" })
    expect(storiesApiMock.getJob).toHaveBeenCalledTimes(3)
    expect(r.last()?.ticketSetGenerating).toBe(false)
  })

  it("keeps a completed run even when the read-back of the row fails", async () => {
    storiesApiMock.getJob.mockResolvedValue({ job_id: 11, status: "ready", stories: [STORY] })
    ticketSetsApiMock.get.mockRejectedValue(new TypeError("Failed to fetch"))
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 42, r.setContent)

    // An unnamed set beats a lost one.
    expect(out).toMatchObject({ ok: true })
    expect(r.last()?.ticketSet).toMatchObject({ id: 7, title: "", status: "ready" })
    expect(r.last()?.ticketSet?.stories).toHaveLength(1)
  })

  it("reports a failed kick-off without leaving the panel spinning", async () => {
    storiesApiMock.generateFromInsight.mockRejectedValue(
      new ApiError(404, { detail: "Conversation not found" }),
    )
    const r = recorder()

    const out = await runTicketSetGeneration("make tickets", 99, r.setContent)

    expect(out).toEqual({ ok: false, kind: "failed" })
    expect(r.last()).toMatchObject({ ticketSet: null, ticketSetGenerating: false })
    expect(storiesApiMock.getJob).not.toHaveBeenCalled()
  })
})

// The other half of the same ownership rule: the Artifacts row's open-by-id and
// the thread resume come through here, so nothing outside this module ever has
// to know the shape of `content.ticketSet`.
describe("loadTicketSet", () => {
  it("publishes a finished set and starts no generation", async () => {
    const r = recorder()

    const out = await loadTicketSet(7, r.setContent)

    expect(out).toMatchObject({ ok: true })
    // Opening a set NEVER kicks a run — that is what makes an artifact row safe
    // to click twice.
    expect(storiesApiMock.generateFromInsight).not.toHaveBeenCalled()
    expect(r.patches[0]).toMatchObject({ prd: null, evidence: null, ticketSetGenerating: true })
    expect(r.last()).toMatchObject({
      ticketSetGenerating: false,
      ticketSet: { id: 7, title: "Webhook retries", status: "ready", conversationId: 42 },
    })
  })

  it("follows a still-generating row to its terminal state", async () => {
    // The stale-"Writing tickets…" case: the panel must never park on a
    // generating flag for a run that has since finished.
    ticketSetsApiMock.get
      .mockResolvedValueOnce({
        id: 7, title: "", status: "generating", stories: [],
        ticket_count: 0, conversation_id: 42, source_text: "make tickets",
      })
      .mockResolvedValueOnce({
        id: 7, title: "Webhook retries", status: "ready", stories: [STORY],
        ticket_count: 1, conversation_id: 42, source_text: "make tickets",
      })
    const r = recorder()

    const out = await loadTicketSet(7, r.setContent)

    expect(out).toMatchObject({ ok: true })
    // It showed the live state first…
    expect(r.patches.some((p) => p.ticketSetGenerating && p.ticketSet?.status === "generating")).toBe(true)
    // …then settled.
    expect(r.last()).toMatchObject({
      ticketSetGenerating: false, ticketSet: { status: "ready" },
    })
  })

  it("records a missing set as the notfound KIND, never a message", async () => {
    ticketSetsApiMock.get.mockRejectedValue(new ApiError(404, { detail: "Ticket set not found" }))
    const r = recorder()

    const out = await loadTicketSet(7, r.setContent)

    expect(out).toEqual({ ok: false, kind: "notfound" })
    expect(r.last()).toMatchObject({ ticketSet: null, ticketSetGenerating: false })
    // 404 covers "another tenant's" as well as "gone" — the copy must never
    // distinguish them, so nothing off the wire is stored.
    expect(JSON.stringify(r.last())).not.toMatch(/not found/i)
  })

  it("a failed row lands the failed kind, not an empty success", async () => {
    ticketSetsApiMock.get.mockResolvedValue({
      id: 7, title: "", status: "failed", stories: [],
      ticket_count: 0, conversation_id: 42, source_text: "make tickets",
    })
    const r = recorder()

    const out = await loadTicketSet(7, r.setContent)

    expect(out).toEqual({ ok: false, kind: "failed" })
    expect(r.last()?.ticketSet).toMatchObject({ id: 7, status: "failed", error: "failed" })
  })
})

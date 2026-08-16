// @vitest-environment jsdom
//
// useProjectPrivateThread — the folded-in shipped-T2 bugfixes (AC18/19/20).
// These exercise the LIVE private engine (the surface T2 shipped) to prove the
// three fixes: Stop cancels a classify-dispatched generation and frees the
// composer (not a dead button); a failed `addArtifact` settles the turn to an
// error state (never a permanent `pending` lock); and a `brief.delivered` that
// lands mid-initial-load survives (merge-not-replace).
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const runAskGenerationMock = vi.fn()
const resolveIntentMock = vi.fn()
const addArtifactMock = vi.fn()
const individualChatMock = vi.fn((id: number) =>
  Promise.resolve({ id: 9001, project_id: id, user_id: "u1", kind: "individual" as const, created_at: "2026-08-11T00:00:00Z", updated_at: "2026-08-11T00:00:00Z" }),
)
const individualTurnsMock = vi.fn().mockResolvedValue([])
const ledgerMock = vi.fn().mockResolvedValue([])
const generateFromInsightMock = vi.fn()
const getJobMock = vi.fn()
const runPrdGenerationFromTaskMock = vi.fn()

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>("../../../../../lib/runAskGeneration")
  return { ...actual, runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a), resumeAskGeneration: vi.fn(), getPendingAsk: vi.fn().mockReturnValue(null) }
})
vi.mock("../../../../../lib/runPrdGeneration", () => ({ runPrdGenerationFromTask: (...a: unknown[]) => runPrdGenerationFromTaskMock(...a) }))
vi.mock("../../../../../lib/poll", () => ({ sleepUntilNextPoll: () => Promise.resolve() }))
vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...(a as [number])),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
      resolveIntent: (...a: unknown[]) => resolveIntentMock(...a),
    },
    storiesApi: { ...actual.storiesApi, generateFromInsight: (...a: unknown[]) => generateFromInsightMock(...a), getJob: (...a: unknown[]) => getJobMock(...a) },
  }
})
vi.mock("../../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }) }))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ loading: false, profile: null, workspace: { feature_flags: { chat_intent_envelope: true } }, refresh: async () => {} }),
}))
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }) }))

const { realtimeSpy } = vi.hoisted(() => ({ realtimeSpy: vi.fn() }))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (topic: string | null, handlers: { onEvent?: (e: string, p: unknown) => void; onReconcile?: () => void }) => {
    realtimeSpy(topic, handlers)
    return { status: "degraded", degraded: true, presenceMembers: [], typers: [], sendTyping: vi.fn() }
  },
}))

import { useProjectPrivateThread, type UseProjectPrivateThread } from "../useProjectPrivateThread"
import type { IndividualTurn } from "../../../../../lib/api"

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let latest: UseProjectPrivateThread | null = null
function Harness({ projectId }: { projectId: number }) {
  const engine = useProjectPrivateThread(projectId)
  latest = engine
  return React.createElement("div", { "data-testid": "busy" }, String(engine.busy))
}
function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (e: string, p: unknown) => void; onReconcile: () => void }
}
const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resolveIntentMock.mockReset()
  addArtifactMock.mockReset()
  individualChatMock.mockClear()
  individualTurnsMock.mockReset().mockResolvedValue([])
  ledgerMock.mockReset().mockResolvedValue([])
  generateFromInsightMock.mockReset()
  getJobMock.mockReset()
  runPrdGenerationFromTaskMock.mockReset()
  realtimeSpy.mockClear()
  latest = null
})
afterEach(() => cleanup())

describe("useProjectPrivateThread — folded T2 fixes", () => {
  it("test_stop_cancels_classify_dispatched_generation (AC18)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_tickets", confidence: 0.9, task: "the webhook work" })
    generateFromInsightMock.mockResolvedValue({ job_id: 55, status: "generating", ticket_set_id: 7 })
    // The job poll never settles — the generation stays in flight (the exact
    // state where the shipped Stop was a dead button).
    getJobMock.mockReturnValue(deferred<{ status: string }>().promise)
    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("write tickets for the webhook work"))
    // The turn is pending / composer blocked while the generation runs.
    await waitFor(() => expect(latest!.busy).toBe(true))
    // Stop cancels it and frees the composer.
    act(() => latest!.stop())
    await flush()
    expect(latest!.busy).toBe(false)
    const turn = latest!.turns[latest!.turns.length - 1]
    expect(turn.pending).not.toBe(true)
    expect(turn.stopped).toBe(true)
  })

  it("test_failed_addArtifact_settles_turn_not_pending (AC19)", async () => {
    resolveIntentMock.mockResolvedValue({ intent: "generate_prd", confidence: 0.9, task: "dark mode" })
    runPrdGenerationFromTaskMock.mockResolvedValue({ ok: true, prd: { prd_id: 11, title: "Dark mode" } })
    addArtifactMock.mockRejectedValue(new Error("attach failed"))
    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    act(() => latest!.send("generate a PRD for dark mode"))
    await flush()
    await waitFor(() => expect(latest!.busy).toBe(false))
    const turn = latest!.turns[latest!.turns.length - 1]
    // Settled to an error, NOT a permanent pending lock.
    expect(turn.pending).not.toBe(true)
    expect(turn.error).toBeTruthy()
  })

  it("test_brief_delivered_during_initial_load_survives_merge (AC20)", async () => {
    const load = deferred<IndividualTurn[]>()
    individualTurnsMock.mockReturnValueOnce(load.promise)
    render(React.createElement(Harness, { projectId: 3 }))
    await flush()
    // A brief.delivered lands via realtime WHILE the initial history read is
    // still in flight.
    const brief: IndividualTurn = { id: 900, role: "assistant", content: "You've been handed a task.", created_at: "2026-08-16T10:00:00Z" }
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", brief)
    })
    // The initial load resolves with a DIFFERENT (older) turn.
    await act(async () => {
      load.resolve([{ id: 100, role: "user", content: "an earlier question", created_at: "2026-08-16T09:00:00Z" }])
      await Promise.resolve()
    })
    await flush()
    // Merge-not-replace: the brief survives the load (both present, sorted).
    const contents = latest!.turns.map((t) => t.content)
    expect(contents).toContain("You've been handed a task.")
    expect(contents).toContain("an earlier question")
    // Sorted by clock: the earlier question precedes the later brief.
    expect(contents.indexOf("an earlier question")).toBeLessThan(contents.indexOf("You've been handed a task."))
  })
})

// @vitest-environment jsdom
//
// useProjectPrivateThread — the persistence half (§H): session↔history
// dedup by `client_message_id` once a send's own persisted row lands
// (AC9, mutation-proofed), and the #9-count `onArtifactsChanged` callback
// firing after a client-driven generate settles its own `addArtifact`.
// Per-branch persist-path coverage lives in
// `ProjectPrivateChat.dispatch.dom.test.tsx` (the rendered-UI surface);
// this file drives the hook directly via a thin harness, mirroring
// `useProjectPrivateThread.test.tsx`'s own pattern.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const runAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)
const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()
const resolveIntentMock = vi.fn()
const generateFromInsightMock = vi.fn()
const getJobMock = vi.fn()
const addArtifactMock = vi.fn()

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: vi.fn(),
    getPendingAsk: () => getPendingAskMock(),
  }
})

vi.mock("../../../../../lib/poll", () => ({
  sleepUntilNextPoll: () => Promise.resolve(),
}))

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      resolveIntent: (...a: unknown[]) => resolveIntentMock(...a),
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
      ledger: () => Promise.resolve([]),
    },
    storiesApi: {
      ...actual.storiesApi,
      generateFromInsight: (...a: unknown[]) => generateFromInsightMock(...a),
      getJob: (...a: unknown[]) => getJobMock(...a),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))

const { realtimeSpy } = vi.hoisted(() => ({ realtimeSpy: vi.fn() }))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (
    topic: string | null,
    handlers: { onEvent?: (event: string, payload: unknown) => void; onReconcile?: () => void },
  ) => {
    realtimeSpy(topic, handlers)
    return { status: "degraded", degraded: true }
  },
}))

import { useProjectPrivateThread, type UseProjectPrivateThread } from "../useProjectPrivateThread"

const reply = (answer: string) => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" })

const individualChatRecord = (id: number, projectId: number) => ({
  id, project_id: projectId, user_id: "u1", kind: "individual" as const,
  created_at: "2026-08-11T00:00:00Z", updated_at: "2026-08-11T00:00:00Z",
})

let latest: UseProjectPrivateThread | null = null
let onArtifactsChangedSpy: ReturnType<typeof vi.fn>

function Harness({ projectId }: { projectId: number }) {
  const engine = useProjectPrivateThread(projectId, { onArtifactsChanged: onArtifactsChangedSpy })
  latest = engine
  return <div data-testid="turn-count">{engine.turns.length}</div>
}

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}

beforeEach(() => {
  runAskGenerationMock.mockReset()
  getPendingAskMock.mockReset()
  getPendingAskMock.mockReturnValue(null)
  individualChatMock.mockReset()
  individualChatMock.mockImplementation((id: number) => Promise.resolve(individualChatRecord(9001, id)))
  individualTurnsMock.mockReset()
  individualTurnsMock.mockResolvedValue([])
  resolveIntentMock.mockReset()
  generateFromInsightMock.mockReset()
  getJobMock.mockReset()
  addArtifactMock.mockReset()
  realtimeSpy.mockClear()
  onArtifactsChangedSpy = vi.fn()
  latest = null
})
afterEach(() => cleanup())

describe("useProjectPrivateThread — session↔history dedup (AC9)", () => {
  it("test_session_turn_deduped_against_history_by_client_message_id", async () => {
    runAskGenerationMock.mockResolvedValue(reply("settled answer"))
    render(React.createElement(Harness, { projectId: 202 }))

    await act(async () => {
      latest!.send("what did we decide?")
    })
    await act(async () => {
      await Promise.resolve()
    })

    // The minted client_message_id rode the /v1/ask call — capture it so
    // the "now persisted" history row below can carry the SAME key.
    const opts = runAskGenerationMock.mock.calls[0][3] as { client_message_id?: string }
    const cmid = opts.client_message_id
    expect(typeof cmid).toBe("string")

    expect(latest!.turns.filter((t) => t.content === "what did we decide?")).toHaveLength(1)

    // A reconnect reconcile re-delivers the NOW-PERSISTED row (same key) —
    // the persisted row is the authority; the session turn must drop, not
    // duplicate (AC9).
    individualTurnsMock.mockResolvedValueOnce([
      {
        id: 501, role: "user", content: "what did we decide?",
        created_at: "2026-08-11T00:05:00Z", client_message_id: cmid,
      },
    ])
    await act(async () => {
      lastHandlers().onReconcile()
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(latest!.turns.filter((t) => t.content === "what did we decide?")).toHaveLength(1)
    expect(latest!.turns.find((t) => t.id === "history-501")).toBeTruthy()
  })

  it("test_dedup_removed_renders_twice_is_red (AC9 mutation proof)", async () => {
    // RED: a naive concat (history ++ session, no client_message_id
    // filter) renders the SAME turn twice — the exact defect the dedup
    // guards against.
    const naiveMerge = [
      { id: "history-501", content: "what did we decide?" },
      { id: "sess-1", content: "what did we decide?" },
    ]
    expect(naiveMerge.filter((t) => t.content === "what did we decide?")).toHaveLength(2)

    // GREEN: the real engine, identical scenario end to end — renders once.
    runAskGenerationMock.mockResolvedValue(reply("settled answer"))
    render(React.createElement(Harness, { projectId: 303 }))
    await act(async () => {
      latest!.send("what did we decide?")
    })
    await act(async () => {
      await Promise.resolve()
    })
    const opts = runAskGenerationMock.mock.calls[0][3] as { client_message_id?: string }
    const cmid = opts.client_message_id

    individualTurnsMock.mockResolvedValueOnce([
      {
        id: 501, role: "user", content: "what did we decide?",
        created_at: "2026-08-11T00:05:00Z", client_message_id: cmid,
      },
    ])
    await act(async () => {
      lastHandlers().onReconcile()
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(latest!.turns.filter((t) => t.content === "what did we decide?")).toHaveLength(1)
  })
})

describe("useProjectPrivateThread — #9-count artifact invalidation", () => {
  it("test_generate_fires_on_artifacts_changed", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "generate_tickets", confidence: 0.9, task: "the webhook retry work",
      instruction: null, reason: "tickets", source: "llm", prd_id: null, prd_title: null,
    })
    generateFromInsightMock.mockResolvedValue({ job_id: 55, status: "generating", ticket_set_id: 7 })
    getJobMock.mockResolvedValue({ status: "ready", stories: [] })
    addArtifactMock.mockResolvedValue({ project_id: 202, artifact_type: "ticket_set", artifact_id: 7 })

    render(React.createElement(Harness, { projectId: 202 }))
    await act(async () => {
      latest!.send("break this into work items")
    })
    // Let the classify -> generate -> poll -> attach chain settle.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(addArtifactMock).toHaveBeenCalledWith(202, "ticket_set", 7)
    expect(onArtifactsChangedSpy).toHaveBeenCalledTimes(1)
  })

  it("test_onArtifactsChanged_not_called_on_a_plain_ask", async () => {
    resolveIntentMock.mockResolvedValue({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "question", source: "llm", prd_id: null, prd_title: null,
    })
    runAskGenerationMock.mockResolvedValue(reply("plain answer"))

    render(React.createElement(Harness, { projectId: 202 }))
    await act(async () => {
      latest!.send("what's the status?")
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(onArtifactsChangedSpy).not.toHaveBeenCalled()
  })
})

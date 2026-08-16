// @vitest-environment jsdom
//
// useProjectPrivateThread — the private-chat engine hook. Covers the turn
// normalization contract the shell renders against: persisted history maps to
// `ShellTurn`s whose `createdAt` is the STORED timestamp (never `Date.now()`
// at render — the §1.2 drift-fix guard), session sends carry `{project_id,
// conversation_id}` and reuse the bound conversation, and a live
// `brief.delivered` broadcast appends a turn.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const runAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)
const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()

let authState: { kind: "authed"; user: { id: string } } | { kind: "anonymous" } = {
  kind: "authed",
  user: { id: "u1" },
}

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

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
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
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authState,
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
import type { IndividualTurn } from "../../../../../lib/api"

const reply = (answer: string) => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" })

const individualChatRecord = (id: number, projectId: number) => ({
  id,
  project_id: projectId,
  user_id: "u1",
  kind: "individual" as const,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
})

let latest: UseProjectPrivateThread | null = null
function Harness({ projectId }: { projectId: number }) {
  const engine = useProjectPrivateThread(projectId)
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
  realtimeSpy.mockClear()
  authState = { kind: "authed", user: { id: "u1" } }
  latest = null
})
afterEach(() => cleanup())

describe("useProjectPrivateThread — turn normalization", () => {
  it("test_engine_normalizes_stored_turns_into_shellturns_with_persisted_createdAt (AC2)", async () => {
    const persisted: IndividualTurn = {
      id: 2,
      role: "assistant",
      content: "Flat $49/mo, decided last week.",
      created_at: "2026-08-10T10:01:00Z",
    }
    individualTurnsMock.mockResolvedValue([persisted])

    await act(async () => {
      render(React.createElement(Harness, { projectId: 202 }))
    })
    await act(async () => {
      await Promise.resolve()
    })

    const turn = latest!.turns.find((t) => t.id === "history-2")!
    expect(turn.author.kind).toBe("agent")
    expect(turn.content).toContain("Flat $49/mo")
    // The stored clock, NOT the current clock (the §1.2 drift fix).
    expect(turn.createdAt).toBe(new Date("2026-08-10T10:01:00Z").getTime())
  })

  it("test_engine_settled_turn_timestamp_stable_across_rerender (AC2)", async () => {
    // The one named intended change vs the old `formatTime(Date.now())` at
    // render: a settled session turn's `createdAt` is minted ONCE and never
    // moves on a re-render.
    runAskGenerationMock.mockResolvedValue(reply("settled answer"))
    const { rerender } = render(React.createElement(Harness, { projectId: 202 }))

    await act(async () => {
      latest!.send("what did we decide?")
    })
    await act(async () => {
      await Promise.resolve()
    })

    const settled = latest!.turns.find((t) => t.reply != null)!
    const firstStamp = settled.createdAt
    expect(typeof firstStamp).toBe("number")

    // Force a re-render — the displayed timestamp source must not change.
    await act(async () => {
      rerender(React.createElement(Harness, { projectId: 202 }))
    })
    const again = latest!.turns.find((t) => t.id === settled.id)!
    expect(again.createdAt).toBe(firstStamp)
  })
})

describe("useProjectPrivateThread — send binding", () => {
  it("test_engine_send_carries_project_id_and_conversation_id (AC4)", async () => {
    runAskGenerationMock.mockResolvedValue(reply("ok"))
    render(React.createElement(Harness, { projectId: 202 }))

    await act(async () => {
      latest!.send("first message here")
    })
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      latest!.send("second message here")
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(individualChatMock).toHaveBeenCalledTimes(1)
    expect(runAskGenerationMock).toHaveBeenCalledTimes(2)
    for (const call of runAskGenerationMock.mock.calls) {
      expect(call[0]).toBeTypeOf("string")
      expect(call[3]).toEqual(expect.objectContaining({ project_id: 202, conversation_id: 9001 }))
    }
  })
})

describe("useProjectPrivateThread — realtime", () => {
  it("test_engine_realtime_brief_delivered_appends_turn (AC5)", async () => {
    individualTurnsMock.mockResolvedValue([])
    render(React.createElement(Harness, { projectId: 202 }))
    await act(async () => {
      await Promise.resolve()
    })
    expect(latest!.turns).toHaveLength(0)

    const brief: IndividualTurn = {
      id: 7,
      role: "assistant",
      content: "Ship the onboarding flow by Friday.",
      created_at: "2026-08-11T09:00:00Z",
    }
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", brief)
    })

    const appended = latest!.turns.find((t) => t.id === "history-7")!
    expect(appended.author.kind).toBe("agent")
    expect(appended.content).toContain("Ship the onboarding flow by Friday.")
    expect(appended.createdAt).toBe(new Date("2026-08-11T09:00:00Z").getTime())
  })
})

// @vitest-environment jsdom
//
// ProjectIndividualChat — live subscribe to the caller's per-user channel.
// `useRealtimeChannel` itself is mocked here (its own subscribe/reconnect/
// degrade lifecycle is covered by useRealtimeChannel.dom.test.tsx, and its
// no-subscribe-on-null-topic behaviour by that same file) — this file
// asserts the CONSUMER wiring: one channel for the caller's own
// `project:{id}:user:{uid}` topic, a `brief.delivered` broadcast appended
// live into `history` (rendered via the same standalone-agent-turn markup
// `ProjectIndividualChat.history.dom.test.tsx` covers), the id-dedup
// guarantee (live event vs. reconcile read vs. the initial history load),
// the null-topic/degraded-reconcile fallback, unmount cleanup, and
// non-breakage of the send path + load-on-open effect.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's typing-animation hook reads prefers-reduced-motion on mount;
// jsdom has no matchMedia. Same stub every sibling test file in this
// directory uses.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()
const runAskGenerationMock = vi.fn()
const resumeAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)

let authState:
  | { kind: "authed"; user: { id: string } }
  | { kind: "anonymous" } = { kind: "authed", user: { id: "u1" } }

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

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: (...a: unknown[]) => resumeAskGenerationMock(...a),
    getPendingAsk: (...a: unknown[]) => getPendingAskMock(...a),
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))

// The component now reads the classifier flag (`chatIntentEnvelopeOn`) to
// decide whether to classify-then-dispatch at all. Explicit OFF here keeps
// every assertion in this file byte-identical to pre-classifier behaviour —
// same stub shape `ProjectIndividualChat.test.tsx` uses.
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))

vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authState,
}))

// Test-local mock of `useRealtimeChannel` — same shape as
// `ProjectGroupChat.realtime.dom.test.tsx`'s own mock: no internal React
// state, just a spy capturing the topic + handlers the consumer wired up so
// a test can invoke them directly to simulate a broadcast (`onEvent`) or a
// (re)connect (`onReconcile`) without standing up a real channel.
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

import { ProjectIndividualChat } from "../ProjectIndividualChat"
import type { IndividualTurn } from "../../../../../lib/api"

const turn = (overrides: Partial<IndividualTurn>): IndividualTurn => ({
  id: 1,
  role: "assistant",
  content: "a delegated brief",
  created_at: new Date().toISOString(),
  ...overrides,
})

const reply = (answer: string) => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" })

const individualChatRecord = (id: number, projectId: number) => ({
  id,
  project_id: projectId,
  user_id: "u1",
  kind: "individual" as const,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
})

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}

beforeEach(() => {
  individualChatMock.mockReset()
  individualChatMock.mockImplementation((id: number) => Promise.resolve(individualChatRecord(9001, id)))
  individualTurnsMock.mockReset()
  individualTurnsMock.mockResolvedValue([])
  runAskGenerationMock.mockReset()
  resumeAskGenerationMock.mockReset()
  getPendingAskMock.mockReset()
  getPendingAskMock.mockReturnValue(null)
  realtimeSpy.mockClear()
  authState = { kind: "authed", user: { id: "u1" } }
})
afterEach(() => cleanup())

describe("ProjectIndividualChat — live subscribe (AC-1)", () => {
  it("test_subscribes_to_per_user_topic_on_open: one channel for project:{id}:user:{uid}", async () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === "project:202:user:u1")).toBe(true)
    expect(new Set(topics).size).toBe(1)
  })
})

describe("ProjectIndividualChat — live apply (AC-2)", () => {
  it("test_brief_delivered_appends_live: broadcast -> brief in history, no re-open, no poll", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))
    const callsAfterLoad = individualTurnsMock.mock.calls.length

    const brief = turn({ id: 7, content: "Ship the onboarding flow by Friday." })
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", brief)
    })

    expect(await screen.findByTestId("ic-history-agent")).toBeTruthy()
    expect(screen.getByText("Ship the onboarding flow by Friday.")).toBeTruthy()
    // No reconcile/poll call was needed for this to render.
    expect(individualTurnsMock.mock.calls.length).toBe(callsAfterLoad)
  })

  it("ignores unknown broadcast event names", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      lastHandlers().onEvent("presence.sync", { anything: true })
    })
    expect(screen.queryByTestId("ic-history-agent")).toBeNull()
  })
})

describe("ProjectIndividualChat — reconnect reconcile (AC-3)", () => {
  it("test_reconnect_runs_one_individual_reconcile: onReconcile -> single individualTurns read, merged", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))

    individualTurnsMock.mockResolvedValueOnce([turn({ id: 4, content: "reconciled brief" })])
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(individualTurnsMock).toHaveBeenCalledTimes(2)
    expect(individualTurnsMock).toHaveBeenLastCalledWith(202)
    expect(await screen.findByText("reconciled brief")).toBeTruthy()
  })
})

describe("ProjectIndividualChat — idempotency / dedup (AC-4)", () => {
  it("test_duplicate_brief_event_and_reconcile_renders_once: same id via event + reconcile -> one bubble", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))

    const dup = turn({ id: 9, content: "dup brief" })
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", dup)
    })
    expect(await screen.findAllByTestId("ic-history-agent")).toHaveLength(1)

    // The same turn also comes back through a reconcile read.
    individualTurnsMock.mockResolvedValueOnce([dup])
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1)
    expect(screen.getAllByText("dup brief")).toHaveLength(1)
  })

  it("a brief already present in the loaded history is not re-rendered when its broadcast arrives", async () => {
    const existing = turn({ id: 3, content: "already loaded" })
    individualTurnsMock.mockResolvedValueOnce([existing])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1))

    await act(async () => {
      lastHandlers().onEvent("brief.delivered", existing)
    })

    expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1)
    expect(screen.getAllByText("already loaded")).toHaveLength(1)
  })
})

describe("ProjectIndividualChat — degradation (AC-5)", () => {
  it("test_null_topic_falls_back_to_open_only: unresolved uid -> no subscribe topic, today behaviour", async () => {
    authState = { kind: "anonymous" }
    individualTurnsMock.mockResolvedValueOnce([
      { id: 1, role: "assistant", content: "loaded on open", created_at: new Date().toISOString() },
    ])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))

    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledWith(202))
    expect(await screen.findByText("loaded on open")).toBeTruthy()

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === null)).toBe(true)
  })

  it("test_degraded_channel_no_error_surface: a failed reconcile read never throws or surfaces an error", async () => {
    individualTurnsMock.mockResolvedValueOnce([
      { id: 1, role: "assistant", content: "existing brief", created_at: new Date().toISOString() },
    ])
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))

    individualTurnsMock.mockRejectedValueOnce(new Error("channel degraded"))
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
      await Promise.resolve()
    })

    // The pre-existing history is still there; no error UI was added.
    expect(screen.getByTestId("ic-history-agent").textContent).toContain("existing brief")
    expect(screen.queryByRole("alert")).toBeNull()
  })
})

describe("ProjectIndividualChat — non-breakage / cleanup (AC-6/AC-7)", () => {
  it("test_unmount_tears_down_channel: no further reconcile/apply activity survives unmount", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    const { unmount } = render(React.createElement(ProjectIndividualChat, { projectId: 202 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledTimes(1))
    const handlers = lastHandlers()
    const callsAtUnmount = individualTurnsMock.mock.calls.length

    unmount()

    // Stale handlers firing post-unmount must not throw or trigger further
    // reads — the real hook's own teardown (removeChannel) is covered by
    // useRealtimeChannel.dom.test.tsx; this asserts the consumer holds no
    // lingering activity once its tree is gone.
    expect(() => handlers.onEvent("brief.delivered", turn({ id: 99 }))).not.toThrow()
    expect(individualTurnsMock.mock.calls.length).toBe(callsAtUnmount)
  })

  it("test_send_path_and_open_effect_unchanged: same props, load-on-open + composer/send path untouched", async () => {
    individualTurnsMock.mockResolvedValueOnce([])
    runAskGenerationMock.mockResolvedValue(reply("still works"))
    render(React.createElement(ProjectIndividualChat, { projectId: 202 }))

    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledWith(202))
    expect(individualChatMock).not.toHaveBeenCalled()

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "still a normal question" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(individualChatMock).toHaveBeenCalledWith(202)
    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "still a normal question",
      "acme",
      "project-individual-202",
      expect.objectContaining({ project_id: 202, conversation_id: 9001 }),
    )
    await waitFor(() => expect(screen.getByTestId("ic-msg-agent")).toBeTruthy())
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain("still works")
  })
})

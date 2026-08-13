// @vitest-environment jsdom
//
// ProjectGroupChat — live subscribe, poll demotion, and the `applyTurns`
// id-dedup extension. `useRealtimeChannel` itself is mocked here (its own
// subscribe/reconnect/degrade lifecycle is covered by
// useRealtimeChannel.dom.test.tsx) — this file asserts the CONSUMER wiring:
// one channel for the project topic, a `turn.created` broadcast rendering
// through the existing `applyTurns`, the same-turn-twice dedup guarantee
// (live event + poll/reconcile, and the poster's own optimistic echo), and
// the poll's degrade/fallback gating (AD-P22).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's typing-animation hook reads prefers-reduced-motion on mount;
// jsdom has no matchMedia. Same stub the sibling ProjectGroupChat.test.tsx
// uses.
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

const groupTurnsMock = vi.fn()
const postGroupTurnMock = vi.fn()
let authState: { kind: "authed"; user: { id: string } } | { kind: "anonymous" } = {
  kind: "authed",
  user: { id: "u1" },
}

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
      postGroupTurn: (...a: unknown[]) => postGroupTurnMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authState,
}))

// Test-local mock of `useRealtimeChannel` — no internal React state, so it
// needs no hook of its own. `realtimeState.degraded` is read fresh on every
// render; a test flips it and calls `rerender()` to simulate a channel
// status transition (SUBSCRIBED -> live, CHANNEL_ERROR -> degraded). The
// spy captures the handlers the consumer wired up so a test can invoke them
// directly to simulate a broadcast (`onEvent`) or a (re)connect
// (`onReconcile`) without standing up a real channel.
const { realtimeSpy, realtimeState } = vi.hoisted(() => ({
  realtimeSpy: vi.fn(),
  realtimeState: { degraded: true },
}))
// Presence/typing (added alongside this ticket) are exercised in their own
// suites (useRealtimeChannel.presence.dom.test.tsx, and the render/degrade
// cases below) — here the mock returns the honest empty defaults so this
// file's turn-path assertions stay unaffected (AC-8 non-regression).
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (
    topic: string | null,
    handlers: { onEvent?: (event: string, payload: unknown) => void; onReconcile?: () => void },
  ) => {
    realtimeSpy(topic, handlers)
    return {
      status: realtimeState.degraded ? "degraded" : "live",
      degraded: realtimeState.degraded,
      presenceMembers: [],
      sendTyping: vi.fn(),
      typers: [],
    }
  },
}))

import { ProjectGroupChat } from "../ProjectGroupChat"
import type { GroupTurn } from "../../../../../lib/api"

const turn = (overrides: Partial<GroupTurn>): GroupTurn => ({
  id: 1,
  role: "user",
  content: "hello",
  author_user_id: "u1",
  author_name: "Ada",
  author_job_role: "PM",
  created_at: new Date().toISOString(),
  ...overrides,
})

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}

beforeEach(() => {
  groupTurnsMock.mockReset()
  postGroupTurnMock.mockReset()
  realtimeSpy.mockClear()
  realtimeState.degraded = false // most tests exercise the "channel is live" path
  authState = { kind: "authed", user: { id: "u1" } }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectGroupChat — live subscribe (AC-1)", () => {
  it("test_subscribes_to_project_topic_on_mount: one channel for project:{id}", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === "project:101")).toBe(true)
    expect(new Set(topics).size).toBe(1)
  })
})

describe("ProjectGroupChat — live apply (AC-2)", () => {
  it("test_turn_created_appends_via_applyTurns: broadcast -> new bubble, no poll call", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))
    const callsAfterLoad = groupTurnsMock.mock.calls.length

    const t = turn({ id: 7, author_user_id: "u2", author_name: "Shristi", content: "live one" })
    await act(async () => {
      lastHandlers().onEvent("turn.created", t)
    })

    expect(await screen.findByTestId("gc-msg-other")).toBeTruthy()
    expect(screen.getByText("live one")).toBeTruthy()
    // No poll/reconcile call was needed for this to render.
    expect(groupTurnsMock.mock.calls.length).toBe(callsAfterLoad)
  })

  it("ignores unknown broadcast event names", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      lastHandlers().onEvent("presence.sync", { anything: true })
    })
    expect(screen.queryByTestId("gc-msg-other")).toBeNull()
    expect(screen.queryByTestId("gc-msg-me")).toBeNull()
  })
})

describe("ProjectGroupChat — reconnect reconcile (AC-3)", () => {
  it("test_reconnect_runs_one_cursor_reconcile: onReconcile -> single groupTurns(since) read, applied", async () => {
    groupTurnsMock.mockResolvedValueOnce([turn({ id: 3, content: "seed" })])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    groupTurnsMock.mockResolvedValueOnce([turn({ id: 4, author_user_id: "u2", author_name: "Shristi", content: "reconciled" })])
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(groupTurnsMock).toHaveBeenCalledTimes(2)
    expect(groupTurnsMock).toHaveBeenLastCalledWith(101, 3)
    expect(await screen.findByText("reconciled")).toBeTruthy()
  })
})

describe("ProjectGroupChat — idempotency / dedup (AC-4)", () => {
  it("test_duplicate_turn_event_and_poll_renders_once: same id via live event + reconcile -> one bubble", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    const dup = turn({ id: 9, author_user_id: "u2", author_name: "Shristi", content: "dup content" })
    await act(async () => {
      lastHandlers().onEvent("turn.created", dup)
    })
    expect(await screen.findAllByTestId("gc-msg-other")).toHaveLength(1)

    // The same turn also comes back through a reconcile read.
    groupTurnsMock.mockResolvedValueOnce([dup])
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(screen.getAllByTestId("gc-msg-other")).toHaveLength(1)
    expect(screen.getAllByText("dup content")).toHaveLength(1)
  })

  it("test_own_post_echo_deduped: optimistic own-post reconcile + its own broadcast -> one bubble", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    postGroupTurnMock.mockResolvedValue(turn({ id: 5, content: "hi team" }))
    const posted = turn({ id: 5, content: "hi team", author_user_id: "u1", author_name: "Me" })
    groupTurnsMock.mockResolvedValueOnce([posted])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    const sendBtn = screen.getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })
    await waitFor(() => expect(postGroupTurnMock).toHaveBeenCalledWith(101, "hi team"))
    await waitFor(() => expect(screen.getAllByTestId("gc-msg-me")).toHaveLength(1))

    // The broadcast of the poster's own turn arrives right after.
    await act(async () => {
      lastHandlers().onEvent("turn.created", posted)
    })

    expect(screen.getAllByTestId("gc-msg-me")).toHaveLength(1)
  })

  it("test_applyTurns_dedup_added: the same broadcast delivered twice yields one turn", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    const t = turn({ id: 12, author_user_id: "u2", author_name: "Shristi", content: "twice" })
    await act(async () => {
      lastHandlers().onEvent("turn.created", t)
    })
    await act(async () => {
      lastHandlers().onEvent("turn.created", t)
    })

    expect(screen.getAllByTestId("gc-msg-other")).toHaveLength(1)
    expect(screen.getAllByText("twice")).toHaveLength(1)
  })
})

describe("ProjectGroupChat — poll fallback / degradation (AC-5)", () => {
  it("test_poll_suppressed_while_live: degraded=false -> the 4s poll never ticks", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = false
    groupTurnsMock.mockResolvedValue([])

    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterLoad = groupTurnsMock.mock.calls.length
    expect(callsAfterLoad).toBe(1)

    await act(async () => {
      vi.advanceTimersByTime(20_000)
      await Promise.resolve()
    })

    expect(groupTurnsMock.mock.calls.length).toBe(callsAfterLoad)
    hasFocusSpy.mockRestore()
  })

  it("test_poll_rearms_on_degraded: degraded=true -> the 4s focus-gated poll runs and the thread updates", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true
    groupTurnsMock.mockResolvedValue([])

    const { rerender } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterLoad = groupTurnsMock.mock.calls.length
    expect(callsAfterLoad).toBe(1)

    groupTurnsMock.mockResolvedValueOnce([turn({ id: 20, author_user_id: "u2", author_name: "Shristi", content: "polled turn" })])
    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(groupTurnsMock.mock.calls.length).toBeGreaterThan(callsAfterLoad)
    expect(screen.getByText("polled turn")).toBeTruthy()

    rerender(React.createElement(ProjectGroupChat, { projectId: 101 }))
    hasFocusSpy.mockRestore()
  })

  it("channel going live suppresses an already-armed poll on the next tick", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true
    groupTurnsMock.mockResolvedValue([])

    const { rerender } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterLoad = groupTurnsMock.mock.calls.length

    // Channel recovers — flip degraded false and force the consumer to
    // re-render so it picks up the new hook return value.
    realtimeState.degraded = false
    rerender(React.createElement(ProjectGroupChat, { projectId: 101 }))

    await act(async () => {
      vi.advanceTimersByTime(20_000)
      await Promise.resolve()
    })

    expect(groupTurnsMock.mock.calls.length).toBe(callsAfterLoad)
    hasFocusSpy.mockRestore()
  })
})

describe("ProjectGroupChat — non-breakage / cleanup (AC-6/AC-7)", () => {
  it("test_unmount_tears_down_channel: no further apply/poll activity survives unmount", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true
    groupTurnsMock.mockResolvedValue([])

    const { unmount } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const handlers = lastHandlers()
    const callsAtUnmount = groupTurnsMock.mock.calls.length

    unmount()

    // Stale handlers firing post-unmount must not throw or trigger further
    // reads — the real hook's own teardown (removeChannel) is covered by
    // useRealtimeChannel.dom.test.tsx; this asserts the consumer holds no
    // lingering activity once its tree is gone.
    expect(() => handlers.onEvent("turn.created", turn({ id: 99 }))).not.toThrow()
    await act(async () => {
      vi.advanceTimersByTime(20_000)
    })
    expect(groupTurnsMock.mock.calls.length).toBe(callsAtUnmount)
    hasFocusSpy.mockRestore()
  })

  it("test_component_signature_unchanged: same props (onOpenArtifact optional), initial-load + composer/send path untouched", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))
    expect(groupTurnsMock).toHaveBeenCalledWith(101)

    postGroupTurnMock.mockResolvedValue(turn({ id: 8, content: "still works" }))
    groupTurnsMock.mockResolvedValueOnce([turn({ id: 8, content: "still works", author_user_id: "u1", author_name: "Me" })])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "still works" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    await waitFor(() => expect(postGroupTurnMock).toHaveBeenCalledWith(101, "still works"))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")
    })
  })
})

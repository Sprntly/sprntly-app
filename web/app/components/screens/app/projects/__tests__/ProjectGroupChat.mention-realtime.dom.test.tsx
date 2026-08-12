// @vitest-environment jsdom
//
// ProjectGroupChat — recipient-side LIVENESS signals (AD-TNM5). A delivered
// `member.added` shows a transient "someone joined" line; a `mention.received`
// shows a transient "you were mentioned" affordance; a malformed/absent or
// unknown event degrades cleanly to the existing poll (no throw). The turn/
// poll/dedup wiring is covered by ProjectGroupChat.realtime.dom.test.tsx —
// this file asserts only the new signal handling. `useRealtimeChannel` is
// mocked (its own lifecycle lives in useRealtimeChannel.dom.test.tsx); the
// spy captures the handlers so a test invokes `onEvent` directly.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed", user: { id: "u1" } }),
}))

const { realtimeSpy, realtimeState } = vi.hoisted(() => ({
  realtimeSpy: vi.fn(),
  realtimeState: { degraded: false },
}))
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

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void }
}

beforeEach(() => {
  groupTurnsMock.mockReset()
  realtimeSpy.mockClear()
  realtimeState.degraded = false
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectGroupChat — liveness signals (AC-8)", () => {
  it("test_member_added_event_refreshes_roster_signal", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      lastHandlers().onEvent("member.added", {
        project_id: 101,
        project_name: "Pricing Revamp",
        actor_name: null,
        kind: "added",
      })
    })

    const signal = await screen.findByTestId("gc-live-signal")
    expect(signal.textContent).toContain("joined")
    expect(signal.textContent).toContain("Pricing Revamp")
  })

  it("test_mention_received_event_shows_affordance", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      lastHandlers().onEvent("mention.received", {
        project_id: 101,
        project_name: "Pricing Revamp",
        actor_name: "Dana",
        kind: "mentioned",
      })
    })

    const signal = await screen.findByTestId("gc-live-signal")
    expect(signal.textContent).toContain("mentioned you")
    expect(signal.textContent).toContain("Dana")
  })

  it("test_mention_received_generic_when_actor_missing", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      lastHandlers().onEvent("mention.received", { project_id: 101, kind: "mentioned" })
    })

    const signal = await screen.findByTestId("gc-live-signal")
    expect(signal.textContent).toContain("You were mentioned")
  })

  it("test_dropped_event_degrades_to_poll", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    // An unknown event name shows NO signal (and never throws) — the surface
    // simply ignores it and keeps its existing poll fallback.
    await act(async () => {
      lastHandlers().onEvent("some.unknown.event", { anything: true })
    })
    expect(screen.queryByTestId("gc-live-signal")).toBeNull()

    // A malformed / absent payload on a KNOWN signal event must never throw —
    // it degrades to the generic copy (AD-TNM6), never a crash. The composer
    // stays alive afterwards.
    expect(() => {
      lastHandlers().onEvent("member.added", null)
      lastHandlers().onEvent("mention.received", undefined)
    }).not.toThrow()
    expect(document.querySelector(".cx-input")).toBeTruthy()
  })

  it("test_signal_auto_dismisses", async () => {
    vi.useFakeTimers()
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      lastHandlers().onEvent("member.added", { project_id: 101, kind: "added" })
    })
    expect(screen.getByTestId("gc-live-signal")).toBeTruthy()

    await act(async () => {
      vi.advanceTimersByTime(7000)
    })
    expect(screen.queryByTestId("gc-live-signal")).toBeNull()
  })
})

// @vitest-environment jsdom
//
// Presence + typing (ephemeral Realtime, no table) — extends the hook-level
// mock pattern from useRealtimeChannel.dom.test.tsx with Presence
// (`track`/`presenceState`/`on("presence", {event:"sync"}, ...)`) and a
// `typing` Broadcast send, then exercises the SAME real hook wired through
// `ProjectGroupChat` to prove the roster dots + typing indicator render,
// expire, throttle, and go honestly empty when degraded — without touching
// the existing group-turn subscription path.
import * as React from "react"
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { readFileSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

;(globalThis as Record<string, unknown>).React = React

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

type SubscribeCb = (status: string) => void
type BroadcastCb = (arg: { type: "broadcast"; event: string; payload: unknown }) => void
type PresenceEntry = { userId?: unknown; name?: unknown }
type RawPresenceState = Record<string, PresenceEntry[]>

/** Test-local mock of a supabase-js RealtimeChannel, extending the
 *  broadcast/subscribe mock from useRealtimeChannel.dom.test.tsx with
 *  Presence (`track`, `presenceState`, the "sync" listener) and a `send()`
 *  the hook uses for the `typing` broadcast — the two surfaces this ticket
 *  adds, verified against the installed @supabase/supabase-js Presence API. */
class MockChannel {
  topic: string
  opts: unknown
  onBroadcast: BroadcastCb | null = null
  onSubscribe: SubscribeCb | null = null
  onPresenceSync: (() => void) | null = null
  presenceStateValue: RawPresenceState = {}
  trackCalls: unknown[] = []
  sendCalls: Array<{ type: string; event: string; payload: unknown }> = []

  constructor(topic: string, opts: unknown) {
    this.topic = topic
    this.opts = opts
    MockChannel.instances.push(this)
  }

  on(type: string, filter: { event: string }, cb: (...args: unknown[]) => void) {
    if (type === "broadcast") this.onBroadcast = cb as BroadcastCb
    if (type === "presence" && filter.event === "sync") this.onPresenceSync = cb as () => void
    return this
  }

  subscribe(cb: SubscribeCb) {
    this.onSubscribe = cb
    MockChannel.subscribeCount++
    return this
  }

  track(payload: unknown) {
    this.trackCalls.push(payload)
    return Promise.resolve("ok")
  }

  send(args: { type: string; event: string; payload: unknown }) {
    this.sendCalls.push(args)
    return Promise.resolve("ok")
  }

  presenceState() {
    return this.presenceStateValue
  }

  emitBroadcast(event: string, payload: unknown) {
    this.onBroadcast?.({ type: "broadcast", event, payload })
  }

  emitStatus(status: string) {
    this.onSubscribe?.(status)
  }

  /** Drives a full presence reconcile — the mock's stand-in for the server
   *  syncing join/leave/hard-disconnect into one consistent state map. */
  emitPresenceSync(state: RawPresenceState) {
    this.presenceStateValue = state
    this.onPresenceSync?.()
  }

  static instances: MockChannel[] = []
  static subscribeCount = 0
  static removeCount = 0
  static clear() {
    MockChannel.instances = []
    MockChannel.subscribeCount = 0
    MockChannel.removeCount = 0
  }
  static latest(): MockChannel {
    return MockChannel.instances[MockChannel.instances.length - 1]
  }
}

const { setAuthMock, channelMock, removeChannelMock, configuredRef } = vi.hoisted(() => ({
  setAuthMock: vi.fn().mockResolvedValue(undefined),
  channelMock: vi.fn(),
  removeChannelMock: vi.fn(),
  configuredRef: { current: true },
}))

vi.mock("../../../../../lib/supabase/client", () => ({
  isSupabaseConfigured: () => configuredRef.current,
  getSupabase: () => ({
    realtime: { setAuth: setAuthMock },
    channel: channelMock,
    removeChannel: removeChannelMock,
  }),
}))

import { useRealtimeChannel } from "../useRealtimeChannel"

beforeEach(() => {
  MockChannel.clear()
  configuredRef.current = true
  setAuthMock.mockClear()
  channelMock.mockReset()
  channelMock.mockImplementation((topic: string, opts: unknown) => new MockChannel(topic, opts))
  removeChannelMock.mockReset()
  removeChannelMock.mockImplementation(() => {
    MockChannel.removeCount++
    return Promise.resolve({ error: null, status: "ok" })
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe("useRealtimeChannel — presence (AC-1/AC-2)", () => {
  it("test_track_self_and_expose_members: tracks self on subscribe; presenceMembers reflects live join/leave", () => {
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()

    act(() => {
      channel.emitStatus("SUBSCRIBED")
    })
    expect(channel.trackCalls).toEqual([{ userId: "u1", name: "Ada" }])
    expect(result.current.presenceMembers).toEqual([])

    act(() => {
      channel.emitPresenceSync({
        conn1: [{ userId: "u1", name: "Ada" }],
        conn2: [{ userId: "u2", name: "Shristi" }],
      })
    })
    expect(result.current.presenceMembers.map((m) => m.userId).sort()).toEqual(["u1", "u2"])

    // u2 leaves — the server's next sync reconciles down to u1 only.
    act(() => {
      channel.emitPresenceSync({ conn1: [{ userId: "u1", name: "Ada" }] })
    })
    expect(result.current.presenceMembers).toEqual([{ userId: "u1", name: "Ada" }])
  })

  it("test_hard_disconnect_reaps_member: a dropped connection's next sync removes it, no stale entry, no cleanup call needed", () => {
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))

    act(() => {
      channel.emitPresenceSync({
        conn1: [{ userId: "u1", name: "Ada" }],
        conn2: [{ userId: "u2", name: "Shristi" }],
      })
    })
    expect(result.current.presenceMembers).toHaveLength(2)

    // A hard tab close never fires a graceful "leave" from the client — the
    // server reaps the dropped socket and the NEXT sync simply omits it.
    act(() => {
      channel.emitPresenceSync({ conn1: [{ userId: "u1", name: "Ada" }] })
    })
    expect(result.current.presenceMembers).toEqual([{ userId: "u1", name: "Ada" }])
    // No untrack/cleanup call was required — Presence reaped it server-side.
    expect(channel.trackCalls).toEqual([{ userId: "u1", name: "Ada" }])
  })
})

describe("useRealtimeChannel — typing (AC-3/AC-4)", () => {
  it("test_send_typing_broadcasts_event: sendTyping -> a typing broadcast; a receiver adds the sender", () => {
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))

    act(() => {
      result.current.sendTyping({ userId: "u1", name: "Ada" })
    })
    expect(channel.sendCalls).toEqual([{ type: "broadcast", event: "typing", payload: { userId: "u1", name: "Ada" } }])

    // A different sender's typing event is received and rendered as a typer.
    act(() => {
      channel.emitBroadcast("typing", { userId: "u2", name: "Shristi" })
    })
    expect(result.current.typers).toEqual([{ userId: "u2", name: "Shristi" }])
  })

  it("test_typer_expires_after_timeout: no refresh -> gone ~3s later; a refreshed event resets the timer", () => {
    vi.useFakeTimers()
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))

    act(() => channel.emitBroadcast("typing", { userId: "u2", name: "Shristi" }))
    expect(result.current.typers).toHaveLength(1)

    act(() => vi.advanceTimersByTime(2000))
    expect(result.current.typers).toHaveLength(1) // still within the 3s window

    // A refresh at t=2000 resets the clock — it must NOT expire at the
    // original t=3000.
    act(() => channel.emitBroadcast("typing", { userId: "u2", name: "Shristi" }))
    act(() => vi.advanceTimersByTime(2000))
    expect(result.current.typers).toHaveLength(1)

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.typers).toEqual([])
  })

  it("test_self_excluded_from_typers: a sender's own typing event never appears in their own typers", () => {
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))

    act(() => channel.emitBroadcast("typing", { userId: "u1", name: "Ada" }))
    expect(result.current.typers).toEqual([])
  })

  it("test_typing_send_is_throttled: a burst of sendTyping calls in <1s produces one broadcast", () => {
    vi.useFakeTimers()
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))

    act(() => {
      result.current.sendTyping({ userId: "u1", name: "Ada" })
      result.current.sendTyping({ userId: "u1", name: "Ada" })
      result.current.sendTyping({ userId: "u1", name: "Ada" })
    })
    expect(channel.sendCalls).toHaveLength(1)

    act(() => vi.advanceTimersByTime(1000))
    act(() => result.current.sendTyping({ userId: "u1", name: "Ada" }))
    expect(channel.sendCalls).toHaveLength(2)
  })
})

describe("useRealtimeChannel — presence/typing degrade to empty, non-regression (AC-6/AC-8)", () => {
  it("null topic / unconfigured never tracks, presenceMembers and typers start and stay empty, no throw", () => {
    const { result } = renderHook(() => useRealtimeChannel(null, { presence: { self: { userId: "u1", name: "Ada" } } }))
    expect(result.current.presenceMembers).toEqual([])
    expect(result.current.typers).toEqual([])
    expect(() => result.current.sendTyping({ userId: "u1", name: "Ada" })).not.toThrow()
    expect(channelMock).not.toHaveBeenCalled()
  })

  it("a CHANNEL_ERROR drop clears presence/typing to empty without touching onEvent/onReconcile behaviour", () => {
    const onEvent = vi.fn()
    const onReconcile = vi.fn()
    const { result } = renderHook(() =>
      useRealtimeChannel("project:42", { onEvent, onReconcile, presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))
    act(() => channel.emitPresenceSync({ conn1: [{ userId: "u2", name: "Shristi" }] }))
    act(() => channel.emitBroadcast("typing", { userId: "u2", name: "Shristi" }))
    expect(result.current.presenceMembers).toHaveLength(1)
    expect(result.current.typers).toHaveLength(1)

    act(() => channel.emitStatus("CHANNEL_ERROR"))
    expect(result.current.degraded).toBe(true)
    expect(result.current.presenceMembers).toEqual([])
    expect(result.current.typers).toEqual([])
    // The pre-existing turn-broadcast/reconcile contract is untouched.
    expect(onReconcile).toHaveBeenCalledTimes(1)
    act(() => channel.emitBroadcast("turn.created", { id: 1 }))
    expect(onEvent).toHaveBeenCalledWith("turn.created", { id: 1 })
  })
})

// ── ProjectGroupChat — the SAME real hook, wired through the consumer ──
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

import { ProjectGroupChat } from "../ProjectGroupChat"

describe("ProjectGroupChat — presence tracking (roster removed) + typing indicator (AC-5)", () => {
  beforeEach(() => {
    groupTurnsMock.mockReset()
    postGroupTurnMock.mockReset()
    authState = { kind: "authed", user: { id: "u1" } }
  })

  it("test_group_chat_still_tracks_presence_no_roster_renders_typing: self is tracked and a presence sync updates members, but the floating in-transcript roster no longer renders; a typing broadcast still shows the indicator", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId("gc-presence")).toBeNull()

    const channel = MockChannel.latest()
    await act(async () => channel.emitStatus("SUBSCRIBED"))
    // Self is STILL tracked on subscribe — the presence transport is intact
    // (kept available for the top-bar member-avatar lift); only the floating
    // in-transcript roster node was removed.
    expect(channel.trackCalls).toEqual([{ userId: "u1", name: "You" }])

    await act(async () => {
      channel.emitPresenceSync({
        conn1: [{ userId: "u1", name: "You" }],
        conn2: [{ userId: "u2", name: "Shristi" }],
      })
    })
    // The floating roster (`gc-presence` / `gc-presence-member`) is GONE even
    // after a live presence sync — no in-transcript dots render anymore.
    expect(screen.queryByTestId("gc-presence")).toBeNull()
    expect(screen.queryAllByTestId("gc-presence-member")).toHaveLength(0)

    // The typing indicator (a separate dock-above-composer node) is unaffected.
    expect(screen.queryByTestId("gc-typing")).toBeNull()
    await act(async () => {
      channel.emitBroadcast("typing", { userId: "u2", name: "Shristi" })
    })
    expect(await screen.findByTestId("gc-typing")).toBeTruthy()
    expect(screen.getByText(/Shristi is typing/)).toBeTruthy()
  })

  it("test_typing_debounced_from_composer: rapid composer input sends at most one typing broadcast per throttle window", async () => {
    vi.useFakeTimers()
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const channel = MockChannel.latest()
    await act(async () => channel.emitStatus("SUBSCRIBED"))

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "h" } })
      fireEvent.change(textarea, { target: { value: "he" } })
      fireEvent.change(textarea, { target: { value: "hel" } })
    })
    expect(channel.sendCalls.filter((c) => c.event === "typing")).toHaveLength(1)

    await act(async () => vi.advanceTimersByTime(1000))
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hello" } })
    })
    expect(channel.sendCalls.filter((c) => c.event === "typing")).toHaveLength(2)
  })
})

describe("ProjectGroupChat — degraded-absent, turn path unaffected (AC-6/AC-8)", () => {
  beforeEach(() => {
    groupTurnsMock.mockReset()
    postGroupTurnMock.mockReset()
    authState = { kind: "authed", user: { id: "u1" } }
  })

  it("test_degraded_presence_and_typing_absent: unconfigured channel -> no dots/typing, no error, turns still load", async () => {
    configuredRef.current = false
    groupTurnsMock.mockResolvedValueOnce([
      {
        id: 1,
        role: "user",
        content: "hello team",
        author_user_id: "u2",
        author_name: "Shristi",
        author_job_role: null,
        created_at: new Date().toISOString(),
      },
    ])

    expect(() => render(React.createElement(ProjectGroupChat, { projectId: 101 }))).not.toThrow()
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    expect(await screen.findByText("hello team")).toBeTruthy()
    expect(screen.queryByTestId("gc-presence")).toBeNull()
    expect(screen.queryByTestId("gc-typing")).toBeNull()
    expect(screen.queryByTestId("gc-error")).toBeNull()
  })

  it("test_turn_path_and_hook_core_unchanged: a turn.created broadcast still applies via applyTurns with the extended hook", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))
    const channel = MockChannel.latest()
    await act(async () => channel.emitStatus("SUBSCRIBED"))

    await act(async () => {
      channel.emitBroadcast("turn.created", {
        id: 7,
        role: "user",
        content: "live one",
        author_user_id: "u2",
        author_name: "Shristi",
        author_job_role: null,
        created_at: new Date().toISOString(),
      })
    })
    expect(await screen.findByTestId("gc-msg-other")).toBeTruthy()
    expect(screen.getByText("live one")).toBeTruthy()
  })
})

describe("no persistence (AC-7)", () => {
  it("test_no_migration_no_db_write: the presence/typing surfaces add no DB call — only Realtime channel methods", () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const hookSrc = readFileSync(join(here, "..", "useRealtimeChannel.ts"), "utf8")
    // RE-POINTED post-fold: the presence/typing + transport logic moved out of
    // the thin `ProjectGroupChat.tsx` host into `useProjectGroupThread.ts` — scan
    // the engine (where a stray DB write would now live), not the host (which
    // trivially has none, a vacuous scan).
    const consumerSrc = readFileSync(join(here, "..", "useProjectGroupThread.ts"), "utf8")
    const forbidden = [/supabase\.from\(/, /\.rpc\(/, /CREATE TABLE/i, /INSERT INTO/i, /ALTER TABLE/i]
    for (const pattern of forbidden) {
      expect(hookSrc).not.toMatch(pattern)
      expect(consumerSrc).not.toMatch(pattern)
    }
    // The two Realtime primitives presence/typing actually use.
    expect(hookSrc).toMatch(/channel\.track\(/)
    expect(hookSrc).toMatch(/channel\.send\(/)
  })
})

// @vitest-environment jsdom
//
// Regression coverage for the production crash on "My chat with Sprntly":
// a parent screen's own per-user unread-badge subscription and a child
// tab's chat subscription both call `useRealtimeChannel(sameTopic, ...)` in
// the same page. Before the fix, EVERY mount created its own
// `supabase.channel(topic, ...)` — a second channel object for a topic
// realtime-js already has joined/joining trips its "cannot add `presence`
// callbacks ... after `subscribe()`" invariant and throws synchronously,
// which the error boundary then renders as "Something went wrong."
//
// This file (a) proves the mock below faithfully encodes that real
// realtime-js invariant, by tripping it directly with two raw channel
// objects for one topic, and (b) proves the ACTUAL hook never reaches that
// path any more — multiple mounts for the same topic share exactly one
// underlying channel, fan events out to every attached consumer, and
// refcount teardown never leaks or double-removes the shared channel.
import * as React from "react"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

type SubscribeCb = (status: string) => void
type BroadcastCb = (arg: { type: "broadcast"; event: string; payload: unknown }) => void
type PresenceEntry = { userId?: unknown; name?: unknown }
type RawPresenceState = Record<string, PresenceEntry[]>

/** Test-local mock of a supabase-js RealtimeChannel — the same
 *  broadcast/subscribe/presence/track/send surface the two existing
 *  `useRealtimeChannel.*.dom.test.tsx` mocks use, PLUS a topic-scoped guard
 *  that reproduces the real realtime-js invariant this ticket fixes: a
 *  SECOND channel object constructed for a topic that already has a
 *  subscribed/subscribing channel throws the instant it tries to add a
 *  `presence` listener — the exact production crash. */
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
    if (type === "presence" && filter.event === "sync") {
      const activeOwner = MockChannel.activeChannelByTopic.get(this.topic)
      if (activeOwner && activeOwner !== this) {
        // The exact production error text (useRealtimeChannel.ts:~166).
        throw new Error(`cannot add \`presence\` callbacks for realtime:${this.topic} after \`subscribe()\``)
      }
      this.onPresenceSync = cb as () => void
      return this
    }
    if (type === "broadcast") this.onBroadcast = cb as BroadcastCb
    return this
  }

  subscribe(cb: SubscribeCb) {
    this.onSubscribe = cb
    MockChannel.subscribeCount++
    MockChannel.activeChannelByTopic.set(this.topic, this)
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

  emitPresenceSync(state: RawPresenceState) {
    this.presenceStateValue = state
    this.onPresenceSync?.()
  }

  static instances: MockChannel[] = []
  static subscribeCount = 0
  static removeCount = 0
  static activeChannelByTopic = new Map<string, MockChannel>()
  static clear() {
    MockChannel.instances = []
    MockChannel.subscribeCount = 0
    MockChannel.removeCount = 0
    MockChannel.activeChannelByTopic = new Map()
  }
  static latest(): MockChannel {
    return MockChannel.instances[MockChannel.instances.length - 1]
  }
  static countForTopic(topic: string): number {
    return MockChannel.instances.filter((c) => c.topic === topic).length
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
  removeChannelMock.mockImplementation((ch: MockChannel) => {
    MockChannel.removeCount++
    return Promise.resolve({ error: null, status: "ok" })
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("mock sanity — the guard reproduces the real realtime-js invariant", () => {
  it("test_mock_guard_reproduces_original_crash: a second raw channel for an already-subscribed topic throws on presence .on()", () => {
    const topic = "project:7:user:u1"
    const a = new MockChannel(topic, { config: { private: true } })
    a.on("broadcast", { event: "*" }, () => {})
    a.on("presence", { event: "sync" }, () => {})
    a.subscribe(() => {})

    const b = new MockChannel(topic, { config: { private: true } })
    b.on("broadcast", { event: "*" }, () => {})
    expect(() => b.on("presence", { event: "sync" }, () => {})).toThrow(
      "cannot add `presence` callbacks for realtime:project:7:user:u1 after `subscribe()`",
    )
  })
})

describe("useRealtimeChannel — shared per-topic channel (production crash fix)", () => {
  it("test_two_consumers_same_topic_share_one_channel_no_throw: both mount fine, exactly one channel, no invariant trip", () => {
    const topic = "project:7:user:u1"
    const onReconcileA = vi.fn()
    const onReconcileB = vi.fn()

    expect(() => {
      renderHook(() => useRealtimeChannel(topic, { onReconcile: onReconcileA }))
    }).not.toThrow()
    expect(channelMock).toHaveBeenCalledTimes(1)
    expect(MockChannel.countForTopic(topic)).toBe(1)

    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))
    expect(onReconcileA).toHaveBeenCalledTimes(1)

    // The second consumer (e.g. the chat tab mounting after the parent's
    // unread-badge subscription is already live) must NOT create a second
    // channel — before the fix, this is exactly where the original crash
    // happened.
    expect(() => {
      renderHook(() => useRealtimeChannel(topic, { onReconcile: onReconcileB }))
    }).not.toThrow()
    expect(channelMock).toHaveBeenCalledTimes(1)
    expect(MockChannel.countForTopic(topic)).toBe(1)
    // The late joiner gets its OWN "just joined" reconcile via the replay,
    // without re-firing the first consumer's.
    expect(onReconcileB).toHaveBeenCalledTimes(1)
    expect(onReconcileA).toHaveBeenCalledTimes(1)
  })

  it("test_broadcast_and_presence_fan_out_to_both_consumers: one event on the shared channel reaches every attached consumer", () => {
    const topic = "project:7"
    const onEventA = vi.fn()
    const onEventB = vi.fn()

    const { result: resultA } = renderHook(() =>
      useRealtimeChannel(topic, { onEvent: onEventA, presence: { self: { userId: "u1", name: "Ada" } } }),
    )
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))
    expect(channel.trackCalls).toEqual([{ userId: "u1", name: "Ada" }])

    const { result: resultB } = renderHook(() =>
      useRealtimeChannel(topic, { onEvent: onEventB, presence: { self: { userId: "u2", name: "Shristi" } } }),
    )
    // The late joiner replays the already-SUBSCRIBED status and tracks its
    // own presence identity on the SAME shared channel — no second channel,
    // no second .on() registration.
    expect(channel.trackCalls).toEqual([
      { userId: "u1", name: "Ada" },
      { userId: "u2", name: "Shristi" },
    ])
    expect(resultB.current.status).toBe("live")

    act(() => channel.emitBroadcast("turn.created", { id: 1 }))
    expect(onEventA).toHaveBeenCalledWith("turn.created", { id: 1 })
    expect(onEventB).toHaveBeenCalledWith("turn.created", { id: 1 })

    act(() =>
      channel.emitPresenceSync({
        conn1: [{ userId: "u1", name: "Ada" }],
        conn2: [{ userId: "u2", name: "Shristi" }],
      }),
    )
    expect(resultA.current.presenceMembers.map((m) => m.userId).sort()).toEqual(["u1", "u2"])
    expect(resultB.current.presenceMembers.map((m) => m.userId).sort()).toEqual(["u1", "u2"])
  })

  it("test_late_joiner_after_degraded_replays_degraded: a consumer mounting after CHANNEL_ERROR starts degraded, no throw", () => {
    const topic = "project:7:user:u1"
    renderHook(() => useRealtimeChannel(topic, {}))
    const channel = MockChannel.latest()
    act(() => channel.emitStatus("SUBSCRIBED"))
    act(() => channel.emitStatus("CHANNEL_ERROR"))

    const { result } = renderHook(() => useRealtimeChannel(topic, {}))
    expect(result.current.status).toBe("degraded")
    expect(result.current.degraded).toBe(true)
    expect(channelMock).toHaveBeenCalledTimes(1)
  })

  it("test_refcount_teardown_no_leak_no_premature_removal: unmounting one consumer keeps the channel alive for the other; unmounting the last removes it", () => {
    const topic = "project:7:user:u1"
    const first = renderHook(() => useRealtimeChannel(topic, {}))
    const second = renderHook(() => useRealtimeChannel(topic, {}))
    expect(MockChannel.countForTopic(topic)).toBe(1)
    expect(MockChannel.removeCount).toBe(0)

    first.unmount()
    expect(MockChannel.removeCount).toBe(0)

    const channel = MockChannel.latest()
    act(() => channel.emitBroadcast("turn.created", { id: 1 }))
    // The still-mounted second consumer keeps working after the first tore
    // down — no removeChannel yet, refCount > 0.
    expect(MockChannel.removeCount).toBe(0)

    second.unmount()
    expect(MockChannel.removeCount).toBe(1)
    expect(removeChannelMock).toHaveBeenCalledTimes(1)
  })

  it("test_three_consumers_sequential_mount_unmount_refcount: refCount tracks 1 -> 2 -> 3 -> 2 -> 1 -> 0, one create, one remove", () => {
    const topic = "project:7:user:u1"
    const a = renderHook(() => useRealtimeChannel(topic, {}))
    const b = renderHook(() => useRealtimeChannel(topic, {}))
    const c = renderHook(() => useRealtimeChannel(topic, {}))
    expect(channelMock).toHaveBeenCalledTimes(1)

    a.unmount()
    b.unmount()
    expect(MockChannel.removeCount).toBe(0)
    c.unmount()
    expect(MockChannel.removeCount).toBe(1)
    expect(channelMock).toHaveBeenCalledTimes(1)
  })
})

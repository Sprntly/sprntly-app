// @vitest-environment jsdom
//
// Lifecycle + reconnect + degradation tests for the shared
// `useRealtimeChannel` subscription primitive, against a mock Supabase
// channel — mirroring the repo's existing SSE mock pattern
// (GenerationLoadingScreen.sse.dom.test.tsx's MockEventSource: constructor
// captures identity, a driver method lets the test push transitions, and a
// static instance registry tracks connect/disconnect counts).
import * as React from "react"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

type SubscribeCb = (status: string) => void
type BroadcastCb = (arg: { type: "broadcast"; event: string; payload: unknown }) => void

/** Test-local mock of a supabase-js RealtimeChannel — constructor records
 *  topic + opts; `.on()` captures the broadcast handler; `.subscribe()`
 *  captures the status callback and bumps the subscribe counter; `.emit*`
 *  helpers let the test drive transitions/events. */
class MockChannel {
  topic: string
  opts: unknown
  onBroadcast: BroadcastCb | null = null
  onSubscribe: SubscribeCb | null = null

  constructor(topic: string, opts: unknown) {
    this.topic = topic
    this.opts = opts
    MockChannel.instances.push(this)
  }

  on(type: string, _filter: unknown, cb: BroadcastCb) {
    if (type === "broadcast") this.onBroadcast = cb
    return this
  }

  subscribe(cb: SubscribeCb) {
    this.onSubscribe = cb
    MockChannel.subscribeCount++
    return this
  }

  emitBroadcast(event: string, payload: unknown) {
    this.onBroadcast?.({ type: "broadcast", event, payload })
  }

  emitStatus(status: string) {
    this.onSubscribe?.(status)
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
  removeChannelMock.mockImplementation((ch: MockChannel) => {
    MockChannel.removeCount++
    return Promise.resolve({ error: null, status: "ok" })
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("useRealtimeChannel — creation / lifecycle", () => {
  it("test_subscribes_once_to_private_topic: one private channel for the given topic, subscribed once", () => {
    renderHook(() => useRealtimeChannel("project:42", {}))

    expect(channelMock).toHaveBeenCalledTimes(1)
    expect(channelMock).toHaveBeenCalledWith("project:42", { config: { private: true } })
    expect(MockChannel.subscribeCount).toBe(1)
    expect(setAuthMock).toHaveBeenCalledTimes(1)
  })

  it("test_teardown_on_unmount_no_leak: subscribe count == removeChannel count on unmount", () => {
    const { unmount } = renderHook(() => useRealtimeChannel("project:42", {}))
    expect(MockChannel.subscribeCount).toBe(1)
    expect(MockChannel.removeCount).toBe(0)

    unmount()

    expect(MockChannel.removeCount).toBe(1)
    expect(MockChannel.subscribeCount).toBe(MockChannel.removeCount)
  })

  it("test_topic_change_reswaps_channel: old torn down, new subscribed", () => {
    const { rerender } = renderHook(({ topic }: { topic: string }) => useRealtimeChannel(topic, {}), {
      initialProps: { topic: "project:42" },
    })
    const first = MockChannel.latest()
    expect(MockChannel.subscribeCount).toBe(1)
    expect(MockChannel.removeCount).toBe(0)

    rerender({ topic: "project:99" })

    expect(MockChannel.removeCount).toBe(1)
    expect(removeChannelMock).toHaveBeenLastCalledWith(first)
    expect(MockChannel.subscribeCount).toBe(2)
    expect(MockChannel.latest().topic).toBe("project:99")
    expect(MockChannel.latest()).not.toBe(first)
  })

  it("test_stable_handlers_no_rechannel: new inline handlers on re-render, same topic → no new channel", () => {
    const { rerender } = renderHook(
      ({ onEvent }: { onEvent: () => void }) => useRealtimeChannel("project:42", { onEvent }),
      { initialProps: { onEvent: () => {} } },
    )
    expect(channelMock).toHaveBeenCalledTimes(1)

    // A brand new inline callback identity every render — the channel must
    // NOT be rebuilt, since identity keys on topic only.
    rerender({ onEvent: () => {} })
    rerender({ onEvent: () => {} })

    expect(channelMock).toHaveBeenCalledTimes(1)
    expect(MockChannel.subscribeCount).toBe(1)
    expect(MockChannel.removeCount).toBe(0)
  })
})

describe("useRealtimeChannel — behaviour / reconcile", () => {
  it("test_broadcast_invokes_onEvent: emitted broadcast calls onEvent(event, payload) verbatim", () => {
    const onEvent = vi.fn()
    renderHook(() => useRealtimeChannel("project:42", { onEvent }))
    const channel = MockChannel.latest()

    act(() => {
      channel.emitBroadcast("turn.created", { id: 7, body: "hi" })
    })

    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledWith("turn.created", { id: 7, body: "hi" })
  })

  it("test_reconcile_fires_once_per_subscribe: SUBSCRIBED fires onReconcile once; reconnect fires again; not on events", () => {
    const onReconcile = vi.fn()
    const onEvent = vi.fn()
    renderHook(() => useRealtimeChannel("project:42", { onReconcile, onEvent }))
    const channel = MockChannel.latest()

    act(() => {
      channel.emitStatus("SUBSCRIBED")
    })
    expect(onReconcile).toHaveBeenCalledTimes(1)

    act(() => {
      channel.emitBroadcast("turn.created", { id: 1 })
      channel.emitBroadcast("turn.created", { id: 2 })
    })
    expect(onReconcile).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledTimes(2)

    // Drop then reconnect — a second SUBSCRIBED transition reconciles again.
    act(() => {
      channel.emitStatus("CHANNEL_ERROR")
      channel.emitStatus("SUBSCRIBED")
    })
    expect(onReconcile).toHaveBeenCalledTimes(2)
  })
})

describe("useRealtimeChannel — degradation (AD-P22)", () => {
  it("test_channel_error_sets_degraded: CHANNEL_ERROR / TIMED_OUT / CLOSED all flip status to degraded; SUBSCRIBED flips back to live", () => {
    const { result } = renderHook(() => useRealtimeChannel("project:42", {}))
    const channel = MockChannel.latest()

    act(() => {
      channel.emitStatus("SUBSCRIBED")
    })
    expect(result.current.status).toBe("live")
    expect(result.current.degraded).toBe(false)

    for (const bad of ["CHANNEL_ERROR", "TIMED_OUT", "CLOSED"] as const) {
      act(() => {
        channel.emitStatus(bad)
      })
      expect(result.current.status).toBe("degraded")
      expect(result.current.degraded).toBe(true)

      act(() => {
        channel.emitStatus("SUBSCRIBED")
      })
      expect(result.current.status).toBe("live")
      expect(result.current.degraded).toBe(false)
    }
  })

  it("test_null_topic_or_unconfigured_degraded: null topic never subscribes, degraded from the start; does not throw", () => {
    const { result } = renderHook(() => useRealtimeChannel(null, {}))

    expect(result.current.status).toBe("degraded")
    expect(result.current.degraded).toBe(true)
    expect(channelMock).not.toHaveBeenCalled()
    expect(setAuthMock).not.toHaveBeenCalled()
  })

  it("test_null_topic_or_unconfigured_degraded: unconfigured Supabase never subscribes, degraded from the start; does not throw", () => {
    configuredRef.current = false
    const { result } = renderHook(() => useRealtimeChannel("project:42", {}))

    expect(result.current.status).toBe("degraded")
    expect(result.current.degraded).toBe(true)
    expect(channelMock).not.toHaveBeenCalled()
  })
})

// @vitest-environment jsdom
//
// Unit tests for useDesignAgentLiveTerminal — the standalone SSE-terminal
// tracker mounted unconditionally by a host whose own loading-overlay
// instance can unmount mid-generation (see the hook's file header). Verifies
// its independent lifecycle: opens once per prototypeId, survives no
// external "open" concept at all, closes the prior connection before opening
// a new one on an id change, and closes on unmount.
import * as React from "react"
import { renderHook, act, cleanup } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { useDesignAgentLiveTerminal } from "../useDesignAgentLiveTerminal"
import { setAccessTokenProvider } from "../../../lib/api"

/** Minimal EventSource mock — mirrors the shape established in
 *  useIterateRun.test.tsx (constructor captures the URL, `.emit(data)`
 *  simulates a message, `.error()` simulates onerror, static instances
 *  tracking). Copied by convention (test-local), not imported cross-file. */
class MockEventSource {
  url: string
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  error() {
    this.onerror?.(new Event("error"))
  }

  static instances: MockEventSource[] = []
  static clear() {
    MockEventSource.instances = []
  }
  static latest(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

beforeEach(() => {
  MockEventSource.clear()
  setAccessTokenProvider(() => Promise.resolve("test-bearer"))
  vi.stubGlobal("EventSource", MockEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setAccessTokenProvider(() => Promise.resolve(null))
  vi.clearAllMocks()
})

describe("useDesignAgentLiveTerminal — basic lifecycle", () => {
  it("does not construct an EventSource when prototypeId is null", async () => {
    const onTerminal = vi.fn()
    renderHook(() => useDesignAgentLiveTerminal(null, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(MockEventSource.instances.length).toBe(0)
  })

  it("test_use_design_agent_live_terminal_invoked_on_done: constructs an EventSource and invokes onTerminal exactly once on a done event", async () => {
    const onTerminal = vi.fn()
    renderHook(() => useDesignAgentLiveTerminal(9, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    const es = MockEventSource.latest()
    expect(es).toBeTruthy()

    await act(async () => {
      es.emit({ kind: "done" })
    })

    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledWith("done")
    expect(es.close).toHaveBeenCalled()
  })

  it("test_use_design_agent_live_terminal_invoked_on_error: invokes onTerminal exactly once with 'error' on an error-kind message", async () => {
    const onTerminal = vi.fn()
    renderHook(() => useDesignAgentLiveTerminal(10, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    const es = MockEventSource.latest()

    await act(async () => {
      es.emit({ kind: "error" })
    })

    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenCalledWith("error")
  })

  it("a transient onerror does NOT close the connection (native reconnect left to the browser)", async () => {
    const onTerminal = vi.fn()
    renderHook(() => useDesignAgentLiveTerminal(11, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    const es = MockEventSource.latest()

    await act(async () => {
      es.error()
    })

    expect(es.close).not.toHaveBeenCalled()
    expect(onTerminal).not.toHaveBeenCalled()
  })

  it("closes the connection on unmount", async () => {
    const onTerminal = vi.fn()
    const { unmount } = renderHook(() => useDesignAgentLiveTerminal(12, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    const es = MockEventSource.latest()

    unmount()

    expect(es.close).toHaveBeenCalled()
  })
})

describe("useDesignAgentLiveTerminal — id-change edge case", () => {
  it("test_use_design_agent_live_terminal_reopens_on_id_change_closes_prior: the OLD EventSource is closed before the new one for a different id is constructed", async () => {
    const onTerminal = vi.fn()
    const { rerender } = renderHook(
      ({ id }: { id: number }) => useDesignAgentLiveTerminal(id, onTerminal),
      { initialProps: { id: 20 } },
    )
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(MockEventSource.instances.length).toBe(1)
    const first = MockEventSource.latest()
    expect(first.close).not.toHaveBeenCalled()

    rerender({ id: 21 })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(first.close).toHaveBeenCalled()
    expect(MockEventSource.instances.length).toBe(2)
    const second = MockEventSource.latest()
    expect(second).not.toBe(first)
    expect(second.url).not.toBe(first.url)
  })
})

describe("useDesignAgentLiveTerminal — token/token-provider edge cases", () => {
  it("degrades silently (no throw, no EventSource) when getAccessToken resolves null", async () => {
    setAccessTokenProvider(() => Promise.resolve(null))
    const onTerminal = vi.fn()
    renderHook(() => useDesignAgentLiveTerminal(30, onTerminal))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(MockEventSource.instances.length).toBe(0)
  })

  it("degrades silently (no throw) when getAccessToken rejects", async () => {
    setAccessTokenProvider(() => Promise.reject(new Error("token fetch failed")))
    const onTerminal = vi.fn()
    await expect(
      act(async () => {
        renderHook(() => useDesignAgentLiveTerminal(31, onTerminal))
        await Promise.resolve()
        await Promise.resolve()
      }),
    ).resolves.not.toThrow()
    expect(MockEventSource.instances.length).toBe(0)
  })
})

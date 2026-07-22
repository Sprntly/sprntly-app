// @vitest-environment jsdom
//
// DOM tests for GenerationLoadingScreen's SSE-owning effect lifecycle. Split
// into its own `.dom.test.tsx` file (rather than folded into the sibling
// `GenerationLoadingScreen.test.tsx`) because that file is deliberately
// node-env, SSR-only (renderToStaticMarkup — no effects run there); this
// suite needs a real jsdom + mounted-effect lifecycle to observe EventSource
// open/close behaviour, matching this repo's existing convention of a
// dedicated `.cancel.dom.test.tsx` sibling for the same component's other
// DOM-only behaviour.
//
// FAIL-WITHOUT-FIX direction: against the pre-fix code, the SSE effect's deps
// array is `[open, prototypeId, mode]` and `onerror` unconditionally calls
// `close()` — both regression assertions below fail on unfixed code.
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as Record<string, unknown>).React = React

import { GenerationLoadingScreen } from "../GenerationLoadingScreen"
import { setAccessTokenProvider } from "../../../lib/api"

/** Minimal EventSource mock — same shape as useIterateRun.test.tsx's
 *  MockEventSource (constructor captures the URL, `.emit(data)` simulates a
 *  message, `.error()` simulates onerror, static instance tracking). Copied
 *  by convention (test-local), not imported cross-file. */
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

async function flushMicrotasks() {
  // getAccessToken() is itself an async function that awaits a resolved
  // promise, so two microtask yields are needed before EventSource is
  // constructed (same shape as useIterateRun.test.tsx's SSE tests).
  await Promise.resolve()
  await Promise.resolve()
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

describe("GenerationLoadingScreen — SSE effect survives open toggling (AC1)", () => {
  it("test_sse_effect_survives_open_toggle_false_to_true: does NOT close EventSource on an open=true→false→true toggle for the same prototypeId; no second connection opens", async () => {
    const { rerender } = render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 100,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    expect(MockEventSource.instances.length).toBe(1)
    const es = MockEventSource.latest()

    rerender(
      React.createElement(GenerationLoadingScreen, {
        open: false,
        prototypeId: 100,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    expect(es.close).not.toHaveBeenCalled()

    rerender(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 100,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    expect(es.close).not.toHaveBeenCalled()
    // Still exactly one connection — no second EventSource for the same id.
    expect(MockEventSource.instances.length).toBe(1)
  })
})

describe("GenerationLoadingScreen — SSE effect reopens on prototypeId change (AC2)", () => {
  it("test_sse_effect_reopens_on_prototype_id_change: closes the old connection and opens a fresh one for a new prototypeId", async () => {
    const { rerender } = render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 200,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    const first = MockEventSource.latest()
    expect(first.close).not.toHaveBeenCalled()

    rerender(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 201,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })

    expect(first.close).toHaveBeenCalled()
    expect(MockEventSource.instances.length).toBe(2)
    const second = MockEventSource.latest()
    expect(second).not.toBe(first)
  })
})

describe("GenerationLoadingScreen — SSE effect closes on unmount (AC3)", () => {
  it("test_sse_effect_closes_on_unmount: true component teardown closes the connection", async () => {
    const { unmount } = render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 300,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    const es = MockEventSource.latest()
    expect(es.close).not.toHaveBeenCalled()

    unmount()

    expect(es.close).toHaveBeenCalled()
  })
})

describe("GenerationLoadingScreen — onerror no longer closes the connection (AC4)", () => {
  it("test_sse_onerror_does_not_close_connection: a transient onerror does not call close(); esRef-driven state is unaffected", async () => {
    const onLiveTerminal = vi.fn()
    render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 400,
        mode: "generate",
        onLiveTerminal,
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    const es = MockEventSource.latest()

    await act(async () => {
      es.error()
    })

    expect(es.close).not.toHaveBeenCalled()
    expect(onLiveTerminal).not.toHaveBeenCalled()

    // The connection is still the one live instance — a subsequent done event
    // still reaches onLiveTerminal, proving esRef/onmessage wiring survived
    // the onerror firing.
    await act(async () => {
      es.emit({ kind: "done" })
    })
    expect(onLiveTerminal).toHaveBeenCalledTimes(1)
    expect(onLiveTerminal).toHaveBeenCalledWith("done")
  })
})

describe("GenerationLoadingScreen — onLiveTerminal invoked on done/error (AC5)", () => {
  it.each([
    ["done" as const],
    ["error" as const],
  ])(
    "test_sse_on_live_terminal_invoked_on_done_and_error: kind=%s invokes onLiveTerminal exactly once with that kind",
    async (kind) => {
      const onLiveTerminal = vi.fn()
      render(
        React.createElement(GenerationLoadingScreen, {
          open: true,
          prototypeId: kind === "done" ? 501 : 502,
          mode: "generate",
          onLiveTerminal,
        }),
      )
      await act(async () => {
        await flushMicrotasks()
      })
      const es = MockEventSource.latest()

      await act(async () => {
        es.emit({ kind })
      })

      expect(onLiveTerminal).toHaveBeenCalledTimes(1)
      expect(onLiveTerminal).toHaveBeenCalledWith(kind)
    },
  )

  it("onLiveTerminal identity changing every render does not re-run the SSE effect (ref-read, not prop-read)", async () => {
    let renderCount = 0
    function Host({ prototypeId }: { prototypeId: number }) {
      renderCount += 1
      // A fresh closure identity every render — neither host memoizes this in
      // production either.
      return React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId,
        mode: "generate",
        onLiveTerminal: () => {},
      })
    }
    const { rerender } = render(React.createElement(Host, { prototypeId: 600 }))
    await act(async () => {
      await flushMicrotasks()
    })
    expect(MockEventSource.instances.length).toBe(1)
    const es = MockEventSource.latest()

    rerender(React.createElement(Host, { prototypeId: 600 }))
    await act(async () => {
      await flushMicrotasks()
    })

    expect(es.close).not.toHaveBeenCalled()
    expect(MockEventSource.instances.length).toBe(1)
  })
})

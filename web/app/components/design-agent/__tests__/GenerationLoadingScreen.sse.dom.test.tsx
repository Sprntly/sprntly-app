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
// FAIL-WITHOUT-FIX direction: against the pre-fix code, `onerror` unconditionally
// calls `close()` — the AC4 regression assertion below fails on that unfixed
// code.
//
// The stream-cleanup block below (previously named for a DIFFERENT ticket's
// "AC1", asserting the OPPOSITE of what it now asserts) was rewritten: the
// SSE effect's deps were `[prototypeId, mode]`, `open` absent — a hidden
// overlay left its stream connected indefinitely (measured live: one connect,
// no disconnect for 5m41s). `open` now joins the deps AND gates the effect
// body directly, so the stream's liveness cannot diverge from the overlay's
// visibility. Against the pre-fix code (deps `[prototypeId, mode]`, no `open`
// check), the two tests in that block below fail: the first because `close()`
// is never called on `open` going false; the second because reopening reuses
// the still-open connection instead of establishing a fresh one.
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

describe("GenerationLoadingScreen — stream liveness tracks overlay visibility", () => {
  it("test_sse_effect_closes_stream_when_overlay_hides: hiding the overlay (open goes false) closes the EventSource — no connect-without-disconnect", async () => {
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
    expect(es.close).not.toHaveBeenCalled()

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

    expect(es.close).toHaveBeenCalled()
  })

  it("test_sse_effect_reattaches_fresh_connection_on_reopen: reopening for the same prototypeId opens a NEW connection and renders live progress reported on it, not a frozen snapshot of the closed run", async () => {
    const { rerender, container } = render(
      React.createElement(GenerationLoadingScreen, {
        open: true,
        prototypeId: 100,
        mode: "generate",
      }),
    )
    await act(async () => {
      await flushMicrotasks()
    })
    const first = MockEventSource.latest()
    await act(async () => {
      first.emit({ kind: "step", text: "Reading the PRD" })
    })
    expect(container.textContent).toContain("Reading the PRD")

    // Hide, then reopen for the SAME prototypeId.
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
    expect(first.close).toHaveBeenCalled()

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

    // A genuinely fresh connection — not the same (already-closed) instance —
    // and exactly one more than before, not a third/fourth leaked connection.
    expect(MockEventSource.instances.length).toBe(2)
    const second = MockEventSource.latest()
    expect(second).not.toBe(first)

    // A step reported on the fresh connection reaches the live UI, proving
    // the re-attach is genuinely live.
    await act(async () => {
      second.emit({ kind: "step", text: "Planning the layout" })
    })
    expect(container.textContent).toContain("Planning the layout")
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

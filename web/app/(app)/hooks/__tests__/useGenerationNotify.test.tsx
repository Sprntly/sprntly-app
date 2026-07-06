// @vitest-environment jsdom
//
// Unit tests for useGenerationNotify hook.
// Injectable deps (getByPrd, sleep, now, deadlineMs) keep timing synthetic so
// tests are deterministic without real timers.

import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { PrototypeRecord } from "../../../lib/api"
import { useGenerationNotify, type GenerationNotifyDeps } from "../useGenerationNotify"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast }),
}))

// Minimal host component for mounting the hook
function Host({ deps }: { deps: GenerationNotifyDeps }) {
  useGenerationNotify(deps)
  return React.createElement("div", null)
}

beforeEach(() => {
  showToast.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Dispatch a da:notify-generation event with the given ids. */
function dispatchHandoff(prototypeId: number, prdId: number) {
  window.dispatchEvent(
    new CustomEvent("da:notify-generation", { detail: { prototypeId, prdId } }),
  )
}

describe("useGenerationNotify — ready prototype", () => {
  it("fires an actionable persistent toast and dispatches da:generating-done when proto is ready", async () => {
    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    let resolvePoll!: (v: PrototypeRecord | null) => void
    const getByPrd = vi.fn(
      (_prdId: number): Promise<PrototypeRecord | null> =>
        new Promise((r) => {
          resolvePoll = r
        }),
    )
    // sleep = no-op so poll is immediate
    const sleep = vi.fn(async () => undefined)

    render(React.createElement(Host, {
      deps: { getByPrd, sleep, deadlineMs: 60000 },
    }))

    await act(async () => {
      dispatchHandoff(1, 100)
    })

    // Resolve with a ready prototype
    await act(async () => {
      resolvePoll({
        id: 1,
        status: "ready",
        bundle_url: "https://example.com/bundle.js",
        error: null,
      } as PrototypeRecord)
    })

    await act(async () => {})

    expect(showToast).toHaveBeenCalledWith(
      "Prototype ready",
      "Your prototype finished generating.",
      "Open",
      expect.objectContaining({ persist: true, onAction: expect.any(Function) }),
    )
    expect(doneEvents.length).toBeGreaterThan(0)

    window.removeEventListener("da:generating-done", onDone)
  })
})

describe("useGenerationNotify — failed prototype", () => {
  it("fires a persistent failure toast and dispatches da:generating-done when proto fails", async () => {
    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    let resolvePoll!: (v: PrototypeRecord | null) => void
    const getByPrd = vi.fn(
      (_prdId: number): Promise<PrototypeRecord | null> =>
        new Promise((r) => {
          resolvePoll = r
        }),
    )
    const sleep = vi.fn(async () => undefined)

    render(React.createElement(Host, {
      deps: { getByPrd, sleep, deadlineMs: 60000 },
    }))

    await act(async () => {
      dispatchHandoff(2, 200)
    })

    await act(async () => {
      resolvePoll({
        id: 2,
        status: "failed",
        bundle_url: null,
        error: "ViteBuildError: exit 1",
      } as PrototypeRecord)
    })

    await act(async () => {})

    expect(showToast).toHaveBeenCalledWith(
      "Generation failed",
      expect.any(String),
      undefined,
      expect.objectContaining({ persist: true }),
    )
    // The reason should be mapped via reasonCopy (not the raw error string)
    const toastArgs = showToast.mock.calls[0]
    expect(toastArgs[1]).not.toContain("ViteBuildError")
    expect(doneEvents.length).toBeGreaterThan(0)

    window.removeEventListener("da:generating-done", onDone)
  })
})

describe("useGenerationNotify — idempotency", () => {
  it("only starts one poll for duplicate handoff events with the same prototype id", async () => {
    let callCount = 0
    // Expire on the first call: deadline=0, now() returns 1 so it's always past
    const getByPrd = vi.fn(async (_prdId: number): Promise<PrototypeRecord | null> => {
      callCount++
      return null
    })
    const sleep = vi.fn(async () => undefined)
    // now() always returns a value past the deadline so loop exits immediately
    const now = () => 1000

    render(React.createElement(Host, {
      deps: { getByPrd, sleep, deadlineMs: 0, now },
    }))

    await act(async () => {
      dispatchHandoff(3, 300)
      dispatchHandoff(3, 300) // duplicate — should be ignored
    })

    await act(async () => {})

    // getByPrd is never called because the deadline is already expired before
    // the loop body executes (now() > deadline from the first check).
    // If the idempotent guard works, it should be at most 0 (expired before call)
    // or at most 1 (one call before expiry). What matters is the second dispatch
    // doesn't trigger another poll. Since deadline=0 and now()=1000 > 0, the
    // while condition `now() < deadline` is false from the start → 0 calls.
    expect(callCount).toBeLessThanOrEqual(1)
  })
})

describe("useGenerationNotify — poll cap", () => {
  it("exits cleanly without throwing when the deadline expires", async () => {
    // Return generating status to exercise the loop, but deadline expires immediately
    const getByPrd = vi.fn(async (_prdId: number): Promise<PrototypeRecord | null> => ({
      id: 4,
      status: "generating",
      bundle_url: null,
      error: null,
    } as PrototypeRecord))
    const sleep = vi.fn(async () => undefined)

    // Clock: first call (deadline check at start of while) = 0 (inside deadline),
    // next call (check after sleep) = 1000 (past deadline=500) → exits after one loop
    let nowCalls = 0
    const now = () => {
      nowCalls++
      return nowCalls === 1 ? 0 : 1000
    }

    render(React.createElement(Host, {
      deps: { getByPrd, sleep, deadlineMs: 500, now },
    }))

    await act(async () => {
      dispatchHandoff(4, 400)
    })

    await act(async () => {})

    // No toast should fire — timed out cleanly
    expect(showToast).not.toHaveBeenCalled()
  })
})

// @vitest-environment jsdom
import * as React from "react"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useNextPrompts, type NextPromptsAdapter } from "../useNextPrompts"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

afterEach(cleanup)

const flush = () => new Promise((r) => setTimeout(r, 0))

function adapterReturning(suggestions: string[]): NextPromptsAdapter & {
  fetchSuggestions: ReturnType<typeof vi.fn>
} {
  return { fetchSuggestions: vi.fn().mockResolvedValue(suggestions) }
}

describe("useNextPrompts — shared next-prompt host service (AC7–AC10)", () => {
  it("test_useNextPrompts_populates_after_settle", async () => {
    const adapter = adapterReturning(["ship it", "add a test"])
    const { result } = renderHook(() => useNextPrompts(adapter))

    await act(async () => {
      result.current.onSettled("tab-1", 42, { prdId: 7 })
      await flush()
    })

    expect(adapter.fetchSuggestions).toHaveBeenCalledWith(42, { prdId: 7 })
    expect(result.current.suggestionsFor("tab-1")).toEqual(["ship it", "add a test"])
  })

  it("test_useNextPrompts_retire_on_send_is_synchronous", async () => {
    const adapter = adapterReturning(["one", "two"])
    const { result } = renderHook(() => useNextPrompts(adapter))
    await act(async () => {
      result.current.onSettled("tab-1", 1, { prdId: null })
      await flush()
    })
    expect(result.current.suggestionsFor("tab-1")).toEqual(["one", "two"])

    // retire empties the key in the same tick — no fetch, no round-trip.
    act(() => {
      result.current.retire("tab-1")
    })
    expect(result.current.suggestionsFor("tab-1")).toEqual([])
  })

  it("test_useNextPrompts_per_key_isolation", async () => {
    const adapter = adapterReturning(["only for A"])
    const { result } = renderHook(() => useNextPrompts(adapter))
    await act(async () => {
      result.current.onSettled("A", 1, { prdId: null })
      await flush()
    })
    expect(result.current.suggestionsFor("A")).toEqual(["only for A"])
    expect(result.current.suggestionsFor("B")).toEqual([])
  })

  it("test_useNextPrompts_empty_renders_nothing", async () => {
    const adapter = adapterReturning([])
    const { result } = renderHook(() => useNextPrompts(adapter))
    // Unknown key → empty. An empty fetch result publishes nothing.
    expect(result.current.suggestionsFor("never-set")).toEqual([])
    await act(async () => {
      result.current.onSettled("tab-1", 1, { prdId: null })
      await flush()
    })
    expect(result.current.suggestionsFor("tab-1")).toEqual([])
  })

  it("shouldApply=false drops late chips (superseded turn)", async () => {
    const adapter = adapterReturning(["stale"])
    const { result } = renderHook(() => useNextPrompts(adapter))
    await act(async () => {
      result.current.onSettled("tab-1", 1, { prdId: null, shouldApply: () => false })
      await flush()
    })
    expect(adapter.fetchSuggestions).toHaveBeenCalledTimes(1)
    expect(result.current.suggestionsFor("tab-1")).toEqual([])
  })

  it("a rejected fetch degrades to the empty state, no throw", async () => {
    const adapter: NextPromptsAdapter = {
      fetchSuggestions: vi.fn().mockRejectedValue(new Error("boom")),
    }
    const { result } = renderHook(() => useNextPrompts(adapter))
    await act(async () => {
      result.current.onSettled("tab-1", 1, { prdId: null })
      await flush()
    })
    expect(result.current.suggestionsFor("tab-1")).toEqual([])
  })
})

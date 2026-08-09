// @vitest-environment jsdom
//
// Container behaviour for the Usage pane: what happens when the ACTIVE provider
// changes underneath it.
//
// The view tests (UsageSettings.test.tsx) cover rendering for a given provider.
// What only a mounted test can catch is the transition: a provider switch has to
// re-fetch, and the previous provider's payload is still sitting in state while
// that request is in flight. Rendering it would put Claude's numbers under an
// "OpenAI usage" heading — a wrong number under a confident label, which is the
// worst thing a spend dashboard can do.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// These components compile against the CLASSIC JSX runtime, so they read a
// global `React` at render time. It has to exist before the component module is
// imported, and imports are hoisted — hence setting it inside vi.hoisted rather
// than at the top level (model: app/r/__tests__/PublicReportViewer.dom.test.tsx).
vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// Hoisted for the same reason: the vi.mock factory below is lifted above every
// import, so a plain top-level const is not initialised by the time it runs.
const summaryMock = vi.hoisted(() => vi.fn())

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>(
    "../../../../../lib/api",
  )
  return {
    ...actual,
    usageApi: { summary: summaryMock, exportCsv: vi.fn() },
  }
})

import { UsageSettings } from "../UsageSettings"

const ZERO = {
  calls: 0,
  failed_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  est_cost_usd: 0,
}

function summaryFor(provider: string, cost: number, calls: number) {
  return {
    range: { start: "", end: "", days: 30, tz: "UTC" },
    cost_basis: "estimated_from_tokens",
    scope: "customer_key",
    provider,
    totals: { ...ZERO, calls, est_cost_usd: cost },
    daily: [{ ...ZERO, calls, est_cost_usd: cost, day: "2026-08-07" }],
    by_feature: [],
    by_model: [],
    by_provider: [],
    by_operation: [],
  }
}

describe("UsageSettings — following the active provider", () => {
  beforeEach(() => {
    summaryMock.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it("asks the server for the provider it was given", async () => {
    summaryMock.mockResolvedValue(summaryFor("openai", 6.0, 90))

    render(<UsageSettings keyConfigured provider="openai" />)

    await waitFor(() => expect(summaryMock).toHaveBeenCalled())
    expect(summaryMock).toHaveBeenCalledWith(30, "openai")
    expect(await screen.findByText(/OpenAI usage/)).toBeTruthy()
  })

  it("re-fetches when the provider changes", async () => {
    summaryMock.mockResolvedValueOnce(summaryFor("anthropic", 8.25, 120))
    const { rerender } = render(
      <UsageSettings keyConfigured provider="anthropic" />,
    )
    await waitFor(() => expect(screen.getByText(/\$8\.25/)).toBeTruthy())

    summaryMock.mockResolvedValueOnce(summaryFor("openai", 6.0, 90))
    rerender(<UsageSettings keyConfigured provider="openai" />)

    await waitFor(() => expect(screen.getByText(/\$6\.00/)).toBeTruthy())
    expect(summaryMock).toHaveBeenNthCalledWith(2, 30, "openai")
    // The old provider's total is gone, not merely pushed down the page.
    expect(screen.queryByText(/\$8\.25/)).toBeNull()
  })

  it("never shows one provider's figures under the other's heading", async () => {
    summaryMock.mockResolvedValueOnce(summaryFor("anthropic", 8.25, 120))
    const { rerender } = render(
      <UsageSettings keyConfigured provider="anthropic" />,
    )
    await waitFor(() => expect(screen.getByText(/\$8\.25/)).toBeTruthy())

    // Switch to a provider whose request never settles: the pane is now
    // captioned OpenAI while the only payload it holds is Claude's.
    summaryMock.mockReturnValueOnce(new Promise(() => {}))
    rerender(<UsageSettings keyConfigured provider="openai" />)

    expect(screen.getByText(/OpenAI usage/)).toBeTruthy()
    expect(screen.queryByText(/\$8\.25/)).toBeNull()
    expect(screen.getByText(/Loading usage/i)).toBeTruthy()
  })
})

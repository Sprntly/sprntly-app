// @vitest-environment jsdom
//
// Settings → Metrics, after the "KPI tree" framing came off the pane
// (2026-09-03). This pane had no coverage before; these pin the behaviour that
// change introduced and the one thing it must NOT do:
//   - no north-star field and no tree Preview are rendered; the section is
//     titled "Metrics" and the list label drops "Supporting"
//   - two named metrics are enough to save — the old north-star requirement
//     would have made this list unsaveable for exactly the new users routed
//     here now that onboarding no longer picks metrics
//   - saving CARRIES the workspace's existing north star through unchanged
//     rather than clobbering it, since briefs still print it
//   - Metric definitions still attach to the picked metrics
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const saveKpiTreeMock = vi.fn()
const saveDefsMock = vi.fn()
const refreshMock = vi.fn()
let workspace: Record<string, unknown> | null = null

vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace, loading: false, refresh: refreshMock }),
}))
vi.mock("../../../../../lib/onboarding/store", () => ({
  saveKpiTree: (...a: unknown[]) => saveKpiTreeMock(...a),
  saveMetricDefinitions: (...a: unknown[]) => saveDefsMock(...a),
}))

import { KpiSettings } from "../KpiSettings"

function ws(over: Record<string, unknown> = {}) {
  return {
    id: "ws-1",
    onboarding_step: 4,
    industry: "B2B SaaS",
    kpi_tree: { north_star: "", north_star_description: "", metrics: [] },
    metric_definitions: [],
    ...over,
  }
}

beforeEach(() => {
  saveKpiTreeMock.mockResolvedValue(undefined)
  saveDefsMock.mockResolvedValue(undefined)
  refreshMock.mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  workspace = null
})

function saveBtn(): HTMLButtonElement {
  return screen.getByText(/Save metrics/).closest("button") as HTMLButtonElement
}

function metricNameInputs(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll('[data-field="metrics"] input[placeholder="Metric name"]'),
  ) as HTMLInputElement[]
}

describe("KpiSettings — the tree framing is gone", () => {
  it("renders a Metrics section with no north-star field and no Preview", () => {
    workspace = ws()
    render(React.createElement(KpiSettings))

    expect(screen.getByText("Metrics")).not.toBeNull()
    expect(screen.queryByText(/KPI tree/)).toBeNull()
    expect(document.querySelector('[data-field="northStar"]')).toBeNull()
    expect(screen.queryByText(/North star metric/)).toBeNull()
    expect(screen.queryByText("Preview")).toBeNull()
    // The list label no longer calls them "supporting" — nothing on screen
    // for them to support.
    expect(screen.getByText("Metrics (2–4) *")).not.toBeNull()
    expect(screen.queryByText(/Supporting metrics/)).toBeNull()
  })

  it("two named metrics are enough to save — no north star required", async () => {
    // THE regression this pane could have shipped: `canSave` used to demand a
    // north star, and the only step that ever set one was removed.
    workspace = ws()
    render(React.createElement(KpiSettings))

    expect(saveBtn().disabled).toBe(true) // the two starter rows are blank
    const [a, b] = metricNameInputs()
    fireEvent.change(a, { target: { value: "Activation rate" } })
    fireEvent.change(b, { target: { value: "Weekly active teams" } })
    expect(saveBtn().disabled).toBe(false)

    await act(async () => {
      saveBtn().click()
    })
    await waitFor(() => expect(saveKpiTreeMock).toHaveBeenCalledTimes(1))
    const [, tree] = saveKpiTreeMock.mock.calls[0]
    expect(tree.metrics.map((m: { name: string }) => m.name)).toEqual([
      "Activation rate",
      "Weekly active teams",
    ])
    // A brand-new company had no north star; it saves as the empty string it
    // already was — nothing invented.
    expect(tree.north_star).toBe("")
    await waitFor(() => expect(screen.getByText("Metrics saved.")).not.toBeNull())
  })

  it("carries an existing north star through a save unchanged", async () => {
    // Set before this change, still printed in briefs — hiding the field must
    // not become a way of silently deleting it.
    workspace = ws({
      kpi_tree: {
        north_star: "Day-30 retention",
        north_star_description: "Are they still here a month later.",
        metrics: [
          { name: "Activation rate", description: "" },
          { name: "NRR", description: "" },
        ],
      },
    })
    render(React.createElement(KpiSettings))

    await act(async () => {
      saveBtn().click()
    })
    await waitFor(() => expect(saveKpiTreeMock).toHaveBeenCalledTimes(1))
    const [, tree] = saveKpiTreeMock.mock.calls[0]
    expect(tree.north_star).toBe("Day-30 retention")
    expect(tree.north_star_description).toBe("Are they still here a month later.")
  })

  it("metric definitions still attach to the picked metrics", () => {
    workspace = ws({
      kpi_tree: {
        north_star: "",
        north_star_description: "",
        metrics: [
          { name: "Activation rate", description: "" },
          { name: "NRR", description: "" },
        ],
      },
    })
    render(React.createElement(KpiSettings))

    expect(screen.getByText("Metric definitions")).not.toBeNull()
    expect(screen.getByLabelText("Activation rate definition")).not.toBeNull()
    expect(screen.getByLabelText("NRR definition")).not.toBeNull()
    expect(screen.queryByText(/Pick metrics above first/)).toBeNull()
  })

  it("points an un-onboarded company at setup, in the new words", () => {
    workspace = null
    render(React.createElement(KpiSettings))
    expect(screen.getByText(/Set up your metrics/)).not.toBeNull()
    expect(screen.queryByText(/KPI tree/)).toBeNull()
  })
})

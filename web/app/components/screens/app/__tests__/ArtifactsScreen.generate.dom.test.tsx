// @vitest-environment jsdom
//
// Integration test for "New report" on ArtifactsScreen: pick a kind → start an
// ask with the skill pinned → poll → refresh the library. Generation goes through
// the ordinary ask pipeline, so this asserts the pinning and the polling rather
// than a bespoke generate route.
//
// SKIPPED: the "+ New report" button is hidden (SHOW_NEW_REPORT_BUTTON in
// ArtifactsScreen.tsx) — reports are asked for in chat and read in that thread's
// Reports tab. The generation path itself is untouched, so these run again the
// moment that flag flips back; they are kept rather than deleted for exactly
// that reason.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const KINDS = [
  {
    skill: "voice-of-customer-report",
    label: "Voice of Customer",
    blurb: "Themes from your customer conversations.",
    prompt: "Produce a Voice of Customer report from our customer feedback.",
  },
  {
    skill: "competitive-intelligence-review",
    label: "Competitor Analysis",
    blurb: "Where we stand against rivals.",
    prompt: "Produce a competitive intelligence review for our product.",
  },
]

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([]))
const reportKinds = vi.fn((..._a: unknown[]) => Promise.resolve({ kinds: KINDS }))
const askStart = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const askGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  askApi: {
    start: (...a: unknown[]) => askStart(...a),
    get: (...a: unknown[]) => askGet(...a),
  },
  prdApi: { importDoc: vi.fn(), get: vi.fn() },
  evidenceApi: { get: vi.fn() },
  reportsApi: {
    get: vi.fn(),
    share: vi.fn(),
    downloadPdf: vi.fn(),
    kinds: (...a: unknown[]) => reportKinds(...a),
  },
}))

const showToast = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openContentPanel: vi.fn(), openPrdTab: vi.fn(), showToast, contentPanelTab: null,
  }),
}))
vi.mock("../../../../context/ContentContext", () => ({ useContent: () => ({ setContent: vi.fn() }) }))
vi.mock("../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: "acme" }) }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("../../../../lib/evidence-adapter", () => ({ markdownToEvidenceState: () => ({}) }))
vi.mock("../../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen } from "../ArtifactsScreen"

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
afterEach(() => {
  vi.useRealTimers()
  cleanup()
  vi.clearAllMocks()
})

async function openPicker() {
  await act(async () => { render(<ArtifactsScreen />) })
  await act(async () => { fireEvent.click(screen.getByTestId("new-report-button")) })
  await waitFor(() => expect(reportKinds).toHaveBeenCalled())
}

describe.skip("ArtifactsScreen — New report (button hidden)", () => {
  it("offers the server's report kinds", async () => {
    await openPicker()
    const menu = screen.getByTestId("new-report-menu")
    expect(menu.textContent).toContain("Voice of Customer")
    expect(menu.textContent).toContain("Competitor Analysis")
  })

  it("starts an ask with the kind's skill pinned and its seeded prompt", async () => {
    askStart.mockResolvedValue({ ask_id: 77, status: "generating" })
    askGet.mockResolvedValue({ status: "ready" })

    await openPicker()
    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-report-kind="voice-of-customer-report"]') as HTMLElement,
      )
    })

    expect(askStart).toHaveBeenCalledWith(
      KINDS[0].prompt, "acme", { pinned_skill: "voice-of-customer-report" },
    )
  })

  it("shows a pending row while the run is in flight, then refreshes the list", async () => {
    askStart.mockResolvedValue({ ask_id: 77, status: "generating" })
    // One poll still generating, then ready.
    askGet
      .mockResolvedValueOnce({ status: "generating" })
      .mockResolvedValue({ status: "ready" })

    await openPicker()
    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-report-kind="voice-of-customer-report"]') as HTMLElement,
      )
    })

    // The artifact does not exist server-side yet, so this row is the only sign
    // the work is happening.
    const pending = await screen.findByTestId("report-pending-row")
    expect(pending.textContent).toContain("Voice of Customer report")
    expect(pending.textContent).toContain("Generating")

    const listCallsBefore = artifactsList.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(9000) })

    await waitFor(() => expect(screen.queryByTestId("report-pending-row")).toBeNull())
    expect(artifactsList.mock.calls.length).toBeGreaterThan(listCallsBefore)
    expect(showToast.mock.calls.at(-1)?.[0]).toContain("Report ready")
  })

  it("says so when the run fails instead of leaving a row that never lands", async () => {
    askStart.mockResolvedValue({ ask_id: 78, status: "generating" })
    askGet.mockResolvedValue({ status: "error" })

    await openPicker()
    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-report-kind="competitive-intelligence-review"]') as HTMLElement,
      )
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(9000) })

    await waitFor(() => expect(showToast.mock.calls.at(-1)?.[0]).toContain("Report failed"))
    expect(screen.queryByTestId("report-pending-row")).toBeNull()
  })

  it("won't start a second run while one is in flight", async () => {
    askStart.mockResolvedValue({ ask_id: 79, status: "generating" })
    askGet.mockResolvedValue({ status: "generating" })

    await openPicker()
    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-report-kind="voice-of-customer-report"]') as HTMLElement,
      )
    })

    const button = screen.getByTestId("new-report-button") as HTMLButtonElement
    expect(button.textContent).toContain("Generating…")
    expect(button.disabled).toBe(true)
    await act(async () => { fireEvent.click(button) })
    expect(screen.queryByTestId("new-report-menu")).toBeNull()
    expect(askStart).toHaveBeenCalledTimes(1)
  })
})

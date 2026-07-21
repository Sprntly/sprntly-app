// @vitest-environment jsdom
//
// Mount tests for the Ideation screen's "Proposed" tab. The ideation pool is
// the REMAINDER of the weekly analysis: the top 3 ranked insights go into the
// brief, ranks ≥ 4 are sequenced into the pool and the weekly prioritization
// pass shortlists the 25-30 worth showing. The screen renders the visible set
// from GET /v1/ideation (ideationApi.list) and shows an empty state when the
// API returns none — which is exactly the case when no brief has ever been
// generated for the company (the backend gates the list on a brief existing).
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { IdeationItem, IdeationList, CompletedList } from "../../../../lib/api"

const listMock = vi.fn<() => Promise<IdeationList>>()
const completedMock = vi.fn<() => Promise<CompletedList>>()
// The detail popup fetches the idea's evidence trail when it opens.
const detailMock = vi.fn<(id: string) => Promise<unknown>>()

// Mock the API client — the screen reads the ideas through ideationApi.list()
// and the Completed tab through ideationApi.completed().
vi.mock("../../../../lib/api", () => ({
  ideationApi: {
    list: () => listMock(),
    completed: () => completedMock(),
    setStatus: vi.fn(),
    detail: (id: string) => detailMock(id),
    create: vi.fn(),
    reorder: vi.fn(),
  },
}))

// The screen wires Generate PRD / prototype through these; stub them so the
// Proposed/Completed rendering tests mount without a router or real generation.
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromIdeation: vi.fn(),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {}, setContent: vi.fn(), resetContent: vi.fn() }),
}))

// AppLayout pulls in the whole app chrome (sidebar, AI bar, …) — stub it to a
// passthrough so the test mounts only the ideation content.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

// Navigation + company contexts: provide no-op / default implementations so the
// screen renders outside the provider tree.
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: vi.fn() }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

import { IdeationScreen } from "../IdeationScreen"

function item(overrides: Partial<IdeationItem>): IdeationItem {
  return {
    id: "id-1",
    theme_id: "t1",
    title: "Ideation item",
    tag: "something_new",
    rank: 4,
    score: 0.5,
    status: "proposed",
    shortlisted: true,
    reasoning: "below the brief top 3 but worth tracking",
    updated_at: "2026-07-10T00:00:00Z",
    ...overrides,
  }
}

beforeEach(() => {
  listMock.mockReset()
  completedMock.mockReset()
  // Default: Proposed-tab tests don't need completed data, but the mock must
  // resolve in case the tab is exercised.
  completedMock.mockResolvedValue({ items: [], count: 0 })
  detailMock.mockReset()
  detailMock.mockResolvedValue({
    id: "a", theme_id: "t4", title: "Rank-4 idea", tag: "something_broken",
    rank: 4, score: 0.5, status: "proposed", reasoning: "Checkout keeps failing",
    evidence: [], evidence_count: 0, sources: [], is_manual: false,
  })
})

afterEach(() => {
  cleanup()
})

describe("IdeationScreen — Proposed tab", () => {
  it("renders the shortlisted ideas returned by the API (ranks ≥ 4)", async () => {
    listMock.mockResolvedValue({
      items: [
        item({ id: "a", theme_id: "t4", title: "Rank-4 idea", rank: 4 }),
        item({ id: "b", theme_id: "t5", title: "Rank-5 idea", rank: 5 }),
      ],
      count: 2,
    })

    render(<IdeationScreen />)

    await waitFor(() => expect(screen.getByText("Rank-4 idea")).toBeTruthy())
    expect(screen.getByText("Rank-5 idea")).toBeTruthy()
    // The count reflects the loaded list, not a hardcoded number. It surfaces
    // in two legitimate places — the top-bar count badge and the info-bar
    // summary line — so assert at least one carries the live count.
    expect(screen.getAllByText(/2 ideas/).length).toBeGreaterThan(0)
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it("shows an empty state (no items) when the API returns none — i.e. no brief", async () => {
    listMock.mockResolvedValue({ items: [], count: 0 })

    render(<IdeationScreen />)

    await waitFor(() => expect(screen.getByText("No ideas yet")).toBeTruthy())
    // No table rows / seeded placeholder ideas leak through.
    expect(screen.queryByText("Rank-4 idea")).toBeNull()
    // The count badge reads zero — nothing surfaced without an analysis.
    expect(screen.getByText(/0 ideas/)).toBeTruthy()
  })

  it("preserves the empty state when the pool is empty (no demo items)", async () => {
    listMock.mockResolvedValue({ items: [], count: 0 })

    render(<IdeationScreen />)

    await waitFor(() => expect(screen.getByText("No ideas yet")).toBeTruthy())
    // The legacy hardcoded demo titles must NOT appear with an empty pool.
    expect(screen.queryByText("First-Handoff Wizard to lift Day-30 activation")).toBeNull()
    expect(screen.queryByText("Co-authoring nudge to amplify the viral loop")).toBeNull()
  })

  it("opens the idea-detail popup with problem framing + evidence on row click", async () => {
    // Clicking an ideation idea opens a modal (was a right-hand pane) that
    // fetches the idea's evidence trail and frames the problem: why it wasn't
    // prioritized, the pain-point TL;DR, the framing lens, and the quotes.
    listMock.mockResolvedValue({
      items: [item({ id: "a", theme_id: "t4", title: "Rank-4 idea", rank: 4 })],
      count: 1,
    })
    detailMock.mockResolvedValue({
      id: "a", theme_id: "t4", title: "Rank-4 idea", tag: "something_broken",
      rank: 4, score: 0.5, status: "proposed",
      reasoning: "Checkout keeps failing for enterprise buyers",
      evidence: [
        { signal_id: "s1", content: "I gave up and emailed sales instead.",
          kind: "complaint", source_type: "zendesk", provenance: {}, confidence: 0.9 },
      ],
      evidence_count: 3, sources: ["zendesk", "hubspot"], is_manual: false,
    })

    const { container } = render(<IdeationScreen />)
    await waitFor(() => expect(screen.getByText("Rank-4 idea")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByText("Rank-4 idea"))
    })

    const modal = container.querySelector(".bl-modal")
    expect(modal).toBeTruthy()
    expect(modal!.getAttribute("role")).toBe("dialog")
    expect(modal!.querySelector(".bl-detail-title")?.textContent).toBe("Rank-4 idea")
    expect(modal!.querySelector(".bl-detail-rank")?.textContent).toBe("#4")

    // It was fetched by id, and the detail's evidence is rendered as a quote
    // attributed to its source.
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith("a"))
    await waitFor(() =>
      expect(screen.getByText("I gave up and emailed sales instead.")).toBeTruthy())
    expect(screen.getByText("Zendesk")).toBeTruthy()

    // Pain-point TL;DR + the tag-derived framing lens.
    expect(screen.getByText("Checkout keeps failing for enterprise buyers")).toBeTruthy()
    expect(screen.getByText(/Something is broken/)).toBeTruthy()
    // Breadth line counts the WHOLE trail, not just the shown head.
    expect(screen.getByText(/3 signals across 2 sources/)).toBeTruthy()
    // Why it's in Ideation at all.
    expect(screen.getByText(/Not prioritized in the weekly brief/)).toBeTruthy()

    // CTA into the chat → PRD → tickets → prototype funnel.
    expect(screen.getByText("Generate a brief")).toBeTruthy()
  })

  it("shows the weekly-prioritization indicator, not the old framework dropdown", async () => {
    // The RICE/ICE/WSJF "Prioritize by" dropdown was fake (client-side
    // pseudo-scores whose order then PERSISTED) — removed. Its slot now carries
    // a read-only "Prioritized weekly" indicator dated from the newest item's
    // updated_at (the last weekly prioritization run).
    listMock.mockResolvedValue({
      items: [
        item({ id: "a", title: "Rank-4 idea", rank: 4, updated_at: "2026-07-01T12:00:00Z" }),
        item({ id: "b", theme_id: "t5", title: "Rank-5 idea", rank: 5, updated_at: "2026-07-10T12:00:00Z" }),
      ],
      count: 2,
    })

    const { container } = render(<IdeationScreen />)
    await waitFor(() => expect(screen.getByText("Rank-4 idea")).toBeTruthy())

    expect(container.querySelector(".bl-group-select")).toBeNull()   // dropdown gone
    expect(screen.queryByText("Prioritize by")).toBeNull()
    const indicator = screen.getByText(/Prioritized weekly/)
    expect(indicator.textContent).toContain("Jul 10, 2026")           // newest updated_at
  })
})

describe("IdeationScreen — Completed tab", () => {
  async function openCompletedTab() {
    listMock.mockResolvedValue({ items: [], count: 0 })
    render(<IdeationScreen />)
    await waitFor(() => expect(screen.getByText("No ideas yet")).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText("Completed initiatives"))
    })
  }

  it("renders the completed findings returned by the API (prd_created/done)", async () => {
    completedMock.mockResolvedValue({
      items: [
        { theme_id: "t-prd", title: "SSO support", action: "prd_created", last_surfaced_at: "2026-06-10T00:00:00Z" },
        { theme_id: "t-done", title: "Bulk import", action: "done", last_surfaced_at: "2026-06-01T00:00:00Z" },
      ],
      count: 2,
    })

    await openCompletedTab()

    await waitFor(() => expect(screen.getByText("SSO support")).toBeTruthy())
    expect(screen.getByText("Bulk import")).toBeTruthy()
    expect(screen.getByText("PRD created")).toBeTruthy()
    expect(screen.getByText("Done")).toBeTruthy()
    // The legacy hardcoded completed demo titles must NOT leak through.
    expect(screen.queryByText("Epic FHIR read integration")).toBeNull()
    // Top-bar count reflects the fetched list.
    expect(screen.getAllByText(/2 shipped/).length).toBeGreaterThan(0)
  })

  it("shows an empty state when no findings are completed", async () => {
    completedMock.mockResolvedValue({ items: [], count: 0 })

    await openCompletedTab()

    await waitFor(() => expect(screen.getByText("Nothing completed yet")).toBeTruthy())
    expect(screen.queryByText("Epic FHIR read integration")).toBeNull()
    expect(screen.getByText(/0 shipped/)).toBeTruthy()
  })
})

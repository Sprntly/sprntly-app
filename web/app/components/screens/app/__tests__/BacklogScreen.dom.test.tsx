// @vitest-environment jsdom
//
// Mount tests for the Backlog screen's "Proposed" tab. The backlog is the
// REMAINDER of the weekly analysis: the top 3 ranked insights go into the
// brief, ranks ≥ 4 are sequenced into the backlog. The screen renders those
// items from GET /v1/backlog (backlogApi.list) and shows an empty state when
// the API returns none — which is exactly the case when no brief has ever been
// generated for the company (the backend gates the list on a brief existing).
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { BacklogItem, BacklogList, CompletedList } from "../../../../lib/api"

const listMock = vi.fn<() => Promise<BacklogList>>()
const completedMock = vi.fn<() => Promise<CompletedList>>()

// Mock the API client — the screen reads the backlog through backlogApi.list()
// and the Completed tab through backlogApi.completed().
vi.mock("../../../../lib/api", () => ({
  backlogApi: {
    list: () => listMock(),
    completed: () => completedMock(),
    setStatus: vi.fn(),
    create: vi.fn(),
    reorder: vi.fn(),
  },
}))

// The screen wires Generate PRD / prototype through these; stub them so the
// Proposed/Completed rendering tests mount without a router or real generation.
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromBacklog: vi.fn(),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {}, setContent: vi.fn(), resetContent: vi.fn() }),
}))

// AppLayout pulls in the whole app chrome (sidebar, AI bar, …) — stub it to a
// passthrough so the test mounts only the backlog content.
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

import { BacklogScreen } from "../BacklogScreen"

function item(overrides: Partial<BacklogItem>): BacklogItem {
  return {
    id: "id-1",
    theme_id: "t1",
    title: "Backlog item",
    tag: "something_new",
    rank: 4,
    score: 0.5,
    status: "backlog",
    reasoning: "below the brief top 3 but worth tracking",
    ...overrides,
  }
}

beforeEach(() => {
  listMock.mockReset()
  completedMock.mockReset()
  // Default: Proposed-tab tests don't need completed data, but the mock must
  // resolve in case the tab is exercised.
  completedMock.mockResolvedValue({ items: [], count: 0 })
})

afterEach(() => {
  cleanup()
})

describe("BacklogScreen — Proposed tab", () => {
  it("renders the backlog items returned by the API (ranks ≥ 4)", async () => {
    listMock.mockResolvedValue({
      items: [
        item({ id: "a", theme_id: "t4", title: "Rank-4 idea", rank: 4 }),
        item({ id: "b", theme_id: "t5", title: "Rank-5 idea", rank: 5 }),
      ],
      count: 2,
    })

    render(<BacklogScreen />)

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

    render(<BacklogScreen />)

    await waitFor(() => expect(screen.getByText("No backlog yet")).toBeTruthy())
    // No table rows / seeded placeholder ideas leak through.
    expect(screen.queryByText("Rank-4 idea")).toBeNull()
    // The count badge reads zero — nothing surfaced without an analysis.
    expect(screen.getByText(/0 ideas/)).toBeTruthy()
  })

  it("preserves the empty state when the backlog is empty (no demo items)", async () => {
    listMock.mockResolvedValue({ items: [], count: 0 })

    render(<BacklogScreen />)

    await waitFor(() => expect(screen.getByText("No backlog yet")).toBeTruthy())
    // The legacy hardcoded demo titles must NOT appear with an empty backlog.
    expect(screen.queryByText("First-Handoff Wizard to lift Day-30 activation")).toBeNull()
    expect(screen.queryByText("Co-authoring nudge to amplify the viral loop")).toBeNull()
  })

  it("opens the restyled idea-detail panel (design .rbd-*) on row click", async () => {
    // Visual restyle (#475): selecting a backlog idea opens the right-hand
    // detail pane, now styled via the `.bl-detail` / serif `.bl-detail-title`
    // classes. Assert the pane + its design hooks render with the idea's data.
    listMock.mockResolvedValue({
      items: [item({ id: "a", theme_id: "t4", title: "Rank-4 idea", rank: 4 })],
      count: 1,
    })

    const { container } = render(<BacklogScreen />)
    await waitFor(() => expect(screen.getByText("Rank-4 idea")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByText("Rank-4 idea"))
    })

    const detail = container.querySelector(".bl-detail")
    expect(detail).toBeTruthy()
    expect(detail!.querySelector(".bl-detail-title")?.textContent).toBe("Rank-4 idea")
    // Brand rank pill + the three next-step CTAs are present.
    expect(detail!.querySelector(".bl-detail-rank")?.textContent).toBe("#4")
    expect(detail!.querySelectorAll(".bl-detail-btn").length).toBe(3)
  })
})

describe("BacklogScreen — Completed tab", () => {
  async function openCompletedTab() {
    listMock.mockResolvedValue({ items: [], count: 0 })
    render(<BacklogScreen />)
    await waitFor(() => expect(screen.getByText("No backlog yet")).toBeTruthy())
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

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
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { BacklogItem, BacklogList } from "../../../../lib/api"

const listMock = vi.fn<() => Promise<BacklogList>>()

// Mock the API client — the screen reads the backlog through backlogApi.list().
vi.mock("../../../../lib/api", () => ({
  backlogApi: {
    list: () => listMock(),
    setStatus: vi.fn(),
  },
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
})

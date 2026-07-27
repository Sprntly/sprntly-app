// @vitest-environment jsdom
//
// "Held back from your brief" strip — a quiet, best-effort read of the current
// brief's Phase 2A `_backlog` ledger, rendered above the Proposed ideas list.
// It's fetched independently of the ideation shortlist (briefApi.current, not
// ideationApi.list), so these tests mock briefApi directly and assert: (1) the
// per-entry reason copy renders when `_backlog` is present, and (2) nothing
// renders — no error text, no spinner — when it's absent/empty.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { Brief, IdeationList, CompletedList } from "../../../../lib/api"

const listMock = vi.fn<() => Promise<IdeationList>>()
const completedMock = vi.fn<() => Promise<CompletedList>>()
const detailMock = vi.fn<(id: string) => Promise<unknown>>()
const briefCurrentMock = vi.fn<(company?: string) => Promise<Brief>>()

// Mock the API client — ideationApi drives the Proposed/Completed tabs
// (untouched by this test) and briefApi.current drives the held-back strip.
vi.mock("../../../../lib/api", () => ({
  ideationApi: {
    list: () => listMock(),
    completed: () => completedMock(),
    setStatus: vi.fn(),
    detail: (id: string) => detailMock(id),
    create: vi.fn(),
    reorder: vi.fn(),
  },
  briefApi: {
    current: (company?: string) => briefCurrentMock(company),
  },
}))

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromIdeation: vi.fn(),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {}, setContent: vi.fn(), resetContent: vi.fn() }),
}))

vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: vi.fn() }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

import { IdeationScreen } from "../IdeationScreen"

function baseBrief(overrides: Partial<Brief>): Brief {
  return {
    id: 1,
    company: "acme",
    generated_at: "2026-07-20T00:00:00Z",
    week_label: "Week of Jul 20",
    summary_headline: "This week's headline",
    insights: [],
    ...overrides,
  }
}

beforeEach(() => {
  listMock.mockReset()
  completedMock.mockReset()
  detailMock.mockReset()
  briefCurrentMock.mockReset()
  listMock.mockResolvedValue({ items: [], count: 0 })
  completedMock.mockResolvedValue({ items: [], count: 0 })
})

afterEach(() => {
  cleanup()
})

describe("IdeationScreen — Held-back strip", () => {
  it("renders the strip with per-entry reason copy when _backlog is present", async () => {
    // Noon UTC (not midnight) so the expected day doesn't shift under a
    // negative-UTC-offset test-runner timezone — same convention the screen's
    // other date fixtures use (see IdeationScreen.dom.test.tsx).
    const deferredUntil = "2026-08-03T12:00:00Z"
    const expectedBackDate = new Date(deferredUntil).toLocaleDateString(undefined, { month: "short", day: "numeric" })

    briefCurrentMock.mockResolvedValue(
      baseBrief({
        _backlog: [
          { theme_id: "t1", theme_label: "Onboarding checklist confusion", reason: "deferred", deferred_until: deferredUntil },
          { theme_id: "t2", theme_label: "Slow export for large datasets", reason: "carried" },
          { theme_id: "t3", theme_label: "Duplicate webhook deliveries", reason: "sibling_deferred" },
        ],
      })
    )

    await act(async () => {
      render(<IdeationScreen />)
    })

    await waitFor(() => expect(screen.getByText("Held back from your brief this cycle")).toBeTruthy())

    expect(screen.getByText("Onboarding checklist confusion")).toBeTruthy()
    expect(screen.getByText(`Not now — back ${expectedBackDate}`)).toBeTruthy()

    expect(screen.getByText("Slow export for large datasets")).toBeTruthy()
    expect(screen.getByText("Unchanged since last surfaced")).toBeTruthy()

    expect(screen.getByText("Duplicate webhook deliveries")).toBeTruthy()
    expect(screen.getByText("Held with a deferred finding on the same topic")).toBeTruthy()

    expect(briefCurrentMock).toHaveBeenCalledWith("acme")
  })

  it("renders nothing when _backlog is absent", async () => {
    briefCurrentMock.mockResolvedValue(baseBrief({}))

    await act(async () => {
      render(<IdeationScreen />)
    })

    // Give the best-effort fetch a beat to resolve before asserting absence.
    await waitFor(() => expect(briefCurrentMock).toHaveBeenCalled())
    expect(screen.queryByText("Held back from your brief this cycle")).toBeNull()
  })

  it("renders nothing when _backlog is an empty array", async () => {
    briefCurrentMock.mockResolvedValue(baseBrief({ _backlog: [] }))

    await act(async () => {
      render(<IdeationScreen />)
    })

    await waitFor(() => expect(briefCurrentMock).toHaveBeenCalled())
    expect(screen.queryByText("Held back from your brief this cycle")).toBeNull()
  })

  it("renders nothing (no error state) when the brief fetch fails", async () => {
    briefCurrentMock.mockRejectedValue(new Error("network error"))

    await act(async () => {
      render(<IdeationScreen />)
    })

    await waitFor(() => expect(briefCurrentMock).toHaveBeenCalled())
    expect(screen.queryByText("Held back from your brief this cycle")).toBeNull()
    expect(screen.queryByText(/couldn.t/i)).toBeNull()
  })

  it("caps visible rows at 8 and shows a +N more line beyond that", async () => {
    const entries = Array.from({ length: 11 }, (_, i) => ({
      theme_id: `t${i}`,
      theme_label: `Theme ${i}`,
      reason: "dismissed" as const,
    }))
    briefCurrentMock.mockResolvedValue(baseBrief({ _backlog: entries }))

    await act(async () => {
      render(<IdeationScreen />)
    })

    await waitFor(() => expect(screen.getByText("Held back from your brief this cycle")).toBeTruthy())
    expect(screen.getByText("Theme 7")).toBeTruthy()
    expect(screen.queryByText("Theme 8")).toBeNull()
    expect(screen.getByText("+3 more")).toBeTruthy()
  })
})

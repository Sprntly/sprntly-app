// @vitest-environment jsdom
//
// Behavior tests for the Backlog screen's WIRED actions — the buttons that were
// stubs before and now drive the real pipeline / persistence:
//   • Generate PRD       → runPrdGenerationFromBacklog + PRD content panel
//   • Generate prototype → ensure PRD, then navigate to /prototype?...&generate=1
//   • + Add idea         → backlogApi.create (persisted)
//   • Re-sequence        → backlogApi.reorder (persisted, by impact score)
//   • Sync with backlog  → re-fetch backlogApi.list
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { BacklogItem, BacklogList, CompletedList } from "../../../../lib/api"

const listMock = vi.fn<() => Promise<BacklogList>>()
const completedMock = vi.fn<() => Promise<CompletedList>>()
const createMock = vi.fn()
const reorderMock = vi.fn()
const runFromBacklogMock = vi.fn()
const pushMock = vi.fn()
const setContentMock = vi.fn()
const openContentPanelMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  backlogApi: {
    list: () => listMock(),
    completed: () => completedMock(),
    setStatus: vi.fn(),
    create: (title: string, tag: unknown) => createMock(title, tag),
    reorder: (ids: string[]) => reorderMock(ids),
  },
}))

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromBacklog: (id: string) => runFromBacklogMock(id),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}))

vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: openContentPanelMock }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {}, setContent: setContentMock, resetContent: vi.fn() }),
}))

import { BacklogScreen } from "../BacklogScreen"

function item(overrides: Partial<BacklogItem>): BacklogItem {
  return {
    id: "id-1", theme_id: "t1", title: "Backlog item", tag: "something_new",
    rank: 4, score: 0.5, status: "backlog", reasoning: "reason", ...overrides,
  }
}

async function renderWith(items: BacklogItem[]) {
  listMock.mockResolvedValue({ items, count: items.length })
  render(<BacklogScreen />)
  if (items.length) {
    await waitFor(() => expect(screen.getByText(items[0].title)).toBeTruthy())
  } else {
    await waitFor(() => expect(screen.getByText("No backlog yet")).toBeTruthy())
  }
}

async function selectFirstIdea(title: string) {
  await act(async () => { fireEvent.click(screen.getByText(title)) })
}

beforeEach(() => {
  listMock.mockReset()
  completedMock.mockReset().mockResolvedValue({ items: [], count: 0 })
  createMock.mockReset().mockResolvedValue(item({ id: "new-1", title: "Fresh idea" }))
  reorderMock.mockReset().mockResolvedValue({ items: [], count: 0 })
  runFromBacklogMock.mockReset()
  pushMock.mockReset()
  setContentMock.mockReset()
  openContentPanelMock.mockReset()
})

afterEach(() => cleanup())

describe("BacklogScreen — wired actions", () => {
  it("Generate PRD kicks off backlog generation and streams into the PRD panel", async () => {
    runFromBacklogMock.mockResolvedValue({
      ok: true, prd: { prd_id: 42, briefId: 7, insightIndex: 0 },
    })
    await renderWith([item({ id: "a", theme_id: "t4", title: "Bulk onboarding", rank: 4 })])
    await selectFirstIdea("Bulk onboarding")

    await act(async () => { fireEvent.click(screen.getByText("Generate PRD")) })

    await waitFor(() => expect(runFromBacklogMock).toHaveBeenCalledWith("a"))
    // Opens the PRD content panel and pushes the finished PRD into it.
    expect(openContentPanelMock).toHaveBeenCalledWith("prd")
    const lastSet = setContentMock.mock.calls.at(-1)?.[0]
    expect(lastSet.prd).toEqual({ prd_id: 42, briefId: 7, insightIndex: 0 })
    expect(lastSet.prdGenerating).toBe(false)
  })

  it("Generate prototype ensures a PRD then navigates to the prototype route", async () => {
    runFromBacklogMock.mockResolvedValue({
      ok: true, prd: { prd_id: 99, briefId: 7, insightIndex: 0 },
    })
    await renderWith([item({ id: "a", theme_id: "t4", title: "Bulk onboarding", rank: 4 })])
    await selectFirstIdea("Bulk onboarding")

    await act(async () => { fireEvent.click(screen.getByText("Generate prototype")) })

    await waitFor(() => expect(runFromBacklogMock).toHaveBeenCalledWith("a"))
    // Prototype builds from the PRD → route carries ?prd=99 and kicks generation.
    const dest = pushMock.mock.calls.at(-1)?.[0] as string
    expect(dest).toContain("prd=99")
    expect(dest).toContain("generate=1")
  })

  it("+ Add idea persists via backlogApi.create", async () => {
    await renderWith([item({ id: "a", theme_id: "t4", title: "Existing", rank: 4 })])

    await act(async () => { fireEvent.click(screen.getByText("+ Add idea")) })
    const textarea = await screen.findByPlaceholderText(/Title, then a line on the problem/)
    await act(async () => { fireEvent.change(textarea, { target: { value: "My new idea" } }) })
    await act(async () => { fireEvent.click(screen.getByLabelText("Add idea")) })

    // New initiative is the default type → maps to the "something_new" tag.
    await waitFor(() => expect(createMock).toHaveBeenCalledWith("My new idea", "something_new"))
  })

  it("Re-sequence persists a new order by impact score (desc)", async () => {
    await renderWith([
      item({ id: "a", theme_id: "t4", title: "Low", rank: 4, score: 3 }),
      item({ id: "b", theme_id: "t5", title: "High", rank: 5, score: 9 }),
    ])

    await act(async () => { fireEvent.click(screen.getByText("Re-sequence")) })

    // Highest score first → ["b", "a"].
    await waitFor(() => expect(reorderMock).toHaveBeenCalledWith(["b", "a"]))
  })

  it("Sync with backlog re-fetches the list", async () => {
    await renderWith([item({ id: "a", theme_id: "t4", title: "Existing", rank: 4 })])
    expect(listMock).toHaveBeenCalledTimes(1)

    await act(async () => { fireEvent.click(screen.getByText("Sync with backlog")) })

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })
})

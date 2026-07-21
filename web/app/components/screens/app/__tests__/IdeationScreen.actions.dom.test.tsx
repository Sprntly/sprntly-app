// @vitest-environment jsdom
//
// Behavior tests for the Ideation screen's WIRED actions — the buttons that
// drive the real pipeline / persistence:
//   • Generate PRD       → openPrdTab (generateIdeation) handoff
//   • Generate prototype → ensure PRD, then navigate to /prototype?...&generate=1
//   • + Add idea         → ideationApi.create (persisted)
//   • Re-sequence        → ideationApi.reorder (persisted, by impact score)
//   • Sync ideas         → re-fetch ideationApi.list
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
const createMock = vi.fn()
const reorderMock = vi.fn()
const runFromIdeationMock = vi.fn()
const pushMock = vi.fn()
const setContentMock = vi.fn()
const openContentPanelMock = vi.fn()
const openPrdTabMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  ideationApi: {
    list: () => listMock(),
    completed: () => completedMock(),
    setStatus: vi.fn(),
    detail: (id: string) => detailMock(id),
    create: (title: string, tag: unknown) => createMock(title, tag),
    reorder: (ids: string[]) => reorderMock(ids),
  },
}))

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromIdeation: (id: string) => runFromIdeationMock(id),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}))

vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), openContentPanel: openContentPanelMock, openPrdTab: openPrdTabMock }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {}, setContent: setContentMock, resetContent: vi.fn() }),
}))

import { IdeationScreen } from "../IdeationScreen"

function item(overrides: Partial<IdeationItem>): IdeationItem {
  return {
    id: "id-1", theme_id: "t1", title: "Ideation item", tag: "something_new",
    rank: 4, score: 0.5, status: "proposed", shortlisted: true,
    reasoning: "reason", ...overrides,
  }
}

async function renderWith(items: IdeationItem[]) {
  listMock.mockResolvedValue({ items, count: items.length })
  render(<IdeationScreen />)
  if (items.length) {
    await waitFor(() => expect(screen.getByText(items[0].title)).toBeTruthy())
  } else {
    await waitFor(() => expect(screen.getByText("No ideas yet")).toBeTruthy())
  }
}

async function selectFirstIdea(title: string) {
  await act(async () => { fireEvent.click(screen.getByText(title)) })
}

beforeEach(() => {
  listMock.mockReset()
  completedMock.mockReset().mockResolvedValue({ items: [], count: 0 })
  detailMock.mockReset().mockResolvedValue({
    id: "a", theme_id: "t4", title: "Bulk onboarding", tag: "something_new",
    rank: 4, score: 0.5, status: "proposed",
    reasoning: "Admins re-key every seat by hand",
    evidence: [], evidence_count: 0, sources: [], is_manual: false,
  })
  createMock.mockReset().mockResolvedValue(item({ id: "new-1", title: "Fresh idea" }))
  reorderMock.mockReset().mockResolvedValue({ items: [], count: 0 })
  runFromIdeationMock.mockReset()
  pushMock.mockReset()
  setContentMock.mockReset()
  openContentPanelMock.mockReset()
  openPrdTabMock.mockReset()
})

afterEach(() => cleanup())

describe("IdeationScreen — wired actions", () => {
  it("Generate a brief opens the PRD as a new chat tab (openPrdTab handoff)", async () => {
    await renderWith([item({ id: "a", theme_id: "t4", title: "Bulk onboarding", rank: 4 })])
    await selectFirstIdea("Bulk onboarding")

    await act(async () => { fireEvent.click(screen.getByText("Generate a brief")) })

    // An ideation PRD opens as a NEW chat tab (with the Evidence/PRD/Tickets
    // panel over it) — IdeationScreen hands the generation off via openPrdTab,
    // and ChatScreen drives it — instead of streaming into an in-place panel.
    // The seed fields ground the thread in the idea: the pain point becomes the
    // opening insight card, the ask becomes a real user turn.
    await waitFor(() => expect(openPrdTabMock).toHaveBeenCalledTimes(1))
    expect(openPrdTabMock).toHaveBeenCalledWith({
      title: "PRD · Bulk onboarding",
      insightBody: "Admins re-key every seat by hand",
      seedQuery:
        "Generate a brief for \"Bulk onboarding\" — an ideation idea that didn't make this week's top 3.",
      source: { kind: "generateIdeation", ideationItemId: "a" },
    })
    // Generation no longer runs on the ideation surface itself.
    expect(runFromIdeationMock).not.toHaveBeenCalled()
    expect(openContentPanelMock).not.toHaveBeenCalled()
  })

  it("Generate prototype ensures a PRD then navigates to the prototype route", async () => {
    runFromIdeationMock.mockResolvedValue({
      ok: true, prd: { prd_id: 99, briefId: 7, insightIndex: 0 },
    })
    await renderWith([item({ id: "a", theme_id: "t4", title: "Bulk onboarding", rank: 4 })])
    await selectFirstIdea("Bulk onboarding")

    await act(async () => { fireEvent.click(screen.getByText("Generate prototype")) })

    await waitFor(() => expect(runFromIdeationMock).toHaveBeenCalledWith("a"))
    // Prototype builds from the PRD → route carries ?prd=99 and kicks generation.
    const dest = pushMock.mock.calls.at(-1)?.[0] as string
    expect(dest).toContain("prd=99")
    expect(dest).toContain("generate=1")
  })

  it("+ Add idea persists via ideationApi.create", async () => {
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

  it("Sync ideas re-fetches the list", async () => {
    await renderWith([item({ id: "a", theme_id: "t4", title: "Existing", rank: 4 })])
    expect(listMock).toHaveBeenCalledTimes(1)

    await act(async () => { fireEvent.click(screen.getByText("Sync ideas")) })

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it("renders no Voice buttons (removed — voice input was never wired)", async () => {
    await renderWith([item({ id: "a", theme_id: "t4", title: "Existing", rank: 4 })])
    // Chat-bar Voice button was a "coming soon" no-op.
    expect(screen.queryByText("Voice")).toBeNull()

    // Add-idea card Voice button had no handler at all.
    await act(async () => { fireEvent.click(screen.getByText("+ Add idea")) })
    await screen.findByPlaceholderText(/Title, then a line on the problem/)
    expect(screen.queryByText("Voice")).toBeNull()
  })
})

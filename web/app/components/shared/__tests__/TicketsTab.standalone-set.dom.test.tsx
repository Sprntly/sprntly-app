// @vitest-environment jsdom
//
// Standalone ticket sets in the Tickets tab — tickets generated from a chat
// with NO PRD behind them (`ticket_sets`, backend commit 0edeea35).
//
// The load-bearing assertion is the same one ContentPanel.guest-mode makes for
// a different reason: the panel RENDERS the set out of content and CALLS
// NOTHING. There the calls are unauthorized; here they would be a second,
// cost-incurring generation of a set the user asked for once — the insight
// path does not dedupe two phrasings of one ask, so a panel that polled on its
// own would write a second `ticket_sets` row. lib/runTicketSetGeneration owns
// every run; the panel only reads.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn(),
  loadEvidenceByInsight: vi.fn().mockResolvedValue(null),
}))

const runTicketSetGenerationMock = vi.hoisted(() => vi.fn())
vi.mock("../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: (...a: unknown[]) => runTicketSetGenerationMock(...a),
}))

const storiesApiMock = vi.hoisted(() => ({
  generate: vi.fn(),
  generateFromInsight: vi.fn(),
  getJob: vi.fn(),
  getForPrd: vi.fn(),
  getSyncState: vi.fn(),
  getTrackerMeta: vi.fn(),
  triggerSync: vi.fn(),
}))
const ticketSetsApiMock = vi.hoisted(() => ({
  get: vi.fn(),
  getSyncState: vi.fn(),
  getTrackerMeta: vi.fn(),
  triggerSync: vi.fn(),
}))
const ticketDataApiMock = vi.hoisted(() => ({
  getData: vi.fn(),
  summarizeComments: vi.fn(),
  getTransitions: vi.fn(),
  saveFields: vi.fn(),
  saveDescription: vi.fn(),
  setLifecycle: vi.fn(),
  addComment: vi.fn(),
}))
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    storiesApi: storiesApiMock,
    ticketSetsApi: ticketSetsApiMock,
    ticketDataApi: ticketDataApiMock,
  }
})

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), tab: "tickets" as string }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
// Stable across renders, exactly as the real ContentContext memoizes it — a
// fresh fn per render re-fires every effect that lists it.
const setContentMock = vi.fn()
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: setContentMock }),
}))

import { ContentPanel } from "../ContentPanel"

type SetSlice = {
  id: number
  title: string
  stories: unknown[]
  conversationId: number | null
  status: string
  sourceText?: string
  stubs?: unknown[]
  progress?: { done: number; total: number } | null
  error?: string | null
}

const STORY_A = {
  id: "sid-a",
  title: "Retry the failed webhook delivery",
  body: "As a merchant I want failed webhooks retried",
  user_story: "As a merchant I want failed webhooks retried",
  acceptance_criteria: ["Given a 500, When retried, Then it succeeds"],
  priority: "high",
  route: null,
}
// Deliberately thin: no acceptance criteria and no priority. The house rule is
// that an empty field still renders its line.
const STORY_B = {
  id: "sid-b",
  title: "Surface the retry count in the merchant dashboard",
  body: "",
  acceptance_criteria: [],
  priority: null,
  route: null,
}

function renderWithSet(set: SetSlice | null, extra?: Record<string, unknown>) {
  navMock.tab = "tickets"
  contentMock.value = {
    // No PRD anywhere: this is the whole point of a standalone set.
    prd: null,
    prdMeta: null,
    prdGenerating: false,
    evidence: null,
    evidenceGenerating: false,
    detail: null,
    connectedConnectorIds: [],
    threadReports: [],
    threadReportsStatus: "idle",
    reportFocusId: null,
    ticketSet: set,
    ticketSetGenerating: false,
    ticketSetStandalone: false,
    ...extra,
  }
  return render(<ContentPanel />)
}

const READY_SET: SetSlice = {
  id: 7,
  title: "Webhook retries",
  stories: [STORY_A, STORY_B],
  conversationId: 42,
  status: "ready",
  sourceText: "generate tickets for the webhook retry problem",
}

beforeEach(() => {
  ticketSetsApiMock.getSyncState.mockResolvedValue({ configured: false })
  ticketSetsApiMock.getTrackerMeta.mockResolvedValue({
    configured: false, provider: null, destination_id: null, meta: null,
  })
  // No saved overrides — the generated story as-is. `attachments`/`comments`
  // are always arrays on the wire; the detail sets them unconditionally.
  ticketDataApiMock.getData.mockResolvedValue({ attachments: [], comments: [] })
  ticketDataApiMock.summarizeComments.mockResolvedValue({ summary: null })
  ticketDataApiMock.getTransitions.mockRejectedValue(new Error("unbound"))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Tickets tab — standalone ticket set", () => {
  it("renders the set's tickets and never calls story generation", async () => {
    renderWithSet(READY_SET)

    expect(screen.getByText(STORY_A.title)).not.toBeNull()
    expect(screen.getByText(STORY_B.title)).not.toBeNull()
    expect(screen.getByText(/2 implementable tickets/)).not.toBeNull()

    // THE assertion: reading a set costs nothing. A panel-side kick would be a
    // duplicate `ticket_sets` row and a duplicate LLM bill.
    expect(storiesApiMock.generate).not.toHaveBeenCalled()
    expect(storiesApiMock.generateFromInsight).not.toHaveBeenCalled()
    expect(storiesApiMock.getJob).not.toHaveBeenCalled()
    expect(storiesApiMock.getForPrd).not.toHaveBeenCalled()
    expect(runTicketSetGenerationMock).not.toHaveBeenCalled()
  })

  it("shows the Tickets tab with no PRD in scope", () => {
    renderWithSet(READY_SET)
    expect(screen.getByRole("button", { name: /Tickets/i })).not.toBeNull()
  })

  it("names the set in the header, and says the tickets came from the conversation", () => {
    renderWithSet(READY_SET)
    expect(screen.getByRole("heading", { name: "Webhook retries" })).not.toBeNull()
    expect(screen.getByText("2 tickets · from this conversation")).not.toBeNull()
  })

  it("falls back to a header line for an unnamed set rather than collapsing it", () => {
    renderWithSet({ ...READY_SET, title: "" })
    expect(
      screen.getByRole("heading", { name: "Tickets from this conversation" }),
    ).not.toBeNull()
  })

  it("says the chat was deleted when the set outlived its thread", () => {
    renderWithSet(READY_SET, { ticketSetStandalone: true })
    expect(
      screen.getByText("2 tickets · the chat this came from was deleted"),
    ).not.toBeNull()
  })

  it("renders the acceptance-criteria line for a ticket that has none", async () => {
    renderWithSet(READY_SET)
    fireEvent.click(screen.getByText(STORY_B.title))
    // The line is rendered, empty — never dropped because the value is blank.
    expect(await screen.findByText("Acceptance criteria — 0")).not.toBeNull()
    expect(screen.getByText("No acceptance criteria yet.")).not.toBeNull()
  })

  it("keys an open set ticket under the disjoint `set-` namespace", async () => {
    renderWithSet(READY_SET)
    fireEvent.click(screen.getByText(STORY_A.title))
    await waitFor(() => expect(ticketDataApiMock.getData).toHaveBeenCalled())
    // `set-7-sid-a`, never `prd-…` — a set key must not resolve onto a PRD.
    expect(ticketDataApiMock.getData).toHaveBeenCalledWith("set-7-sid-a")
  })

  it("offers Try again when the run came back with no tickets", () => {
    renderWithSet({ ...READY_SET, stories: [] })
    expect(screen.getByTestId("standalone-tickets-empty")).not.toBeNull()
    expect(screen.getByText("No tickets came back from that run")).not.toBeNull()
    const retry = screen.getByRole("button", { name: /Try again/i })
    fireEvent.click(retry)
    expect(runTicketSetGenerationMock).toHaveBeenCalledWith(
      "generate tickets for the webhook retry problem", 42, expect.any(Function),
    )
  })

  it("announces a failed run without ever printing the backend's words", () => {
    renderWithSet({
      ...READY_SET,
      stories: [],
      status: "failed",
      error: "timeout",
    })
    const alert = screen.getByTestId("standalone-tickets-error")
    expect(alert.getAttribute("role")).toBe("alert")
    expect(screen.getByText("The ticket run took too long and stopped.")).not.toBeNull()
    // Not the raw message, not a status code, not a stack frame.
    expect(alert.textContent).not.toMatch(/Error|Traceback|50\d|ReadTimeout/)
    expect(screen.getByRole("button", { name: /Try again/i })).not.toBeNull()
  })

  it("offers no retry for a set that is gone, and no access language", () => {
    renderWithSet({ ...READY_SET, stories: [], status: "failed", error: "notfound" })
    const alert = screen.getByTestId("standalone-tickets-error")
    expect(screen.getByText("This ticket set isn’t available.")).not.toBeNull()
    // A foreign tenant's set and a deleted one are the same 404 by design —
    // the copy must not let them be told apart.
    expect(alert.textContent).not.toMatch(/permission|access|not yours|forbidden/i)
    expect(screen.queryByRole("button", { name: /Try again/i })).toBeNull()
  })

  it("shows the working state while a set is being written, and still calls nothing", () => {
    renderWithSet(
      { ...READY_SET, title: "", stories: [], status: "generating" },
      { ticketSetGenerating: true },
    )
    expect(screen.getByTestId("standalone-tickets-generating")).not.toBeNull()
    expect(
      screen.getByText("Turning this conversation into tickets…"),
    ).not.toBeNull()
    expect(storiesApiMock.generate).not.toHaveBeenCalled()
    expect(storiesApiMock.getJob).not.toHaveBeenCalled()
  })

  it("streams the planned roster with a banner instead of a blank pane", () => {
    renderWithSet(
      {
        ...READY_SET,
        stories: [STORY_A],
        status: "generating",
        stubs: [{ title: "Backfill the retry counter" }],
        progress: { done: 1, total: 2 },
      },
      { ticketSetGenerating: true },
    )
    expect(screen.getByTestId("tickets-streaming")).not.toBeNull()
    expect(screen.getByText("Writing tickets · 1 ready so far")).not.toBeNull()
    expect(screen.getByTestId("ticket-skeleton")).not.toBeNull()
  })

  it("uses the singular for a one-ticket set", () => {
    renderWithSet({ ...READY_SET, stories: [STORY_A] })
    expect(screen.getByText("1 ticket · from this conversation")).not.toBeNull()
  })

  it("reads the set's tracker-sync state, not a PRD's", async () => {
    renderWithSet(READY_SET)
    await waitFor(() => expect(ticketSetsApiMock.getSyncState).toHaveBeenCalledWith(7))
    expect(storiesApiMock.getSyncState).not.toHaveBeenCalled()
  })
})

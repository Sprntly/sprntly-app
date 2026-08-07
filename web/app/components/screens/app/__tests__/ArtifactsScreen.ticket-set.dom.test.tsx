// @vitest-environment jsdom
//
// A standalone ticket set in the Artifacts library.
//
// Structurally the report row's story, one artifact over: a set's home is the
// chat that produced it, so clicking one hands off to that thread (the ordinary
// `sprntly_resume_conv` payload) and asks ChatScreen to land the panel's Tickets
// tab on the set. Only a set whose chat is gone opens in the panel with no
// thread under it.
//
// The row itself has two rules worth locking: the COUNT is the affordance (it is
// what distinguishes one set from another at a glance), and a deleted chat omits
// the "from …" clause rather than inventing a label for a thread that no longer
// exists.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const SET_ROW = {
  type: "ticket_set" as const,
  id: 7,
  title: "Checkout drop-off tickets",
  status: "ready" as const,
  created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(), // 3h ago
  ticket_count: 6,
  source: {
    conversation_id: 88,
    conversation_title: "Checkout drop-off",
    question: "break this into tickets",
  },
  open: { ticket_set_id: 7 },
}

/** The chat was deleted: `on delete set null` leaves the id but no title. */
const ORPHANED_ROW = {
  ...SET_ROW,
  id: 8,
  source: { ...SET_ROW.source, conversation_id: 99, conversation_title: null },
  open: { ticket_set_id: 8 },
}

/** Born outside a chat entirely. */
const UNATTACHED_ROW = {
  ...SET_ROW,
  id: 9,
  source: { ...SET_ROW.source, conversation_id: null, conversation_title: null },
  open: { ticket_set_id: 9 },
}

const GENERATING_ROW = {
  ...SET_ROW,
  id: 10,
  title: "",
  status: "generating" as const,
  ticket_count: 0,
  created_at: new Date().toISOString(),
  open: { ticket_set_id: 10 },
}

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([SET_ROW]))
const loadTicketSet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>({ ok: true }))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  prdApi: { importDoc: vi.fn(), get: vi.fn() },
  evidenceApi: { get: vi.fn() },
  ticketSetsApi: { get: vi.fn() },
}))
vi.mock("../../../../lib/runTicketSetGeneration", () => ({
  loadTicketSet: (...a: unknown[]) => loadTicketSet(...a),
  runTicketSetGeneration: vi.fn(),
}))

const setContent = vi.fn()
const openContentPanel = vi.fn()
const openPrdTab = vi.fn()
const openReportTab = vi.fn()
const openTicketSetTab = vi.fn()
const showToast = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openContentPanel, openPrdTab, openReportTab, openTicketSetTab, showToast,
    contentPanelTab: null,
  }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("../../../../lib/evidence-adapter", () => ({ markdownToEvidenceState: () => ({}) }))
vi.mock("../../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen, ArtifactsView } from "../ArtifactsScreen"
import type { ArtifactItem } from "../../../../lib/api"

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

async function renderAndClick(row: unknown = SET_ROW) {
  artifactsList.mockResolvedValue([row])
  await act(async () => { render(<ArtifactsScreen />) })
  await waitFor(() => expect(artifactsList).toHaveBeenCalled())
  const el = await waitFor(() =>
    document.querySelector('[data-artifact-type="ticket_set"]') as HTMLElement,
  )
  await act(async () => { fireEvent.click(el) })
  return el
}

function viewMarkup(items: ArtifactItem[]) {
  return renderToStaticMarkup(
    React.createElement(ArtifactsView, {
      items, filter: "all" as const, loading: false,
      onFilterChange: () => {}, onOpen: () => {},
    }),
  )
}

describe("ArtifactsScreen — the ticket-set row", () => {
  it("renders the TICKETS badge, the count and the chat it came from", async () => {
    const html = viewMarkup([SET_ROW as ArtifactItem])
    expect(html).toContain(">TICKETS<")
    expect(html).toContain("Checkout drop-off tickets")
    // The count leads the source line — it is the row's affordance.
    expect(html).toContain("6 tickets · from Checkout drop-off · 3h ago")
  })

  it("says '1 ticket', not '1 tickets'", () => {
    const one = { ...SET_ROW, ticket_count: 1 } as ArtifactItem
    expect(viewMarkup([one])).toContain("1 ticket ·")
  })

  it("omits the 'from' clause when the chat was deleted, inventing nothing", () => {
    const html = viewMarkup([ORPHANED_ROW as ArtifactItem])
    // The row still renders, with its count…
    expect(html).toContain("6 tickets")
    // …and no fabricated thread name.
    expect(html).not.toContain("from ")
  })

  it("falls back to the panel's own words for a set with no title yet", () => {
    // `ticket_sets.title` is empty until the naming leg runs; a blank bold line
    // is not an option, and a per-surface invention would contradict the panel.
    const untitled = { ...SET_ROW, title: "" } as ArtifactItem
    expect(viewMarkup([untitled])).toContain("Tickets from this conversation")
  })

  it("marks a set still being written as not clickable", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [GENERATING_ROW as ArtifactItem], filter: "all" as const, loading: false,
        onFilterChange: () => {}, onOpen,
      }),
    )
    const row = container.querySelector('[data-artifact-type="ticket_set"]') as HTMLDivElement
    expect(row.textContent).toContain("Writing tickets")
    expect(row.textContent).not.toContain("0 tickets")
    expect(row.getAttribute("data-clickable")).toBe("false")
    expect(row.getAttribute("role")).toBeNull()
    fireEvent.click(row)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it("renders only ticket sets when the Tickets chip is selected", () => {
    const html = renderToStaticMarkup(
      React.createElement(ArtifactsView, {
        items: [SET_ROW as ArtifactItem], filter: "ticket_set" as const, loading: false,
        onFilterChange: () => {}, onOpen: () => {},
      }),
    )
    expect(html).toContain(">TICKETS<")
  })
})

describe("ArtifactsScreen — opening a ticket set attached to a chat", () => {
  it("hands off to the set's own thread and asks for it in the panel", async () => {
    await renderAndClick()

    expect(openTicketSetTab).toHaveBeenCalledWith({ conversationId: 88, ticketSetId: 7 })
    // The set is fetched by the thread's panel, not here.
    expect(loadTicketSet).not.toHaveBeenCalled()
  })

  it("writes the ordinary resume hand-off so ChatScreen reopens that chat", async () => {
    await renderAndClick()

    const payload = JSON.parse(localStorage.getItem("sprntly_resume_conv") ?? "{}")
    expect(payload.dbId).toBe(88)
    expect(payload.title).toBe("Checkout drop-off")
  })
})

describe("ArtifactsScreen — a ticket set with no chat to open", () => {
  it("opens an unattached set in the panel, flagged standalone", async () => {
    await renderAndClick(UNATTACHED_ROW)

    expect(openTicketSetTab).not.toHaveBeenCalled()
    // Stated, never inferred from a null conversation id — a brand-new chat tab
    // has one of those too (see reportFocusStandalone).
    expect(setContent).toHaveBeenCalledWith({ ticketSetStandalone: true })
    expect(openContentPanel).toHaveBeenCalledWith("tickets")
    expect(loadTicketSet).toHaveBeenCalledWith(9, setContent)
  })

  it("does the same when the set's chat was deleted", async () => {
    await renderAndClick(ORPHANED_ROW)

    expect(openTicketSetTab).not.toHaveBeenCalled()
    expect(localStorage.getItem("sprntly_resume_conv")).toBeNull()
    expect(loadTicketSet).toHaveBeenCalledWith(8, setContent)
  })

  it("marks the clicked row as the selected one", async () => {
    const row = await renderAndClick(UNATTACHED_ROW)
    await waitFor(() => expect(row.getAttribute("data-active")).toBe("true"))
  })
})

describe("ArtifactsScreen — a set does not follow the reader onto the next artifact", () => {
  it("clears the set when a non-ticket artifact is opened", async () => {
    const PRD_ROW = {
      type: "prd" as const,
      id: 1,
      title: "Checkout revamp",
      status: "ready",
      created_at: new Date().toISOString(),
      source: { brief_id: 10, week_label: "Week of May 20", insight_index: 0 },
      open: { brief_id: 10, insight_index: 0, prd_id: 1 },
    }
    artifactsList.mockResolvedValue([PRD_ROW])
    await act(async () => { render(<ArtifactsScreen />) })
    const el = await waitFor(() =>
      document.querySelector('[data-artifact-type="prd"]') as HTMLElement,
    )
    await act(async () => { fireEvent.click(el) })

    // `content.ticketSet` decides whether the Tickets tab EXISTS, so a set left
    // behind would sit on this PRD's Tickets tab in place of its own tickets.
    expect(setContent).toHaveBeenCalledWith({
      ticketSet: null, ticketSetGenerating: false, ticketSetStandalone: false,
    })
  })
})

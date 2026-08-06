// @vitest-environment jsdom
//
// Which tabs a standalone ticket set is allowed to put on the panel.
//
// THE RULE the panel already enforces is "a tab exists only when this thread
// actually has that artifact". A set generated from a chat with no PRD has
// tickets and nothing else, so the Tickets tab appears — and the PRD and
// Evidence tabs must NOT, because clicking the PRD tab IS a request to
// generate one (handleTabClick → useResolvePrd), which would write a document
// nobody asked for off a tab that should never have been there. The prototype
// bottom bar goes for the same reason: it generates from a PRD.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
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

const runPrdGenerationMock = vi.hoisted(() => vi.fn())
vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...a: unknown[]) => runPrdGenerationMock(...a),
}))

vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn(),
  loadEvidenceByInsight: vi.fn().mockResolvedValue(null),
}))

vi.mock("../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: vi.fn(),
}))

// The real CTA reaches for WorkspaceProvider, which this panel-level test has
// no reason to stand up. Stubbed to the same test id the panel's bottom bar
// renders, so "the bar is present / withheld" stays the thing under test.
vi.mock("../../design-agent/GeneratePrototypeCTA", () => ({
  GeneratePrototypeCTA: ({ render: r }: {
    render: (a: { label: string; onClick: () => void; disabled: boolean }) => React.ReactNode
  }) => r({ label: "Generate Prototype", onClick: () => {}, disabled: false }),
}))

const storiesApiMock = vi.hoisted(() => ({
  generate: vi.fn(),
  generateFromInsight: vi.fn(),
  getJob: vi.fn(),
  getForPrd: vi.fn(),
  getSyncState: vi.fn(),
  getTrackerMeta: vi.fn(),
}))
const ticketSetsApiMock = vi.hoisted(() => ({
  get: vi.fn(),
  getSyncState: vi.fn(),
  getTrackerMeta: vi.fn(),
  triggerSync: vi.fn(),
}))
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return { ...actual, storiesApi: storiesApiMock, ticketSetsApi: ticketSetsApiMock }
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
const setContentMock = vi.fn()
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: setContentMock }),
}))

import { ContentPanel } from "../ContentPanel"

const TICKET_SET = {
  id: 7,
  title: "Webhook retries",
  stories: [
    { id: "sid-a", title: "Retry the failed webhook", body: "", acceptance_criteria: [], priority: null, route: null },
  ],
  conversationId: 42,
  status: "ready",
  sourceText: "generate tickets for webhook retries",
}

function renderPanel(over?: Record<string, unknown>) {
  navMock.tab = "tickets"
  contentMock.value = {
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
    ticketSet: TICKET_SET,
    ticketSetGenerating: false,
    ticketSetStandalone: false,
    ...over,
  }
  return render(<ContentPanel />)
}

beforeEach(() => {
  ticketSetsApiMock.getSyncState.mockResolvedValue({ configured: false })
  ticketSetsApiMock.getTrackerMeta.mockResolvedValue({
    configured: false, provider: null, destination_id: null, meta: null,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ContentPanel — tabs for a standalone ticket set", () => {
  it("shows the Tickets tab", () => {
    renderPanel()
    expect(screen.getByRole("button", { name: /Tickets/i })).not.toBeNull()
  })

  it("shows neither the PRD nor the Evidence tab", () => {
    renderPanel()
    expect(screen.queryByRole("button", { name: /^PRD$/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /Evidence/i })).toBeNull()
  })

  it("withholds the prototype bottom bar — there is no PRD to build from", () => {
    renderPanel()
    expect(screen.queryByTestId("tickets-footer-prototype-cta")).toBeNull()
  })

  it("does not arm the Share menu on a PRD left over from a previous open", () => {
    // A stale `content.prd` must not make Share/PDF act on a document the
    // reader has navigated away from (lib/panelPrdScope.prdInScopeFor).
    renderPanel({
      prd: { prd_id: 3, title: "Some other PRD", metaLine: "", sections: [], source: "brief" },
    })
    const share = screen.getByRole("button", { name: /share/i })
    expect(share).toHaveProperty("disabled", true)
  })

  it("names the set — not a PRD — in the panel header", () => {
    renderPanel()
    expect(screen.getByText("Tickets · Webhook retries")).not.toBeNull()
  })

  it("keeps the PRD and Evidence tabs — and the prototype bar — when a PRD IS in scope", async () => {
    storiesApiMock.getForPrd.mockResolvedValue({ status: "ready", fresh: true, stories: [] })
    storiesApiMock.getSyncState.mockResolvedValue({ configured: false })
    storiesApiMock.getTrackerMeta.mockResolvedValue({
      configured: false, provider: null, destination_id: null, meta: null,
    })
    renderPanel({
      ticketSet: null,
      prd: { prd_id: 3, title: "Retention PRD", metaLine: "", sections: [], source: "brief" },
      prdMeta: { briefId: 9, insightIndex: 1 },
    })
    expect(screen.getByRole("button", { name: /^PRD$/i })).not.toBeNull()
    expect(screen.getByRole("button", { name: /Evidence/i })).not.toBeNull()
    expect(screen.getByTestId("tickets-footer-prototype-cta")).not.toBeNull()
  })
})

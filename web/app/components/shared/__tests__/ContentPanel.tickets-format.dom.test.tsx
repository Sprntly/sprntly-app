// @vitest-environment jsdom
//
// The Tickets tab's Format control — the PRD footer's twin, on the tickets
// bottom bar (feat: switch a ticket set's format from the panel and from chat).
//
// What must hold:
//   * the bar names the CURRENT format — the slice's stamp for a standalone
//     set, the `for-prd` cache's stamp for a PRD's tickets — and "Sprntly
//     built-in" for an unstamped set, never a wrong name.
//   * the switch goes through POST /v1/stories/change-template with the RIGHT
//     target shape ({ticketSetId} vs {prdId}) and re-renders from the
//     response: a set republishes its slice; a PRD's tickets bump
//     `ticketsRefreshNonce` so the tab re-reads its (already persisted) cache.
//   * a set still generating gets NO toggle — nothing to re-format yet.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

vi.mock("../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: vi.fn(),
}))

// The prototype CTA drags in useWorkspace/useGeneratePrototype — out of scope
// here (this suite is about the Format control that shares the bar with it).
vi.mock("../../design-agent/GeneratePrototypeCTA", () => ({
  GeneratePrototypeCTA: () =>
    React.createElement("div", { "data-testid": "prototype-cta-stub" }),
}))

const storiesApiMock = vi.hoisted(() => ({
  generate: vi.fn(),
  generateFromInsight: vi.fn(),
  getJob: vi.fn(),
  getForPrd: vi.fn(),
  getSyncState: vi.fn(),
  getTrackerMeta: vi.fn(),
  triggerSync: vi.fn(),
  changeTemplate: vi.fn(),
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
const artifactTemplatesApiMock = vi.hoisted(() => ({
  list: vi.fn(),
}))
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    storiesApi: storiesApiMock,
    ticketSetsApi: ticketSetsApiMock,
    ticketDataApi: ticketDataApiMock,
    artifactTemplatesApi: artifactTemplatesApiMock,
  }
})

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), showToast: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: "tickets",
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
    showToast: navMock.showToast,
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

const STORY = {
  id: "sid-a",
  title: "Retry the failed webhook delivery",
  body: "As a merchant I want failed webhooks retried",
  acceptance_criteria: [],
  priority: null,
  route: null,
}

const ACME = {
  id: "tpl-tick",
  name: "Acme Tickets",
  artifact_type: "tickets",
  is_active: false,
  compile_status: "ready",
}

function baseContent(extra: Record<string, unknown>) {
  return {
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
    ticketSet: null,
    ticketSetGenerating: false,
    ticketSetStandalone: false,
    ...extra,
  }
}

function renderPanel(extra: Record<string, unknown>) {
  contentMock.value = baseContent(extra)
  return render(<ContentPanel />)
}

const READY_SET = {
  id: 7,
  title: "Webhook retries",
  stories: [STORY],
  conversationId: 42,
  status: "ready",
  sourceText: "make tickets",
  artifactTemplateId: null,
  artifactTemplateName: null,
}

beforeEach(() => {
  ticketSetsApiMock.getSyncState.mockResolvedValue({ configured: false })
  ticketSetsApiMock.getTrackerMeta.mockResolvedValue({
    configured: false, provider: null, destination_id: null, meta: null,
  })
  storiesApiMock.getSyncState.mockResolvedValue({ configured: false })
  storiesApiMock.getTrackerMeta.mockResolvedValue({
    configured: false, provider: null, destination_id: null, meta: null,
  })
  ticketDataApiMock.getData.mockResolvedValue({ attachments: [], comments: [] })
  artifactTemplatesApiMock.list.mockResolvedValue({ templates: [ACME] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Tickets tab — the Format control", () => {
  it("names the built-in for an unstamped standalone set", async () => {
    renderPanel({ ticketSet: READY_SET })
    const toggle = await screen.findByTestId("tickets-format-toggle")
    expect(toggle.textContent).toContain("Format: Sprntly built-in")
  })

  it("names the stamped format off the slice, with no extra fetch", async () => {
    renderPanel({
      ticketSet: {
        ...READY_SET,
        artifactTemplateId: "tpl-tick",
        artifactTemplateName: "Acme Tickets",
      },
    })
    const toggle = await screen.findByTestId("tickets-format-toggle")
    expect(toggle.textContent).toContain("Format: Acme Tickets")
    expect(storiesApiMock.getForPrd).not.toHaveBeenCalled()
  })

  it("withholds the toggle while the set is still generating", () => {
    renderPanel({
      ticketSet: { ...READY_SET, status: "generating", stories: [] },
      ticketSetGenerating: true,
    })
    expect(screen.queryByTestId("tickets-format-toggle")).toBeNull()
  })

  it("switches a standalone set: {ticketSetId} target, slice republished", async () => {
    const relaid = [{ ...STORY, description_layout: [{ label: "Summary", source: "what" }] }]
    storiesApiMock.changeTemplate.mockResolvedValue({
      status: "ready", artifact_template_id: "tpl-tick",
      artifact_template_name: "Acme Tickets", stories: relaid,
    })
    renderPanel({ ticketSet: READY_SET })

    fireEvent.click(await screen.findByTestId("tickets-format-toggle"))
    // The picker lists the built-in row + the uploaded format.
    await waitFor(() => expect(artifactTemplatesApiMock.list).toHaveBeenCalledWith("tickets"))
    fireEvent.click(await screen.findByText("Acme Tickets"))
    const useBtn = document.getElementById("tickets-format-use-tpl-tick") as HTMLElement
    fireEvent.click(useBtn)

    // The confirm restates the switch; confirming fires the route call.
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))

    await waitFor(() =>
      expect(storiesApiMock.changeTemplate).toHaveBeenCalledWith(
        { ticketSetId: 7 }, "tpl-tick",
      ),
    )
    // The slice is republished with the re-laid stories + the new stamp — the
    // tab renders from it, so this IS the re-render.
    await waitFor(() => {
      const patch = setContentMock.mock.calls.at(-1)?.[0] as Record<string, unknown>
      expect(patch?.ticketSet).toMatchObject({
        id: 7,
        artifactTemplateId: "tpl-tick",
        artifactTemplateName: "Acme Tickets",
        stories: relaid,
      })
    })
  })

  it("switches a PRD's tickets: {prdId} target, cache re-read via the nonce", async () => {
    storiesApiMock.getForPrd.mockResolvedValue({
      status: "ready", fresh: true, stories: [STORY],
      artifact_template_id: null, artifact_template_name: null,
    })
    storiesApiMock.changeTemplate.mockResolvedValue({
      status: "ready", artifact_template_id: "tpl-tick",
      artifact_template_name: "Acme Tickets", stories: [STORY],
    })
    renderPanel({
      prd: { prd_id: 501, title: "Dark mode", metaLine: "", sections: [] },
    })

    fireEvent.click(await screen.findByTestId("tickets-format-toggle"))
    await waitFor(() => expect(artifactTemplatesApiMock.list).toHaveBeenCalledWith("tickets"))
    const useBtn = document.getElementById("tickets-format-use-tpl-tick") as HTMLElement
    fireEvent.click(useBtn)
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))

    await waitFor(() =>
      expect(storiesApiMock.changeTemplate).toHaveBeenCalledWith(
        { prdId: 501 }, "tpl-tick",
      ),
    )
    // The persisted cache is the truth — the tab re-reads it on the nonce
    // rather than trusting a second copy of the stories.
    await waitFor(() => {
      const nonces = setContentMock.mock.calls
        .map((c) => (c[0] as Record<string, unknown>).ticketsRefreshNonce)
        .filter((n) => n !== undefined)
      expect(nonces.length).toBeGreaterThan(0)
    })
  })
})

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
//     target shape ({ticketSetId} vs {prdId}) and the confirm dialog closes on
//     the ACK, not on the finished work — the re-lay runs in the background,
//     and a dialog held open until it lands is the bug this suite now guards.
//   * while it runs, the tickets stay on screen under a working strip, the
//     footer keeps naming the format they are ACTUALLY in, and a second switch
//     is not offered (the backend 409s it).
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
  followTicketSetSwitch: vi.fn().mockResolvedValue(true),
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
import { followTicketSetSwitch } from "../../../lib/runTicketSetGeneration"

const followTicketSetSwitchMock = vi.mocked(followTicketSetSwitch)

/** Every `ticketsRefreshNonce` the panel has published — the PRD path's
 *  "re-read the persisted set" signal, and the thing that must NOT fire while
 *  a switch is still running. */
function nonceBumps() {
  return setContentMock.mock.calls
    .map((c) => (c[0] as Record<string, unknown>).ticketsRefreshNonce)
    .filter((n) => n !== undefined)
}

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
  // The Format control's poll runs on a 2.5s timer; the switch-lands assertion
  // drives it rather than waiting on the wall clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  followTicketSetSwitchMock.mockResolvedValue(true)
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
  vi.useRealTimers()
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

  it("switches a standalone set: {ticketSetId} target, followed in place", async () => {
    storiesApiMock.changeTemplate.mockResolvedValue({
      status: "relaying", artifact_template_id: "tpl-tick",
      artifact_template_name: "Acme Tickets",
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
    // Followed in place — NOT loadTicketSet, which would blank tickets that
    // are still perfectly readable while the re-lay runs.
    await waitFor(() =>
      expect(followTicketSetSwitchMock).toHaveBeenCalledWith(
        7, expect.any(Function), expect.objectContaining({ id: 7 }), "Acme Tickets",
      ),
    )
  })

  it("closes the confirm as soon as the switch is scheduled", async () => {
    // The reported bug: the dialog sat on "Switching…" for the whole re-lay,
    // so the user could neither dismiss it nor go and do something else. It
    // must close on the acknowledgement.
    storiesApiMock.changeTemplate.mockResolvedValue({
      status: "relaying", artifact_template_id: "tpl-tick",
      artifact_template_name: "Acme Tickets",
    })
    renderPanel({ ticketSet: READY_SET })

    fireEvent.click(await screen.findByTestId("tickets-format-toggle"))
    await waitFor(() => expect(artifactTemplatesApiMock.list).toHaveBeenCalledWith("tickets"))
    fireEvent.click(document.getElementById("tickets-format-use-tpl-tick") as HTMLElement)
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("keeps the tickets on screen under a working strip while the switch runs", async () => {
    renderPanel({
      ticketSet: { ...READY_SET, relaying: true, relayingIntoName: "Acme Tickets" },
    })

    const strip = await screen.findByTestId("tickets-relaying-strip")
    expect(strip.textContent).toContain("Acme Tickets")
    // The footer still names what the tickets ARE, not what they are becoming.
    const toggle = await screen.findByTestId("tickets-format-toggle")
    expect(toggle.textContent).toContain("Format: Sprntly built-in")
    expect(toggle.textContent).toContain("switching")

    // …and a second switch is not on offer: the backend refuses one, so the
    // reason replaces the button rather than a dead control being rendered.
    fireEvent.click(toggle)
    await waitFor(() => expect(artifactTemplatesApiMock.list).toHaveBeenCalledWith("tickets"))
    await screen.findByText("Switch in progress")
    expect(document.getElementById("tickets-format-use-tpl-tick")).toBeNull()
  })

  it("switches a PRD's tickets: {prdId} target, re-read only once it lands", async () => {
    // The row is the truth on this path: the tab re-reads on the nonce, and it
    // must not do so until the re-lay has actually landed — a re-read taken
    // mid-switch serves the OLD format and looks like the switch did nothing.
    storiesApiMock.getForPrd.mockResolvedValue({
      status: "ready", fresh: true, stories: [STORY],
      artifact_template_id: null, artifact_template_name: null, relaying: false,
    })
    storiesApiMock.changeTemplate.mockResolvedValue({
      status: "relaying", artifact_template_id: "tpl-tick",
      artifact_template_name: "Acme Tickets",
    })
    renderPanel({
      prd: { prd_id: 501, title: "Dark mode", metaLine: "", sections: [] },
    })

    fireEvent.click(await screen.findByTestId("tickets-format-toggle"))
    await waitFor(() => expect(artifactTemplatesApiMock.list).toHaveBeenCalledWith("tickets"))
    const useBtn = document.getElementById("tickets-format-use-tpl-tick") as HTMLElement
    fireEvent.click(useBtn)
    const dialog = await screen.findByRole("dialog")

    // The row reports the switch running from here on, so the poll has
    // something to follow.
    storiesApiMock.getForPrd.mockResolvedValue({
      status: "ready", fresh: true, stories: [STORY],
      artifact_template_id: null, artifact_template_name: null,
      relaying: true, relaying_into_name: "Acme Tickets",
    })
    fireEvent.click(within(dialog).getByRole("button", { name: "Use this format" }))

    await waitFor(() =>
      expect(storiesApiMock.changeTemplate).toHaveBeenCalledWith(
        { prdId: 501 }, "tpl-tick",
      ),
    )
    // Dialog gone, strip up, and NO re-read yet.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    await screen.findByTestId("tickets-relaying-strip")
    expect(nonceBumps()).toHaveLength(0)

    // The switch lands.
    storiesApiMock.getForPrd.mockResolvedValue({
      status: "ready", fresh: true, stories: [STORY],
      artifact_template_id: "tpl-tick", artifact_template_name: "Acme Tickets",
      relaying: false,
    })
    await vi.advanceTimersByTimeAsync(2600)

    await waitFor(() => expect(nonceBumps().length).toBeGreaterThan(0))
    expect(screen.queryByTestId("tickets-relaying-strip")).toBeNull()
  })
})

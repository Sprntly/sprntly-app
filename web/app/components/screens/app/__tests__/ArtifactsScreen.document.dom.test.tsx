// @vitest-environment jsdom
//
// A team document in the Artifacts library.
//
// A document born in a chat opens OVER that chat, with the panel's Document tab
// on it — the same posture as the PRD, report and ticket-set rows beside it in
// the same list. It used to open its own page on the reasoning that writing
// wants the full measure of a page; the page is still there and still does, but
// a row that behaved differently from every other row in the same list was the
// surprise, not the feature.
//
// A document with NO chat behind it — uploaded, or its thread deleted — still
// opens the page, because there is no thread to open over.

import * as React from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const DOC_ROW = {
  type: "custom_artifact" as const,
  id: 12,
  title: "Customer Insights Summary for Leadership",
  kind: "customer insights summary",
  status: "ready" as const,
  created_at: new Date().toISOString(),
  source: {
    kind: "customer insights summary",
    conversation_id: 998,
    conversation_title: "Draft a customer insights summary",
  },
  open: { custom_artifact_id: 12 },
}

/** No chat behind it: uploaded, or the thread was deleted (`on delete set
 *  null` leaves the id but no title). */
const UNATTACHED_ROW = {
  ...DOC_ROW,
  id: 13,
  source: { ...DOC_ROW.source, conversation_id: null, conversation_title: null },
  open: { custom_artifact_id: 13 },
}

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([DOC_ROW]))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  prdApi: { importDoc: vi.fn(), get: vi.fn() },
  evidenceApi: { get: vi.fn() },
  ticketSetsApi: { get: vi.fn() },
}))
vi.mock("../../../../lib/runTicketSetGeneration", () => ({
  loadTicketSet: vi.fn(),
  runTicketSetGeneration: vi.fn(),
}))

const setContent = vi.fn()
const openContentPanel = vi.fn()
const openPrdTab = vi.fn()
const openReportTab = vi.fn()
const openTicketSetTab = vi.fn()
const openDocumentTab = vi.fn()
const showToast = vi.fn()
const routerPush = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openContentPanel, openPrdTab, openReportTab, openTicketSetTab, openDocumentTab,
    showToast, contentPanelTab: null,
  }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: routerPush }) }))
vi.mock("../../../../lib/evidence-adapter", () => ({ markdownToEvidenceState: () => ({}) }))
vi.mock("../../../../lib/routes", () => ({
  prototypePath: () => "/prototype",
  documentPath: (id: number) => `/artifacts/doc?id=${id}`,
}))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen } from "../ArtifactsScreen"

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

async function renderAndClick(row: unknown = DOC_ROW) {
  artifactsList.mockResolvedValue([row])
  await act(async () => { render(<ArtifactsScreen />) })
  await waitFor(() => expect(artifactsList).toHaveBeenCalled())
  const el = await waitFor(() =>
    document.querySelector('[data-artifact-type="custom_artifact"]') as HTMLElement,
  )
  await act(async () => { fireEvent.click(el) })
}

describe("ArtifactsScreen — opening a document", () => {
  it("opens the chat it was written in, with the panel on the document", async () => {
    await renderAndClick()

    // The ordinary resume payload — the same one ChatsScreen and the command
    // palette write — so the thread spawns through the path that already works.
    const handoff = JSON.parse(localStorage.getItem("sprntly_resume_conv") || "{}")
    expect(handoff.dbId).toBe(998)
    expect(handoff.title).toBe("Draft a customer insights summary")

    expect(openDocumentTab).toHaveBeenCalledWith({
      conversationId: 998,
      documentId: 12,
    })
    // NOT the page: that is the fallback for a document with no thread.
    expect(routerPush).not.toHaveBeenCalled()
  })

  it("opens the page for a document with no chat behind it", async () => {
    await renderAndClick(UNATTACHED_ROW)

    expect(openDocumentTab).not.toHaveBeenCalled()
    expect(localStorage.getItem("sprntly_resume_conv")).toBeNull()
    expect(routerPush).toHaveBeenCalledWith("/artifacts/doc?id=13")
  })
})

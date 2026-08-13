// @vitest-environment jsdom
//
// The right-panel Document tab: where "draft a leadership update" lands.
//
// Same posture as Reports, and for the same reason — a team document hangs off
// the CHAT THREAD, not off the PRD — so it sits after the pipeline and stays
// hidden until this thread actually has one. An always-present, usually-empty
// tab teaches people to ignore it.
//
// These lock the tab bar: when it shows, when it does not, and that opening it
// does not put the PRD pipeline in scope.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

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

// The tab BODY is the DocumentTab component, which fetches and mounts a real
// editor. Stubbed here so this file tests the tab BAR — the body has its own
// coverage, and mounting ProseMirror in every tab-bar case would make these
// tests slow and about the wrong thing.
vi.mock("../DocumentTab", () => ({
  DocumentTab: ({ documentId }: { documentId: number }) =>
    React.createElement("div", { "data-testid": "document-body" }, `doc:${documentId}`),
}))

vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error { status = 0 },
  storiesApi: { getForPrd: vi.fn().mockResolvedValue({ stories: [] }) },
  reportsApi: {
    listForConversation: vi.fn(),
    get: vi.fn().mockResolvedValue(null),
    share: vi.fn(),
    downloadPdf: vi.fn(),
  },
}))

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), tab: "prd" as string | null }))
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
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel } from "../ContentPanel"

async function renderPanel(opts: { tab?: string; documentId?: number | null; prd?: unknown }) {
  navMock.tab = opts.tab ?? "prd"
  contentMock.value = {
    prd: opts.prd ?? null,
    evidence: null,
    evidenceGenerating: false,
    prdMeta: null,
    detail: null,
    conversationId: 77,
    reportFocusId: null,
    reportFocusStandalone: false,
    threadReports: [],
    threadReportsStatus: "ready",
    threadReportsConversationId: 77,
    connectedConnectorIds: [],
    documentId: opts.documentId ?? null,
    documentGenerating: false,
  }
  await act(async () => { render(React.createElement(ContentPanel)) })
}

const PRD = { prd_id: 1, title: "T", metaLine: "", sections: [], source: "brief" }

const tabLabels = () =>
  Array.from(document.querySelectorAll(".cpanel-tab")).map((b) => b.textContent?.trim())
const documentTab = () => tabLabels().find((t) => t === "Document") ?? null

afterEach(() => {
  cleanup()
  navMock.openContentPanel.mockClear()
})

describe("the Document tab", () => {
  it("is hidden on a thread that has no document", async () => {
    await renderPanel({ prd: PRD, documentId: null })
    expect(documentTab()).toBeNull()
  })

  it("appears once the thread has one", async () => {
    await renderPanel({ prd: PRD, documentId: 42 })
    expect(documentTab()).toBe("Document")
  })

  it("sits after the pipeline, not inside it", async () => {
    // Order is meaning here: Evidence → PRD → Tickets is a pipeline, and a
    // document is not a step in it.
    await renderPanel({ prd: PRD, documentId: 42 })
    const labels = tabLabels()
    expect(labels.indexOf("Document")).toBeGreaterThan(labels.indexOf("Tickets"))
  })

  it("renders the document it was given when open", async () => {
    await renderPanel({ prd: PRD, documentId: 42, tab: "document" })
    await waitFor(() =>
      expect(document.querySelector("[data-testid='document-body']")?.textContent)
        .toBe("doc:42"),
    )
  })

  it("shows on a thread with NO PRD at all", async () => {
    // A leadership update does not need a PRD to exist — requiring one would
    // make the whole feature unreachable outside a PRD tab.
    await renderPanel({ prd: null, documentId: 42, tab: "document" })
    expect(documentTab()).toBe("Document")
  })

  it("does not render a document body when there is no document", async () => {
    // Guards the pairing: the tab is gated on `documentId`, and so is the body.
    // If only the tab were gated, a stale `tab: "document"` would render an
    // empty panel with no way back.
    await renderPanel({ prd: PRD, documentId: null, tab: "document" })
    expect(document.querySelector("[data-testid='document-body']")).toBeNull()
  })
})

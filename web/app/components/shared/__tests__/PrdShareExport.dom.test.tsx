// @vitest-environment jsdom
//
// Tests for the PRD "Share" rework:
//   • TOP Share button (ContentPanel header) is a dropdown offering
//     Email / Download PDF / Download DOCX when a PRD is loaded.
//   • Email sets a mailto: URL carrying the PRD title in the subject.
//   • Download PDF / Download DOCX lazy-load their generator and trigger a
//     file download with the slugified filename (file-saver is mocked).
//   • The BOTTOM Share control is gone from PrdPanelContent's footer.
//
// We mock the context hooks (so the components render standalone) and the
// heavy export libs, then exercise the REAL ContentPanel / PrdPanelContent.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// ── Spyable contexts ───────────────────────────────────────────────────────
const openContentPanel = vi.fn()
const closeContentPanel = vi.fn()
const showToast = vi.fn()
const setContent = vi.fn()

let content: Record<string, unknown> = {}
let contentPanelTab: string = "prd"

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    aiBarValue: "",
    setAIBarValue: vi.fn(),
    openContentPanel,
    closeContentPanel,
    showToast,
    goTo: vi.fn(),
    contentPanelTab,
    expandAiPanel: vi.fn(),
    openModal: vi.fn(),
    shareMenuOpen: false,
    setShareMenuOpen: vi.fn(),
  }),
}))

const stableSetContent = (patch: Record<string, unknown>) => {
  setContent(patch)
  content = { ...content, ...patch }
}
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content, setContent: stableSetContent }),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "meridian", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../lib/onboarding/store", () => ({
  updateWorkspace: vi.fn(async () => {}),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}))

// Spy for the on-demand evidence read (loadEvidenceByInsight → evidenceApi.byInsight).
const evidenceByInsight = vi.fn((_briefId: number, _insightIndex: number) =>
  Promise.resolve(null as unknown),
)
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    prdApi: {
      ...actual.prdApi,
      latest: vi.fn(async () => { throw new actual.ApiError(404, "none") }),
    },
    evidenceApi: {
      ...actual.evidenceApi,
      byInsight: (briefId: number, insightIndex: number) => evidenceByInsight(briefId, insightIndex),
    },
  }
})

// ── Mock the heavy export generators (lazy-imported in lib/prdExport) ────────
const saveAs = vi.fn()
vi.mock("file-saver", () => ({ saveAs }))

const pdfOutput = vi.fn((_type?: string) => new Blob(["pdf"], { type: "application/pdf" }))
vi.mock("jspdf", () => {
  class FakeDoc {
    internal = { pageSize: { getWidth: () => 595, getHeight: () => 842 } }
    setFont() {}
    setFontSize() {}
    splitTextToSize(t: string) { return [t] }
    text() {}
    addPage() {}
    output(type?: string) { return pdfOutput(type) }
  }
  return { jsPDF: FakeDoc }
})

const packerToBlob = vi.fn(async () => new Blob(["docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }))
vi.mock("docx", () => {
  class Paragraph { constructor(public o: unknown) {} }
  class TextRun { constructor(public o: unknown) {} }
  class Table { constructor(public o: unknown) {} }
  class TableRow { constructor(public o: unknown) {} }
  class TableCell { constructor(public o: unknown) {} }
  class Document { constructor(public o: unknown) {} }
  return {
    Document,
    Paragraph,
    TextRun,
    Table,
    TableRow,
    TableCell,
    HeadingLevel: { TITLE: "Title", HEADING_2: "Heading2" },
    WidthType: { PERCENTAGE: "pct" },
    Packer: { toBlob: packerToBlob },
  }
})

import { ContentPanel } from "../ContentPanel"
import { PrdPanelContent } from "../PrdPanelContent"

const FAKE_PRD = {
  prd_id: 42,
  title: "Handoff Threshold PRD",
  metaLine: "From Brief · insight 0",
  sections: [
    { type: "h2", text: "Problem" },
    { type: "p", text: "Users drop off after day 30." },
    { type: "ul", items: ["Onboarding too long", "No reminders"] },
  ],
  figma_file_key: undefined,
}

const EMPTY_CONTENT = {
  prd: null,
  prdMeta: null,
  prdGenerating: false,
  evidence: null,
  evidenceGenerating: false,
  detail: null,
  briefDetails: {},
  brief: { findings: [] },
  teamMembers: [],
  connectedConnectorIds: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  // ContentPanel's width-restore effect reads window.localStorage on panel open;
  // provide a no-op stub so it doesn't throw in the test env (width persistence is
  // not under test here).
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
    clear: () => {},
  })
  content = { ...EMPTY_CONTENT, prd: FAKE_PRD }
  contentPanelTab = "prd"
})
afterEach(cleanup)

describe("ContentPanel header Share dropdown", () => {
  it("renders Download PDF / Download DOCX (no Email) once opened with a PRD loaded", () => {
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    const menu = screen.getByRole("menu")
    expect(within(menu).getByText("Download PDF")).toBeTruthy()
    expect(within(menu).getByText("Download DOCX")).toBeTruthy()
    // Email was removed from Share (mailto can't attach the docs).
    expect(within(menu).queryByText("Email")).toBeNull()
  })

  it("Share is disabled when no PRD is loaded", () => {
    content = { ...EMPTY_CONTENT, prd: null }
    render(<ContentPanel />)
    const btn = screen.getByRole("button", { name: /Share/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(btn)
    expect(screen.queryByRole("menu")).toBeNull()
  })

  it("does not render a Save button in the content-panel header", () => {
    render(<ContentPanel />)
    const buttons = screen.getAllByRole("button")
    expect(buttons.some((b) => /^\s*Save\s*$/i.test(b.textContent ?? ""))).toBe(false)
  })

  it("Download PDF generates a PDF and triggers a download with the slugified filename", async () => {
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    fireEvent.click(within(screen.getByRole("menu")).getByText("Download PDF"))
    await waitFor(() => expect(pdfOutput).toHaveBeenCalled())
    await waitFor(() => expect(saveAs).toHaveBeenCalled())
    const [, filename] = saveAs.mock.calls[0]
    expect(filename).toBe("handoff-threshold-prd.pdf")
  })

  it("Download DOCX generates a docx and triggers a download with the slugified filename", async () => {
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    fireEvent.click(within(screen.getByRole("menu")).getByText("Download DOCX"))
    await waitFor(() => expect(packerToBlob).toHaveBeenCalled())
    await waitFor(() => expect(saveAs).toHaveBeenCalled())
    const [, filename] = saveAs.mock.calls[0]
    expect(filename).toBe("handoff-threshold-prd.docx")
  })
})

describe("ContentPanel Share — combined Evidence + PRD", () => {
  const HTML_PRD = {
    prd_id: 42,
    title: "Handoff Threshold PRD",
    html: "<html><head><style>:root{--g:#00f} .page{color:var(--g)}</style></head><body><div class='page'>prd body</div></body></html>",
  }
  const HTML_EVIDENCE = {
    evidence_id: 9,
    title: "Handoff Evidence",
    html: "<html><head><style>:root{--g:#0a0} .wrap{color:var(--g)}</style></head><body><div class='wrap'>evidence body</div></body></html>",
  }

  it("labels the downloads as Evidence + PRD when both are HTML briefs", () => {
    content = { ...EMPTY_CONTENT, prd: HTML_PRD, evidence: HTML_EVIDENCE }
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    const menu = screen.getByRole("menu")
    expect(within(menu).getByText(/Evidence \+ PRD as \.pdf/i)).toBeTruthy()
    expect(within(menu).getByText(/Evidence \+ PRD as \.doc/i)).toBeTruthy()
  })

  it("Download DOCX saves ONE combined .doc containing both briefs", async () => {
    content = { ...EMPTY_CONTENT, prd: HTML_PRD, evidence: HTML_EVIDENCE }
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    fireEvent.click(within(screen.getByRole("menu")).getByText("Download DOCX"))
    await waitFor(() => expect(saveAs).toHaveBeenCalled())
    const [blob, filename] = saveAs.mock.calls[0]
    // One combined Word doc (the -evidence-prd suffix marks the combined export);
    // the HTML content + CSS scoping is asserted in combinedExport.test.ts.
    expect(filename).toBe("handoff-threshold-prd-evidence-prd.doc")
    expect((blob as Blob).type).toBe("application/msword")
  })

  it("fetches evidence on demand (PRD tab) and still exports one combined .doc", async () => {
    // Evidence is NOT in context (user never opened the Evidence tab), but the
    // PRD carries its insight, so the handler reads the evidence and combines.
    const HTML_PRD_WITH_INSIGHT = { ...HTML_PRD, briefId: 3, insightIndex: 1 }
    evidenceByInsight.mockResolvedValueOnce({
      status: "ready",
      payload_md: HTML_EVIDENCE.html,
    })
    content = { ...EMPTY_CONTENT, prd: HTML_PRD_WITH_INSIGHT, evidence: null }
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    // Optimistic combined label because the PRD has an insight to read from.
    expect(within(screen.getByRole("menu")).getByText(/Evidence \+ PRD as \.doc/i)).toBeTruthy()
    fireEvent.click(within(screen.getByRole("menu")).getByText("Download DOCX"))
    await waitFor(() => expect(evidenceByInsight).toHaveBeenCalledWith(3, 1))
    await waitFor(() => expect(saveAs).toHaveBeenCalled())
    const [, filename] = saveAs.mock.calls[0]
    expect(filename).toBe("handoff-threshold-prd-evidence-prd.doc")
  })

  it("falls back to single-PRD export when there is no evidence", async () => {
    content = { ...EMPTY_CONTENT, prd: HTML_PRD, evidence: null }
    render(<ContentPanel />)
    fireEvent.click(screen.getByRole("button", { name: /Share/i }))
    // Single-PRD labels, not the combined ones.
    expect(within(screen.getByRole("menu")).getByText(/Export as \.docx/i)).toBeTruthy()
    fireEvent.click(within(screen.getByRole("menu")).getByText("Download DOCX"))
    await waitFor(() => expect(saveAs).toHaveBeenCalled())
    const [, filename] = saveAs.mock.calls[0]
    // HTML PRD → single .doc (no -evidence-prd suffix).
    expect(filename).toBe("handoff-threshold-prd.doc")
  })
})

describe("PrdPanelContent bottom bar", () => {
  it("renders Version history + the autosave/Save control, and NOT Approve or Share", () => {
    content = { ...EMPTY_CONTENT, prd: FAKE_PRD }
    const { container } = render(<PrdPanelContent />)
    // The mid-page footer is gone; actions live in the bottom bar.
    expect(container.querySelector(".prd-foot")).toBeNull()
    const foot = container.querySelector(".prd-bottom-bar")
    expect(foot).toBeTruthy()
    // Version history (relocated to the bottom) + the autosave/save button.
    expect(within(foot as HTMLElement).getByText(/Version history/i)).toBeTruthy()
    expect(
      within(foot as HTMLElement).getByText(/Autosaved|Save now|Saving/i),
    ).toBeTruthy()
    // The old "Approve & next step" button and any Share control are gone.
    const footButtons = within(foot as HTMLElement).queryAllByRole("button")
    expect(footButtons.some((b) => /approve & next step/i.test(b.textContent ?? ""))).toBe(false)
    expect(footButtons.some((b) => /share/i.test(b.textContent ?? ""))).toBe(false)
    expect(container.querySelector(".share-menu")).toBeNull()
  })

  it("does not render the prototype preview section (hidden for now)", () => {
    content = { ...EMPTY_CONTENT, prd: FAKE_PRD }
    const { container } = render(<PrdPanelContent />)
    // SHOW_PROTOTYPE_SECTION is off — the prototype preview card (which showed a
    // broken thumbnail) must not render in the PRD view.
    expect(within(container).queryByText(/click to open the design/i)).toBeNull()
    expect(container.querySelector(".prototype-preview-card")).toBeNull()
  })
})

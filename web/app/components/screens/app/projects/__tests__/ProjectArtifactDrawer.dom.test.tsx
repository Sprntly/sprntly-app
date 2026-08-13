// @vitest-environment jsdom
//
// ProjectArtifactDrawer — the in-place, side-by-side artifact reading pane the
// redesign opens BESIDE the group chat (no route change, no overlay). These
// tests pin the load-bearing invariant of the ad-hoc redesign: it is a labelled
// reading REGION (`role="region"`), NOT a modal — no `role="dialog"`, no
// `aria-modal`, no backdrop/overlay — so the chat column to its left stays fully
// interactive. They also cover the per-type body routing (prd/evidence markdown,
// report HTML iframe, prototype canvas link, ticket_set empty) and the graceful
// 403/404 state, all against the SAME authenticated GET routes the app's other
// artifact-open paths call.
//
// AD-P13b (main-chat drawer parity — REUSE not fork): for a PRD artifact the
// drawer also adds a Document / Evidence / Tickets segmented control, in-place
// PRD editing through the shared `PrdHtmlView`/`PrdMarkdownEditor` primitives
// (with a project-scoped, IDOR-gated `projectsApi.savePrdContent` injected as
// the save handler), a Tickets view over the SAME `storiesApi` cache/generate
// pipeline the main-chat Tickets tab uses, lazy evidence loading, and a footer
// `DesignAgentLauncher`. None of those primitives are forked here — the module
// mocks below stand in for the REAL shared components (verified imported, not
// reimplemented, by the DRY source-scan block at the bottom of this file).
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const prdGetMock = vi.fn()
const evidenceGetMock = vi.fn()
const reportsGetMock = vi.fn()
const savePrdContentMock = vi.fn()
const getForPrdMock = vi.fn()
const generateMock = vi.fn()
const getJobMock = vi.fn()
const byInsightMock = vi.fn()
const designAgentGetByPrdMock = vi.fn()

vi.mock("../../../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown, message?: string) {
      super(message ?? String(status))
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError,
    prdApi: { get: (...a: unknown[]) => prdGetMock(...a) },
    evidenceApi: {
      get: (...a: unknown[]) => evidenceGetMock(...a),
      byInsight: (...a: unknown[]) => byInsightMock(...a),
    },
    reportsApi: { get: (...a: unknown[]) => reportsGetMock(...a) },
    projectsApi: { savePrdContent: (...a: unknown[]) => savePrdContentMock(...a) },
    storiesApi: {
      getForPrd: (...a: unknown[]) => getForPrdMock(...a),
      generate: (...a: unknown[]) => generateMock(...a),
      getJob: (...a: unknown[]) => getJobMock(...a),
    },
    // Not imported directly by the drawer (DesignAgentLauncher is module-
    // mocked below), but kept here so the mocked `lib/api` module stays a
    // faithful superset if any transitive import reaches for it.
    designAgentApi: { getByPrd: (...a: unknown[]) => designAgentGetByPrdMock(...a) },
  }
})
// `next/link` needs no app-router provider here — render it as a plain anchor,
// same stub the sibling Projects tests use.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
// The tickets poll loop sleeps via `sleepUntilNextPoll` — resolve instantly so
// generate() tests don't wait on real timers.
vi.mock("../../../../../lib/poll", () => ({
  sleepUntilNextPoll: () => Promise.resolve(),
}))
// The prototype launcher is the app's REAL, already props-based component —
// reused, not forked (AC-11). Stub it so the drawer test doesn't also have to
// stand up its own `designAgentApi.getByPrd` fetch cycle.
vi.mock("../../../../design-agent/DesignAgentLauncher", () => ({
  DesignAgentLauncher: () => React.createElement("div", { "data-testid": "design-agent-launcher-stub" }),
}))
// The shared inline PRD editors — stubbed to a button that fires the
// injected `onSave` with a fixed payload, so these tests drive the SAME save
// path a real edit would (AC-2) without needing a real iframe/contenteditable.
vi.mock("../../../../shared/PrdHtmlView", () => ({
  PrdHtmlView: ({ onSave }: any) =>
    React.createElement("button", { "data-testid": "prd-html-save", onClick: () => onSave("<p>x</p>", "T") }, "save"),
}))
vi.mock("../../../../shared/PrdMarkdownEditor", () => ({
  PrdMarkdownEditor: ({ onSave, children }: any) =>
    React.createElement("button", { "data-testid": "prd-md-save", onClick: () => onSave("x", "T") }, children),
}))

import { ProjectArtifactDrawer } from "../ProjectArtifactDrawer"
import { ApiError } from "../../../../../lib/api"
import type { ArtifactItem } from "../../../../../lib/api"

const PROJECT_ID = 1

const PRD = { type: "prd", id: 1, title: "Instant-quote flow — v3", status: "ready", created_at: new Date().toISOString(), open: { prd_id: 7 } } as unknown as ArtifactItem
// A PRD carrying a real (brief_id, insight_index) pointer, for the evidence
// lazy-fetch coverage (AC-5).
const PRD_WITH_EVIDENCE = { type: "prd", id: 1, title: "Instant-quote flow — v3", status: "ready", created_at: new Date().toISOString(), open: { prd_id: 7, brief_id: 55, insight_index: 2 } } as unknown as ArtifactItem
const EVIDENCE = { type: "evidence", id: 2, title: "Xometry call", status: "ready", created_at: new Date().toISOString(), open: { evidence_id: 9 } } as unknown as ArtifactItem
const REPORT = { type: "report", id: 3, title: "Weekly report", status: "ready", created_at: new Date().toISOString(), open: { report_id: 11 } } as unknown as ArtifactItem
const PROTOTYPE = { type: "prototype", id: 4, title: "Clickthrough", status: "ready", created_at: new Date().toISOString(), open: { prototype_id: 4, prd_id: 7 } } as unknown as ArtifactItem
const PROTOTYPE_NO_PRD = { type: "prototype", id: 5, title: "Orphan proto", status: "ready", created_at: new Date().toISOString(), open: { prototype_id: 5, prd_id: null } } as unknown as ArtifactItem
const TICKET_SET = { type: "ticket_set", id: 6, title: "Tickets", status: "ready", created_at: new Date().toISOString(), open: { ticket_set_id: 6 } } as unknown as ArtifactItem

afterEach(() => {
  cleanup()
  prdGetMock.mockReset()
  evidenceGetMock.mockReset()
  reportsGetMock.mockReset()
  savePrdContentMock.mockReset()
  getForPrdMock.mockReset()
  generateMock.mockReset()
  getJobMock.mockReset()
  byInsightMock.mockReset()
  designAgentGetByPrdMock.mockReset()
})

describe("ProjectArtifactDrawer — a side-column reading pane, never a modal", () => {
  it("renders a labelled role=region with NO dialog/aria-modal/backdrop (the chat stays interactive beside it)", async () => {
    prdGetMock.mockResolvedValue({ title: "Instant-quote flow — v3", payload_md: "# Body" })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    const drawer = screen.getByTestId("project-artifact-drawer")
    expect(drawer.tagName).toBe("ASIDE")
    expect(drawer.getAttribute("role")).toBe("region")
    // The load-bearing migration: it is NOT a modal dialog.
    expect(drawer.getAttribute("aria-modal")).toBeNull()
    expect(drawer.getAttribute("role")).not.toBe("dialog")
    expect(screen.queryByRole("dialog")).toBeNull()
    // No overlay/backdrop element and no legacy overlay testid.
    expect(document.querySelector(".modal-overlay")).toBeNull()
    expect(screen.queryByTestId("project-artifact-drawer-overlay")).toBeNull()
    // Region has an accessible name referencing the artifact.
    expect(drawer.getAttribute("aria-label")).toContain("Instant-quote flow — v3")
  })

  it("renders nothing when no artifact is open", () => {
    const { container } = render(React.createElement(ProjectArtifactDrawer, { artifact: null, projectId: PROJECT_ID, onClose: () => {} }))
    expect(container.firstChild).toBeNull()
    expect(screen.queryByTestId("project-artifact-drawer")).toBeNull()
  })

  it("closes on the close control and on Escape", async () => {
    prdGetMock.mockResolvedValue({ title: "X", payload_md: "hello" })
    const onClose = vi.fn()
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose }))
    })
    fireEvent.click(screen.getByTestId("project-artifact-drawer-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe("ProjectArtifactDrawer — per-type body routing (real GET routes)", () => {
  it("prd → fetches prdApi.get and renders the markdown body", async () => {
    prdGetMock.mockResolvedValue({ title: "Instant-quote flow — v3", payload_md: "The quote flow body." })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    expect(prdGetMock).toHaveBeenCalledWith(7)
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("The quote flow body."))
  })

  it("evidence → fetches evidenceApi.get and renders its body", async () => {
    evidenceGetMock.mockResolvedValue({ title: "Xometry call", payload_md: "Quoting friction notes." })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }))
    })
    expect(evidenceGetMock).toHaveBeenCalledWith(9)
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Quoting friction notes."))
  })

  it("report → fetches reportsApi.get and renders the HTML in a sandboxed iframe (no allow-scripts)", async () => {
    reportsGetMock.mockResolvedValue({ title: "Weekly report", html: "<h1>Report</h1><p>rendered</p>" })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: REPORT, projectId: PROJECT_ID, onClose: () => {} }))
    })
    expect(reportsGetMock).toHaveBeenCalledWith(11)
    const frame = await screen.findByTestId("project-artifact-drawer-report")
    expect(frame.tagName).toBe("IFRAME")
    expect(frame.getAttribute("srcdoc")).toContain("rendered")
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("prototype with a PRD behind it → offers a canvas link, never a fabricated page", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: PROTOTYPE, projectId: PROJECT_ID, onClose: () => {} }))
    const link = await screen.findByTestId("project-artifact-drawer-open-canvas")
    expect(link.getAttribute("href")).toBe("/prototype?prd=7")
  })

  it("prototype with no PRD → an honest empty note, no link", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: PROTOTYPE_NO_PRD, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("no canvas"))
    expect(screen.queryByTestId("project-artifact-drawer-open-canvas")).toBeNull()
  })

  it("ticket_set → an empty note pointing at the Tickets workspace (no in-place body)", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: TICKET_SET, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Ticket sets"))
  })

  it("a 403/404 renders a graceful 'not available' state, never a crash", async () => {
    prdGetMock.mockRejectedValue(new ApiError(403, "no"))
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("isn't available"))
  })
})

describe("ProjectArtifactDrawer.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property (only #fff allowed for on-dark text)", () => {
    const css = readFileSync(join(__dirname, "../ProjectArtifactDrawer.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})

describe("ProjectArtifactDrawer — PRD seg toggle (AC-3)", () => {
  it("a PRD artifact renders the three-tab tablist; evidence/report render none", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    const { unmount } = render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-seg")).toBeTruthy())
    expect(screen.getByRole("tablist")).toBeTruthy()
    expect(screen.getByTestId("project-artifact-drawer-seg-document")).toBeTruthy()
    expect(screen.getByTestId("project-artifact-drawer-seg-evidence")).toBeTruthy()
    expect(screen.getByTestId("project-artifact-drawer-seg-tickets")).toBeTruthy()
    unmount()

    evidenceGetMock.mockResolvedValue({ title: "E", payload_md: "body" })
    render(React.createElement(ProjectArtifactDrawer, { artifact: EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body")).toBeTruthy())
    expect(screen.queryByTestId("project-artifact-drawer-seg")).toBeNull()
    expect(screen.queryByRole("tablist")).toBeNull()
  })

  it("clicking Evidence/Tickets sets aria-selected + swaps body; opening a new artifact resets to Document", async () => {
    getForPrdMock.mockResolvedValue({ status: "none", fresh: false, stories: [] })
    byInsightMock.mockResolvedValue({ title: "Evidence", payload_md: "Evidence body." })
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    const { rerender } = render(React.createElement(ProjectArtifactDrawer, { artifact: PRD_WITH_EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-seg-document").getAttribute("aria-selected")).toBe("true"))

    fireEvent.click(screen.getByTestId("project-artifact-drawer-seg-evidence"))
    expect(screen.getByTestId("project-artifact-drawer-seg-evidence").getAttribute("aria-selected")).toBe("true")
    expect(screen.getByTestId("project-artifact-drawer-seg-document").getAttribute("aria-selected")).toBe("false")
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Evidence body."))

    fireEvent.click(screen.getByTestId("project-artifact-drawer-seg-tickets"))
    expect(screen.getByTestId("project-artifact-drawer-seg-tickets").getAttribute("aria-selected")).toBe("true")
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-tickets")).toBeTruthy())

    // Opening a fresh artifact resets the view to Document.
    rerender(React.createElement(ProjectArtifactDrawer, { artifact: { ...PRD_WITH_EVIDENCE, id: 999 } as unknown as ArtifactItem, projectId: PROJECT_ID, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-seg-document").getAttribute("aria-selected")).toBe("true"))
  })
})

describe("ProjectArtifactDrawer — document editor host injects the project-scoped save (AC-2)", () => {
  it("saving an HTML PRD calls projectsApi.savePrdContent(projectId, prdId, title, html) — never prdApi.update", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "<!doctype html><html><body>hi</body></html>" })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    const saveBtn = await screen.findByTestId("prd-html-save")
    await act(async () => {
      fireEvent.click(saveBtn)
    })
    await waitFor(() => expect(savePrdContentMock).toHaveBeenCalledWith(PROJECT_ID, 7, "T", "<p>x</p>"))
  })

  it("saving a markdown PRD calls projectsApi.savePrdContent(projectId, prdId, title, text)", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "Plain markdown body." })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    const saveBtn = await screen.findByTestId("prd-md-save")
    await act(async () => {
      fireEvent.click(saveBtn)
    })
    await waitFor(() => expect(savePrdContentMock).toHaveBeenCalledWith(PROJECT_ID, 7, "T", "x"))
  })
})

describe("ProjectArtifactDrawer — Tickets view reuses storiesApi, no second pipeline (AC-4)", () => {
  it("reads the cache first; empty state offers Generate tickets which calls generate() then polls getJob()", async () => {
    getForPrdMock.mockResolvedValueOnce({ status: "none", fresh: false, stories: [] })
    generateMock.mockResolvedValue({ job_id: 42, status: "generating" })
    getJobMock.mockResolvedValue({ job_id: 42, status: "ready" })
    getForPrdMock.mockResolvedValueOnce({ status: "ready", fresh: true, stories: [{ title: "Ticket one", body: "b" }] })
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })

    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-tickets"))
    await waitFor(() => expect(getForPrdMock).toHaveBeenCalledWith(7))
    const genBtn = await screen.findByTestId("project-drawer-generate-tickets")

    await act(async () => {
      fireEvent.click(genBtn)
    })

    await waitFor(() => expect(generateMock).toHaveBeenCalledWith(7))
    await waitFor(() => expect(getJobMock).toHaveBeenCalledWith(42))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-tickets").textContent).toContain("Ticket one"))
  })

  it("ready (fresh) renders the story list with no Regenerate affordance", async () => {
    getForPrdMock.mockResolvedValue({ status: "ready", fresh: true, stories: [{ title: "Story A", body: "" }] })
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-tickets"))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-tickets").textContent).toContain("Story A"))
    expect(screen.queryByText(/Regenerate/)).toBeNull()
  })

  it("stale (fresh:false) renders the list plus a Regenerate affordance", async () => {
    getForPrdMock.mockResolvedValue({ status: "ready", fresh: false, stories: [{ title: "Story B", body: "" }] })
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-tickets"))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-tickets").textContent).toContain("Story B"))
    expect(screen.getByText(/Regenerate/)).toBeTruthy()
  })

  it("error state offers Try again", async () => {
    getForPrdMock.mockRejectedValue(new Error("boom"))
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-tickets"))
    await waitFor(() => expect(screen.getByText(/Try again/)).toBeTruthy())
  })
})

describe("ProjectArtifactDrawer — Evidence lazy fetch (AC-5)", () => {
  it("fetches evidenceApi.byInsight exactly once for the artifact; re-selecting the tab does not refetch", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    byInsightMock.mockResolvedValue({ title: "Evidence", payload_md: "The evidence body." })
    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD_WITH_EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }))

    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-evidence"))
    await waitFor(() => expect(byInsightMock).toHaveBeenCalledWith(55, 2))
    expect(byInsightMock).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId("project-artifact-drawer-seg-document"))
    fireEvent.click(screen.getByTestId("project-artifact-drawer-seg-evidence"))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("The evidence body."))
    expect(byInsightMock).toHaveBeenCalledTimes(1)
  })

  it("a PRD with no brief/insight pointer shows an honest empty note and never calls byInsight", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-evidence"))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("no research evidence"))
    expect(byInsightMock).not.toHaveBeenCalled()
  })
})

describe("ProjectArtifactDrawer — footer reuses the launcher + canvas link (AC-7)", () => {
  it("the Document view of a PRD renders the DesignAgentLauncher stub, a save-status pip, and the open-canvas link", async () => {
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "Plain markdown body." })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, projectId: PROJECT_ID, onClose: () => {} }))
    })
    expect(await screen.findByTestId("design-agent-launcher-stub")).toBeTruthy()
    expect(screen.getByTestId("project-artifact-drawer-save-status")).toBeTruthy()
    const link = screen.getByTestId("project-drawer-open-canvas")
    expect(link.getAttribute("href")).toBe("/prototype?prd=7")
  })
})

describe("ProjectArtifactDrawer — BodyRender is the SAME renderer for Document and Evidence (AC-6)", () => {
  it("a non-PRD Document view and a PRD's Evidence tab render identical markdown-body DOM shape", async () => {
    // Non-PRD artifact — its Document (only) view goes straight through
    // BodyRender for a markdown body.
    evidenceGetMock.mockResolvedValue({ title: "E", payload_md: "Same body text." })
    const { unmount, container: nonPrdContainer } = render(
      React.createElement(ProjectArtifactDrawer, { artifact: EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }),
    )
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Same body text."))
    const nonPrdArticle = nonPrdContainer.querySelector("article")
    expect(nonPrdArticle).toBeTruthy()
    expect(nonPrdArticle?.textContent).toBe("Same body text.")
    unmount()

    // A PRD's Evidence tab renders through the SAME BodyRender component for
    // the same markdown text.
    prdGetMock.mockResolvedValue({ title: "T", payload_md: "" })
    byInsightMock.mockResolvedValue({ title: "Evidence", payload_md: "Same body text." })
    const { container: prdContainer } = render(
      React.createElement(ProjectArtifactDrawer, { artifact: PRD_WITH_EVIDENCE, projectId: PROJECT_ID, onClose: () => {} }),
    )
    fireEvent.click(await screen.findByTestId("project-artifact-drawer-seg-evidence"))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Same body text."))
    const prdArticle = prdContainer.querySelector("article")
    expect(prdArticle).toBeTruthy()
    expect(prdArticle?.textContent).toBe(nonPrdArticle?.textContent)
  })
})

describe("ProjectArtifactDrawer — DRY (Gate-1 check, AC-11)", () => {
  it("imports the shared editor/launcher/tickets primitives — no drawer-local fork", () => {
    const src = readFileSync(join(__dirname, "../ProjectArtifactDrawer.tsx"), "utf8")
    expect(src).toMatch(/from\s+["']\.\.\/\.\.\/\.\.\/shared\/PrdHtmlView["']/)
    expect(src).toMatch(/from\s+["']\.\.\/\.\.\/\.\.\/shared\/PrdMarkdownEditor["']/)
    expect(src).toMatch(/from\s+["']\.\.\/\.\.\/\.\.\/design-agent\/DesignAgentLauncher["']/)
    expect(src).toMatch(/storiesApi/)
    // No local re-definition of the shared primitives.
    expect(src).not.toMatch(/function PrdHtmlView/)
    expect(src).not.toMatch(/function PrdMarkdownEditor/)
    expect(src).not.toMatch(/function DesignAgentLauncher/)
    expect(src).not.toMatch(/\/v1\/stories\/generate/)
  })
})

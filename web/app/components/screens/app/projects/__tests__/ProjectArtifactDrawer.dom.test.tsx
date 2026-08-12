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
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const prdGetMock = vi.fn()
const evidenceGetMock = vi.fn()
const reportsGetMock = vi.fn()

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
    evidenceApi: { get: (...a: unknown[]) => evidenceGetMock(...a) },
    reportsApi: { get: (...a: unknown[]) => reportsGetMock(...a) },
  }
})
// `next/link` needs no app-router provider here — render it as a plain anchor,
// same stub the sibling Projects tests use.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))

import { ProjectArtifactDrawer } from "../ProjectArtifactDrawer"
import { ApiError } from "../../../../../lib/api"
import type { ArtifactItem } from "../../../../../lib/api"

const PRD = { type: "prd", id: 1, title: "Instant-quote flow — v3", status: "ready", created_at: new Date().toISOString(), open: { prd_id: 7 } } as unknown as ArtifactItem
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
})

describe("ProjectArtifactDrawer — a side-column reading pane, never a modal", () => {
  it("renders a labelled role=region with NO dialog/aria-modal/backdrop (the chat stays interactive beside it)", async () => {
    prdGetMock.mockResolvedValue({ title: "Instant-quote flow — v3", payload_md: "# Body" })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, onClose: () => {} }))
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
    const { container } = render(React.createElement(ProjectArtifactDrawer, { artifact: null, onClose: () => {} }))
    expect(container.firstChild).toBeNull()
    expect(screen.queryByTestId("project-artifact-drawer")).toBeNull()
  })

  it("closes on the close control and on Escape", async () => {
    prdGetMock.mockResolvedValue({ title: "X", payload_md: "hello" })
    const onClose = vi.fn()
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, onClose }))
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
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, onClose: () => {} }))
    })
    expect(prdGetMock).toHaveBeenCalledWith(7)
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("The quote flow body."))
  })

  it("evidence → fetches evidenceApi.get and renders its body", async () => {
    evidenceGetMock.mockResolvedValue({ title: "Xometry call", payload_md: "Quoting friction notes." })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: EVIDENCE, onClose: () => {} }))
    })
    expect(evidenceGetMock).toHaveBeenCalledWith(9)
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Quoting friction notes."))
  })

  it("report → fetches reportsApi.get and renders the HTML in a sandboxed iframe (no allow-scripts)", async () => {
    reportsGetMock.mockResolvedValue({ title: "Weekly report", html: "<h1>Report</h1><p>rendered</p>" })
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: REPORT, onClose: () => {} }))
    })
    expect(reportsGetMock).toHaveBeenCalledWith(11)
    const frame = await screen.findByTestId("project-artifact-drawer-report")
    expect(frame.tagName).toBe("IFRAME")
    expect(frame.getAttribute("srcdoc")).toContain("rendered")
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("prototype with a PRD behind it → offers a canvas link, never a fabricated page", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: PROTOTYPE, onClose: () => {} }))
    const link = await screen.findByTestId("project-artifact-drawer-open-canvas")
    expect(link.getAttribute("href")).toBe("/prototype?prd=7")
  })

  it("prototype with no PRD → an honest empty note, no link", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: PROTOTYPE_NO_PRD, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("no canvas"))
    expect(screen.queryByTestId("project-artifact-drawer-open-canvas")).toBeNull()
  })

  it("ticket_set → an empty note pointing at the Tickets workspace (no in-place body)", async () => {
    render(React.createElement(ProjectArtifactDrawer, { artifact: TICKET_SET, onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("project-artifact-drawer-body").textContent).toContain("Ticket sets"))
  })

  it("a 403/404 renders a graceful 'not available' state, never a crash", async () => {
    prdGetMock.mockRejectedValue(new ApiError(403, "no"))
    await act(async () => {
      render(React.createElement(ProjectArtifactDrawer, { artifact: PRD, onClose: () => {} }))
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

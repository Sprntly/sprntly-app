// @vitest-environment jsdom
//
// ArtifactsModal — the app-faithful artifacts library in a modal: the
// filter-chip row + single-column row list reusing `ArtifactsScreen.tsx`'s
// `ARTIFACT_FILTERS` order / `ARTIFACT_BADGE` palette (AC7), and the inline
// Preview/Spec canvas a row click opens (AC8). Tests cover both the pure
// `ArtifactsModalView` and the `ArtifactsModal` container's fetch wiring,
// same View/Screen split posture as the sibling test files in this dir.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const artifactsMock = vi.fn()

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
    projectsApi: {
      artifacts: (...a: unknown[]) => artifactsMock(...a),
    },
  }
})

import { ArtifactsModalView, ArtifactsModal, type ArtifactsModalViewProps } from "../ArtifactsModal"
import { ApiError } from "../../../../../lib/api"
import type { ArtifactItem } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const ARTIFACTS: ArtifactItem[] = [
  {
    type: "prd",
    id: 1,
    title: "Instant-quote flow — v3",
    status: "ready",
    created_at: hoursAgo(2),
    source: { brief_id: 1, week_label: "wk 32", insight_index: null },
    open: { brief_id: 1, insight_index: null, prd_id: 1 },
  } as ArtifactItem,
  {
    type: "prototype",
    id: 2,
    title: "Upload-to-quote clickthrough",
    status: "ready",
    created_at: hoursAgo(48),
    source: { prd_id: 1, prd_title: "Instant-quote flow" },
    open: { prototype_id: 2, prd_id: 1 },
    is_complete: true,
    preview_image_url: null,
  } as ArtifactItem,
  {
    type: "evidence",
    id: 3,
    title: "Xometry call — quoting friction",
    status: "ready",
    created_at: hoursAgo(70),
    source: { brief_id: 1, week_label: "wk 31", insight_index: null },
    open: { brief_id: 1, insight_index: null, evidence_id: 3 },
  } as ArtifactItem,
]

const noop = () => {}

function viewProps(overrides: Partial<ArtifactsModalViewProps> = {}): ArtifactsModalViewProps {
  return {
    open: true,
    status: "ready",
    artifacts: ARTIFACTS,
    filter: "all",
    onFilterChange: noop,
    selected: null,
    onSelect: noop,
    onClose: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  artifactsMock.mockReset()
})

describe("ArtifactsModalView — app-faithful list (AC7)", () => {
  it("renders the filter-chip row with counts, mirroring ARTIFACT_FILTERS order", () => {
    render(React.createElement(ArtifactsModalView, viewProps()))
    const chips = ["all", "report", "prd", "prototype", "evidence", "ticket_set"]
    for (const id of chips) expect(screen.getByTestId(`artifacts-filter-${id}`)).toBeTruthy()
    expect(screen.getByTestId("artifacts-filter-all").textContent).toContain("3")
    expect(screen.getByTestId("artifacts-filter-prd").textContent).toContain("1")
    expect(screen.getByTestId("artifacts-filter-report").textContent).toContain("0")
  })

  it("renders one row per artifact, badge palette matching ArtifactsScreen's real hexes", () => {
    render(React.createElement(ArtifactsModalView, viewProps()))
    const list = screen.getByTestId("artifacts-modal-list")
    expect(within(list).getAllByRole("button")).toHaveLength(3)
    const src = readFileSync(join(__dirname, "../ArtifactsModal.tsx"), "utf8")
    expect(src).toContain("#DBEAFE")
    expect(src).toContain("#1E40AF")
    expect(src).not.toContain("634AB0")
  })

  it("filtering by type shows only that type's rows", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ filter: "prd" })))
    const list = screen.getByTestId("artifacts-modal-list")
    expect(within(list).getAllByRole("button")).toHaveLength(1)
    expect(list.textContent).toContain("Instant-quote flow — v3")
  })

  it("clicking a chip calls onFilterChange", () => {
    const onFilterChange = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onFilterChange })))
    fireEvent.click(screen.getByTestId("artifacts-filter-evidence"))
    expect(onFilterChange).toHaveBeenCalledWith("evidence")
  })
})

describe("ArtifactsModalView — inline Preview/Spec canvas (AC8)", () => {
  it("clicking a row calls onSelect, and a selected artifact renders the canvas with Preview/Spec tabs + ≥2 version chips", () => {
    const onSelect = vi.fn()
    const { rerender } = render(React.createElement(ArtifactsModalView, viewProps({ onSelect })))
    fireEvent.click(screen.getByTestId(`artifacts-row-${ARTIFACTS[0].type}-${ARTIFACTS[0].id}`))
    expect(onSelect).toHaveBeenCalledWith(ARTIFACTS[0])

    rerender(React.createElement(ArtifactsModalView, viewProps({ selected: ARTIFACTS[0], onSelect })))
    expect(screen.getByTestId("artifact-canvas-tab-preview")).toBeTruthy()
    expect(screen.getByTestId("artifact-canvas-tab-spec")).toBeTruthy()
    const chips = within(screen.getByTestId("artifact-canvas-versions")).getAllByText(/^v\d/)
    expect(chips.length).toBeGreaterThanOrEqual(2)
  })

  it("the selected row renders the app active state (data-active/aria-current)", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ selected: ARTIFACTS[1] })))
    const row = screen.getByTestId(`artifacts-row-${ARTIFACTS[1].type}-${ARTIFACTS[1].id}`)
    expect(row.getAttribute("data-active")).toBe("true")
    expect(row.getAttribute("aria-current")).toBe("true")
    const other = screen.getByTestId(`artifacts-row-${ARTIFACTS[0].type}-${ARTIFACTS[0].id}`)
    expect(other.getAttribute("data-active")).toBeNull()
  })

  it("Spec tab swaps the pane content", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ selected: ARTIFACTS[0] })))
    expect(screen.getByTestId("artifact-canvas-preview")).toBeTruthy()
    fireEvent.click(screen.getByTestId("artifact-canvas-tab-spec"))
    expect(screen.getByTestId("artifact-canvas-spec")).toBeTruthy()
    expect(screen.queryByTestId("artifact-canvas-preview")).toBeNull()
  })
})

describe("ArtifactsModalView — empty state (AC — empty artifacts)", () => {
  it("renders an empty state, not a crash, when there are no artifacts", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ artifacts: [] })))
    expect(screen.getByTestId("artifacts-modal-empty")).toBeTruthy()
    expect(screen.queryByTestId("artifacts-modal-list")).toBeNull()
  })
})

describe("ArtifactsModalView — a11y mechanics", () => {
  it("closes on Escape and on backdrop click", () => {
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onClose })))
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(document.querySelector(".modal-overlay") as Element)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("closes on Escape dispatched at the document level — not routed through the panel's own onKeyDown", () => {
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onClose })))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("renders nothing when closed", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ open: false })))
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})

describe("ArtifactsModalView — Tab focus-trap wraps within the dialog (regression)", () => {
  it("Tab from the last focusable wraps to the first; Shift+Tab from the first wraps to the last", () => {
    render(React.createElement(ArtifactsModalView, viewProps()))
    const dialog = screen.getByRole("dialog")
    const first = screen.getByTestId("artifacts-modal-close")
    const last = screen.getByTestId(`artifacts-row-${ARTIFACTS[2].type}-${ARTIFACTS[2].id}`)

    last.focus()
    expect(document.activeElement).toBe(last)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(first)

    first.focus()
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(last)
  })
})

describe("ArtifactsModalView — Escape listener cleanup (no leaked listener)", () => {
  it("does not call onClose for Escape dispatched after the modal has closed", () => {
    const onClose = vi.fn()
    const { rerender } = render(React.createElement(ArtifactsModalView, viewProps({ onClose })))
    rerender(React.createElement(ArtifactsModalView, viewProps({ open: false, onClose })))
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("does not call onClose for Escape dispatched after the modal has unmounted", () => {
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onClose })))
    cleanup()
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe("ArtifactsModal.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../ArtifactsModal.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})

// ── ArtifactsModal container — fetch on open, membership gate ──
describe("ArtifactsModal — data fetch (AC7, membership)", () => {
  it("fetches artifacts from GET /projects/{id}/artifacts on open", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    expect(artifactsMock).toHaveBeenCalledWith("101")
  })

  it("fetches nothing while closed", () => {
    render(React.createElement(ArtifactsModal, { projectId: "101", open: false, onClose: noop }))
    expect(artifactsMock).not.toHaveBeenCalled()
  })

  it("preselects the filter passed via initialFilter (rail-card ↗ per type)", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    await act(async () => {
      render(
        React.createElement(ArtifactsModal, { projectId: "101", open: true, initialFilter: "prd", onClose: noop }),
      )
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-filter-prd")).toBeTruthy())
    expect(screen.getByTestId("artifacts-filter-prd").getAttribute("aria-selected")).toBe("true")
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    artifactsMock.mockRejectedValue(new ApiError(403, "Not a member"))
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-forbidden")).toBeTruthy())
  })

  it("renders a graceful 'not found' state on a 404, never a crash", async () => {
    artifactsMock.mockRejectedValue(new ApiError(404, "Not found"))
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "999", open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-not-found")).toBeTruthy())
  })
})

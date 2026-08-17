// @vitest-environment jsdom
//
// ArtifactsModal — the app-faithful artifacts library in a modal: the
// filter-chip row + single-column row list reusing `ArtifactsScreen.tsx`'s
// `ARTIFACT_FILTERS` order / `ARTIFACT_BADGE` palette (AC7). A row click now
// opens the artifact in the drawer AND closes the modal (via `onOpen`) — the
// old in-modal Preview/Spec canvas is gone (AC8). "Add existing artifact" is a
// folded in-modal VIEW (list ⇆ add) rather than a separate modal. Tests cover
// both the pure `ArtifactsModalView` and the `ArtifactsModal` container's fetch
// wiring, same View/Screen split posture as the sibling test files in this dir.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const artifactsMock = vi.fn()
// The modal now folds `AddArtifactPanel` in as its in-modal "add" view; that
// panel reads `artifactsApi.list(company)` for the company library and
// `projectsApi.addArtifact` on confirm. A safe empty-list default keeps every
// list-view test (which never enters the add view) unaffected.
const artifactsListMock = vi.fn().mockResolvedValue([])
const addArtifactMock = vi.fn()

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
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
    },
    // Consumed by the folded `AddArtifactPanel` (in-modal add view).
    artifactsApi: { list: (...a: unknown[]) => artifactsListMock(...a) },
    // Real implementation (no API call, no side effect) — mirrors
    // lib/api.ts's own five-value check, kept here rather than importing the
    // real module so this mock stays self-contained.
    isProjectArtifactType: (t: string) =>
      ["prd", "evidence", "prototype", "report", "ticket_set"].includes(t),
  }
})

// The redesign's ArtifactsModal container now reads `useRouter` for its legacy
// deep-link FALLBACK (used only when no in-place `onOpenInPlace` is wired). The
// hook must resolve to a stub here — there is no Next app-router provider in
// jsdom — or the container throws on mount. The Projects screen always passes
// `onOpenInPlace`, so this stub's `push` is never actually invoked.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

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
    onOpen: noop,
    onClose: noop,
    // The redesign replaced `selected`/`onSelect`/`onAddExisting` with an
    // internal list ⇆ add view: `view` picks which internal view renders,
    // `onShowAdd`/`onBackToList` swap between them, and `addPanel` is the
    // folded AddArtifactPanel node the container passes in.
    view: "list",
    onShowAdd: noop,
    onBackToList: noop,
    addPanel: null,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  artifactsMock.mockReset()
  artifactsListMock.mockReset()
  artifactsListMock.mockResolvedValue([])
  addArtifactMock.mockReset()
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

describe("ArtifactsModalView — add-existing trigger (list-view header)", () => {
  it("test_artifacts_modal_renders_add_existing_trigger — the list-view header button with the trigger's testid/label is present, including in loading/empty states", () => {
    const { unmount: unmountReady } = render(React.createElement(ArtifactsModalView, viewProps()))
    const trigger = screen.getByTestId("artifacts-modal-add-existing")
    expect(trigger.tagName).toBe("BUTTON")
    expect(trigger.textContent).toContain("Add existing artifact")
    unmountReady()

    const { unmount: unmountLoading } = render(React.createElement(ArtifactsModalView, viewProps({ status: "loading" })))
    expect(screen.getByTestId("artifacts-modal-add-existing")).toBeTruthy()
    unmountLoading()

    render(React.createElement(ArtifactsModalView, viewProps({ artifacts: [] })))
    expect(screen.getByTestId("artifacts-modal-add-existing")).toBeTruthy()
  })

  it("test_artifacts_modal_add_existing_click_shows_add_view — clicking it calls onShowAdd (swap to the in-modal add view) exactly once and never onClose", () => {
    const onShowAdd = vi.fn()
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onShowAdd, onClose })))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-existing"))
    expect(onShowAdd).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it("test_artifacts_modal_add_view_renders_panel_and_back — view='add' renders the passed addPanel node behind an icon '← Back' control and swaps the header title, hiding the list-view add trigger", () => {
    render(
      React.createElement(
        ArtifactsModalView,
        viewProps({
          view: "add",
          addPanel: React.createElement("div", { "data-testid": "fake-add-panel" }, "add panel"),
        }),
      ),
    )
    // The folded panel node renders, behind the in-modal back control.
    expect(screen.getByTestId("fake-add-panel")).toBeTruthy()
    expect(screen.getByTestId("artifacts-modal-back")).toBeTruthy()
    expect(screen.getByTestId("artifacts-modal-title").textContent).toContain("Add existing artifact")
    // The list view (and its own add trigger + list) are not shown in add view.
    expect(screen.queryByTestId("artifacts-modal-add-existing")).toBeNull()
    expect(screen.queryByTestId("artifacts-modal-list")).toBeNull()
  })

  it("test_artifacts_modal_back_returns_to_list — clicking '← Back' in the add view calls onBackToList", () => {
    const onBackToList = vi.fn()
    render(
      React.createElement(
        ArtifactsModalView,
        viewProps({ view: "add", onBackToList, addPanel: React.createElement("div", null, "panel") }),
      ),
    )
    fireEvent.click(screen.getByTestId("artifacts-modal-back"))
    expect(onBackToList).toHaveBeenCalledTimes(1)
  })
})

describe("ArtifactsModalView — row click opens the artifact (no in-modal canvas) (AC8)", () => {
  it("clicking a row calls onOpen with that artifact (the container opens the drawer AND closes the modal); no in-modal Preview/Spec canvas renders", () => {
    const onOpen = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onOpen })))
    fireEvent.click(screen.getByTestId(`artifacts-row-${ARTIFACTS[0].type}-${ARTIFACTS[0].id}`))
    expect(onOpen).toHaveBeenCalledWith(ARTIFACTS[0])
    // The inline Preview/Spec canvas was removed entirely — a row click routes
    // straight to onOpen, never a selectable in-modal preview.
    expect(screen.queryByTestId("artifact-canvas")).toBeNull()
    expect(screen.queryByTestId("artifact-canvas-tab-preview")).toBeNull()
    expect(screen.queryByTestId("artifact-canvas-tab-spec")).toBeNull()
  })

  it("Enter on a focused row also calls onOpen (keyboard parity)", () => {
    const onOpen = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onOpen })))
    fireEvent.keyDown(screen.getByTestId(`artifacts-row-${ARTIFACTS[1].type}-${ARTIFACTS[1].id}`), { key: "Enter" })
    expect(onOpen).toHaveBeenCalledWith(ARTIFACTS[1])
  })

  it("rows carry no selected/active state — the removed selection model leaves no data-active/aria-current on any row", () => {
    render(React.createElement(ArtifactsModalView, viewProps()))
    for (const a of ARTIFACTS) {
      const row = screen.getByTestId(`artifacts-row-${a.type}-${a.id}`)
      expect(row.getAttribute("data-active")).toBeNull()
      expect(row.getAttribute("aria-current")).toBeNull()
    }
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
    // The relocated "Add existing artifact" trigger now precedes the close
    // button in the header, so it — not the close button — is the dialog's
    // first focusable element.
    const first = screen.getByTestId("artifacts-modal-add-existing")
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
        React.createElement(ArtifactsModal, {
          projectId: "101",
          open: true,
          initialFilter: "prd",
          onClose: noop,
        }),
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

  it("test_artifacts_modal_container_swaps_to_folded_add_view — clicking add-existing swaps the single modal to the folded AddArtifactPanel view (add-artifact-modal-body + ← Back), replacing the list, with no separate add-artifact dialog", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    await act(async () => {
      render(
        React.createElement(ArtifactsModal, {
          projectId: "101",
          open: true,
          onClose: noop,
        }),
      )
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())

    fireEvent.click(screen.getByTestId("artifacts-modal-add-existing"))

    // Same modal, internal view swap: the folded panel body + the in-modal
    // back control render, the list is gone, and no standalone add-artifact
    // dialog is mounted.
    await waitFor(() => expect(screen.getByTestId("add-artifact-modal-body")).toBeTruthy())
    expect(screen.getByTestId("artifacts-modal-back")).toBeTruthy()
    expect(screen.queryByTestId("artifacts-modal-list")).toBeNull()
    expect(screen.queryByTestId("add-artifact-modal")).toBeNull()
    // The folded panel loaded the company library once it became active.
    expect(artifactsListMock).toHaveBeenCalled()

    // "← Back" returns to the list view within the same modal.
    fireEvent.click(screen.getByTestId("artifacts-modal-back"))
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    expect(screen.queryByTestId("add-artifact-modal-body")).toBeNull()
  })
})

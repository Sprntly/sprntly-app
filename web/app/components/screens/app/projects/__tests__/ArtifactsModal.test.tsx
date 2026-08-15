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
const uploadDocumentMock = vi.fn()

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
      uploadDocument: (...a: unknown[]) => uploadDocumentMock(...a),
    },
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

import {
  ArtifactsModalView,
  ArtifactsModal,
  type ArtifactsModalViewProps,
  type ArtifactUploadState,
} from "../ArtifactsModal"
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

const DOC: ArtifactItem = {
  type: "custom_artifact",
  id: 9,
  title: "Launch plan.txt",
  status: "ready",
  created_at: hoursAgo(1),
  updated_at: hoursAgo(1),
  born_at: hoursAgo(1),
  kind: "document",
  source: { kind: "document", conversation_id: null, conversation_title: null },
  open: { custom_artifact_id: 9 },
} as ArtifactItem

const noop = () => {}
const IDLE_UPLOAD: ArtifactUploadState = { status: "idle" }

function viewProps(overrides: Partial<ArtifactsModalViewProps> = {}): ArtifactsModalViewProps {
  return {
    open: true,
    status: "ready",
    artifacts: ARTIFACTS,
    filter: "all",
    onFilterChange: noop,
    selected: null,
    onSelect: noop,
    onOpen: noop,
    onClose: noop,
    onAddExisting: noop,
    upload: IDLE_UPLOAD,
    onSelectFile: noop,
    onCancelUpload: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  artifactsMock.mockReset()
  uploadDocumentMock.mockReset()
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

describe("ArtifactsModalView — + Add ▾ menu (header, AC15)", () => {
  it("test_artifacts_modal_renders_add_existing_trigger — the + Add ▾ trigger is present, including in loading/empty states, and opens a menu containing the relocated Add-existing item", () => {
    const { unmount: unmountReady } = render(React.createElement(ArtifactsModalView, viewProps()))
    const openTrigger = screen.getByTestId("artifacts-modal-add-menu-trigger")
    expect(openTrigger.tagName).toBe("BUTTON")
    fireEvent.click(openTrigger)
    const trigger = screen.getByTestId("artifacts-modal-add-existing")
    expect(trigger.tagName).toBe("BUTTON")
    expect(trigger.textContent).toContain("Add existing artifact")
    unmountReady()

    const { unmount: unmountLoading } = render(React.createElement(ArtifactsModalView, viewProps({ status: "loading" })))
    expect(screen.getByTestId("artifacts-modal-add-menu-trigger")).toBeTruthy()
    unmountLoading()

    render(React.createElement(ArtifactsModalView, viewProps({ artifacts: [] })))
    expect(screen.getByTestId("artifacts-modal-add-menu-trigger")).toBeTruthy()
  })

  it("test_add_menu_renders_two_items — opening the menu shows Upload document AND Add existing artifact", () => {
    render(React.createElement(ArtifactsModalView, viewProps()))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    expect(screen.getByTestId("artifacts-modal-upload-document").textContent).toContain("Upload document")
    expect(screen.getByTestId("artifacts-modal-add-existing").textContent).toContain("Add existing artifact")
  })

  it("test_add_existing_fires_existing_handler — clicking the menu's Add-existing item calls onAddExisting exactly once, closes the menu, and never fires onClose", () => {
    const onAddExisting = vi.fn()
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onAddExisting, onClose })))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-existing"))
    expect(onAddExisting).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.queryByTestId("artifacts-modal-add-menu")).toBeNull()
  })

  it("test_artifacts_modal_add_existing_click_invokes_handler_only — clicking it calls onAddExisting exactly once and never onClose", () => {
    const onAddExisting = vi.fn()
    const onClose = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onAddExisting, onClose })))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-existing"))
    expect(onAddExisting).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe("ArtifactsModalView — upload strip, DOCUMENT badge, Documents filter (AC15-18)", () => {
  it("test_upload_document_calls_uploadDocument_and_inserts_row — selecting a file off the menu's file input calls onSelectFile", () => {
    const onSelectFile = vi.fn()
    render(React.createElement(ArtifactsModalView, viewProps({ onSelectFile })))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    const input = screen.getByTestId("artifacts-modal-file-input") as HTMLInputElement
    const file = new File(["hello"], "notes.txt", { type: "text/plain" })
    fireEvent.change(input, { target: { files: [file] } })
    expect(onSelectFile).toHaveBeenCalledWith(file)
    // The menu closes on select.
    expect(screen.queryByTestId("artifacts-modal-add-menu")).toBeNull()
  })

  it("a document row renders a neutral DOCUMENT badge and the 'Sprntly can reference this' chip", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ artifacts: [...ARTIFACTS, DOC] })))
    const row = screen.getByTestId(`artifacts-row-${DOC.type}-${DOC.id}`)
    expect(row.textContent).toContain("DOCUMENT")
    expect(row.textContent).toContain("Sprntly can reference this")
  })

  it("the processing row shows while uploading, with a Cancel that calls onCancelUpload", () => {
    const onCancelUpload = vi.fn()
    render(
      React.createElement(
        ArtifactsModalView,
        viewProps({ upload: { status: "uploading", filename: "notes.txt" }, onCancelUpload }),
      ),
    )
    const row = screen.getByTestId("artifacts-modal-upload-processing")
    expect(row.textContent).toContain("notes.txt")
    fireEvent.click(screen.getByTestId("artifacts-modal-upload-cancel"))
    expect(onCancelUpload).toHaveBeenCalledTimes(1)
  })

  it("test_upload_failure_shows_inline_error_no_row — a failed upload renders the mapped inline error and inserts no row", () => {
    render(
      React.createElement(
        ArtifactsModalView,
        viewProps({
          upload: { status: "error", filename: "notes.txt", message: "That file is too large (max 25 MB)." },
        }),
      ),
    )
    expect(screen.getByTestId("artifacts-modal-upload-error").textContent).toContain(
      "That file is too large (max 25 MB).",
    )
    // No document row was inserted — the artifacts list is unchanged from
    // the default fixture (no custom_artifact row present).
    expect(screen.queryByTestId(`artifacts-row-${DOC.type}-${DOC.id}`)).toBeNull()
  })

  it("test_documents_filter_chip_lists_only_documents — the Documents chip filters to custom_artifact rows and counts them", () => {
    render(React.createElement(ArtifactsModalView, viewProps({ artifacts: [...ARTIFACTS, DOC], filter: "custom_artifact" })))
    expect(screen.getByTestId("artifacts-filter-custom_artifact").textContent).toContain("Documents")
    expect(screen.getByTestId("artifacts-filter-custom_artifact").textContent).toContain("1")
    const list = screen.getByTestId("artifacts-modal-list")
    expect(within(list).getAllByRole("button")).toHaveLength(1)
    expect(list.textContent).toContain("Launch plan.txt")
  })
})

describe("ArtifactsModal container — upload wiring", () => {
  it("a real upload calls projectsApi.uploadDocument(projectId, file) and inserts the returned row into the list without a refetch", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    uploadDocumentMock.mockResolvedValue(DOC)
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop, onAddExisting: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    const input = screen.getByTestId("artifacts-modal-file-input") as HTMLInputElement
    const file = new File(["hello"], "notes.txt", { type: "text/plain" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })
    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalledWith("101", file))
    // artifactsMock (the fetch-on-open call) is called exactly once — no
    // refetch after a successful upload.
    expect(artifactsMock).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByTestId(`artifacts-row-${DOC.type}-${DOC.id}`)).toBeTruthy())
  })

  it("a failed upload (413) shows the mapped inline error and does not insert a row", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    uploadDocumentMock.mockRejectedValue(new ApiError(413, "File too large"))
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop, onAddExisting: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    const input = screen.getByTestId("artifacts-modal-file-input") as HTMLInputElement
    const file = new File(["x".repeat(30_000_000)], "big.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-upload-error")).toBeTruthy())
    expect(screen.getByTestId("artifacts-modal-upload-error").textContent).toContain("too large")
    expect(screen.queryByTestId(`artifacts-row-${DOC.type}-${DOC.id}`)).toBeNull()
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
    // The + Add ▾ trigger now precedes the close button in the header, so
    // it — not the close button — is the dialog's first focusable element
    // (the menu itself is closed by default and contributes no focusable
    // items until opened).
    const first = screen.getByTestId("artifacts-modal-add-menu-trigger")
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
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop, onAddExisting: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    expect(artifactsMock).toHaveBeenCalledWith("101")
  })

  it("fetches nothing while closed", () => {
    render(React.createElement(ArtifactsModal, { projectId: "101", open: false, onClose: noop, onAddExisting: noop }))
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
          onAddExisting: noop,
        }),
      )
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-filter-prd")).toBeTruthy())
    expect(screen.getByTestId("artifacts-filter-prd").getAttribute("aria-selected")).toBe("true")
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    artifactsMock.mockRejectedValue(new ApiError(403, "Not a member"))
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "101", open: true, onClose: noop, onAddExisting: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-forbidden")).toBeTruthy())
  })

  it("renders a graceful 'not found' state on a 404, never a crash", async () => {
    artifactsMock.mockRejectedValue(new ApiError(404, "Not found"))
    await act(async () => {
      render(React.createElement(ArtifactsModal, { projectId: "999", open: true, onClose: noop, onAddExisting: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-not-found")).toBeTruthy())
  })

  it("test_artifacts_modal_container_forwards_on_add_existing — the container forwards onAddExisting straight to the View, and clicking it invokes the exact spy passed in", async () => {
    artifactsMock.mockResolvedValue(ARTIFACTS)
    const onAddExisting = vi.fn()
    await act(async () => {
      render(
        React.createElement(ArtifactsModal, {
          projectId: "101",
          open: true,
          onClose: noop,
          onAddExisting,
        }),
      )
    })
    await waitFor(() => expect(screen.getByTestId("artifacts-modal-list")).toBeTruthy())
    fireEvent.click(screen.getByTestId("artifacts-modal-add-menu-trigger"))
    fireEvent.click(screen.getByTestId("artifacts-modal-add-existing"))
    expect(onAddExisting).toHaveBeenCalledTimes(1)
  })
})

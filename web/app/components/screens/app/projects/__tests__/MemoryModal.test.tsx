// @vitest-environment jsdom
//
// MemoryModal — reduced to a READ-ONLY, summary-only surface: a synthesized
// "What this project knows" block + the privacy strip. No add composer, no
// per-entry list, no edit/remove — this ticket removes all of them. Tests
// cover both the pure `MemoryModalView` (rendering/a11y) and the
// `MemoryModal` container's summary-only fetch, mirroring
// `ProjectDetailScreen.test.tsx`'s View/Screen split posture.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const memorySummaryMock = vi.fn()
const memoryEntriesMock = vi.fn()
const addMemoryMock = vi.fn()
const patchMemoryMock = vi.fn()
const deleteMemoryMock = vi.fn()

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
      memorySummary: (...a: unknown[]) => memorySummaryMock(...a),
      // Dormant — kept on the mock so a test can assert they're NEVER
      // called (AC6), mirroring the real, unchanged `lib/api.ts` exports.
      memoryEntries: (...a: unknown[]) => memoryEntriesMock(...a),
      addMemory: (...a: unknown[]) => addMemoryMock(...a),
      patchMemory: (...a: unknown[]) => patchMemoryMock(...a),
      deleteMemory: (...a: unknown[]) => deleteMemoryMock(...a),
    },
  }
})

import { MemoryModalView, MemoryModal, type MemoryModalViewProps } from "../MemoryModal"
import { ApiError } from "../../../../../lib/api"
import type { ProjectMember, ProjectMemorySummary } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const MEMBERS: ProjectMember[] = [
  { kind: "agent", user_id: null, name: "Sprntly", role_label: "Agent coworker", status: "working" },
  { kind: "human", user_id: "u1", name: "David M.", email: "d@example.com", avatar_url: null, job_role: "PM", added_at: hoursAgo(48) },
]

const SUMMARY: ProjectMemorySummary = {
  summary_md: "A Xometry-driven redesign of on-demand quoting.",
  entry_count: 2,
  stale: false,
}

const noop = () => {}

function viewProps(overrides: Partial<MemoryModalViewProps> = {}): MemoryModalViewProps {
  return {
    open: true,
    state: { status: "ready", summary: SUMMARY },
    onClose: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  memorySummaryMock.mockReset()
  memoryEntriesMock.mockReset()
  addMemoryMock.mockReset()
  patchMemoryMock.mockReset()
  deleteMemoryMock.mockReset()
})

describe("MemoryModalView — title relabel (C1)", () => {
  it("test_memory_modal_title_reads_memory — memory-modal-title starts with Memory", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    expect(screen.getByTestId("memory-modal-title").textContent?.trim()).toMatch(/^Memory/)
  })
})

describe("MemoryModalView — synthesized summary block", () => {
  it("renders the read-only block with the lock tag, narrative, and entry-count foot — no edit/remove controls on it", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const block = screen.getByTestId("memory-synth-block")
    expect(within(block).getByTestId("memory-synth-readonly-tag").textContent).toContain("Read-only")
    expect(within(block).getByTestId("memory-synth-body").textContent).toContain("Xometry-driven redesign")
    expect(block.textContent).toContain("Synthesized from 2 memories")
    expect(within(block).queryByRole("button")).toBeNull()
  })

  it("renders the synthesized Markdown as structured, safe prose instead of raw syntax", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({
          state: {
            status: "ready",
            summary: {
              ...SUMMARY,
              summary_md: "## Priorities\n\n- **Fast** quoting\n- Keep `lead time` visible\n\n<script>alert('no')</script>",
            },
          },
        }),
      ),
    )

    const body = screen.getByTestId("memory-synth-body")
    expect(within(body).getByRole("heading", { level: 2, name: "Priorities" })).toBeTruthy()
    expect(within(body).getAllByRole("listitem")).toHaveLength(2)
    expect(within(body).getByText("Fast").tagName).toBe("STRONG")
    expect(within(body).getByText("lead time").tagName).toBe("CODE")
    expect(body.textContent).not.toContain("##")
    expect(body.querySelector("script")).toBeNull()
  })

  it("renders a muted 'Synthesis pending' placeholder when summary_md is null, without crashing", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, summary_md: null } } }),
      ),
    )
    expect(screen.getByTestId("memory-synth-pending").textContent).toContain("Synthesis pending")
    expect(screen.queryByTestId("memory-synth-body")).toBeNull()
  })

  it("test_summary_stale_shows_refreshing — summary.stale === true renders the Updating… indicator", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, stale: true } } }),
      ),
    )
    const indicator = screen.getByTestId("memory-synth-refreshing")
    expect(indicator.textContent).toContain("Updating")
  })

  it("test_summary_not_stale_hides_refreshing — summary.stale === false hides the indicator; the summary body still renders", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, stale: false } } }),
      ),
    )
    expect(screen.queryByTestId("memory-synth-refreshing")).toBeNull()
    expect(screen.getByTestId("memory-synth-body").textContent).toContain("Xometry-driven redesign")
  })
})

describe("MemoryModalView — summary-only render (C2 / AC5)", () => {
  it("test_memory_modal_renders_summary_only — open+loaded renders the summary + privacy strip, and NONE of the removed affordances", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    expect(screen.getByTestId("memory-synth-block")).toBeTruthy()
    expect(screen.getByTestId("memory-privacy-strip")).toBeTruthy()

    expect(screen.queryByTestId("memory-add-input")).toBeNull()
    expect(screen.queryByTestId("memory-add-submit")).toBeNull()
    expect(screen.queryByTestId("memory-entries-list")).toBeNull()
    expect(screen.queryByTestId("memory-entries-empty")).toBeNull()
    expect(screen.queryByTestId(/^memory-edit-/)).toBeNull()
    expect(screen.queryByTestId(/^memory-remove-/)).toBeNull()
    expect(screen.queryByTestId("memory-reversibility-note")).toBeNull()
    expect(screen.queryByTestId("memory-mutation-error")).toBeNull()
    expect(screen.queryByTestId("memory-src-user")).toBeNull()
    expect(screen.queryByTestId("memory-src-agent")).toBeNull()
  })

  it("renders the privacy-boundary strip", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    expect(screen.getByTestId("memory-privacy-strip").textContent).toContain("never feed project memory")
  })
})

describe("MemoryModalView — load states", () => {
  it("renders a loading indicator", () => {
    render(React.createElement(MemoryModalView, viewProps({ state: { status: "loading" } })))
    expect(screen.getByTestId("memory-modal-loading")).toBeTruthy()
  })

  it("renders a graceful 'not a member' state on forbidden", () => {
    render(React.createElement(MemoryModalView, viewProps({ state: { status: "forbidden" } })))
    expect(screen.getByTestId("memory-modal-forbidden")).toBeTruthy()
  })

  it("renders a graceful 'not found' state", () => {
    render(React.createElement(MemoryModalView, viewProps({ state: { status: "not_found" } })))
    expect(screen.getByTestId("memory-modal-not-found")).toBeTruthy()
  })

  it("renders a graceful generic error state", () => {
    render(React.createElement(MemoryModalView, viewProps({ state: { status: "error" } })))
    expect(screen.getByTestId("memory-modal-error")).toBeTruthy()
  })
})

describe("MemoryModalView — a11y mechanics", () => {
  it("closes on Escape and on backdrop click", () => {
    const onClose = vi.fn()
    render(React.createElement(MemoryModalView, viewProps({ onClose })))
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(document.querySelector(".modal-overlay") as Element)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("closes on Escape dispatched at the document level — not routed through the panel's own onKeyDown", () => {
    const onClose = vi.fn()
    render(React.createElement(MemoryModalView, viewProps({ onClose })))
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("focus lands inside the dialog on open, and the close button is keyboard-reachable", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    expect(document.activeElement).not.toBe(document.body)
    expect(screen.getByTestId("memory-modal-close").tagName).toBe("BUTTON")
  })

  it("renders nothing when closed", () => {
    render(React.createElement(MemoryModalView, viewProps({ open: false })))
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})

describe("MemoryModalView — Tab focus-trap (regression — now a single focusable control)", () => {
  it("Tab from the (only) focusable close button wraps to itself; Shift+Tab does too", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const dialog = screen.getByRole("dialog")
    const close = screen.getByTestId("memory-modal-close")

    close.focus()
    expect(document.activeElement).toBe(close)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(close)

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(close)
  })
})

describe("MemoryModalView — Escape listener cleanup (no leaked listener)", () => {
  it("does not call onClose for Escape dispatched after the modal has closed", () => {
    const onClose = vi.fn()
    const { rerender } = render(React.createElement(MemoryModalView, viewProps({ onClose })))
    rerender(React.createElement(MemoryModalView, viewProps({ open: false, onClose })))
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("does not call onClose for Escape dispatched after the modal has unmounted", () => {
    const onClose = vi.fn()
    render(React.createElement(MemoryModalView, viewProps({ onClose })))
    cleanup()
    onClose.mockClear()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe("MemoryModal.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../MemoryModal.module.css"), "utf8")
    const globals = readFileSync(join(__dirname, "../../../../../globals.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])

    const synthBlocks = [...css.matchAll(/([^{}]*\.synthBody[^{}]*)\{([^{}]*)\}/g)]
      .map(([, , declarations]) => declarations)
      .join("\n")
    const colorDeclarations = synthBlocks
      .split(";")
      .filter((declaration) => /^\s*(?:color|background|border(?:-[\w-]+)?)\s*:/.test(declaration))
      .join("\n")
    const referencedTokens = [...colorDeclarations.matchAll(/var\((--[\w-]+)\)/g)].map(([, token]) => token)
    const declaredTokens = new Set([...globals.matchAll(/(--[\w-]+)\s*:/g)].map(([, token]) => token))

    expect(referencedTokens.length).toBeGreaterThan(0)
    expect(referencedTokens.filter((token) => !declaredTokens.has(token))).toEqual([])
  })
})

// ── MemoryModal container — summary-only fetch (AC6) ──
describe("MemoryModal — data fetch (summary-only)", () => {
  it("test_memory_modal_loads_summary_only_no_entries_fetch — opening the modal calls memorySummary and NOT memoryEntries/addMemory/patchMemory/deleteMemory", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-synth-block")).toBeTruthy())
    expect(memorySummaryMock).toHaveBeenCalledWith("101")
    expect(memoryEntriesMock).not.toHaveBeenCalled()
    expect(addMemoryMock).not.toHaveBeenCalled()
    expect(patchMemoryMock).not.toHaveBeenCalled()
    expect(deleteMemoryMock).not.toHaveBeenCalled()
  })

  it("fetches nothing while closed", () => {
    render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: false, onClose: noop }))
    expect(memorySummaryMock).not.toHaveBeenCalled()
    expect(memoryEntriesMock).not.toHaveBeenCalled()
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    memorySummaryMock.mockRejectedValue(new ApiError(403, "Not a member"))
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-modal-forbidden")).toBeTruthy())
  })

  it("renders a graceful 'not found' state on a 404, never a crash", async () => {
    memorySummaryMock.mockRejectedValue(new ApiError(404, "Not found"))
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "999", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-modal-not-found")).toBeTruthy())
  })

  it("renders a graceful generic-error state, never a crash", async () => {
    memorySummaryMock.mockRejectedValue(new Error("network blip"))
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-modal-error")).toBeTruthy())
  })
})

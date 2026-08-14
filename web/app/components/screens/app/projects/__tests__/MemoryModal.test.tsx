// @vitest-environment jsdom
//
// MemoryModal — the layered project-memory modal: a read-only synthesized
// "What this project knows" block (AC1/AC2) above an add-composer + the
// discrete, provenance-tagged entries list (AC3/AC4/AC5/AC6). Tests cover
// both the pure `MemoryModalView` (rendering/provenance/a11y) and the
// `MemoryModal` container's fetch + mutate wiring against a mocked
// `projectsApi`, mirroring `ProjectDetailScreen.test.tsx`'s View/Screen
// split posture.
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
      memoryEntries: (...a: unknown[]) => memoryEntriesMock(...a),
      addMemory: (...a: unknown[]) => addMemoryMock(...a),
      patchMemory: (...a: unknown[]) => patchMemoryMock(...a),
      deleteMemory: (...a: unknown[]) => deleteMemoryMock(...a),
    },
  }
})

import { MemoryModalView, MemoryModal, type MemoryModalViewProps } from "../MemoryModal"
import { ApiError } from "../../../../../lib/api"
import type { ProjectMember, ProjectMemoryEntry, ProjectMemorySummary } from "../../../../../lib/api"

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

const USER_ENTRY: ProjectMemoryEntry = {
  id: 1,
  project_id: 101,
  body: "Guardrail: never quote below cost + 12% margin.",
  author_user_id: "u1",
  promoted_by: null,
  source_conversation_id: null,
  created_at: hoursAgo(2),
  updated_at: hoursAgo(1),
}

const AGENT_ENTRY: ProjectMemoryEntry = {
  id: 2,
  project_id: 101,
  body: "Upload is the drop-off point, not the summary screen.",
  author_user_id: null,
  promoted_by: "agent",
  source_conversation_id: 55,
  source_conversation_kind: "group",
  created_at: hoursAgo(5),
  updated_at: hoursAgo(2),
}

const noop = () => {}

function viewProps(overrides: Partial<MemoryModalViewProps> = {}): MemoryModalViewProps {
  return {
    open: true,
    members: MEMBERS,
    state: { status: "ready", summary: SUMMARY, entries: [USER_ENTRY, AGENT_ENTRY] },
    addValue: "",
    onAddValueChange: noop,
    onAdd: noop,
    adding: false,
    editingId: null,
    editValue: "",
    onEditValueChange: noop,
    onStartEdit: noop,
    onSaveEdit: noop,
    onCancelEdit: noop,
    onRemove: noop,
    onClose: noop,
    mutationError: null,
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

describe("MemoryModalView — synthesized summary block (AC1/AC2)", () => {
  it("renders the read-only block with the lock tag, narrative, and entry-count foot — no edit/remove controls on it", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const block = screen.getByTestId("memory-synth-block")
    expect(within(block).getByTestId("memory-synth-readonly-tag").textContent).toContain("Read-only")
    expect(within(block).getByTestId("memory-synth-body").textContent).toContain("Xometry-driven redesign")
    expect(block.textContent).toContain("Synthesized from 2 memories")
    expect(within(block).queryByRole("button")).toBeNull()
  })

  it("renders a muted 'Synthesis pending' placeholder when summary_md is null, without crashing", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, summary_md: null }, entries: [] } }),
      ),
    )
    expect(screen.getByTestId("memory-synth-pending").textContent).toContain("Synthesis pending")
    expect(screen.queryByTestId("memory-synth-body")).toBeNull()
  })
})

describe("MemoryModalView — provenance differentiation (AC3/AC4)", () => {
  it("test_user_entry_renders_manual_chip — renders user-authored entries with a Manual pill, 'Added by <name> · <role>', and the accent user treatment", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const row = screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)
    expect(row.getAttribute("data-provenance")).toBe("user")
    expect(row.textContent).toContain("Manual")
    expect(row.textContent).toContain("Added by David M. · PM")
    expect(within(row).getByTestId("memory-src-user").textContent).toContain("Manual")
    expect(within(row).queryByTestId("memory-src-agent")).toBeNull()
  })

  it("test_agent_entry_renders_promoted_chip — renders agent-promoted entries as muted 'Promoted by Sprntly' with a header chip, never a guessed author", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const row = screen.getByTestId(`memory-entry-${AGENT_ENTRY.id}`)
    expect(row.getAttribute("data-provenance")).toBe("agent")
    expect(row.textContent).toContain("Promoted by Sprntly")
    expect(row.textContent).not.toContain("Manual")
    expect(row.textContent).not.toContain("David")
    // NEW: header chip, visually parallel to the Manual chip on user rows.
    const chip = within(row).getByTestId("memory-src-agent")
    expect(chip.textContent).toContain("Promoted by Sprntly")
    expect(within(row).queryByTestId("memory-src-user")).toBeNull()
  })

  it("test_agent_entry_source_hint — an agent entry sourced from the group chat shows the group-chat hint; with no source, no hint", () => {
    const { rerender } = render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: SUMMARY, entries: [AGENT_ENTRY] } }),
      ),
    )
    const withSource = screen.getByTestId(`memory-entry-${AGENT_ENTRY.id}`)
    expect(withSource.textContent).toContain("from the group chat")
    expect(withSource.textContent).not.toContain("from a chat with Sprntly")

    const noSourceEntry: ProjectMemoryEntry = {
      ...AGENT_ENTRY,
      source_conversation_id: null,
      source_conversation_kind: null,
    }
    rerender(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: SUMMARY, entries: [noSourceEntry] } }),
      ),
    )
    const withoutSource = screen.getByTestId(`memory-entry-${noSourceEntry.id}`)
    expect(withoutSource.textContent).not.toContain("from the group chat")
  })

  it("FIX B — an agent entry sourced from an INDIVIDUAL chat shows 'from a chat with Sprntly', never 'from the group chat'", () => {
    // Ground: source_conversation_id is set for BOTH the group-chat
    // mention path and the individual-chat cross-chat promotion path — the
    // chip must read the actual source_conversation_kind, not assume group
    // whenever the id is set (the mislabel this fixes).
    const individualEntry: ProjectMemoryEntry = {
      ...AGENT_ENTRY,
      id: 3,
      source_conversation_id: 77,
      source_conversation_kind: "individual",
    }
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: SUMMARY, entries: [individualEntry] } }),
      ),
    )
    const row = screen.getByTestId(`memory-entry-${individualEntry.id}`)
    expect(row.textContent).toContain("from a chat with Sprntly")
    expect(row.textContent).not.toContain("from the group chat")
  })
})

describe("MemoryModalView — stale/refreshing affordance (AD-P3 honesty)", () => {
  it("test_summary_stale_shows_refreshing — summary.stale === true renders the Updating… indicator", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, stale: true }, entries: [] } }),
      ),
    )
    const indicator = screen.getByTestId("memory-synth-refreshing")
    expect(indicator.textContent).toContain("Updating")
  })

  it("test_summary_not_stale_hides_refreshing — summary.stale === false hides the indicator; the summary body still renders", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, stale: false }, entries: [] } }),
      ),
    )
    expect(screen.queryByTestId("memory-synth-refreshing")).toBeNull()
    expect(screen.getByTestId("memory-synth-body").textContent).toContain("Xometry-driven redesign")
  })

  it("test_summary_block_never_shows_edit_controls — no edit/remove controls in the synth block regardless of stale", () => {
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: { ...SUMMARY, stale: true }, entries: [] } }),
      ),
    )
    const block = screen.getByTestId("memory-synth-block")
    expect(within(block).queryByRole("button")).toBeNull()
    expect(within(block).queryByTestId("memory-edit-1")).toBeNull()
    expect(within(block).queryByTestId("memory-remove-1")).toBeNull()
  })
})

describe("MemoryModalView — edit/remove regression across provenance", () => {
  it("test_edit_remove_present_on_agent_row — pencil/trash controls render and fire for an agent entry", () => {
    const onStartEdit = vi.fn()
    const onRemove = vi.fn()
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: SUMMARY, entries: [AGENT_ENTRY] }, onStartEdit, onRemove }),
      ),
    )
    fireEvent.click(screen.getByTestId(`memory-edit-${AGENT_ENTRY.id}`))
    expect(onStartEdit).toHaveBeenCalledWith(AGENT_ENTRY)
    fireEvent.click(screen.getByTestId(`memory-remove-${AGENT_ENTRY.id}`))
    expect(onRemove).toHaveBeenCalledWith(AGENT_ENTRY.id)
  })

  it("test_edit_remove_present_on_user_row — pencil/trash controls render and fire for a user entry (unchanged P1 behavior)", () => {
    const onStartEdit = vi.fn()
    const onRemove = vi.fn()
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ state: { status: "ready", summary: SUMMARY, entries: [USER_ENTRY] }, onStartEdit, onRemove }),
      ),
    )
    fireEvent.click(screen.getByTestId(`memory-edit-${USER_ENTRY.id}`))
    expect(onStartEdit).toHaveBeenCalledWith(USER_ENTRY)
    fireEvent.click(screen.getByTestId(`memory-remove-${USER_ENTRY.id}`))
    expect(onRemove).toHaveBeenCalledWith(USER_ENTRY.id)
  })
})

describe("MemoryModalView — privacy + reversibility (AC6)", () => {
  it("renders both the privacy-boundary strip and the reversibility note", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    expect(screen.getByTestId("memory-privacy-strip").textContent).toContain("never feed project memory")
    expect(screen.getByTestId("memory-reversibility-note").textContent).toContain("reversible")
  })
})

describe("MemoryModalView — edit/remove controls wired (AC5)", () => {
  it("every entry (regardless of provenance) exposes edit + remove, calling the passed callbacks", () => {
    const onStartEdit = vi.fn()
    const onRemove = vi.fn()
    render(React.createElement(MemoryModalView, viewProps({ onStartEdit, onRemove })))
    fireEvent.click(screen.getByTestId(`memory-edit-${USER_ENTRY.id}`))
    expect(onStartEdit).toHaveBeenCalledWith(USER_ENTRY)
    fireEvent.click(screen.getByTestId(`memory-remove-${AGENT_ENTRY.id}`))
    expect(onRemove).toHaveBeenCalledWith(AGENT_ENTRY.id)
  })

  it("editing mode renders a Save/Cancel pair wired to the passed callbacks", () => {
    const onSaveEdit = vi.fn()
    const onCancelEdit = vi.fn()
    render(
      React.createElement(
        MemoryModalView,
        viewProps({ editingId: USER_ENTRY.id, editValue: "Edited body", onSaveEdit, onCancelEdit }),
      ),
    )
    fireEvent.click(screen.getByTestId(`memory-edit-save-${USER_ENTRY.id}`))
    expect(onSaveEdit).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId(`memory-edit-cancel-${USER_ENTRY.id}`))
    expect(onCancelEdit).toHaveBeenCalledTimes(1)
  })
})

describe("MemoryModalView — add composer", () => {
  it("Add is disabled on empty input and calls onAdd with content typed", () => {
    const onAdd = vi.fn()
    const onAddValueChange = vi.fn()
    const { rerender } = render(React.createElement(MemoryModalView, viewProps({ addValue: "", onAdd, onAddValueChange })))
    expect((screen.getByTestId("memory-add-submit") as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(screen.getByTestId("memory-add-input"), { target: { value: "New guardrail" } })
    expect(onAddValueChange).toHaveBeenCalledWith("New guardrail")

    rerender(React.createElement(MemoryModalView, viewProps({ addValue: "New guardrail", onAdd, onAddValueChange })))
    expect((screen.getByTestId("memory-add-submit") as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByTestId("memory-add-submit"))
    expect(onAdd).toHaveBeenCalledTimes(1)
  })
})

describe("MemoryModalView — mutation-failure alert", () => {
  it("renders a role=alert banner when mutationError is set", () => {
    render(React.createElement(MemoryModalView, viewProps({ mutationError: "Couldn't save that change. Try again." })))
    const alert = screen.getByTestId("memory-mutation-error")
    expect(alert.getAttribute("role")).toBe("alert")
    expect(alert.textContent).toContain("Couldn't save that change. Try again.")
  })

  it("renders nothing when mutationError is null", () => {
    render(React.createElement(MemoryModalView, viewProps({ mutationError: null })))
    expect(screen.queryByTestId("memory-mutation-error")).toBeNull()
  })

  it("test_summary_and_entries_unchanged_on_mutation_error — the summary block and entries list still render normally alongside the alert", () => {
    render(React.createElement(MemoryModalView, viewProps({ mutationError: "Couldn't save that change. Try again." })))
    expect(screen.getByTestId("memory-synth-block")).toBeTruthy()
    expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeTruthy()
    expect(screen.getByTestId(`memory-entry-${AGENT_ENTRY.id}`)).toBeTruthy()
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

  it("Escape closes the whole modal even mid-edit, matching the existing handler's intent (not a new sub-state-first behavior)", () => {
    const onClose = vi.fn()
    render(React.createElement(MemoryModalView, viewProps({ onClose, editingId: USER_ENTRY.id })))
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

describe("MemoryModalView — Tab focus-trap wraps within the dialog (regression)", () => {
  it("Tab from the last focusable wraps to the first; Shift+Tab from the first wraps to the last", () => {
    render(React.createElement(MemoryModalView, viewProps()))
    const dialog = screen.getByRole("dialog")
    const first = screen.getByTestId("memory-modal-close")
    const last = screen.getByTestId(`memory-remove-${AGENT_ENTRY.id}`)

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
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})

// ── MemoryModal container — fetch + mutate against the real endpoints ──
describe("MemoryModal — data fetch + mutations (AC1/AC3/AC5)", () => {
  it("fetches summary + entries from GET .../memory/summary and GET .../memory on open", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([USER_ENTRY, AGENT_ENTRY])
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-synth-block")).toBeTruthy())
    expect(memorySummaryMock).toHaveBeenCalledWith("101")
    expect(memoryEntriesMock).toHaveBeenCalledWith("101")
    expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeTruthy()
  })

  it("fetches nothing while closed", () => {
    render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: false, onClose: noop }))
    expect(memorySummaryMock).not.toHaveBeenCalled()
    expect(memoryEntriesMock).not.toHaveBeenCalled()
  })

  it("the add composer POSTs to addMemory and the new Manual entry appears in the list (AC3)", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([])
    const newEntry: ProjectMemoryEntry = {
      id: 9,
      project_id: 101,
      body: "New team guardrail",
      author_user_id: "u1",
      promoted_by: null,
      source_conversation_id: null,
      created_at: hoursAgo(0),
      updated_at: hoursAgo(0),
    }
    addMemoryMock.mockResolvedValue(newEntry)
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-add-input")).toBeTruthy())
    fireEvent.change(screen.getByTestId("memory-add-input"), { target: { value: "New team guardrail" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId("memory-add-submit"))
    })
    await waitFor(() => expect(addMemoryMock).toHaveBeenCalledWith("101", "New team guardrail"))
    await waitFor(() => expect(screen.getByTestId(`memory-entry-${newEntry.id}`)).toBeTruthy())
    const row = screen.getByTestId(`memory-entry-${newEntry.id}`)
    expect(row.textContent).toContain("Manual")
  })

  it("edit calls PATCH and remove calls DELETE, updating the list on success (AC5)", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([USER_ENTRY])
    patchMemoryMock.mockResolvedValue({ ...USER_ENTRY, body: "Edited guardrail" })
    deleteMemoryMock.mockResolvedValue({ deleted: true })
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeTruthy())

    fireEvent.click(screen.getByTestId(`memory-edit-${USER_ENTRY.id}`))
    fireEvent.change(screen.getByTestId(`memory-edit-input-${USER_ENTRY.id}`), { target: { value: "Edited guardrail" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId(`memory-edit-save-${USER_ENTRY.id}`))
    })
    await waitFor(() => expect(patchMemoryMock).toHaveBeenCalledWith("101", USER_ENTRY.id, "Edited guardrail"))
    await waitFor(() => expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`).textContent).toContain("Edited guardrail"))

    await act(async () => {
      fireEvent.click(screen.getByTestId(`memory-remove-${USER_ENTRY.id}`))
    })
    await waitFor(() => expect(deleteMemoryMock).toHaveBeenCalledWith("101", USER_ENTRY.id))
    await waitFor(() => expect(screen.queryByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeNull())
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    memorySummaryMock.mockRejectedValue(new ApiError(403, "Not a member"))
    memoryEntriesMock.mockResolvedValue([])
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-modal-forbidden")).toBeTruthy())
  })

  it("renders a graceful 'not found' state on a 404, never a crash", async () => {
    memorySummaryMock.mockRejectedValue(new ApiError(404, "Not found"))
    memoryEntriesMock.mockResolvedValue([])
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "999", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-modal-not-found")).toBeTruthy())
  })
})

// ── MemoryModal container — mutation-failure surfacing (this ticket) ──
describe("MemoryModal — mutation-failure surfacing", () => {
  it("test_failed_add_shows_mutation_error — a rejected addMemory renders the alert, and the composer text is preserved", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([])
    addMemoryMock.mockRejectedValue(new Error("network down"))
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-add-input")).toBeTruthy())
    fireEvent.change(screen.getByTestId("memory-add-input"), { target: { value: "New guardrail" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId("memory-add-submit"))
    })
    await waitFor(() => expect(screen.getByTestId("memory-mutation-error")).toBeTruthy())
    expect((screen.getByTestId("memory-add-input") as HTMLTextAreaElement).value).toBe("New guardrail")
  })

  it("test_failed_edit_and_delete_show_error — a rejected patchMemory / deleteMemory each render the alert", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([USER_ENTRY])
    patchMemoryMock.mockRejectedValue(new Error("network down"))
    deleteMemoryMock.mockRejectedValue(new Error("network down"))
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeTruthy())

    fireEvent.click(screen.getByTestId(`memory-edit-${USER_ENTRY.id}`))
    fireEvent.change(screen.getByTestId(`memory-edit-input-${USER_ENTRY.id}`), { target: { value: "Edited" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId(`memory-edit-save-${USER_ENTRY.id}`))
    })
    await waitFor(() => expect(screen.getByTestId("memory-mutation-error")).toBeTruthy())
    // Failed edit leaves the row in edit mode — no silent data loss.
    expect(screen.getByTestId(`memory-edit-input-${USER_ENTRY.id}`)).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByTestId(`memory-remove-${USER_ENTRY.id}`))
    })
    await waitFor(() => expect(deleteMemoryMock).toHaveBeenCalled())
    expect(screen.getByTestId("memory-mutation-error")).toBeTruthy()
    // Row stays visible on a failed delete.
    expect(screen.getByTestId(`memory-entry-${USER_ENTRY.id}`)).toBeTruthy()
  })

  it("test_successful_mutation_clears_error — a following successful mutation removes the alert", async () => {
    memorySummaryMock.mockResolvedValue(SUMMARY)
    memoryEntriesMock.mockResolvedValue([])
    const newEntry: ProjectMemoryEntry = {
      id: 9,
      project_id: 101,
      body: "New team guardrail",
      author_user_id: "u1",
      promoted_by: null,
      source_conversation_id: null,
      created_at: hoursAgo(0),
      updated_at: hoursAgo(0),
    }
    addMemoryMock.mockRejectedValueOnce(new Error("network down")).mockResolvedValueOnce(newEntry)
    await act(async () => {
      render(React.createElement(MemoryModal, { projectId: "101", members: MEMBERS, open: true, onClose: noop }))
    })
    await waitFor(() => expect(screen.getByTestId("memory-add-input")).toBeTruthy())
    fireEvent.change(screen.getByTestId("memory-add-input"), { target: { value: "New team guardrail" } })
    await act(async () => {
      fireEvent.click(screen.getByTestId("memory-add-submit"))
    })
    await waitFor(() => expect(screen.getByTestId("memory-mutation-error")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByTestId("memory-add-submit"))
    })
    await waitFor(() => expect(screen.getByTestId(`memory-entry-${newEntry.id}`)).toBeTruthy())
    expect(screen.queryByTestId("memory-mutation-error")).toBeNull()
  })
})

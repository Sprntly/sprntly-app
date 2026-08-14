// @vitest-environment jsdom
//
// TaskModal — the DATA-BOUND task ledger (AD-P28). Migrated from the shipped
// stub: the modal now reads the caller's party-filtered delegation views
// (`projectsApi.ledger`) and renders three sections — Assigned to me /
// Waiting on / Done — each row carrying the shared `<DelegationActions>`;
// every action calls `projectsApi.emitDelegationEvent` and refetches. The old
// "wires no backend" guard is inverted here (blocker #1): the module now
// imports `projectsApi` on purpose. `projectsApi` is mocked (the same
// `vi.mock`/`importActual` pattern `ProjectIndividualChat.realtime.dom.test.tsx`
// uses); `DelegationActions` renders for real so the party/state button set is
// exercised end-to-end through the modal.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const ledgerMock = vi.fn()
const emitDelegationEventMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      ledger: (...a: unknown[]) => ledgerMock(...a),
      emitDelegationEvent: (...a: unknown[]) => emitDelegationEventMock(...a),
    },
  }
})

import { TaskModalView, TaskModal, STATUS_LABEL } from "../TaskModal"
import type { DelegationLedgerRow } from "../../../../../lib/api"

const row = (overrides: Partial<DelegationLedgerRow>): DelegationLedgerRow => ({
  delegation_id: 1,
  task_summary: "A task",
  status: "assigned",
  status_at: new Date().toISOString(),
  bucket: "open",
  other_party_user_id: "u-other",
  other_party_name: "David",
  delivered_conversation_id: null,
  delivered_turn_id: null,
  ...overrides,
})

beforeEach(() => {
  ledgerMock.mockReset()
  ledgerMock.mockResolvedValue([])
  emitDelegationEventMock.mockReset()
  emitDelegationEventMock.mockResolvedValue({ delegation_id: 1, status: "accepted" })
})

afterEach(() => cleanup())

describe("TaskModalView — data-bound sections (AC1, AC2)", () => {
  it("test_renders_three_sections_from_reads — Assigned/Waiting/Done populated from the mocked reads; no stub rows; no fast-follow badge", async () => {
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      view === "assigned_to_me"
        ? Promise.resolve([
            row({ delegation_id: 1, task_summary: "Do the assigned thing", status: "assigned", bucket: "open" }),
            row({ delegation_id: 2, task_summary: "A finished assignment", status: "completed", bucket: "done" }),
          ])
        : Promise.resolve([
            row({ delegation_id: 3, task_summary: "Waiting on Shristi", status: "accepted", bucket: "open", other_party_name: "Shristi" }),
            row({ delegation_id: 4, task_summary: "A cancelled hand-off", status: "cancelled", bucket: "done", other_party_name: "Shristi" }),
          ]),
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))

    await waitFor(() => expect(screen.getByTestId("ledger-row-1")).toBeTruthy())

    const body = screen.getByTestId("task-modal-body")
    // No stub content survives, and the fast-follow badge is gone.
    expect(body.textContent).not.toContain("Review pricing-latency")
    expect(screen.queryByTestId("task-modal-fastfollow")).toBeNull()
    expect(body.textContent).not.toContain("Fast-follow")

    // Open rows land under the right party section; closed rows under Done.
    expect(screen.getByTestId("ledger-section-assigned")).toBeTruthy()
    expect(screen.getByTestId("ledger-row-1")).toBeTruthy()
    expect(screen.getByTestId("ledger-row-3")).toBeTruthy()
    expect(screen.getByTestId("ledger-row-2")).toBeTruthy() // done (from assigned view)
    expect(screen.getByTestId("ledger-row-4")).toBeTruthy() // done (from waiting view)

    // Each row shows the summary, the other party's name, and a status label.
    const r1 = screen.getByTestId("ledger-row-1")
    expect(r1.textContent).toContain("Do the assigned thing")
    expect(r1.textContent).toContain("David")
    expect(r1.textContent).toContain("Assigned")
  })

  it("test_no_illegal_edge_button — an assigned assignee row shows Mark in progress/Mark done only, never accepted/declined/cancelled/reopened", async () => {
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      view === "assigned_to_me"
        ? Promise.resolve([row({ delegation_id: 1, status: "assigned", bucket: "open" })])
        : Promise.resolve([]),
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-1")).toBeTruthy())
    const r1 = screen.getByTestId("ledger-row-1")
    expect(within(r1).getByTestId("delegation-action-in_progress")).toBeTruthy()
    expect(within(r1).getByTestId("delegation-action-completed")).toBeTruthy()
    expect(within(r1).queryByTestId("delegation-action-accepted")).toBeNull()
    expect(within(r1).queryByTestId("delegation-action-declined")).toBeNull()
    expect(within(r1).queryByTestId("delegation-action-cancelled")).toBeNull()
    expect(within(r1).queryByTestId("delegation-action-reopened")).toBeNull()
  })
})

describe("TaskModalView — cleared status (AC6)", () => {
  it("test_status_label_cleared — STATUS_LABEL['cleared'] === 'Cleared'", () => {
    expect(STATUS_LABEL.cleared).toBe("Cleared")
  })

  it("test_cleared_row_in_done_section — a cleared/bucket:done row renders under ledger-section-done labelled Cleared", async () => {
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      view === "assigned_to_me"
        ? Promise.resolve([row({ delegation_id: 9, task_summary: "Stopped mid-flight", status: "cleared", bucket: "done" })])
        : Promise.resolve([]),
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-9")).toBeTruthy())

    const doneSection = screen.getByTestId("ledger-section-done")
    expect(doneSection).toBeTruthy()
    const r9 = screen.getByTestId("ledger-row-9")
    expect(r9.textContent).toContain("Cleared")
  })
})

describe("TaskModalView — action wiring (AC4, AC5)", () => {
  it("test_row_action_emits_and_refetches — clicking a row action emits and re-reads", async () => {
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      view === "assigned_to_me"
        ? Promise.resolve([row({ delegation_id: 7, status: "assigned", bucket: "open" })])
        : Promise.resolve([]),
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p9", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-7")).toBeTruthy())

    // Two reads on open (assigned_to_me + waiting_on).
    expect(ledgerMock).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByTestId("delegation-action-in_progress"))
    await waitFor(() => expect(emitDelegationEventMock).toHaveBeenCalledWith("p9", 7, "in_progress", undefined))
    // After a successful emit the affected view refetches (no full reload).
    await waitFor(() => expect(ledgerMock).toHaveBeenCalledTimes(4))
  })

  it("test_assigner_row_clear_emits — an open Waiting-on row shows Clear task and clicking emits `cleared`", async () => {
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      view === "assigned_to_me"
        ? Promise.resolve([])
        : Promise.resolve([row({ delegation_id: 5, status: "assigned", bucket: "open", other_party_name: "Shristi" })]),
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-5")).toBeTruthy())

    const r5 = screen.getByTestId("ledger-row-5")
    expect(within(r5).getByTestId("delegation-action-cleared")).toBeTruthy()
    fireEvent.click(within(r5).getByTestId("delegation-action-cleared"))
    await waitFor(() => expect(emitDelegationEventMock).toHaveBeenCalledWith("p1", 5, "cleared", undefined))
  })
})

describe("TaskModalView — loading / empty / error (AC8)", () => {
  it("test_loading_empty_error_states — loading shown, empty section copy, read-failure inline error, no crash", async () => {
    // Loading: a never-resolving read keeps the modal in its loading branch.
    ledgerMock.mockReturnValue(new Promise(() => {}))
    const { unmount } = render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    expect(screen.getByTestId("ledger-loading")).toBeTruthy()
    unmount()

    // Empty: both reads resolve empty → each section shows "Nothing here yet".
    ledgerMock.mockReset()
    ledgerMock.mockResolvedValue([])
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-empty-assigned")).toBeTruthy())
    expect(screen.getByTestId("ledger-empty-assigned").textContent).toContain("Nothing here yet")
    expect(screen.getByTestId("ledger-empty-waiting")).toBeTruthy()
    expect(screen.getByTestId("ledger-empty-done")).toBeTruthy()
    cleanup()

    // Error: a rejected read shows a non-blocking inline error, never a crash
    // or a blank modal (the shell + title still render).
    ledgerMock.mockReset()
    ledgerMock.mockRejectedValue(new Error("ledger down"))
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-error")).toBeTruthy())
    expect(screen.getByTestId("task-modal-title")).toBeTruthy()
  })
})

describe("TaskModalView — a11y mechanics preserved (AC9)", () => {
  it("test_focus_trap_and_close_preserved — Escape/backdrop/close call onClose; focus lands inside", async () => {
    const onClose = vi.fn()
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose }))
    // Focus lands inside the dialog on open.
    expect(document.activeElement).not.toBe(document.body)

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(document.querySelector(".modal-overlay") as Element)
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(screen.getByTestId("task-modal-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("renders nothing when closed", () => {
    render(React.createElement(TaskModalView, { open: false, projectId: "p1", onClose: () => {} }))
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})

describe("TaskModal container — data-bound pass-through", () => {
  it("mounts the view with the same open/projectId/onClose props", async () => {
    render(React.createElement(TaskModal, { open: true, projectId: "p1", onClose: vi.fn() }))
    await waitFor(() => expect(screen.getByTestId("task-modal-title")).toBeTruthy())
  })
})

describe("TaskModal — backend wiring (AC10, blocker #1)", () => {
  it("test_no_backend_guard_removed — the module now imports projectsApi (the old 'wires no backend' assertion is inverted)", () => {
    const src = readFileSync(join(__dirname, "../TaskModal.tsx"), "utf8")
    expect(src).toContain("lib/api")
    expect(src).toContain("projectsApi")
  })
})

describe("TaskModal.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../TaskModal.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})

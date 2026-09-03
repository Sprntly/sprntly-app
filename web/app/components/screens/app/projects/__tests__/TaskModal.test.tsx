// @vitest-environment jsdom
//
// TaskModal — the DATA-BOUND task ledger, redesigned as ONE FLAT TABLE
// (commit 8f217ad3c, replacing the three-section Assigned/Waiting/Done layout).
// The modal reads the caller's party-filtered delegation views
// (`projectsApi.ledger`) and unions them into a single table with columns
// Task / Assigned to / Created by plus a leading complete-checkbox. Only rows
// the viewer OWNS (they are the assignee — `assigned_to_me`) get a checkbox;
// rows they created but handed off (`waiting_on`) are informational and render
// a no-checkbox placeholder. Ticking an owned, routable row completes it AS IF
// the assignee typed a completion into their chat — routed through the project
// chat's own `submitAsk`, exposed to the modal as the `onCompleteTask(text)`
// prop (the single ask→persist path); the row optimistically strikes/checks.
//
// This file preserves the ORIGINAL test intents against the new DOM:
//  - table rows populated from the two mocked reads (was: three sections)
//  - the right per-party affordance (was: legal-action button set) — a tick
//    checkbox ONLY on owned rows, a no-checkbox placeholder on handed-off rows
//  - a done/cleared row renders struck/checked (was: under the Done section)
//  - completing a task invokes the completion path with the right text and the
//    row goes to a done/struck state (was: emit + refetch)
//  - the open-count summary reflects open tasks
//  - loading / empty / read-error states (was: per-section empties)
//  - a live `ledgerVersion` bump re-reads the table (realtime reconcile intent)
//  - a11y focus-trap + close mechanics, STATUS_LABEL export, backend wiring,
//    module-CSS-tokens — all preserved.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const ledgerMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      ledger: (...a: unknown[]) => ledgerMock(...a),
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
  // A routable owned row needs a delivered conversation; default provides one so
  // an owned row is tickable unless a test overrides it to null.
  delivered_conversation_id: 9001,
  delivered_turn_id: 7,
  ...overrides,
})

/** Drive the two party-reads from explicit assigned/waiting arrays. */
function mockViews(assigned: DelegationLedgerRow[], waiting: DelegationLedgerRow[]) {
  ledgerMock.mockImplementation((_id: unknown, view: string) =>
    Promise.resolve(view === "assigned_to_me" ? assigned : waiting),
  )
}

beforeEach(() => {
  ledgerMock.mockReset()
  ledgerMock.mockResolvedValue([])
})

afterEach(() => cleanup())

describe("TaskModalView — flat table from the two reads (AC1, AC2)", () => {
  it("test_renders_table_rows_from_reads — owned + handed-off rows land in ONE table; no stub content", async () => {
    mockViews(
      [
        row({ delegation_id: 1, task_summary: "Do the assigned thing", status: "assigned", bucket: "open" }),
        row({ delegation_id: 2, task_summary: "A finished assignment", status: "completed", bucket: "done" }),
      ],
      [
        row({ delegation_id: 3, task_summary: "Waiting on Shristi", status: "accepted", bucket: "open", other_party_name: "Shristi", other_party_user_id: "u-shristi" }),
        row({ delegation_id: 4, task_summary: "A cancelled hand-off", status: "cancelled", bucket: "done", other_party_name: "Shristi", other_party_user_id: "u-shristi" }),
      ],
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))

    await waitFor(() => expect(screen.getByTestId("ledger-row-1")).toBeTruthy())

    const body = screen.getByTestId("task-modal-body")
    // No stub content survives from the shipped placeholder modal.
    expect(body.textContent).not.toContain("Review pricing-latency")
    expect(screen.queryByTestId("task-modal-fastfollow")).toBeNull()

    // One flat table carries rows from BOTH party-views (open + done alike).
    expect(screen.getByTestId("ledger-table")).toBeTruthy()
    expect(screen.getByTestId("ledger-row-1")).toBeTruthy() // owned, open
    expect(screen.getByTestId("ledger-row-2")).toBeTruthy() // owned, done
    expect(screen.getByTestId("ledger-row-3")).toBeTruthy() // handed off, open
    expect(screen.getByTestId("ledger-row-4")).toBeTruthy() // handed off, done

    // The header advertises the three data columns.
    const table = screen.getByTestId("ledger-table")
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent)
    expect(headers).toContain("Task")
    expect(headers).toContain("Assigned to")
    expect(headers).toContain("Created by")

    // An OWNED row: Task text present; the viewer is the assignee ("You" in
    // Assigned-to) and the other party is the creator ("David" in Created-by).
    const r1 = screen.getByTestId("ledger-row-1")
    const cells1 = within(r1).getAllByRole("cell")
    expect(r1.textContent).toContain("Do the assigned thing")
    // cells: [check][Task][Assigned to][Created by]
    expect(cells1[2].textContent).toContain("You")
    expect(cells1[3].textContent).toContain("David")

    // A HANDED-OFF row: the other party is the assignee ("Shristi" in
    // Assigned-to) and the viewer is the creator ("You" in Created-by).
    const r3 = screen.getByTestId("ledger-row-3")
    const cells3 = within(r3).getAllByRole("cell")
    expect(cells3[2].textContent).toContain("Shristi")
    expect(cells3[3].textContent).toContain("You")
  })

  it("test_checkbox_only_on_owned_rows — owned row gets a tick checkbox; handed-off row gets a no-checkbox placeholder", async () => {
    mockViews(
      [row({ delegation_id: 1, status: "assigned", bucket: "open" })],
      [row({ delegation_id: 5, status: "assigned", bucket: "open", other_party_name: "Shristi", other_party_user_id: "u-shristi" })],
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-1")).toBeTruthy())

    // Owned row: a real, enabled checkbox is the completion affordance.
    const check = screen.getByTestId("ledger-check-1") as HTMLButtonElement
    expect(check).toBeTruthy()
    expect(check.getAttribute("role")).toBe("checkbox")
    expect(check.getAttribute("aria-checked")).toBe("false")
    expect(check.disabled).toBe(false)
    // No legacy per-row status-transition buttons survive the redesign.
    expect(within(screen.getByTestId("ledger-row-1")).queryByTestId("delegation-action-in_progress")).toBeNull()
    expect(within(screen.getByTestId("ledger-row-1")).queryByTestId("delegation-action-completed")).toBeNull()

    // Handed-off (waiting-on) row: informational — NO checkbox, a placeholder.
    expect(screen.queryByTestId("ledger-check-5")).toBeNull()
    expect(screen.getByTestId("ledger-nocheck-5")).toBeTruthy()
  })

  it("test_owned_non_routable_row_disabled — an owned row with no delivered conversation shows a disabled checkbox", async () => {
    mockViews(
      [row({ delegation_id: 1, status: "assigned", bucket: "open", delivered_conversation_id: null })],
      [],
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-1")).toBeTruthy())
    const check = screen.getByTestId("ledger-check-1") as HTMLButtonElement
    expect(check.disabled).toBe(true)
  })
})

describe("TaskModalView — cleared / done rows render struck (AC6)", () => {
  it("test_status_label_cleared — STATUS_LABEL['cleared'] === 'Cleared'", () => {
    expect(STATUS_LABEL.cleared).toBe("Cleared")
  })

  it("test_done_row_renders_struck — a cleared/bucket:done owned row is checked, struck, and its checkbox disabled", async () => {
    mockViews(
      [row({ delegation_id: 9, task_summary: "Stopped mid-flight", status: "cleared", bucket: "done" })],
      [],
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-row-9")).toBeTruthy())

    const r9 = screen.getByTestId("ledger-row-9")
    // The done row carries the struck row class (its class list changes vs open).
    expect(r9.className).not.toBe("")
    const check = screen.getByTestId("ledger-check-9") as HTMLButtonElement
    expect(check.getAttribute("aria-checked")).toBe("true")
    expect(check.disabled).toBe(true)
  })
})

describe("TaskModalView — completing a task (AC4, AC5)", () => {
  it("test_tick_owned_row_invokes_completion — clicking calls onCompleteTask with the completion text and the row goes done/struck", async () => {
    mockViews(
      [row({ delegation_id: 7, task_summary: "Ship the pricing page", status: "assigned", bucket: "open", delivered_conversation_id: 4242 })],
      [],
    )
    const onCompleteTask = vi.fn().mockResolvedValue(undefined)
    render(
      React.createElement(TaskModalView, { open: true, projectId: "p9", onClose: () => {}, onCompleteTask }),
    )
    await waitFor(() => expect(screen.getByTestId("ledger-row-7")).toBeTruthy())

    // Two party-reads on open (assigned_to_me + waiting_on).
    expect(ledgerMock).toHaveBeenCalledTimes(2)

    const check = screen.getByTestId("ledger-check-7") as HTMLButtonElement
    expect(check.getAttribute("aria-checked")).toBe("false")

    fireEvent.click(check)

    // The completion is routed through the chat's ask path with the exact
    // completion phrasing the classifier keys on (embeds the task summary).
    await waitFor(() =>
      expect(onCompleteTask).toHaveBeenCalledWith(
        'I\'ve finished this task: "Ship the pricing page". Please mark it complete.',
      ),
    )

    // Optimistic strike: the row is now checked/struck (kept until the live
    // reconcile lands — the code deliberately does NOT re-read on resolve).
    await waitFor(() => expect(screen.getByTestId("ledger-check-7").getAttribute("aria-checked")).toBe("true"))
  })

  it("test_tick_failure_reverts_and_surfaces_error — a rejected completion un-strikes the row and shows the inline error", async () => {
    mockViews(
      [row({ delegation_id: 8, task_summary: "Flaky task", status: "assigned", bucket: "open", delivered_conversation_id: 4242 })],
      [],
    )
    const onCompleteTask = vi.fn().mockRejectedValue(new Error("chat not ready"))
    render(
      React.createElement(TaskModalView, { open: true, projectId: "p9", onClose: () => {}, onCompleteTask }),
    )
    await waitFor(() => expect(screen.getByTestId("ledger-row-8")).toBeTruthy())

    fireEvent.click(screen.getByTestId("ledger-check-8"))

    // The inline action error surfaces and the optimistic strike reverts.
    await waitFor(() => expect(screen.getByTestId("ledger-action-error")).toBeTruthy())
    await waitFor(() => expect(screen.getByTestId("ledger-check-8").getAttribute("aria-checked")).toBe("false"))
  })

  it("test_handed_off_row_cannot_complete — a handed-off row has no checkbox, so the viewer can't complete it", async () => {
    mockViews(
      [],
      [row({ delegation_id: 5, status: "assigned", bucket: "open", other_party_name: "Shristi", other_party_user_id: "u-shristi" })],
    )
    const onCompleteTask = vi.fn().mockResolvedValue(undefined)
    render(
      React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {}, onCompleteTask }),
    )
    await waitFor(() => expect(screen.getByTestId("ledger-row-5")).toBeTruthy())
    // No completion affordance on an assigner row.
    expect(screen.queryByTestId("ledger-check-5")).toBeNull()
    expect(onCompleteTask).not.toHaveBeenCalled()
  })
})

describe("TaskModalView — open-count summary (AC3)", () => {
  it("test_open_summary_counts_open_rows — the summary reflects only open (not done) rows", async () => {
    mockViews(
      [
        row({ delegation_id: 1, bucket: "open" }),
        row({ delegation_id: 2, bucket: "done", status: "completed" }),
      ],
      [row({ delegation_id: 3, bucket: "open", other_party_name: "Shristi", other_party_user_id: "u-shristi" })],
    )
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-open-summary")).toBeTruthy())
    // Two open rows (ids 1 + 3); the done row (id 2) is excluded.
    expect(screen.getByTestId("ledger-open-summary").textContent).toContain("2 open")
  })
})

describe("TaskModalView — live ledgerVersion reconcile (realtime intent)", () => {
  it("test_ledgerversion_bump_rereads_table — a version bump re-reads the views and the table reflects the new state", async () => {
    // Start: one open owned row.
    let assigned: DelegationLedgerRow[] = [
      row({ delegation_id: 7, task_summary: "Ship the pricing page", status: "assigned", bucket: "open", delivered_conversation_id: 4242 }),
    ]
    ledgerMock.mockImplementation((_id: unknown, view: string) =>
      Promise.resolve(view === "assigned_to_me" ? assigned : []),
    )
    const { rerender } = render(
      React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {}, ledgerVersion: 0 }),
    )
    await waitFor(() => expect(screen.getByTestId("ledger-check-7").getAttribute("aria-checked")).toBe("false"))
    expect(screen.getByTestId("ledger-open-summary").textContent).toContain("1 open")

    // A live delegation.event moved the row to done on the server; the parent
    // bumps ledgerVersion → the modal reconciles from the new read.
    assigned = [
      row({ delegation_id: 7, task_summary: "Ship the pricing page", status: "completed", bucket: "done", delivered_conversation_id: 4242 }),
    ]
    rerender(
      React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {}, ledgerVersion: 1 }),
    )

    await waitFor(() => expect(screen.getByTestId("ledger-check-7").getAttribute("aria-checked")).toBe("true"))
    await waitFor(() => expect(screen.getByTestId("ledger-open-summary").textContent).toContain("0 open"))
  })
})

describe("TaskModalView — loading / empty / error (AC8)", () => {
  it("test_loading_empty_error_states — loading shown, single empty-state copy, read-failure inline error, no crash", async () => {
    // Loading: a never-resolving read keeps the modal in its loading branch.
    ledgerMock.mockReturnValue(new Promise(() => {}))
    const { unmount } = render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    expect(screen.getByTestId("ledger-loading")).toBeTruthy()
    unmount()

    // Empty: both reads resolve empty → one empty-state row (no per-section splits).
    ledgerMock.mockReset()
    ledgerMock.mockResolvedValue([])
    render(React.createElement(TaskModalView, { open: true, projectId: "p1", onClose: () => {} }))
    await waitFor(() => expect(screen.getByTestId("ledger-empty")).toBeTruthy())
    expect(screen.queryByTestId("ledger-table")).toBeNull()
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

describe("TaskModal — backend wiring (AC10)", () => {
  it("test_reads_the_ledger_api — the module imports projectsApi and reads the ledger views", () => {
    const src = readFileSync(join(__dirname, "../TaskModal.tsx"), "utf8")
    expect(src).toContain("lib/api")
    expect(src).toContain("projectsApi")
    expect(src).toContain("projectsApi.ledger")
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

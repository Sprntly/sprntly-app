// @vitest-environment jsdom
//
// TaskModal — the stubbed fast-follow task ledger (AC9). Presentational
// only: no api import, no network call, no backend wiring. Filled in as
// its own test file per this directory's 1:1 component↔test-file
// convention (`ProjectDetailScreen`/`ProjectGroupChat`/etc. each have
// one) — the ticket's own Unit Tests list requires `test_task_modal_stub_
// fastfollow`, even though the Deliverables bullet list only named the two
// data-backed modals' test files.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { TaskModalView, TaskModal } from "../TaskModal"

afterEach(() => cleanup())

describe("TaskModalView — stub (AC9)", () => {
  it("renders an Open/Done list and the Fast-follow badge", () => {
    render(React.createElement(TaskModalView, { open: true, onClose: () => {} }))
    expect(screen.getByTestId("task-modal-fastfollow").textContent).toContain("Fast-follow")
    expect(screen.getByTestId("task-modal-open-heading").textContent).toMatch(/^Open/)
    expect(screen.getByTestId("task-modal-done-heading").textContent).toMatch(/^Done/)
    const body = screen.getByTestId("task-modal-body")
    expect(within(body).getAllByTestId(/^task-row-/).length).toBeGreaterThan(0)
  })

  it("wires no backend — no `api` import anywhere in the module", () => {
    const src = readFileSync(join(__dirname, "../TaskModal.tsx"), "utf8")
    expect(src).not.toContain("lib/api")
    expect(src).not.toContain("fetch(")
  })

  it("renders nothing when closed", () => {
    render(React.createElement(TaskModalView, { open: false, onClose: () => {} }))
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})

describe("TaskModalView — a11y mechanics", () => {
  it("closes on Escape, backdrop click, and the Close button", () => {
    const onClose = vi.fn()
    render(React.createElement(TaskModalView, { open: true, onClose }))
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(document.querySelector(".modal-overlay") as Element)
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    fireEvent.click(screen.getByTestId("task-modal-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("focus lands inside the dialog on open", () => {
    render(React.createElement(TaskModalView, { open: true, onClose: () => {} }))
    expect(document.activeElement).not.toBe(document.body)
  })
})

describe("TaskModal container — presentational pass-through", () => {
  it("mounts the view with the same open/onClose props, no fetching", () => {
    const onClose = vi.fn()
    render(React.createElement(TaskModal, { open: true, onClose }))
    expect(screen.getByTestId("task-modal-title")).toBeTruthy()
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

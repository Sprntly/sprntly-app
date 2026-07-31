// @vitest-environment jsdom
//
// Deleting a ticket, and excluding one from the PM tool.
//
// Both non-active states delete the tracker copy, so each goes through an
// in-app ConfirmDialog that names the consequence — and a cancelled confirm
// must leave everything untouched. The dialog is queried through
// getByRole("dialog") rather than by button label because the header button
// and the confirm button deliberately read the same ("Exclude from Jira").
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const api = vi.hoisted(() => ({
  getData: vi.fn(),
  saveDescription: vi.fn(),
  saveFields: vi.fn(),
  addAttachment: vi.fn(),
  removeAttachment: vi.fn(),
  addComment: vi.fn(),
  removeComment: vi.fn(),
  summarizeComments: vi.fn(),
  setLifecycle: vi.fn(),
  remove: vi.fn(),
  teamList: vi.fn(),
}))

vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    ticketDataApi: {
      getData: api.getData,
      saveDescription: api.saveDescription,
      saveFields: api.saveFields,
      addAttachment: api.addAttachment,
      removeAttachment: api.removeAttachment,
      addComment: api.addComment,
      removeComment: api.removeComment,
      summarizeComments: api.summarizeComments,
      setLifecycle: api.setLifecycle,
      remove: api.remove,
    },
    teamApi: { list: api.teamList },
  }
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ showToast }) }
})

import { TicketDetail } from "../TicketDetail"

const STORY = {
  title: "Guest alert data model",
  body: "One-click guest-alert.",
  acceptance_criteria: [],
  priority: "P1",
  route: null,
}
const KEY = "prd-7-guest-alert-data-model"

const JIRA_TRACKER = {
  provider: "jira" as const,
  meta: { statuses: [], priorities: [], issue_types: [], fields: [] },
  synced: { status: "To Do", url: "https://acme.atlassian.net/browse/KAN-1" },
}

beforeEach(() => {
  api.getData.mockResolvedValue({
    description: null, acceptance_criteria: null, title: null, priority: null,
    status: null, sprint: null, assignee: null, attachments: [], comments: [],
  })
  api.saveFields.mockResolvedValue({ ok: true })
  api.teamList.mockResolvedValue({ members: [] })
  api.summarizeComments.mockResolvedValue({ summary: null })
  api.setLifecycle.mockResolvedValue({ ok: true, lifecycle: "deleted", tracker_sync_started: true })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function renderDetail(props: Record<string, unknown> = {}) {
  const onBack = vi.fn()
  const onLifecycleChange = vi.fn()
  await act(async () => {
    render(React.createElement(TicketDetail, {
      story: STORY, index: 2, prdId: 7, onBack, onLifecycleChange, ...props,
    }))
  })
  await waitFor(() => expect(api.getData).toHaveBeenCalledWith(KEY))
  return { onBack, onLifecycleChange }
}

const click = async (el: HTMLElement) => { await act(async () => { fireEvent.click(el) }) }
const dialog = () => within(screen.getByRole("dialog"))
const headerBtn = (name: RegExp) =>
  screen.getAllByRole("button", { name }).find((b) => !b.closest('[role="dialog"]')) as HTMLElement

describe("TicketDetail — confirmation dialog", () => {
  it("asks in-app instead of firing the browser confirm", async () => {
    const nativeConfirm = vi.fn()
    vi.stubGlobal("confirm", nativeConfirm)
    await renderDetail()

    await click(headerBtn(/^Delete$/))

    // The native dialog cannot show progress and renders unstyled browser
    // chrome — the whole reason this moved in-app.
    expect(nativeConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole("dialog")).toBeTruthy()
    expect(api.setLifecycle).not.toHaveBeenCalled()  // nothing happens on open
    vi.unstubAllGlobals()
  })

  it("deletes only once the dialog is confirmed, then closes the ticket", async () => {
    const { onBack, onLifecycleChange } = await renderDetail()
    await click(headerBtn(/^Delete$/))

    await click(dialog().getByRole("button", { name: /Delete ticket/i }))

    expect(api.setLifecycle).toHaveBeenCalledWith(KEY, "deleted")
    expect(onLifecycleChange).toHaveBeenCalledWith("deleted")
    // The list drops the ticket, so the index this detail renders is gone.
    expect(onBack).toHaveBeenCalled()
  })

  it("does nothing when the dialog is cancelled", async () => {
    const { onBack, onLifecycleChange } = await renderDetail()
    await click(headerBtn(/^Delete$/))

    await click(dialog().getByRole("button", { name: /^Cancel$/ }))

    expect(screen.queryByRole("dialog")).toBeNull()
    expect(api.setLifecycle).not.toHaveBeenCalled()
    expect(onLifecycleChange).not.toHaveBeenCalled()
    expect(onBack).not.toHaveBeenCalled()
  })

  it("shows progress while the delete is in flight and locks both buttons", async () => {
    let release: (v: unknown) => void = () => {}
    api.setLifecycle.mockReturnValue(new Promise((r) => { release = r }))
    await renderDetail()
    await click(headerBtn(/^Delete$/))

    await click(dialog().getByRole("button", { name: /Delete ticket/i }))

    // Mid-flight: the confirm reports progress and neither button can fire
    // again — the native confirm could show none of this.
    expect(dialog().getByRole("button", { name: /Deleting…/ })).toBeTruthy()
    expect(dialog().getByRole("button", { name: /Deleting…/ })).toHaveProperty("disabled", true)
    expect(dialog().getByRole("button", { name: /^Cancel$/ })).toHaveProperty("disabled", true)

    await act(async () => { release({ ok: true }) })
    expect(api.setLifecycle).toHaveBeenCalledTimes(1)
  })

  it("keeps the dialog open and warns when the request fails", async () => {
    api.setLifecycle.mockRejectedValue(new Error("boom"))
    const { onBack, onLifecycleChange } = await renderDetail()
    await click(headerBtn(/^Delete$/))

    await click(dialog().getByRole("button", { name: /Delete ticket/i }))

    // Closing on failure would read as "done".
    expect(screen.getByRole("dialog")).toBeTruthy()
    expect(showToast).toHaveBeenCalled()
    expect(onLifecycleChange).not.toHaveBeenCalled()
    expect(onBack).not.toHaveBeenCalled()
  })

  it("names the tracker issue only when the ticket was actually pushed", async () => {
    await renderDetail({ tracker: JIRA_TRACKER })
    await click(headerBtn(/^Delete$/))
    expect(screen.getByRole("dialog").textContent).toMatch(/Jira issue will be deleted/i)

    cleanup()
    await renderDetail({ tracker: { ...JIRA_TRACKER, synced: undefined } })
    await click(headerBtn(/^Delete$/))
    // Promising to delete an issue that never existed would be a lie.
    expect(screen.getByRole("dialog").textContent).toMatch(/never pushed/i)
  })

  it("cancels on Escape, but not while the action is in flight", async () => {
    let release: (v: unknown) => void = () => {}
    api.setLifecycle.mockReturnValue(new Promise((r) => { release = r }))
    await renderDetail()
    await click(headerBtn(/^Delete$/))

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }) })
    expect(screen.queryByRole("dialog")).toBeNull()

    await click(headerBtn(/^Delete$/))
    await click(dialog().getByRole("button", { name: /Delete ticket/i }))
    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }) })

    // The request is already gone; closing would suggest it was backed out.
    expect(screen.getByRole("dialog")).toBeTruthy()
    await act(async () => { release({ ok: true }) })
  })
})

describe("TicketDetail — exclude from the tracker", () => {
  it("excludes the ticket without closing the detail", async () => {
    const { onBack, onLifecycleChange } = await renderDetail({ tracker: JIRA_TRACKER })

    await click(headerBtn(/^Exclude from Jira$/))
    expect(screen.getByRole("dialog").textContent).toMatch(/stays in Sprntly/i)
    await click(dialog().getByRole("button", { name: /^Exclude from Jira$/ }))

    expect(api.setLifecycle).toHaveBeenCalledWith(KEY, "excluded")
    expect(onLifecycleChange).toHaveBeenCalledWith("excluded")
    expect(onBack).not.toHaveBeenCalled()
    expect(screen.getByRole("status").textContent).toMatch(/not sent to Jira/i)
  })

  it("puts an excluded ticket back with no confirmation at all", async () => {
    const { onLifecycleChange } = await renderDetail({
      story: { ...STORY, lifecycle: "excluded" }, tracker: JIRA_TRACKER,
    })

    await click(headerBtn(/Include in Jira/))

    // Restoring only ever adds a ticket back — nothing to warn about.
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(api.setLifecycle).toHaveBeenCalledWith(KEY, "active")
    expect(onLifecycleChange).toHaveBeenCalledWith("active")
  })

  it("falls back to a neutral name when the PRD is not bound to a tracker", async () => {
    await renderDetail()
    // Naming a specific tool here would promise one the user may not have.
    expect(headerBtn(/^Exclude from your PM tool$/)).toBeTruthy()
  })

  it("hides the controls when the list cannot act on them", async () => {
    await renderDetail({ onLifecycleChange: undefined })

    expect(screen.queryByRole("button", { name: /^Delete$/ })).toBeNull()
    expect(screen.queryByRole("button", { name: /Exclude/ })).toBeNull()
  })
})

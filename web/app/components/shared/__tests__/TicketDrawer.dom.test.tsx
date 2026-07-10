// @vitest-environment jsdom
//
// TicketDrawer's "Create ticket" surface. When Jira is connected it renders the
// real JiraTicketForm: pick a project, pick an assignee (project members), and
// create a real issue via ticketPushApi.pushToJira (per-task assignee_account_id
// from #701). These tests mock the api client + context hooks and assert that
// wiring.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { connList, listJiraProjects, listJiraMembers, pushToJira, saveTicket } = vi.hoisted(() => ({
  connList: vi.fn(),
  listJiraProjects: vi.fn(),
  listJiraMembers: vi.fn(),
  pushToJira: vi.fn(),
  saveTicket: vi.fn(),
}))

vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    connectorsApi: { ...actual.connectorsApi, list: connList },
    ticketPushApi: { ...actual.ticketPushApi, listJiraProjects, listJiraMembers, pushToJira },
  }
})

vi.mock("../../screens/app/TicketsScreen", () => ({ saveTicket }))

const showToast = vi.fn()
const goTo = vi.fn()
const closeDrawers = vi.fn()
let nav: Record<string, unknown> = {}
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => nav }
})

let content: Record<string, unknown> = {}
vi.mock("../../../context/ContentContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/ContentContext")>()
  return { ...actual, useContent: () => ({ content, setContent: vi.fn() }) }
})

import { TicketDrawer } from "../TicketDrawer"

beforeEach(() => {
  nav = { activeDrawer: "ticket", closeDrawers, showToast, goTo }
  content = { prd: { prd_id: 42, title: "My PRD", sections: [], html: null } }
  saveTicket.mockReturnValue({ id: "SPR-101", title: "My PRD", description: "desc", priority: "P1" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TicketDrawer — real Jira create with per-ticket assignee", () => {
  it("when Jira is connected, creates a real issue assigned to a chosen member", async () => {
    connList.mockResolvedValue({ connections: [{ provider: "jira", status: "active" }] })
    listJiraProjects.mockResolvedValue({ projects: [{ id: "1", key: "KAN", name: "Kanban" }] })
    listJiraMembers.mockResolvedValue({ members: [
      { accountId: "acc-1", displayName: "Apurva Jain", email: "a@x.co" },
    ] })
    pushToJira.mockResolvedValue({ ok: true, created: [{ task_id: "SPR-101", jira_issue_key: "KAN-5", url: "u", title: "My PRD" }], errors: [] })

    await act(async () => {
      render(React.createElement(TicketDrawer))
    })

    // Projects load → the target picker shows the project.
    await waitFor(() => expect(listJiraProjects).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole("option", { name: "Kanban (KAN)" })).toBeTruthy())
    // Members load for the selected project.
    await waitFor(() => expect(listJiraMembers).toHaveBeenCalledWith("KAN"))

    const assignee = await screen.findByLabelText("Assignee") as HTMLSelectElement
    await waitFor(() => expect(assignee.options.length).toBe(2))
    await act(async () => {
      fireEvent.change(assignee, { target: { value: "acc-1" } })
    })

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create & push/i }))
    })

    expect(pushToJira).toHaveBeenCalledWith(
      "KAN",
      [{ task_id: "SPR-101", title: "My PRD", description: "desc", priority: "P1", assignee_account_id: "acc-1" }],
      "Task",
    )
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringMatching(/created in jira · KAN-5/i),
      expect.any(String),
      expect.any(String),
    ))
  })

  it("does not use the Jira form when Jira is not connected", async () => {
    connList.mockResolvedValue({ connections: [] })

    await act(async () => {
      render(React.createElement(TicketDrawer))
    })
    // The internal form renders instead; no Jira project fetch happens.
    await waitFor(() => expect(connList).toHaveBeenCalled())
    expect(listJiraProjects).not.toHaveBeenCalled()
  })
})

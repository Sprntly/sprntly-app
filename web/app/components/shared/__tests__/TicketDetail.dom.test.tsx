// @vitest-environment jsdom
//
// TicketDetail is the editable in-panel ticket view. It loads saved overrides
// (ticketDataApi.getData) merged over the generated story, and persists each
// edit: description/AC -> saveDescription, the pickers + assignee -> saveFields,
// attachments/comments -> their CRUD. These tests mock the api + nav context and
// assert the save wiring + the ticket-key derivation.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
    },
    teamApi: { list: api.teamList },
  }
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ showToast }) }
})

import { TicketDetail, ticketKeyFor } from "../TicketDetail"

const STORY = {
  title: "Guest alert data model",
  body: "One-click guest-alert for Deal Alerts.",
  acceptance_criteria: ["Admin can enable in one click"],
  priority: "P1",
  route: null,
}
const KEY = "prd-7-guest-alert-data-model"

function noEdits() {
  return {
    description: null, acceptance_criteria: null, title: null, priority: null,
    status: null, sprint: null, assignee: null, attachments: [], comments: [],
  }
}

beforeEach(() => {
  api.getData.mockResolvedValue(noEdits())
  api.saveDescription.mockResolvedValue({ ok: true })
  api.saveFields.mockResolvedValue({ ok: true })
  api.teamList.mockResolvedValue({ members: [] })
  api.summarizeComments.mockResolvedValue({ summary: null })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function renderDetail(onBack = vi.fn()) {
  await act(async () => {
    render(React.createElement(TicketDetail, { story: STORY, index: 2, prdId: 7, onBack }))
  })
  await waitFor(() => expect(api.getData).toHaveBeenCalledWith(KEY))
  return onBack
}

describe("ticketKeyFor", () => {
  it("prefers the content-derived id when present", () => {
    expect(ticketKeyFor(7, { ...STORY, id: "3e7b3c1fa35a" })).toBe("prd-7-3e7b3c1fa35a")
  })
  it("falls back to a title slug for sets cached before id existed", () => {
    expect(ticketKeyFor(7, STORY)).toBe(KEY)
  })
})

describe("TicketDetail", () => {
  it("renders the generated story when there are no saved overrides", async () => {
    await renderDetail()
    expect((screen.getByLabelText("Ticket title") as HTMLInputElement).value).toBe("Guest alert data model")
    expect(screen.getByText("T-3")).toBeTruthy() // id chip = index+1
  })

  it("saved overrides win over the generated story", async () => {
    api.getData.mockResolvedValue({ ...noEdits(), title: "Edited title", priority: "P0 — Critical" })
    await renderDetail()
    expect((screen.getByLabelText("Ticket title") as HTMLInputElement).value).toBe("Edited title")
  })

  it("editing the description persists via saveDescription", async () => {
    await renderDetail()
    const ta = screen.getByPlaceholderText("Add a description…")
    await act(async () => {
      fireEvent.change(ta, { target: { value: "New description" } })
      fireEvent.blur(ta)
    })
    expect(api.saveDescription).toHaveBeenCalledWith(KEY, "New description", ["Admin can enable in one click"])
  })

  it("changing the priority picker persists via saveFields", async () => {
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "priority" })) })
    await act(async () => { fireEvent.click(screen.getByText("P0 — Critical")) })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { priority: "P0 — Critical" })
  })

  it("reassigning persists the picked team member via saveFields", async () => {
    api.teamList.mockResolvedValue({ members: [
      { user_id: "u-1", display_name: "Neville Crawley", email: "neville@slickdeals.net", role: "Product", avatar_url: null },
    ] })
    await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /reassign/i })) })
    await waitFor(() => expect(screen.getByText("Neville Crawley")).toBeTruthy())
    await act(async () => { fireEvent.click(screen.getByText("Neville Crawley")) })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, {
      assignee: { user_id: "u-1", display_name: "Neville Crawley", email: "neville@slickdeals.net", role: "Product", avatar_url: null },
    })
  })

  it("posting a comment persists via addComment", async () => {
    api.addComment.mockResolvedValue({ id: 1, author: "You", body: "Looks good", time: "now" })
    await renderDetail()
    const ta = screen.getByPlaceholderText("Add a comment…")
    await act(async () => {
      fireEvent.change(ta, { target: { value: "Looks good" } })
      fireEvent.click(screen.getByRole("button", { name: /post comment/i }))
    })
    expect(api.addComment).toHaveBeenCalledWith(KEY, "You", "Looks good")
  })

  it("shows the AI summary block once there are 2+ comments", async () => {
    api.getData.mockResolvedValue({
      ...noEdits(),
      comments: [
        { id: 1, author: "Sam", body: "Ship behind a flag?", time: "t1" },
        { id: 2, author: "Lee", body: "Yes, step 3 still open.", time: "t2" },
      ],
    })
    api.summarizeComments.mockResolvedValue({ summary: "Team aligned to ship behind a flag." })
    await renderDetail()
    await waitFor(() => expect(api.summarizeComments).toHaveBeenCalledWith(KEY))
    await waitFor(() => expect(screen.getByText("Team aligned to ship behind a flag.")).toBeTruthy())
  })

  it("does not summarize with fewer than 2 comments", async () => {
    api.getData.mockResolvedValue({
      ...noEdits(),
      comments: [{ id: 1, author: "Sam", body: "Only one.", time: "t1" }],
    })
    await renderDetail()
    expect(api.summarizeComments).not.toHaveBeenCalled()
  })

  it("Back invokes onBack", async () => {
    const onBack = await renderDetail()
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /all chunks/i })) })
    expect(onBack).toHaveBeenCalled()
  })
})

// @vitest-environment jsdom
//
// Tracker-native TicketDetail: a ticket bound to a Jira project / ClickUp
// list renders the DESTINATION's vocabulary — status options are the
// ticket's legal transitions (lazy-fetched), priorities are the workspace's
// real scheme, and the destination's custom fields render as editable
// controls (read-only for exotic types). Unbound tickets keep the default
// vocabulary — covered by TicketDetail.dom.test.tsx.
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
  addComment: vi.fn(),
  summarizeComments: vi.fn(),
  getTransitions: vi.fn(),
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
      addComment: api.addComment,
      summarizeComments: api.summarizeComments,
      getTransitions: api.getTransitions,
    },
    teamApi: { list: api.teamList },
  }
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ showToast }) }
})

import { TicketDetail, type TicketTrackerCtx } from "../TicketDetail"
import type { TrackerMeta } from "../../../lib/api"

const STORY = {
  id: "abc123", title: "Login flow", body: "Body.",
  acceptance_criteria: [], priority: "normal", route: null,
}
const KEY = "prd-7-abc123"

const META: TrackerMeta = {
  provider: "jira",
  destination_id: "KAN",
  statuses: [
    { id: "1", name: "Groomed", color: null, category: "open" },
    { id: "2", name: "Building", color: "#fd0", category: "in_progress" },
    { id: "3", name: "Shipped", color: "#0f0", category: "done" },
  ],
  priorities: [
    { id: "1", name: "Blocker", color: "#d04437" },
    { id: "2", name: "Nice to have", color: null },
  ],
  issue_types: null,
  fields: [
    {
      id: "customfield_1", name: "Team", type: "select", raw_type: "select",
      required: false, editable: true,
      options: [
        { id: "o1", name: "Platform", color: null },
        { id: "o2", name: "Growth", color: null },
      ],
    },
    {
      id: "customfield_2", name: "Notes", type: "text", raw_type: "textfield",
      required: false, editable: true, options: null,
    },
    {
      id: "customfield_3", name: "Org", type: "unsupported",
      raw_type: "cascadingselect", required: false, editable: false, options: null,
    },
  ],
}

const TRACKER: TicketTrackerCtx = {
  provider: "jira",
  meta: META,
  synced: {
    status: "Groomed", assignee: null, url: null, status_category: "open",
    custom_fields: { customfield_1: { id: "o1", name: "Platform" }, customfield_2: null },
  },
}

function noEdits() {
  return {
    description: null, acceptance_criteria: null, title: null, priority: null,
    status: null, sprint: null, assignee: null, subtasks: null,
    custom_fields: null, attachments: [], comments: [],
  }
}

beforeEach(() => {
  api.getData.mockResolvedValue(noEdits())
  api.saveDescription.mockResolvedValue({ ok: true })
  api.saveFields.mockResolvedValue({ ok: true })
  api.teamList.mockResolvedValue({ members: [] })
  api.summarizeComments.mockResolvedValue({ summary: null })
  api.getTransitions.mockResolvedValue({
    provider: "jira",
    transitions: [
      { id: "31", name: "Start", to_status_id: "2", to_status_name: "Building", category: "in_progress" },
      { id: "41", name: "Ship", to_status_id: "3", to_status_name: "Shipped", category: "done" },
    ],
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function renderBound(tracker: TicketTrackerCtx | null = TRACKER) {
  await act(async () => {
    render(React.createElement(TicketDetail, {
      story: STORY, index: 0, prdId: 7, onBack: vi.fn(), tracker,
    }))
  })
  await waitFor(() => expect(api.getData).toHaveBeenCalledWith(KEY))
}

describe("TicketDetail — tracker-native vocabulary", () => {
  it("seeds the status from the tracker's pulled state when there's no local override", async () => {
    await renderBound()
    // "Groomed" (their vocabulary), not the default "Backlog".
    expect(screen.getByRole("button", { name: /groomed/i })).toBeTruthy()
  })

  it("bound tickets show ONLY tracker properties — Sprntly metadata rows are hidden", async () => {
    await renderBound({
      ...TRACKER,
      // A story rich in generated metadata would normally render every row.
    })
    expect(screen.queryByText("Reporter")).toBeNull()
    expect(screen.queryByText("Provenance")).toBeNull()
    expect(screen.queryByText("Story points")).toBeNull()
    expect(screen.queryByText("Route")).toBeNull()
    // Tracker-native rows stay.
    expect(screen.getByText("Assignee")).toBeTruthy()
    expect(screen.getByText("Priority")).toBeTruthy()
  })

  it("the status dropdown offers the ticket's LEGAL transitions, lazily fetched", async () => {
    await renderBound()
    expect(api.getTransitions).not.toHaveBeenCalled() // lazy — only on open
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /groomed/i }))
    })
    await waitFor(() => expect(api.getTransitions).toHaveBeenCalledWith(KEY))
    await waitFor(() => expect(screen.getByRole("button", { name: /shipped/i })).toBeTruthy())
    expect(screen.getByRole("button", { name: /building/i })).toBeTruthy()
    // The default vocabulary is nowhere to be seen.
    expect(screen.queryByRole("button", { name: /^backlog$/i })).toBeNull()

    // Picking a transition saves the tracker-native name.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /shipped/i }))
    })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { status: "Shipped" })
  })

  it("falls back to the full meta status list when the transitions fetch fails", async () => {
    api.getTransitions.mockRejectedValue(new Error("404"))
    await renderBound()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /groomed/i }))
    })
    // All three meta statuses offered (ClickUp-style fallback).
    await waitFor(() => expect(screen.getByRole("button", { name: /shipped/i })).toBeTruthy())
    expect(screen.getAllByRole("button", { name: /groomed/i }).length).toBeGreaterThan(1)
  })

  it("the priority picker offers the workspace's real scheme", async () => {
    await renderBound()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /change priority/i }))
    })
    expect(screen.getByRole("button", { name: /blocker/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /nice to have/i })).toBeTruthy()
    // The generator's enum is not offered on a bound ticket.
    expect(screen.queryByRole("button", { name: /^urgent$/i })).toBeNull()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /blocker/i }))
    })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { priority: "Blocker" })
  })

  it("renders the destination's custom fields — editable select saves the normalized value", async () => {
    await renderBound()
    const section = screen.getByTestId("tracker-fields")
    expect(section.textContent).toContain("Team")
    expect(section.textContent).toContain("Notes")

    // Pulled value shows (no local override yet).
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /change team/i }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /growth/i }))
    })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, {
      custom_fields: { customfield_1: { id: "o2", name: "Growth" } },
    })
  })

  it("text custom fields are click-to-edit and commit on blur; non-editable fields are hidden", async () => {
    await renderBound()
    // The value renders like every other bar field; clicking swaps in the input.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /edit notes/i }))
    })
    const notes = screen.getByLabelText("Notes") as HTMLInputElement
    fireEvent.change(notes, { target: { value: "rate-limited rollout" } })
    fireEvent.blur(notes)
    expect(api.saveFields).toHaveBeenCalledWith(KEY, {
      custom_fields: { customfield_2: "rate-limited rollout" },
    })
    // The exotic (editable: false) field doesn't appear AT ALL — product
    // decision: don't surface what can't be edited here.
    expect(screen.queryByLabelText("Org")).toBeNull()
    expect(screen.getByTestId("tracker-fields").textContent).not.toContain("Org")
  })

  it("Jira-bound tickets show an editable issue-type picker from meta", async () => {
    const withTypes: TicketTrackerCtx = {
      ...TRACKER,
      meta: {
        ...META,
        issue_types: [
          { id: "t1", name: "Task", subtask: false },
          { id: "t2", name: "Story", subtask: false },
          { id: "t3", name: "Sub-task", subtask: true },
        ],
      },
      synced: { ...TRACKER.synced!, issue_type: "Task" },
    }
    await renderBound(withTypes)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /change issue type/i }))
    })
    // Non-subtask types only.
    expect(screen.getByRole("button", { name: /story/i })).toBeTruthy()
    expect(screen.queryByRole("button", { name: /sub-task/i })).toBeNull()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /story/i }))
    })
    expect(api.saveFields).toHaveBeenCalledWith(KEY, { issue_type: "Story" })
  })

  it("unbound tickets keep the default vocabulary and never fetch transitions", async () => {
    await renderBound(null)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /backlog/i }))
    })
    expect(screen.getByRole("button", { name: /in progress/i })).toBeTruthy()
    expect(api.getTransitions).not.toHaveBeenCalled()
    expect(screen.queryByTestId("tracker-fields")).toBeNull()
  })
})

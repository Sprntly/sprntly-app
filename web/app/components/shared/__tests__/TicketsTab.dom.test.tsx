// @vitest-environment jsdom
//
// TicketsTab is the "Create ticket" surface: it breaks the current PRD into
// real tickets via the user-stories skill (POST /v1/stories/generate) and
// pushes the reviewed set into ClickUp (POST /v1/stories/push). These tests
// mock the api client + context hooks and assert the generate→render→push wiring
// (replacing the old hardcoded mock tickets).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// Defensive stub: nothing in the TicketsTab tree drives navigation now, but
// keep next/navigation mocked so any incidental useRouter() call is harmless.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

const { getForPrd, generate, getJob, listClickUpLists, pushToClickUp, getData, teamList } = vi.hoisted(() => ({
  getForPrd: vi.fn(),
  generate: vi.fn(),
  getJob: vi.fn(),
  listClickUpLists: vi.fn(),
  pushToClickUp: vi.fn(),
  getData: vi.fn(),
  teamList: vi.fn(),
}))
vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    storiesApi: { getForPrd, generate, getJob, listClickUpLists, pushToClickUp },
    ticketDataApi: { ...actual.ticketDataApi, getData },
    teamApi: { list: teamList },
  }
})

// Default: no persisted tickets → the tab regenerates (matches first-open).
// Individual tests override getForPrd to exercise the cache-hit path.
beforeEach(() => {
  getForPrd.mockResolvedValue({ status: "none", fresh: false, stories: [] })
  getData.mockResolvedValue({
    description: null, acceptance_criteria: null, title: null, priority: null,
    status: null, sprint: null, assignee: null, attachments: [], comments: [],
  })
  teamList.mockResolvedValue({ members: [] })
})

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ showToast }) }
})

let content: Record<string, unknown> = {}
vi.mock("../../../context/ContentContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/ContentContext")>()
  return { ...actual, useContent: () => ({ content, setContent: vi.fn() }) }
})

import { ApiError } from "../../../lib/api"
import { TicketsTab } from "../ContentPanel"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TicketsTab — generate from the PRD, push to ClickUp", () => {
  it("breaks the current PRD into tickets and renders them", async () => {
    content = { prd: { prd_id: 42, title: "Onboarding PRD" }, connectedConnectorIds: [] }
    // Fire-and-forget: generate returns a job id, then we poll getJob → ready.
    generate.mockResolvedValue({ job_id: 11, status: "generating" })
    getJob.mockResolvedValue({
      job_id: 11,
      status: "ready",
      stories: [
        { title: "Instrument wizard steps", body: "Track each onboarding step", acceptance_criteria: ["G", "W"], priority: "P1", route: null },
        { title: "Resume on re-login", body: "", acceptance_criteria: [], priority: null, route: null },
      ],
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    // Generated from the PRD's id, then polled by the returned job id.
    expect(generate).toHaveBeenCalledWith(42)
    await waitFor(() => expect(getJob).toHaveBeenCalledWith(11))
    await waitFor(() => expect(screen.getByText("Instrument wizard steps")).toBeTruthy())
    expect(screen.getByText("Resume on re-login")).toBeTruthy()
    // Acceptance-criteria count surfaces on the row.
    expect(screen.getByText("2 AC")).toBeTruthy()
  })

  it("serves persisted tickets without regenerating when the PRD is unchanged", async () => {
    content = { prd: { prd_id: 42, title: "Onboarding PRD" }, connectedConnectorIds: [] }
    // Fresh cache hit → render the stored stories, never call generate.
    getForPrd.mockResolvedValue({
      status: "ready",
      fresh: true,
      stories: [{ title: "Cached ticket", body: "", acceptance_criteria: [], priority: null, route: null }],
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    await waitFor(() => expect(screen.getByText("Cached ticket")).toBeTruthy())
    expect(getForPrd).toHaveBeenCalledWith(42)
    expect(generate).not.toHaveBeenCalled()
  })

  it("regenerates when the cache is stale (PRD changed)", async () => {
    content = { prd: { prd_id: 42, title: "PRD" }, connectedConnectorIds: [] }
    // Stale: stored stories exist but fresh=false → must regenerate.
    getForPrd.mockResolvedValue({
      status: "ready",
      fresh: false,
      stories: [{ title: "Old ticket", body: "", acceptance_criteria: [], priority: null, route: null }],
    })
    generate.mockResolvedValue({ job_id: 9, status: "generating" })
    getJob.mockResolvedValue({ job_id: 9, status: "ready", stories: [
      { title: "Fresh ticket", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    await waitFor(() => expect(generate).toHaveBeenCalledWith(42))
    await waitFor(() => expect(screen.getByText("Fresh ticket")).toBeTruthy())
  })

  it("Regenerate button forces a fresh generation even on a fresh cache", async () => {
    content = { prd: { prd_id: 42, title: "PRD" }, connectedConnectorIds: [] }
    getForPrd.mockResolvedValue({
      status: "ready",
      fresh: true,
      stories: [{ title: "Cached ticket", body: "", acceptance_criteria: [], priority: null, route: null }],
    })
    generate.mockResolvedValue({ job_id: 5, status: "generating" })
    getJob.mockResolvedValue({ job_id: 5, status: "ready", stories: [
      { title: "Regenerated ticket", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("Cached ticket")).toBeTruthy())
    expect(generate).not.toHaveBeenCalled()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /regenerate/i }))
    })
    await waitFor(() => expect(generate).toHaveBeenCalledWith(42))
    await waitFor(() => expect(screen.getByText("Regenerated ticket")).toBeTruthy())
  })

  it("re-generates when the poll 404s (backend restart dropped the in-memory job)", async () => {
    content = { prd: { prd_id: 42, title: "PRD" }, connectedConnectorIds: [] }
    generate
      .mockResolvedValueOnce({ job_id: 1, status: "generating" })
      .mockResolvedValueOnce({ job_id: 2, status: "generating" })
    // First poll 404s (job lost on restart) → the tab must re-kick generation,
    // not dead-end on an error. Second job polls ready.
    getJob
      .mockRejectedValueOnce(new ApiError(404, null, "Job not found"))
      .mockResolvedValue({ job_id: 2, status: "ready", stories: [
        { title: "Recovered ticket", body: "", acceptance_criteria: [], priority: null, route: null },
      ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    await waitFor(() => expect(generate).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText("Recovered ticket")).toBeTruthy())
    expect(screen.queryByTestId("tickets-error")).toBeNull()
  })

  it("shows a connect-ClickUp toast (no auto-push) when ClickUp isn't connected", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: [] }
    generate.mockResolvedValue({ job_id: 3, status: "generating" })
    getJob.mockResolvedValue({ job_id: 3, status: "ready", stories: [
      { title: "T1", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // The Push button is shown at the top even when not connected…
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to clickup/i }))
    })
    // …but it points to Settings instead of pushing.
    expect(listClickUpLists).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith("ClickUp not connected", expect.stringMatching(/Settings/i))
  })

  it("clicking a ticket opens the editable detail; Back returns to the list", async () => {
    content = { prd: { prd_id: 42, title: "Onboarding PRD" }, connectedConnectorIds: [] }
    generate.mockResolvedValue({ job_id: 11, status: "generating" })
    getJob.mockResolvedValue({ job_id: 11, status: "ready", stories: [
      { title: "Instrument wizard steps", body: "Track each step", acceptance_criteria: ["G"], priority: "P1", route: null },
    ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("Instrument wizard steps")).toBeTruthy())

    // Click the row → detail opens (reads saved overrides) with a Back button.
    await act(async () => {
      fireEvent.click(screen.getByText("Instrument wizard steps"))
    })
    await waitFor(() => expect(getData).toHaveBeenCalledWith("prd-42-instrument-wizard-steps"))
    expect(screen.getByRole("button", { name: /all chunks/i })).toBeTruthy()

    // Back → list returns, regen button is visible again.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /all chunks/i }))
    })
    await waitFor(() => expect(screen.getByRole("button", { name: /regenerate/i })).toBeTruthy())
  })

  it("does not generate when there is no PRD yet", async () => {
    content = { prd: null, connectedConnectorIds: [] }
    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    expect(generate).not.toHaveBeenCalled()
    expect(screen.getByText(/generate a PRD first/i)).toBeTruthy()
  })

  it("pushing fetches ClickUp lists then creates the generated tickets", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    const stories = [{ title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    listClickUpLists.mockResolvedValue({ lists: [{ id: "list-1", name: "Sprint", folder: null }] })
    pushToClickUp.mockResolvedValue({ created: [{ story: "T1", task_id: "cu-1", url: "http://x" }], errors: [] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // First push click (top-bar button) → fetch lists.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to clickup/i }))
    })
    expect(listClickUpLists).toHaveBeenCalled()

    // Second click (list picked) → push the generated stories.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to selected list/i }))
    })
    expect(pushToClickUp).toHaveBeenCalledWith("list-1", stories)
  })

  it("shows the Story map toggle and renders the backbone when the map is built", async () => {
    content = { prd: { prd_id: 42, title: "Activation PRD" }, connectedConnectorIds: [] }
    getForPrd.mockResolvedValue({
      status: "ready",
      fresh: true,
      stories: [
        { title: "Create workspace", body: "As an owner…", acceptance_criteria: ["G"], priority: "urgent", route: null, activity: "Set up workspace", release: "Release 1" },
        { title: "Invite teammate", body: "As an owner…", acceptance_criteria: ["G"], priority: "high", route: null, activity: "Invite the team", release: "Release 2" },
      ],
      story_map: {
        built: true,
        summary: "Story map: built — 3 user activities · 8 requirements · 2 releases (sizing gate: 2 of 5 signals)",
        activities: ["Set up workspace", "Invite the team"],
        releases: [
          { name: "Release 1", note: "walking skeleton", walking_skeleton: true },
          { name: "Release 2", note: "richer journey", walking_skeleton: false },
        ],
        gaps: [{ activity: "Invite the team", release: "Release 2", note: "[edge] invite hits an existing seat" }],
      },
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    await waitFor(() => expect(screen.getByText("Create workspace")).toBeTruthy())
    // The sizing summary rides on the intro line.
    expect(screen.getByText(/Story map: built/)).toBeTruthy()

    // Switch to the map view via the toggle (a tab, not a new content-panel tab).
    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: "Story map" }))
    })
    // Backbone activity + the gap note render on the board.
    expect(screen.getByText("Set up workspace")).toBeTruthy()
    expect(screen.getByText("Invite the team")).toBeTruthy()
    expect(screen.getByText(/invite hits an existing seat/)).toBeTruthy()
  })

  it("offers no Story map toggle for a flat (unsized) ticket set", async () => {
    content = { prd: { prd_id: 42, title: "Small PRD" }, connectedConnectorIds: [] }
    getForPrd.mockResolvedValue({
      status: "ready",
      fresh: true,
      stories: [{ title: "One ticket", body: "", acceptance_criteria: [], priority: null, route: null }],
      story_map: { built: false, summary: "Story map: not needed — sized flat" },
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })

    await waitFor(() => expect(screen.getByText("One ticket")).toBeTruthy())
    expect(screen.queryByRole("tab", { name: "Story map" })).toBeNull()
  })
})

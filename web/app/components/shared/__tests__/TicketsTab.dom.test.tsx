// @vitest-environment jsdom
//
// TicketsTab is the "Create ticket" surface: it breaks the current PRD into
// real tickets via the user-stories skill (POST /v1/stories/generate) and
// syncs the reviewed set with the workspace's tracker through ONE button:
// Connect (nothing connected) → Push to <tool> (first push registers the
// destination) → Syncing…/Synced Xm ago (backend auto-syncs; click = sync
// now). These tests mock the api client + context hooks and assert the
// generate→render→sync wiring.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// The connect-a-tracker button routes to Settings → Connectors; capture pushes.
const routerPush = vi.hoisted(() => vi.fn())
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn(), prefetch: vi.fn() }),
}))

const {
  getForPrd, generate, getJob, listClickUpLists, listJiraProjects,
  listJiraMembers, pushToJira, getSyncState, triggerSync, getData, teamList,
} = vi.hoisted(() => ({
  getForPrd: vi.fn(),
  generate: vi.fn(),
  getJob: vi.fn(),
  listClickUpLists: vi.fn(),
  listJiraProjects: vi.fn(),
  listJiraMembers: vi.fn(),
  pushToJira: vi.fn(),
  getSyncState: vi.fn(),
  triggerSync: vi.fn(),
  getData: vi.fn(),
  teamList: vi.fn(),
}))
vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    storiesApi: {
      getForPrd, generate, getJob, listClickUpLists, listJiraProjects,
      listJiraMembers, pushToJira, getSyncState, triggerSync,
    },
    ticketDataApi: { ...actual.ticketDataApi, getData },
    teamApi: { list: teamList },
  }
})

// Default: no persisted tickets → the tab regenerates (matches first-open);
// no sync destination yet. Individual tests override these.
beforeEach(() => {
  getForPrd.mockResolvedValue({ status: "none", fresh: false, stories: [] })
  getSyncState.mockResolvedValue({ configured: false })
  triggerSync.mockResolvedValue({ status: "syncing" })
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

  it("with no tracker connected, the button routes to Settings → Connectors", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: [] }
    generate.mockResolvedValue({ job_id: 3, status: "generating" })
    getJob.mockResolvedValue({ job_id: 3, status: "ready", stories: [
      { title: "T1", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // No tool connected → the tracker button becomes the connect entry point.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /connect a tracker/i }))
    })
    expect(listClickUpLists).not.toHaveBeenCalled()
    expect(routerPush).toHaveBeenCalledWith("/settings?section=connectors")
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
    expect(screen.getByRole("button", { name: /all tickets/i })).toBeTruthy()

    // Back → list returns, regen button is visible again.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /all tickets/i }))
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

  it("first push: fetches ClickUp lists, opens the picker, registers the destination via triggerSync", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    const stories = [{ title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    listClickUpLists.mockResolvedValue({ lists: [{ id: "list-1", name: "Sprint", space: "Product", folder: null }] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // One connected tool → the button is labeled for it; click opens the picker.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to clickup/i }))
    })
    expect(listClickUpLists).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText(/select a project/i)).toBeTruthy())

    // The picker's "Push N tickets" action → register the destination and run
    // the first sync (the backend keeps it synced automatically after this).
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push 1 ticket/i }))
    })
    expect(triggerSync).toHaveBeenCalledWith(7, {
      provider: "clickup", destination_id: "list-1", destination_name: "Sprint",
    })
  })

  it("a configured PRD shows Synced-ago and an ad-hoc click re-syncs (no picker)", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    const stories = [{ title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    getSyncState.mockResolvedValue({
      configured: true, provider: "clickup", destination_id: "list-1",
      destination_name: "Sprint", sync_status: "idle",
      last_synced_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      last_error: null, statuses: {},
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())
    const btn = await screen.findByRole("button", { name: /synced 5m ago/i })

    await act(async () => { fireEvent.click(btn) })
    // Ad-hoc sync of the registered destination — no destination re-pick.
    expect(triggerSync).toHaveBeenCalledWith(7)
    expect(screen.queryByText(/select a project/i)).toBeNull()
  })

  it("shows Syncing… (disabled) while the backend reports a run in flight", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories: [
      { title: "T1", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })
    getSyncState.mockResolvedValue({
      configured: true, provider: "clickup", destination_id: "list-1",
      destination_name: "Sprint", sync_status: "syncing",
      last_synced_at: null, last_error: null, statuses: {},
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    const btn = await screen.findByRole("button", { name: /syncing/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Syncing 1 ticket with ClickUp/i)).toBeTruthy()
  })

  it("Push to Jira opens the assignee modal; push carries accountIds then registers the sync", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["jira"] }
    const stories = [{ id: "tk-1", title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    listJiraProjects.mockResolvedValue({ projects: [{ id: "1", key: "KAN", name: "Kanban" }] })
    listJiraMembers.mockResolvedValue({ members: [
      { accountId: "acc-1", displayName: "Apurva Jain", email: "a@x.co", active: true, avatarUrl: null },
    ] })
    pushToJira.mockResolvedValue({ created: [{ story: "T1", task_id: "KAN-1", url: "u" }], errors: [] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // Single tracker (Jira) → the button goes straight into the Jira flow.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to jira/i }))
    })
    expect(listJiraProjects).toHaveBeenCalled()
    // Modal + members load (project-scoped assignable users).
    await waitFor(() => expect(listJiraMembers).toHaveBeenCalledWith("KAN"))
    const assigneeSelect = await screen.findByLabelText("Assignee for T1") as HTMLSelectElement
    // Wait for members to populate the per-ticket picker (Unassigned + Apurva).
    await waitFor(() => expect(assigneeSelect.options.length).toBe(2))

    await act(async () => {
      fireEvent.change(assigneeSelect, { target: { value: "acc-1" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push 1 ticket/i }))
    })

    // The assignee-carrying push runs first…
    expect(pushToJira).toHaveBeenCalledWith(
      "KAN",
      [{ ...stories[0], assignee_account_id: "acc-1" }],
      "Task",
    )
    // …then the destination registers server-side so the backend keeps it
    // synced from here on (assignees persist — sync never writes them).
    expect(triggerSync).toHaveBeenCalledWith(7, {
      provider: "jira", destination_id: "KAN", destination_name: "Kanban",
    })
  })

  it("persisted tracker statuses from the sync state render on the ticket cards", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    const stories = [{ id: "tk-1", title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    getSyncState.mockResolvedValue({
      configured: true, provider: "clickup", destination_id: "list-1",
      destination_name: "Sprint", sync_status: "idle",
      last_synced_at: new Date().toISOString(), last_error: null,
      statuses: { "tk-1": { status: "in progress", assignee: "nadia", url: "u" } },
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    // No click needed — the persisted pull renders directly.
    await waitFor(() => expect(screen.getByText(/ClickUp: in progress/i)).toBeTruthy())
  })

  it("with several tools connected, the button opens a tool menu (Jira flows into its modal)", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup", "jira"] }
    const stories = [{ title: "T1", body: "", acceptance_criteria: [], priority: "P0", route: null }]
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories })
    listJiraProjects.mockResolvedValue({ projects: [{ id: "10001", key: "SPR", name: "Sprntly Core" }] })
    listJiraMembers.mockResolvedValue({ members: [] })
    pushToJira.mockResolvedValue({ created: [{ story: "T1", task_id: "SPR-1", url: "u" }], errors: [] })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText("T1")).toBeTruthy())

    // Multiple trackers → generic label opening the tool menu.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to tracker/i }))
    })
    expect(screen.getByText(/sync these tickets with/i)).toBeTruthy()

    // Pick Jira → its projects load into the assignee modal.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^jira$/i }))
    })
    expect(listJiraProjects).toHaveBeenCalled()
    expect(listClickUpLists).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText(/Sprntly Core/)).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push 1 ticket/i }))
    })
    // The assignee-carrying push, then sync registration with the project KEY.
    expect(pushToJira).toHaveBeenCalledWith(
      "SPR", [{ ...stories[0], assignee_account_id: null }], "Task",
    )
    expect(triggerSync).toHaveBeenCalledWith(7, {
      provider: "jira", destination_id: "SPR", destination_name: "Sprntly Core",
    })
  })

  it("surfaces the last sync error under the header when idle", async () => {
    content = { prd: { prd_id: 7, title: "PRD" }, connectedConnectorIds: ["clickup"] }
    generate.mockResolvedValue({ job_id: 12, status: "generating" })
    getJob.mockResolvedValue({ job_id: 12, status: "ready", stories: [
      { title: "T1", body: "", acceptance_criteria: [], priority: null, route: null },
    ] })
    getSyncState.mockResolvedValue({
      configured: true, provider: "clickup", destination_id: "list-1",
      destination_name: "Sprint", sync_status: "idle",
      last_synced_at: null, last_error: "ClickUp is not connected", statuses: {},
    })

    await act(async () => {
      render(React.createElement(TicketsTab))
    })
    await waitFor(() => expect(screen.getByText(/Last sync had problems/i)).toBeTruthy())
  })
})

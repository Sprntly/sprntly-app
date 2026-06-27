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

const { getForPrd, generate, getJob, listClickUpLists, pushToClickUp } = vi.hoisted(() => ({
  getForPrd: vi.fn(),
  generate: vi.fn(),
  getJob: vi.fn(),
  listClickUpLists: vi.fn(),
  pushToClickUp: vi.fn(),
}))
vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return { ...actual, storiesApi: { getForPrd, generate, getJob, listClickUpLists, pushToClickUp } }
})

// Default: no persisted tickets → the tab regenerates (matches first-open).
// Individual tests override getForPrd to exercise the cache-hit path.
beforeEach(() => {
  getForPrd.mockResolvedValue({ status: "none", fresh: false, stories: [] })
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
})

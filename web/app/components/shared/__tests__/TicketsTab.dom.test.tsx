// @vitest-environment jsdom
//
// TicketsTab is the "Create ticket" surface: it breaks the current PRD into
// real tickets via the user-stories skill (POST /v1/stories/generate) and
// pushes the reviewed set into ClickUp (POST /v1/stories/push). These tests
// mock the api client + context hooks and assert the generate→render→push wiring
// (replacing the old hardcoded mock tickets).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// TicketsTab now renders ArtifactFooterActions, which calls useRouter() for the
// "View prototype" action — stub next/navigation so the footer mounts.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

const { generate, getJob, listClickUpLists, pushToClickUp } = vi.hoisted(() => ({
  generate: vi.fn(),
  getJob: vi.fn(),
  listClickUpLists: vi.fn(),
  pushToClickUp: vi.fn(),
}))
vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return { ...actual, storiesApi: { generate, getJob, listClickUpLists, pushToClickUp } }
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

    // First push click → fetch lists.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /sync to clickup/i }))
    })
    expect(listClickUpLists).toHaveBeenCalled()

    // Second click (list picked) → push the generated stories.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /push to clickup/i }))
    })
    expect(pushToClickUp).toHaveBeenCalledWith("list-1", stories)
  })
})

// @vitest-environment jsdom
//
// `ProjectPanelSection` — the in-panel project view mounted from
// `ContentPanel` (`ContentPanel.project-section.dom.test.tsx` covers the
// toggle chrome around it; this file covers what it actually renders once
// mounted with a `projectId`). Fetches its own data
// (`projectsApi.get`/`memorySummary`) keyed on the prop, and covers every
// load outcome: loading, ready (members + memory teaser + invite row),
// 403, 404, and a generic error.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const memorySummaryMock = vi.fn()

vi.mock("../../../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown, message?: string) {
      super(message ?? String(status))
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError,
    projectsApi: {
      get: (...a: unknown[]) => getMock(...a),
      memorySummary: (...a: unknown[]) => memorySummaryMock(...a),
    },
  }
})

const memoryModalMock = vi.fn()
vi.mock("../MemoryModal", () => ({
  MemoryModal: (props: { projectId: number | string; open: boolean; onClose: () => void }) => {
    memoryModalMock(props)
    return props.open
      ? React.createElement("div", { "data-testid": "memory-modal-stub" }, "memory modal open")
      : null
  },
}))

const inviteModalMock = vi.fn()
vi.mock("../ProjectInviteModal", () => ({
  ProjectInviteModal: (props: {
    projectId: number | string
    open: boolean
    onClose: () => void
    onInvited: () => void
  }) => {
    inviteModalMock(props)
    return props.open
      ? React.createElement("div", { "data-testid": "invite-modal-stub" }, "invite modal open")
      : null
  },
}))

import { ProjectPanelSection } from "../ProjectPanelSection"
import { ApiError } from "../../../../../lib/api"
import type { ProjectDetail, ProjectMemorySummary } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const PROJECT: ProjectDetail = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "manual",
  created_by: "u1",
  created_at: hoursAgo(48),
  updated_at: hoursAgo(2),
  group_chat_id: 55,
  members: [
    { kind: "agent", user_id: null, name: "Sprntly", role_label: "Agent coworker · dispatches tasks", status: "working" },
    { kind: "human", user_id: "u1", name: "David M.", email: "david@example.com", avatar_url: null, job_role: "PM", added_at: hoursAgo(48) },
    { kind: "human", user_id: "u2", name: "Shristi", email: "shristi@example.com", avatar_url: null, job_role: "Design", added_at: hoursAgo(40) },
  ],
}

const MEMORY: ProjectMemorySummary = {
  summary_md: "A Contoso-driven redesign of on-demand quoting — a priced quote in under 60 seconds.",
  entry_count: 24,
  stale: false,
}

afterEach(() => {
  cleanup()
  getMock.mockReset()
  memorySummaryMock.mockReset()
  memoryModalMock.mockReset()
  inviteModalMock.mockReset()
})

describe("ProjectPanelSection — load states", () => {
  it("renders a loading state before the fetch resolves", async () => {
    let resolveGet: (v: ProjectDetail) => void = () => {}
    getMock.mockReturnValue(new Promise((res) => { resolveGet = res }))
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)
    expect(screen.getByTestId("project-panel-loading")).toBeTruthy()

    await act(async () => { resolveGet(PROJECT) })
  })

  it("renders members, memory teaser, and the invite row once ready", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)

    await waitFor(() => expect(getMock).toHaveBeenCalledWith(101))
    expect(memorySummaryMock).toHaveBeenCalledWith(101)

    const section = await screen.findByTestId("project-panel-section")
    expect(section).toBeTruthy()

    // Memory teaser — count + first-sentence extraction.
    expect(screen.getByTestId("memory-card").textContent).toContain("24")
    expect(screen.getByTestId("memory-teaser").textContent).toContain(
      "A Contoso-driven redesign of on-demand quoting — a priced quote in under 60 seconds.",
    )
    // Relabel (C1) + summary-only (C2): no Add button, no "insight" copy.
    expect(screen.getByTestId("memory-card").querySelector("h4")?.textContent?.trim()).toBe("Memory")
    expect(screen.queryByTestId("memory-add")).toBeNull()
    expect(screen.getByTestId("memory-view-all").textContent?.toLowerCase()).not.toContain("insight")

    // Human members render, the virtual agent member renders separately.
    const humanRows = screen.getAllByTestId("member-row-human")
    expect(humanRows).toHaveLength(2)
    expect(humanRows.map((r) => r.textContent).join(" ")).toContain("David M.")
    expect(humanRows.map((r) => r.textContent).join(" ")).toContain("Shristi")
    const agentRow = screen.getByTestId("member-row-agent")
    expect(agentRow.textContent).toContain("Sprntly")
    expect(screen.getByTestId("agent-working-status").textContent).toBe("working")

    // Invite row present.
    expect(screen.getByLabelText("Invite by email")).toBeTruthy()
    expect(screen.getByTestId("invite-button")).toBeTruthy()
  })

  it("test_panel_section_rail_and_heading_relabelled — rail section reads Project Settings, memory-card heading reads Memory", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)
    await screen.findByTestId("project-panel-section")

    const labels = screen.getAllByTestId("rail-section-label").map((el) => el.textContent?.trim())
    expect(labels).toContain("Project Settings")
    expect(labels).not.toContain("Project")
    expect(screen.getByTestId("memory-card").querySelector("h4")?.textContent?.trim()).toBe("Memory")
  })

  it("test_panel_section_memory_card_summary_only — no Add button; View all label carries no 'insight' copy", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)
    await screen.findByTestId("project-panel-section")

    expect(screen.queryByTestId("memory-add")).toBeNull()
    const viewAll = screen.getByTestId("memory-view-all")
    expect(viewAll).toBeTruthy()
    expect(viewAll.textContent?.toLowerCase()).not.toContain("insight")
  })

  it("a summary with no periods falls back to the whole stripped string, and a null summary shows the empty-state copy", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue({ ...MEMORY, summary_md: null, entry_count: 0 })

    render(<ProjectPanelSection projectId={101} />)
    await screen.findByTestId("project-panel-section")

    expect(screen.getByTestId("memory-teaser").textContent).toContain(
      "Nothing synthesized yet — insights will appear as the team collaborates.",
    )
  })

  it("shows a forbidden message on a 403", async () => {
    getMock.mockRejectedValue(new ApiError(403, {}))
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)

    const err = await screen.findByTestId("project-panel-error")
    expect(err.textContent).toContain("don't have access")
  })

  it("shows a not-found message on a 404", async () => {
    getMock.mockRejectedValue(new ApiError(404, {}))
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)

    const err = await screen.findByTestId("project-panel-error")
    expect(err.textContent).toContain("no longer exists")
  })

  it("shows a generic error message on any other failure", async () => {
    getMock.mockRejectedValue(new Error("network blip"))
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)

    const err = await screen.findByTestId("project-panel-error")
    expect(err.textContent).toContain("Couldn't load this project")
  })
})

describe("ProjectPanelSection — invite + memory modals", () => {
  it("the invite row opens ProjectInviteModal, closing it dismisses", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)
    await screen.findByTestId("project-panel-section")

    expect(screen.queryByTestId("invite-modal-stub")).toBeNull()
    fireEvent.click(screen.getByTestId("invite-button"))
    expect(screen.getByTestId("invite-modal-stub")).toBeTruthy()

    const lastCall = inviteModalMock.mock.calls.at(-1)?.[0]
    expect(lastCall.projectId).toBe(101)
  })

  it("opening memory via 'View memory' opens MemoryModal", async () => {
    getMock.mockResolvedValue(PROJECT)
    memorySummaryMock.mockResolvedValue(MEMORY)

    render(<ProjectPanelSection projectId={101} />)
    await screen.findByTestId("project-panel-section")

    expect(screen.queryByTestId("memory-modal-stub")).toBeNull()
    fireEvent.click(screen.getByTestId("memory-view-all"))
    expect(screen.getByTestId("memory-modal-stub")).toBeTruthy()
  })
})

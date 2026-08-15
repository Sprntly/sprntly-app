// @vitest-environment jsdom
//
// `ProjectSettingsModal` — the top-bar gear's 4-tab modal (layout redesign).
// A thin presentational shell + tab-state; every tab reuses an already
// existing surface (`MemorySummaryBody`/`ProjectInviteBody`/the shared
// member-row classes) rather than re-implementing it. This file ALSO absorbs
// the member/agent-row assertions migrated out of `ProjectDetailScreen.test.tsx`
// (the old rail's Members section) — nothing from that surface is lost, it
// just now renders inside this modal's Members tab.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const candidateSearchMock = vi.fn()
const tagCandidateMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      candidateSearch: (...a: unknown[]) => candidateSearchMock(...a),
      tagCandidate: (...a: unknown[]) => tagCandidateMock(...a),
    },
  }
})

import { ProjectSettingsModal, type ProjectSettingsModalProps } from "../ProjectSettingsModal"
import type { ArtifactItem, ProjectDetail, ProjectMemorySummary } from "../../../../../lib/api"

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
    {
      kind: "agent",
      user_id: null,
      name: "Sprntly",
      role_label: "Agent coworker · dispatches tasks",
      status: "working",
    },
    {
      kind: "human",
      user_id: "u1",
      name: "David M.",
      email: "david@example.com",
      avatar_url: null,
      job_role: "PM",
      added_at: hoursAgo(48),
    },
    {
      kind: "human",
      user_id: "u2",
      name: "Shristi",
      email: "shristi@example.com",
      avatar_url: null,
      job_role: "Design",
      added_at: hoursAgo(40),
    },
  ],
}

const MEMORY: ProjectMemorySummary = {
  summary_md:
    "A Xometry-driven redesign of on-demand quoting — a priced quote in under 60 seconds. It also covers the guest path for first-time buyers.",
  entry_count: 24,
  stale: false,
}

const noop = () => {}

function modalProps(overrides: Partial<ProjectSettingsModalProps> = {}): ProjectSettingsModalProps {
  return {
    open: true,
    onClose: noop,
    projectId: 101,
    project: PROJECT,
    memory: MEMORY,
    currentUserId: "current-viewer",
    onRemoveMember: noop,
    onInvited: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  candidateSearchMock.mockReset()
  candidateSearchMock.mockResolvedValue([])
  tagCandidateMock.mockReset()
})

describe("ProjectSettingsModal — creation / tabs", () => {
  it("test_settings_modal_renders_four_tabs_in_order — the four role=tab labels are exactly Instructions, Memory, Members, Invite, in order (AC5)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    const tabs = screen.getAllByRole("tab")
    expect(tabs.map((t) => t.textContent?.trim())).toEqual(["Instructions", "Memory", "Members", "Invite"])
  })

  it("test_settings_modal_defaults_to_instructions_tab — on open, settings-panel-instructions is visible, the other three panels are not (AC5)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    expect(screen.getByTestId("settings-panel-instructions")).toBeTruthy()
    expect(screen.queryByTestId("settings-panel-memory")).toBeNull()
    expect(screen.queryByTestId("settings-panel-members")).toBeNull()
    expect(screen.queryByTestId("settings-panel-invite")).toBeNull()
  })

  it("test_settings_modal_tab_click_switches_panel — clicking the Members tab shows settings-panel-members and hides settings-panel-instructions (AC5)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    fireEvent.click(screen.getByTestId("settings-tab-members"))
    expect(screen.getByTestId("settings-panel-members")).toBeTruthy()
    expect(screen.queryByTestId("settings-panel-instructions")).toBeNull()
  })

  it("test_settings_modal_is_dialog — role=dialog + aria-modal=true and Escape calls onClose (AC1)", () => {
    const onClose = vi.fn()
    render(React.createElement(ProjectSettingsModal, modalProps({ onClose })))
    const dialog = screen.getByTestId("project-settings-modal")
    expect(dialog.getAttribute("role")).toBe("dialog")
    expect(dialog.getAttribute("aria-modal")).toBe("true")
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("test_settings_modal_title_reads_project_settings — the modal heading reads 'Project settings' (migrated intent from the old rail-section label)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    expect(screen.getByTestId("project-settings-modal-title").textContent?.trim()).toBe("Project settings")
  })
})

describe("ProjectSettingsModal — Members tab", () => {
  it("test_settings_members_lists_humans_and_agent — all human rows + the pinned member-row-agent row render (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    expect(screen.getAllByTestId("member-row-human")).toHaveLength(2)
    const agentRow = screen.getByTestId("member-row-agent")
    expect(agentRow.textContent).toContain("Sprntly")
    expect(within(agentRow).getByText("Agent")).toBeTruthy()
    expect(within(agentRow).getByText("Agent coworker · dispatches tasks")).toBeTruthy()
    expect(within(agentRow).getByText("working")).toBeTruthy()
  })

  it("test_settings_members_human_rows_carry_job_role — each member-row-human shows its name + job_role label (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const rows = screen.getAllByTestId("member-row-human")
    expect(within(rows[0]).getByText("David M.")).toBeTruthy()
    expect(within(rows[0]).getByText("PM")).toBeTruthy()
    expect(within(rows[1]).getByText("Shristi")).toBeTruthy()
    expect(within(rows[1]).getByText("Design")).toBeTruthy()
  })

  it("test_settings_members_agent_pill_has_status_role — the agent-working-status pill has role=status and an accessible name containing Sprntly + the status string (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const pill = screen.getByTestId("agent-working-status")
    expect(pill.getAttribute("role")).toBe("status")
    const accessibleName = pill.getAttribute("aria-label") ?? pill.textContent ?? ""
    expect(accessibleName).toContain("Sprntly")
    expect(accessibleName).toContain("working")
  })

  it("test_settings_members_agent_pill_shows_backend_status_string — the pill text equals the virtual member's status constant, unmodified (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    expect(screen.getByTestId("agent-working-status").textContent).toBe("working")
  })

  it("test_settings_members_working_pill_only_for_agent — no member-row-human renders the agent-working-status pill; exactly one renders overall (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const humanRows = screen.getAllByTestId("member-row-human")
    for (const row of humanRows) {
      expect(within(row).queryByTestId("agent-working-status")).toBeNull()
    }
    expect(screen.getAllByTestId("agent-working-status")).toHaveLength(1)
  })

  it("test_settings_members_search_filters_by_name_or_role — typing a name OR job_role substring hides non-matching human rows and keeps the agent row (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))

    fireEvent.change(screen.getByTestId("settings-members-search"), { target: { value: "shristi" } })
    let rows = screen.getAllByTestId("member-row-human")
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain("Shristi")
    expect(screen.getByTestId("member-row-agent")).toBeTruthy()

    // Role-substring match ("Design" is Shristi's job_role, not her name).
    fireEvent.change(screen.getByTestId("settings-members-search"), { target: { value: "design" } })
    rows = screen.getAllByTestId("member-row-human")
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain("Shristi")
  })

  it("test_settings_members_scroll_region_is_fixed_height — settings-members-scroll has a max-height + overflow-y: auto (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const region = screen.getByTestId("settings-members-scroll")
    const computed = getComputedStyle(region)
    expect(computed.overflowY).toBe("auto")
    expect(parseInt(computed.maxHeight, 10)).toBeGreaterThan(0)
  })

  it("test_settings_members_remove_visible_for_removable — a member who is not creator/caller/agent renders member-remove and it calls onRemoveMember with that member (AC7)", () => {
    const onRemoveMember = vi.fn()
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members", onRemoveMember })))
    const rows = screen.getAllByTestId("member-row-human")
    // David M. (u1) is the creator — not removable; Shristi (u2) is removable.
    expect(within(rows[0]).queryByTestId("member-remove")).toBeNull()
    const removeBtn = within(rows[1]).getByTestId("member-remove")
    fireEvent.click(removeBtn)
    expect(onRemoveMember).toHaveBeenCalledTimes(1)
    expect(onRemoveMember.mock.calls[0][0]).toMatchObject({ user_id: "u2", name: "Shristi" })
  })

  it("test_settings_members_remove_hidden_for_creator_and_self — the creator row, the caller's OWN row, and the agent row render no member-remove control (AC7)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members", currentUserId: "u2" })))
    expect(screen.queryAllByTestId("member-remove")).toHaveLength(0)
    expect(screen.queryByTestId("member-remove")).toBeNull()
    expect(within(screen.getByTestId("member-row-agent")).queryByTestId("member-remove")).toBeNull()
  })
})

describe("ProjectSettingsModal — Invite tab", () => {
  it("test_settings_invite_renders_picker_body — project-invite-search + at least one project-invite-add render from the reused body (AC8)", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
    ])
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "invite" })))
    expect(screen.getByTestId("project-invite-search")).toBeTruthy()
    expect(await screen.findByTestId("project-invite-add")).toBeTruthy()
  })

  it("test_settings_invite_shows_not_yet_count — settings-invite-count equals the workspace-candidate count (AC8)", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
      { kind: "workspace", user_id: "u4", name: "Priya", email: "priya@example.com" },
    ])
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "invite" })))
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())
    const count = await screen.findByTestId("settings-invite-count")
    expect(count.textContent).toContain("2")
    expect(count.textContent).toContain("not yet in this project")
  })

  it("test_settings_invite_email_needle_shows_invite_by_email — an email query renders project-invite-by-email (AC8)", async () => {
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "invite" })))
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "new.person@example.com" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    expect(await screen.findByTestId("project-invite-by-email")).toBeTruthy()
  })
})

describe("ProjectSettingsModal — Memory tab", () => {
  it("test_settings_memory_renders_readonly_summary — memory-synth-block + the read-only tag render; no add/edit/remove control is present (AC9)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "memory" })))
    expect(screen.getByTestId("memory-synth-block")).toBeTruthy()
    expect(screen.getByTestId("memory-synth-readonly-tag")).toBeTruthy()
    expect(screen.queryByTestId("memory-add")).toBeNull()
    expect(screen.queryByTestId("memory-edit")).toBeNull()
    expect(screen.queryByTestId("memory-remove")).toBeNull()
  })
})

describe("ProjectSettingsModal — Instructions tab (shell)", () => {
  it("test_settings_instructions_textarea_and_count — settings-instructions-input renders and settings-instructions-count updates on type (AC10)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    const textarea = screen.getByTestId("settings-instructions-input")
    expect(screen.getByTestId("settings-instructions-count").textContent).toContain("0")
    fireEvent.change(textarea, { target: { value: "Priced quotes must return in under 60s." } })
    expect(screen.getByTestId("settings-instructions-count").textContent).toContain("40")
  })

  it("test_settings_instructions_save_disabled_and_no_network — settings-instructions-save is disabled and a spied fetch/api is never called from the Instructions panel (AC10)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    render(React.createElement(ProjectSettingsModal, modalProps()))
    const save = screen.getByTestId("settings-instructions-save")
    expect(save).toHaveProperty("disabled", true)
    fireEvent.change(screen.getByTestId("settings-instructions-input"), { target: { value: "hello" } })
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(candidateSearchMock).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })
})

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
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const candidateSearchMock = vi.fn()
const tagCandidateMock = vi.fn()
// Every render (any tab) triggers the Instructions tab's GET-on-open effect
// — give it a safe default up front (not just in afterEach) so the very
// FIRST test in the file doesn't call `.then()` on an unconfigured mock's
// `undefined` return.
const instructionsMock = vi.fn().mockResolvedValue({ instructions: null })
const setInstructionsMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      candidateSearch: (...a: unknown[]) => candidateSearchMock(...a),
      tagCandidate: (...a: unknown[]) => tagCandidateMock(...a),
      instructions: (...a: unknown[]) => instructionsMock(...a),
      setInstructions: (...a: unknown[]) => setInstructionsMock(...a),
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
    "A Contoso-driven redesign of on-demand quoting — a priced quote in under 60 seconds. It also covers the guest path for first-time buyers.",
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
  instructionsMock.mockReset()
  instructionsMock.mockResolvedValue({ instructions: null })
  setInstructionsMock.mockReset()
})

// The GET-on-open fires from a `useEffect`; every test that cares about its
// resolved value needs the mount to have flushed before asserting, mirroring
// `waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())` above.
async function waitForInstructionsLoaded() {
  await waitFor(() => expect(instructionsMock).toHaveBeenCalled())
}

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

  it("test_settings_modal_is_modal_md_not_modal_lg — the dialog wrapper carries modal-md, matching the mockup's 580px width, not modal-lg's 980px", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    const dialog = screen.getByTestId("project-settings-modal")
    expect(dialog.className).toContain("modal-md")
    expect(dialog.className).not.toContain("modal-lg")
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

  it("test_settings_modal_title_reads_project_settings — the modal heading reads 'Project Settings' (title-cased for product consistency)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    expect(screen.getByTestId("project-settings-modal-title").textContent?.trim()).toBe("Project Settings")
  })
})

describe("ProjectSettingsModal — Members tab", () => {
  it("test_settings_members_lists_humans_only — human rows render and the Sprntly agent row is no longer shown; the count reflects humans only (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    expect(screen.getAllByTestId("member-row-human")).toHaveLength(2)
    // The Members list is human-only now — the pinned Sprntly agent row was
    // removed, and the section count is `humans.length` (2), not
    // `members.length` (3, which would include the agent).
    expect(screen.queryByTestId("member-row-agent")).toBeNull()
    const panel = screen.getByTestId("settings-panel-members")
    expect(panel.textContent).toMatch(/Members\s*2/)
    expect(panel.textContent).not.toMatch(/Members\s*3/)
  })

  it("test_settings_members_human_rows_carry_job_role — each member-row-human shows its name + job_role label (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const rows = screen.getAllByTestId("member-row-human")
    expect(within(rows[0]).getByText("David M.")).toBeTruthy()
    expect(within(rows[0]).getByText("PM")).toBeTruthy()
    expect(within(rows[1]).getByText("Shristi")).toBeTruthy()
    expect(within(rows[1]).getByText("Design")).toBeTruthy()
  })

  it("test_settings_members_no_agent_working_pill — the agent-working-status pill is no longer rendered anywhere in the Members tab (agent row removed) (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    expect(screen.queryByTestId("agent-working-status")).toBeNull()
    expect(screen.queryByTestId("member-row-agent")).toBeNull()
  })

  it("test_settings_members_no_agent_labels — the Members panel shows none of the removed agent affordances (the 'Agent' tag, the role label, the 'working' status) (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const panel = screen.getByTestId("settings-panel-members")
    expect(within(panel).queryByText("Agent")).toBeNull()
    expect(within(panel).queryByText("Agent coworker · dispatches tasks")).toBeNull()
    expect(within(panel).queryByText("working")).toBeNull()
  })

  it("test_settings_members_no_working_pill_on_any_row — no member-row-human renders the agent-working-status pill, and none renders overall (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const humanRows = screen.getAllByTestId("member-row-human")
    for (const row of humanRows) {
      expect(within(row).queryByTestId("agent-working-status")).toBeNull()
    }
    expect(screen.queryAllByTestId("agent-working-status")).toHaveLength(0)
  })

  it("test_settings_members_search_filters_by_name_or_role — typing a name OR job_role substring hides non-matching human rows (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))

    fireEvent.change(screen.getByTestId("settings-members-search"), { target: { value: "shristi" } })
    let rows = screen.getAllByTestId("member-row-human")
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain("Shristi")
    // (The pinned agent row that used to remain visible through the filter is
    // gone — the list is human-only now.)
    expect(screen.queryByTestId("member-row-agent")).toBeNull()

    // Role-substring match ("Design" is Shristi's job_role, not her name).
    fireEvent.change(screen.getByTestId("settings-members-search"), { target: { value: "design" } })
    rows = screen.getAllByTestId("member-row-human")
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain("Shristi")
  })

  it("test_settings_members_scroll_region_is_full_height_card — settings-members-scroll carries the module scroll-region class, which the CSS module makes a full-height, internally-scrolling card (flex:1 + min-height:0 + overflow-y:auto) inside the .tabFill column (AC6)", () => {
    render(React.createElement(ProjectSettingsModal, modalProps({ initialTab: "members" })))
    const region = screen.getByTestId("settings-members-scroll")
    // The old inline `max-height`/`overflow-y` was replaced by the module's
    // `.scrollRegion` class so the card fills the fixed-height modal instead
    // of floating content-sized. jsdom doesn't apply CSS-module stylesheets,
    // so the class presence + the module rule are asserted directly.
    expect(region.className).toMatch(/scrollRegion/)
    const css = readFileSync(join(__dirname, "../ProjectSettingsModal.module.css"), "utf8")
    // `.scrollRegion` fills its flex parent and scrolls internally.
    expect(css).toMatch(/\.scrollRegion\s*\{[^}]*flex:\s*1/)
    expect(css).toMatch(/\.scrollRegion\s*\{[^}]*min-height:\s*0/)
    expect(css).toMatch(/\.scrollRegion\s*\{[^}]*overflow-y:\s*auto/)
    // `.tabFill` is the full-height flex column the Members/Invite tabs use.
    expect(css).toMatch(/\.tabFill\s*\{[^}]*flex:\s*1/)
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
    // (The agent row that previously also had to be checked here is gone — the
    // Members list is human-only now.)
    expect(screen.queryByTestId("member-row-agent")).toBeNull()
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

describe("ProjectSettingsModal — Instructions tab (persistence)", () => {
  it("test_settings_instructions_textarea_and_count — settings-instructions-input renders and settings-instructions-count updates on type (AC10/AC12)", async () => {
    render(React.createElement(ProjectSettingsModal, modalProps()))
    await waitForInstructionsLoaded()
    const textarea = screen.getByTestId("settings-instructions-input")
    expect(screen.getByTestId("settings-instructions-count").textContent).toContain("0")
    fireEvent.change(textarea, { target: { value: "Priced quotes must return in under 60s." } })
    // NOTE (fixed a pre-existing false-positive): the string is 39 chars —
    // the old `.toContain("40")` only ever passed because it was matching
    // the "40" prefix of the (then) 4000-char max in "39 / 4000 characters",
    // not the actual count. Now that INSTRUCTIONS_MAX is 2000 (matching the
    // server cap), "39 / 2000 characters" exposes the real assertion needed.
    expect(screen.getByTestId("settings-instructions-count").textContent).toContain("39")
  })

  it("test_settings_instructions_loads_saved_value — on open, the textarea is populated from a mocked projectsApi.instructions GET (AC12)", async () => {
    instructionsMock.mockResolvedValue({ instructions: "Ship pricing under 60s." })
    render(React.createElement(ProjectSettingsModal, modalProps()))
    expect(instructionsMock).toHaveBeenCalledWith(101)
    const textarea = await screen.findByDisplayValue("Ship pricing under 60s.")
    expect(textarea).toBe(screen.getByTestId("settings-instructions-input"))
  })

  it("test_settings_instructions_save_enabled_and_puts — Save is enabled after a change and clicking it calls projectsApi.setInstructions with the field value (AC12)", async () => {
    instructionsMock.mockResolvedValue({ instructions: "" })
    setInstructionsMock.mockResolvedValue({ instructions: "New guidance for the team." })
    render(React.createElement(ProjectSettingsModal, modalProps()))
    await waitForInstructionsLoaded()

    const save = screen.getByTestId("settings-instructions-save")
    expect(save).toHaveProperty("disabled", true)

    fireEvent.change(screen.getByTestId("settings-instructions-input"), {
      target: { value: "New guidance for the team." },
    })
    expect(save).toHaveProperty("disabled", false)

    await act(async () => {
      fireEvent.click(save)
      await Promise.resolve()
    })
    expect(setInstructionsMock).toHaveBeenCalledWith(101, "New guidance for the team.")
  })

  it("test_settings_instructions_save_disabled_when_unchanged_or_over_cap — Save is disabled when the field equals the loaded value and when it exceeds 2000 chars (AC12)", async () => {
    instructionsMock.mockResolvedValue({ instructions: "baseline" })
    render(React.createElement(ProjectSettingsModal, modalProps()))
    await waitForInstructionsLoaded()
    await screen.findByDisplayValue("baseline")

    const textarea = screen.getByTestId("settings-instructions-input")
    const save = screen.getByTestId("settings-instructions-save")
    expect(save).toHaveProperty("disabled", true)

    // Changed -> enabled.
    fireEvent.change(textarea, { target: { value: "baseline plus more" } })
    expect(save).toHaveProperty("disabled", false)

    // Reverted back to the loaded value -> disabled again (unchanged).
    fireEvent.change(textarea, { target: { value: "baseline" } })
    expect(save).toHaveProperty("disabled", true)

    // Over the 2000-char cap -> disabled even though it differs from the
    // loaded value.
    fireEvent.change(textarea, { target: { value: "x".repeat(2001) } })
    expect(save).toHaveProperty("disabled", true)
  })

  it("test_settings_instructions_load_failure_shows_error_not_crash — a rejected GET renders an in-tab error line instead of crashing (AC12)", async () => {
    instructionsMock.mockRejectedValue(new Error("network down"))
    render(React.createElement(ProjectSettingsModal, modalProps()))
    expect(await screen.findByTestId("settings-instructions-error")).toBeTruthy()
    // The panel is still usable — textarea/save render normally.
    expect(screen.getByTestId("settings-instructions-input")).toBeTruthy()
  })
})

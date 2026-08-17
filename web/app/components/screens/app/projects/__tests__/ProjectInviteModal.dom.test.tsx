// @vitest-environment jsdom
//
// ProjectInviteModal — the project rail's Invite surface. On open (empty
// query) fetches + lists workspace NON-members (each with an add button) —
// a member already on the project never appears in this primary list. A
// typed query narrows via the same real `/tag`-fed typeahead
// (`projectsApi.candidateSearch`), and adding calls `projectsApi.tagCandidate`
// — never the global mock InviteModal's toast stub. The "On this project"
// current-members block is gone entirely.
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

import { ProjectInviteModal } from "../ProjectInviteModal"

function renderModal(overrides: { onClose?: () => void; onInvited?: () => void; open?: boolean } = {}) {
  return render(
    React.createElement(ProjectInviteModal, {
      projectId: 101,
      open: overrides.open ?? true,
      onClose: overrides.onClose ?? (() => {}),
      onInvited: overrides.onInvited ?? (() => {}),
    }),
  )
}

afterEach(() => {
  cleanup()
  candidateSearchMock.mockReset()
  tagCandidateMock.mockReset()
})

describe("ProjectInviteModal — no current-members block", () => {
  it("test_invite_modal_has_no_current_members_block — project-invite-members-label and project-invite-members are absent", async () => {
    candidateSearchMock.mockResolvedValue([])
    renderModal()
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())
    expect(screen.queryByTestId("project-invite-members-label")).toBeNull()
    expect(screen.queryByTestId("project-invite-members")).toBeNull()
    expect(screen.queryByTestId("project-invite-member-row")).toBeNull()
    expect(screen.queryByTestId("project-invite-member-row-agent")).toBeNull()
  })

  it("renders nothing when closed", () => {
    renderModal({ open: false })
    expect(screen.queryByTestId("project-invite-modal")).toBeNull()
  })
})

describe("ProjectInviteModal — open (empty query) fetches workspace non-members", () => {
  it("test_invite_modal_open_fetches_workspace_non_members — candidateSearch is called on open; the add list shows only kind:workspace rows; kind:member is absent; no project-invite-hint", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Grace Hopper", email: "grace@example.com" },
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
      { kind: "company", user_id: "u4", name: "Someone Else", email: "else@example.com" },
    ])
    renderModal()
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, ""))

    const rows = await screen.findAllByTestId("project-invite-candidate")
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain("Fortune")
    expect(screen.queryByText("Grace Hopper")).toBeNull()
    expect(screen.queryByText("Someone Else")).toBeNull()
    expect(screen.queryByTestId("project-invite-hint")).toBeNull()
  })

  it("test_invite_modal_empty_workspace_shows_empty_state — no kind:workspace rows renders an empty-state, not the old hint", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Grace Hopper", email: "grace@example.com" },
    ])
    renderModal()
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())

    expect(await screen.findByTestId("project-invite-empty-workspace")).toBeTruthy()
    expect(screen.queryByTestId("project-invite-hint")).toBeNull()
    expect(screen.queryByTestId("project-invite-candidate")).toBeNull()
  })

  it("test_invite_modal_add_button_calls_tag_candidate — clicking a workspace row's add button calls tagCandidate(projectId, needle) once, fires onInvited, shows the added affordance on t_workspace", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
    ])
    tagCandidateMock.mockResolvedValue({ tier: "t_workspace", added: true })
    const onInvited = vi.fn()
    renderModal({ onInvited })
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalled())

    const addBtn = await screen.findByTestId("project-invite-add")
    await act(async () => {
      fireEvent.click(addBtn)
    })
    await waitFor(() => expect(tagCandidateMock).toHaveBeenCalledTimes(1))
    expect(tagCandidateMock).toHaveBeenCalledWith(101, "fortune@example.com")
    await waitFor(() => expect(onInvited).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId("project-invite-affordance").textContent).toContain("added to the project")
  })
})

describe("ProjectInviteModal — typed query: typeahead + email invite path still present", () => {
  it("test_invite_modal_email_path_still_present — the search input + email-invite affordance render and call tagCandidate", async () => {
    candidateSearchMock.mockResolvedValue([])
    const onInvited = vi.fn()
    tagCandidateMock.mockResolvedValue({ tier: "t_newuser", invited: true, email_status: "sent" })
    renderModal({ onInvited })
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, ""))

    expect(screen.getByTestId("project-invite-search")).toBeTruthy()
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "new.person@example.com" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    const inviteBtn = await screen.findByTestId("project-invite-by-email")
    await act(async () => {
      fireEvent.click(inviteBtn)
    })
    await waitFor(() => expect(tagCandidateMock).toHaveBeenCalledWith(101, "new.person@example.com"))
    await waitFor(() => expect(onInvited).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId("project-invite-affordance").textContent).toContain("Invite sent")
  })

  it("searches via projectsApi.candidateSearch as the user types (typeahead, unchanged)", async () => {
    candidateSearchMock.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
    ])
    renderModal()
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, ""))

    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "fort" } })
      // Debounced (150ms) — flush it.
      await new Promise((r) => setTimeout(r, 200))
    })
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, "fort"))
    expect(await screen.findByTestId("project-invite-candidate")).toBeTruthy()
  })

  it("a members-already row for an existing member is not clickable", async () => {
    candidateSearchMock.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { kind: "member", user_id: "u2", name: "Grace Hopper", email: "grace@example.com" },
    ])
    renderModal()
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, ""))

    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "grac" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    const row = await screen.findByTestId("project-invite-candidate")
    expect(within(row).getByTestId("project-invite-already")).toBeTruthy()
    expect(within(row).queryByTestId("project-invite-add")).toBeNull()
  })
})

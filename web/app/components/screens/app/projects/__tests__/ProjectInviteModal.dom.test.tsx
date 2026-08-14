// @vitest-environment jsdom
//
// ProjectInviteModal — the project rail's Invite surface. Lists the
// project's CURRENT members (fed by the caller, no second fetch) and adds a
// candidate through the real `/tag` path (`projectsApi.tagCandidate`,
// fed by `projectsApi.candidateSearch`) — never the global mock InviteModal's
// toast stub.
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
import type { ProjectMember } from "../../../../../lib/api"

const MEMBERS: ProjectMember[] = [
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
    name: "Ada Lovelace",
    email: "ada@example.com",
    avatar_url: null,
    job_role: "PM",
    added_at: new Date().toISOString(),
  },
  {
    kind: "human",
    user_id: "u2",
    name: "Grace Hopper",
    email: "grace@example.com",
    avatar_url: null,
    job_role: "Design",
    added_at: new Date().toISOString(),
  },
]

afterEach(() => {
  cleanup()
  candidateSearchMock.mockReset()
  tagCandidateMock.mockReset()
})

describe("ProjectInviteModal — current members", () => {
  it("lists every human member, and the agent member separately", () => {
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: true,
        onClose: () => {},
        onInvited: () => {},
      }),
    )
    const rows = screen.getAllByTestId("project-invite-member-row")
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("Ada Lovelace"), expect.stringContaining("Grace Hopper")]),
    )
    expect(screen.getByTestId("project-invite-member-row-agent").textContent).toContain("Sprntly")
  })

  it("renders nothing when closed", () => {
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: false,
        onClose: () => {},
        onInvited: () => {},
      }),
    )
    expect(screen.queryByTestId("project-invite-modal")).toBeNull()
  })
})

describe("ProjectInviteModal — add via the real /tag path", () => {
  it("searches via projectsApi.candidateSearch as the user types", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
    ])
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: true,
        onClose: () => {},
        onInvited: () => {},
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "fort" } })
      // Debounced (150ms) — flush it.
      await new Promise((r) => setTimeout(r, 200))
    })
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, "fort"))
    expect(await screen.findByTestId("project-invite-candidate")).toBeTruthy()
  })

  it("adding a candidate calls projectsApi.tagCandidate and fires onInvited on a t_workspace add", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Fortune", email: "fortune@example.com" },
    ])
    tagCandidateMock.mockResolvedValue({ tier: "t_workspace", added: true })
    const onInvited = vi.fn()
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: true,
        onClose: () => {},
        onInvited,
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "fort" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    const addBtn = await screen.findByTestId("project-invite-add")
    await act(async () => {
      fireEvent.click(addBtn)
    })
    await waitFor(() => expect(tagCandidateMock).toHaveBeenCalledWith(101, "fortune@example.com"))
    await waitFor(() => expect(onInvited).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId("project-invite-affordance").textContent).toContain("added to the project")
  })

  it("a members-already row for an existing member is not clickable", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Grace Hopper", email: "grace@example.com" },
    ])
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: true,
        onClose: () => {},
        onInvited: () => {},
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "grac" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    const row = await screen.findByTestId("project-invite-candidate")
    expect(within(row).getByTestId("project-invite-already")).toBeTruthy()
    expect(within(row).queryByTestId("project-invite-add")).toBeNull()
  })

  it("inviting a brand-new email uses the SAME /tag path and shows the invite affordance", async () => {
    candidateSearchMock.mockResolvedValue([])
    tagCandidateMock.mockResolvedValue({ tier: "t_newuser", invited: true, email_status: "sent" })
    const onInvited = vi.fn()
    render(
      React.createElement(ProjectInviteModal, {
        projectId: 101,
        members: MEMBERS,
        open: true,
        onClose: () => {},
        onInvited,
      }),
    )
    await act(async () => {
      fireEvent.change(screen.getByTestId("project-invite-search"), { target: { value: "new.person@example.com" } })
      await new Promise((r) => setTimeout(r, 200))
    })
    const inviteBtn = await screen.findByTestId("project-invite-by-email")
    await act(async () => {
      fireEvent.click(inviteBtn)
    })
    await waitFor(() =>
      expect(tagCandidateMock).toHaveBeenCalledWith(101, "new.person@example.com"),
    )
    await waitFor(() => expect(onInvited).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId("project-invite-affordance").textContent).toContain("Invite sent")
  })
})

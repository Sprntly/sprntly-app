// @vitest-environment jsdom
//
// Job-designation (profiles.role, surfaced as `job_role`) render + self-edit
// tests for the Settings → Team & roles pane. Two layers:
//   - Pure View (renderToStaticMarkup, same pattern as TeamSettings.test.tsx)
//     for static render assertions (teammate designation shown, self "add
//     your role" prompt when null).
//   - Full mount of the hooks wrapper (TeamSettings), mocked teamApi, same
//     pattern as YourName.dom.test.tsx, for the self-edit → PATCH round trip
//     and the "no editable control on a teammate row" guarantee.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { TeamSettingsView } from "../TeamSettings"
import type { TeamMember, TeamInvite } from "../TeamSettings"

function noop() {}
function noopAsync() {
  return Promise.resolve()
}

const SELF_ID = "user-self"
const MATE_ID = "user-mate"

function baseProps(
  override: Partial<React.ComponentProps<typeof TeamSettingsView>> = {},
): React.ComponentProps<typeof TeamSettingsView> {
  const members: TeamMember[] = [
    {
      user_id: SELF_ID,
      role: "owner",
      display_name: "Self Person",
      email: "self@co.com",
      avatar_url: null,
      job_role: null,
    },
    {
      user_id: MATE_ID,
      role: "member",
      display_name: "Mate Person",
      email: "mate@co.com",
      avatar_url: null,
      job_role: "Designer",
    },
  ]
  return {
    members,
    invites: [] as TeamInvite[],
    currentUserId: SELF_ID,
    currentUserRole: "owner",
    loading: false,
    loadError: null,
    showInviteForm: false,
    inviteEmail: "",
    inviteRole: "member",
    inviteSubmitting: false,
    inviteError: null,
    inviteNotice: null,
    onToggleInviteForm: noop,
    onChangeInviteEmail: noop,
    onChangeInviteRole: noop,
    onSubmitInvite: noopAsync,
    bulkOpen: false,
    onToggleBulk: noop,
    bulkText: "",
    onChangeBulkText: noop,
    onSubmitBulkPaste: noop,
    bulkSubmitting: false,
    bulkNotice: null,
    csvInputRef: { current: null },
    onPickBulkCsv: noop,
    onRevokeInvite: noop,
    onResendInvite: noop,
    onChangeMemberRole: noop,
    onRemoveMember: noop,
    onChangeMyJobRole: noop,
    myJobRoleSaving: false,
    ...override,
  }
}

describe("TeamSettingsView — job designation (SSR)", () => {
  it("test_teamscreen_renders_member_job_role — a teammate with job_role='Designer' shows the designation on their row", () => {
    const html = renderToStaticMarkup(
      React.createElement(TeamSettingsView, baseProps()),
    )
    expect(html).toContain("Designer")
  })

  it("test_teamscreen_null_role_prompt — signed-in user with null job_role sees an 'add your role' affordance", () => {
    const html = renderToStaticMarkup(
      React.createElement(TeamSettingsView, baseProps()),
    )
    expect(html).toContain("Add your role")
  })

  it("does not show the self add-role affordance on a teammate's row", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        TeamSettingsView,
        baseProps({
          members: [
            {
              user_id: SELF_ID,
              role: "owner",
              display_name: "Self Person",
              email: "self@co.com",
              avatar_url: null,
              job_role: "Founder",
            },
            {
              user_id: MATE_ID,
              role: "member",
              display_name: "Mate Person",
              email: "mate@co.com",
              avatar_url: null,
              job_role: null,
            },
          ],
        }),
      ),
    )
    // Self has a role → shows it, editable (aria-label "Edit your role").
    expect(html).toContain('aria-label="Edit your role"')
    // Teammate has no role and gets no affordance at all (self-only).
    expect(html).not.toContain("Add your role")
  })
})

describe("TeamSettings — self job-role edit (mounted)", () => {
  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
    vi.doUnmock("../../../../../lib/teamApi")
    vi.doUnmock("../../../../../lib/auth")
    vi.doUnmock("../../../../../context/WorkspaceContext")
  })

  it("test_teamscreen_self_role_editable — signed-in user sees an editable role control; save calls the PATCH; a teammate row shows no editable control", async () => {
    // TeamSettingsView was already statically imported above (for the SSR
    // tests) — that pulled in the whole TeamSettings.tsx module graph
    // (teamApi/auth/WorkspaceContext) unmocked and cached it. Reset the
    // module registry so the dynamic import below re-evaluates against the
    // mocks registered next.
    vi.resetModules()

    const patchMyJobRole = vi.fn().mockResolvedValue({
      user_id: SELF_ID,
      job_role: "Engineer",
    })
    const listMembers = vi.fn().mockResolvedValue({
      members: [
        {
          user_id: SELF_ID,
          role: "owner",
          display_name: "Self Person",
          email: "self@co.com",
          avatar_url: null,
          job_role: null,
        },
        {
          user_id: MATE_ID,
          role: "member",
          display_name: "Mate Person",
          email: "mate@co.com",
          avatar_url: null,
          job_role: "Designer",
        },
      ],
    })
    const listInvites = vi.fn().mockResolvedValue({ invites: [] })

    vi.doMock("../../../../../lib/teamApi", () => ({
      teamApi: {
        listMembers,
        listInvites,
        patchMyJobRole,
        invite: vi.fn(),
        revokeInvite: vi.fn(),
        resendInvite: vi.fn(),
        patchMemberRole: vi.fn(),
        setMemberWorkspaces: vi.fn(),
        removeMember: vi.fn(),
        acceptInvite: vi.fn(),
      },
    }))
    vi.doMock("../../../../../lib/auth", () => ({
      useAuth: () => ({
        kind: "authed",
        user: { id: SELF_ID, email: "self@co.com" },
      }),
    }))
    vi.doMock("../../../../../context/WorkspaceContext", () => ({
      useWorkspace: () => ({ workspaces: [], activeWorkspace: null }),
    }))

    const { TeamSettings } = await import("../TeamSettings")

    await act(async () => {
      render(React.createElement(TeamSettings))
    })

    await waitFor(() => expect(listMembers).toHaveBeenCalled())
    await waitFor(() => screen.getByText("Mate Person"))

    // Teammate row: static designation text only, no editable control.
    expect(screen.getByText("Designer")).toBeTruthy()
    expect(screen.queryByLabelText("Edit your role")).toBeNull()

    // Self row: null role → "add your role" affordance.
    const addBtn = screen.getByLabelText("Add your role")
    fireEvent.click(addBtn)

    const select = screen.getByLabelText("Your role") as HTMLSelectElement
    fireEvent.change(select, { target: { value: "Engineer" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() =>
      expect(patchMyJobRole).toHaveBeenCalledWith(SELF_ID, "Engineer"),
    )
  })
})

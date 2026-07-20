// View tests for the redesigned Settings → Team & roles pane (SC3).
// Same node-env SSR pattern as ConnectorsSettings.test.tsx.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { TeamSettingsView } from "../TeamSettings"
import type { TeamMember, TeamInvite } from "../TeamSettings"

function noop() {}
function noopAsync() {
  return Promise.resolve()
}

const OWNER_ID = "user-owner"
const ADMIN_ID = "user-admin"
const MEMBER_ID = "user-member"

const members: TeamMember[] = [
  {
    user_id: OWNER_ID,
    role: "owner",
    display_name: "Owner Person",
    email: "owner@co.com",
    avatar_url: null,
  },
  {
    user_id: ADMIN_ID,
    role: "admin",
    display_name: "Admin Person",
    email: "admin@co.com",
    avatar_url: null,
  },
  {
    user_id: MEMBER_ID,
    role: "member",
    display_name: "Mem Person",
    email: "mem@co.com",
    avatar_url: null,
  },
]

const invites: TeamInvite[] = [
  { id: "inv-1", email: "pending1@co.com", role: "member", created_at: "2026-06-05T00:00:00Z" },
  { id: "inv-2", email: "pending2@co.com", role: "admin", created_at: "2026-06-06T00:00:00Z" },
]

function render(override: Partial<React.ComponentProps<typeof TeamSettingsView>> = {}): string {
  const defaults: React.ComponentProps<typeof TeamSettingsView> = {
    members,
    invites,
    currentUserId: OWNER_ID,
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
    onRevokeInvite: noop,
    onResendInvite: noop,
    onChangeMemberRole: noop,
    onRemoveMember: noop,
  }
  return renderToStaticMarkup(
    React.createElement(TeamSettingsView, { ...defaults, ...override }),
  )
}

describe("TeamSettingsView — chrome (mockup-aligned)", () => {
  it("uses the mockup's set-pane / set-h / set-sub structure", () => {
    const html = render()
    expect(html).toContain('class="set-pane sp-team"')
    expect(html).toContain('class="set-h"')
    expect(html).toContain("Team &amp; roles")
    expect(html).toContain("Anyone on your team can sign in")
  })

  it("renders a single set-block card (roles ref folded into dropdowns)", () => {
    const html = render()
    const matches = html.match(/class="set-block"/g) || []
    expect(matches.length).toBe(1)
  })

  it("renders the combined header count ('3 members · 2 pending invites')", () => {
    const html = render()
    expect(html).toContain("3 members")
    expect(html).toContain("2 pending invites")
  })

  it("singularises counts (1 member · 1 pending invite)", () => {
    const html = render({
      members: [members[0]],
      invites: [invites[0]],
    })
    expect(html).toContain("1 member ")
    expect(html).toContain("1 pending invite")
  })

  it("shows loading state when loading=true", () => {
    expect(render({ loading: true })).toContain("Loading team")
  })

  it("shows load error when set", () => {
    expect(render({ loadError: "API 500" })).toContain("API 500")
  })
})

describe("TeamSettingsView — combined roster", () => {
  it("renders all members AND all pending invites in one list", () => {
    const html = render()
    expect(html).toContain("owner@co.com")
    expect(html).toContain("admin@co.com")
    expect(html).toContain("mem@co.com")
    expect(html).toContain("pending1@co.com")
    expect(html).toContain("pending2@co.com")
  })

  it("renders an Active status chip per member and Invited per invite", () => {
    const html = render()
    const active = html.match(/class="st active"/g) || []
    expect(active.length).toBe(members.length)
    const invited = html.match(/class="st invited"/g) || []
    expect(invited.length).toBe(invites.length)
  })

  it("renders an avatar per row (member + invite)", () => {
    const html = render()
    const avatars = html.match(/class="set-team-row-av"/g) || []
    expect(avatars.length).toBe(members.length + invites.length)
  })

  it("admin sees the 3-dot actions menu (<details>)", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    expect(html).toContain('class="set-team-row-actions"')
    expect(html).toContain("⋯")
  })

  it("member viewer: no actions menu, no role dropdown", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html).not.toContain('class="set-team-row-actions"')
    expect(html).not.toContain("themed-select")
  })

  it("viewer-role caller: same read-only treatment as member", () => {
    const html = render({ currentUserRole: "viewer", currentUserId: MEMBER_ID })
    expect(html).not.toContain('class="set-team-row-actions"')
    expect(html).not.toContain("themed-select")
  })

  it("sole-owner row has its role select disabled (kept guard, decision 3-A)", () => {
    const html = render({
      members: [members[0]],
      currentUserRole: "owner",
      currentUserId: OWNER_ID,
    })
    expect(html).toContain("themed-select disabled")
  })

  it("renders the role select trigger with the member's current role", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    expect(html).toContain("themed-select-trigger")
    expect(html).toContain(">Owner<")
    expect(html).toContain(">Admin<")
    expect(html).toContain(">Member<")
  })
})

describe("TeamSettingsView — workspaces multi-select", () => {
  const workspaces = [
    { id: "w1", name: "Default" },
    { id: "w2", name: "Notifications" },
  ]

  it("member-role rows get a workspaces multi-select showing their grants", () => {
    const html = render({
      members: [members[0], { ...members[2], workspace_ids: ["w1"] }],
      availableWorkspaces: workspaces,
      onToggleMemberWorkspace: noop,
    })
    expect(html).toContain("set-team-row-ws")
    expect(html).toContain(">Default<")
  })

  it("summarises a full selection as All workspaces", () => {
    const html = render({
      members: [members[0], { ...members[2], workspace_ids: ["w1", "w2"] }],
      availableWorkspaces: workspaces,
      onToggleMemberWorkspace: noop,
    })
    expect(html).toContain(">All workspaces<")
  })

  it("owner/admin rows show the implicit-access label, not a picker", () => {
    const html = render({
      members: [members[0], members[1]],
      availableWorkspaces: workspaces,
      onToggleMemberWorkspace: noop,
    })
    expect(html).toContain("set-team-row-ws-all")
    expect(html).not.toContain("themed-multi-select")
  })

  it("single-workspace companies get no workspace pickers", () => {
    const html = render({
      members: [{ ...members[2], workspace_ids: ["w1"] }],
      availableWorkspaces: [workspaces[0]],
      onToggleMemberWorkspace: noop,
    })
    expect(html).not.toContain("set-team-row-ws")
  })

  it("invite form uses a workspaces dropdown instead of checkboxes", () => {
    const html = render({
      showInviteForm: true,
      availableWorkspaces: workspaces,
      inviteWorkspaceIds: ["w2"],
      onToggleInviteWorkspace: noop,
    })
    expect(html).toContain("set-team-invite-ws")
    expect(html).toContain(">Notifications<")
    expect(html).not.toContain('type="checkbox"')
  })
})

describe("TeamSettingsView — invite form", () => {
  it("invite form hidden by default", () => {
    const html = render()
    expect(html).not.toContain('type="email"')
  })

  it("admin sees + Invite teammate trigger", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    expect(html).toContain("+ Invite teammate")
  })

  it("member doesn't see the invite trigger", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html).not.toContain("Invite teammate")
  })

  it("renders inline form when showInviteForm=true", () => {
    const html = render({ showInviteForm: true })
    expect(html).toContain('type="email"')
    expect(html).toContain("set-team-invite-select")
  })

  it("submit disabled while submitting", () => {
    const html = render({ showInviteForm: true, inviteSubmitting: true })
    expect(html).toContain("disabled")
  })

  it("shows 'invite emailed' notice on sent", () => {
    const html = render({
      inviteNotice: { kind: "sent", email: "fresh@co.com" },
    })
    expect(html.toLowerCase()).toContain("invite emailed")
    expect(html).toContain("fresh@co.com")
  })

  it("shows warning on saved-without-email", () => {
    const html = render({
      inviteNotice: { kind: "saved", email: "fresh@co.com" },
    })
    expect(html.toLowerCase()).toContain("didn&#x27;t send")
  })
})

describe("TeamSettingsView — Roles reference card removed", () => {
  it("no longer renders the standalone Roles card", () => {
    const html = render()
    expect(html).not.toContain('class="set-row"')
    expect(html).not.toContain("Full access · billing")
  })
})

// View tests for the Settings → Team & roles pane (C4 of the team-roles slice).
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
  { user_id: OWNER_ID, role: "owner", display: "Owner Person", email: "owner@co.com" },
  { user_id: ADMIN_ID, role: "admin", display: "Admin Person", email: "admin@co.com" },
  { user_id: MEMBER_ID, role: "member", display: "Mem Person", email: "mem@co.com" },
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
    inviteEmail: "",
    inviteRole: "member",
    inviteSubmitting: false,
    inviteError: null,
    inviteNotice: null,
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

describe("TeamSettingsView — chrome", () => {
  it("renders the section title and sub-copy", () => {
    const html = render()
    expect(html).toContain("Team &amp; roles")
    // Some hint about the page's purpose.
    expect(html.toLowerCase()).toContain("invite")
  })

  it("shows loading state when loading=true", () => {
    expect(render({ loading: true })).toContain("Loading")
  })

  it("shows load error when set", () => {
    expect(render({ loadError: "API 500" })).toContain("API 500")
  })
})

describe("TeamSettingsView — members table", () => {
  it("renders every member with their role", () => {
    const html = render()
    expect(html).toContain("owner@co.com")
    expect(html).toContain("admin@co.com")
    expect(html).toContain("mem@co.com")
    // role chips/labels
    expect(html.toLowerCase()).toContain("owner")
    expect(html.toLowerCase()).toContain("admin")
    expect(html.toLowerCase()).toContain("member")
  })

  it("admin role unlocks the role <select> and remove control", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    // Some role-edit control is present.
    expect(html).toContain("<select")
    // Remove control is present.
    expect(html.toLowerCase()).toContain("remove")
  })

  it("member role: no role <select>, no remove buttons", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html).not.toContain("<select")
    expect(html.toLowerCase()).not.toContain("remove")
  })

  it("owner is flagged in the row (not editable away on the UI level when sole owner)", () => {
    const html = render({
      members: [members[0]], // only the owner
      currentUserRole: "owner",
      currentUserId: OWNER_ID,
    })
    // A 'last owner' or 'sole owner' marker, OR the select for that row is disabled.
    expect(
      html.toLowerCase().includes("sole owner") ||
        html.toLowerCase().includes("last owner") ||
        html.includes("disabled"),
    ).toBe(true)
  })
})

describe("TeamSettingsView — invite form", () => {
  it("renders the invite form when caller is admin", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    // An email input + a role select + a submit button.
    expect(html).toContain('type="email"')
    expect(html.toLowerCase()).toContain("invite")
  })

  it("hides the invite form when caller is a member", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html).not.toContain('type="email"')
  })

  it("shows inline invite error", () => {
    expect(
      render({ inviteError: "That email is already a member" }),
    ).toContain("That email is already a member")
  })

  it("disables submit while submitting=true", () => {
    const html = render({ inviteSubmitting: true })
    expect(html).toContain("disabled")
  })

  it("shows 'invite emailed' notice when inviteNotice.kind === 'sent'", () => {
    const html = render({
      inviteNotice: { kind: "sent", email: "fresh@co.com" },
    })
    expect(html.toLowerCase()).toContain("invite emailed")
    expect(html).toContain("fresh@co.com")
  })

  it("shows 'email didn't send — Resend' warning when kind === 'saved'", () => {
    const html = render({
      inviteNotice: { kind: "saved", email: "fresh@co.com" },
    })
    expect(html.toLowerCase()).toContain("didn&#x27;t send")
    expect(html).toContain("fresh@co.com")
  })
})

describe("TeamSettingsView — pending invites table", () => {
  it("lists every pending invite", () => {
    const html = render()
    expect(html).toContain("pending1@co.com")
    expect(html).toContain("pending2@co.com")
  })

  it("renders revoke + resend controls when admin", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    expect(html.toLowerCase()).toContain("revoke")
    expect(html.toLowerCase()).toContain("resend")
  })

  it("no controls for member viewer", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html.toLowerCase()).not.toContain("revoke")
    expect(html.toLowerCase()).not.toContain("resend")
  })

  it("renders empty-state when no invites", () => {
    const html = render({ invites: [] })
    expect(html.toLowerCase()).toContain("no pending")
  })
})

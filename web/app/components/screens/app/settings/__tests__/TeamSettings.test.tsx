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

  it("renders two set-block cards (combined roster + roles ref)", () => {
    const html = render()
    const matches = html.match(/class="set-block"/g) || []
    expect(matches.length).toBeGreaterThanOrEqual(2)
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

  it("member viewer: no actions menu, no role <select>", () => {
    const html = render({ currentUserRole: "member", currentUserId: MEMBER_ID })
    expect(html).not.toContain('class="set-team-row-actions"')
    expect(html).not.toContain("<select")
  })

  it("viewer-role caller: same read-only treatment as member", () => {
    const html = render({ currentUserRole: "viewer", currentUserId: MEMBER_ID })
    expect(html).not.toContain('class="set-team-row-actions"')
    expect(html).not.toContain("<select")
  })

  it("sole-owner row has its role select disabled (kept guard, decision 3-A)", () => {
    const html = render({
      members: [members[0]],
      currentUserRole: "owner",
      currentUserId: OWNER_ID,
    })
    expect(html).toMatch(/<select[^>]*\bdisabled\b/)
  })

  it("includes Viewer in the member role select dropdown", () => {
    const html = render({ currentUserRole: "admin", currentUserId: ADMIN_ID })
    expect(html).toContain('value="viewer"')
    expect(html).toContain(">Viewer<")
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
    expect(html).toContain(">Viewer<")
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

describe("TeamSettingsView — Roles reference block", () => {
  it("renders all four role descriptions", () => {
    const html = render()
    expect(html).toMatch(/<strong>\s*Owner\s*<\/strong>/)
    expect(html).toMatch(/<strong>\s*Admin\s*<\/strong>/)
    expect(html).toMatch(/<strong>\s*Member\s*<\/strong>/)
    expect(html).toMatch(/<strong>\s*Viewer\s*<\/strong>/)
  })

  it("uses the set-row primitives for the role reference", () => {
    const html = render()
    const setRows = html.match(/class="set-row"/g) || []
    expect(setRows.length).toBeGreaterThanOrEqual(4)
  })
})

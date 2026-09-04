// @vitest-environment jsdom
//
// Mount tests for Settings → Team & roles' bulk invite (paste + CSV), ported
// from the retired onboarding InviteStep (2026-09-03). Covers the wiring the
// pure parsers (app/lib/__tests__/teamApi.test.ts) don't: the "Add multiple
// at once" disclosure opens/closes, a paste sends one teamApi.invite per row
// with the CURRENT invite-workspace selection, a bad address among good ones
// is best-effort (the rest still send), and CSV import's click wiring opens
// the file picker (the full read is untestable under this repo's jsdom — see
// that test's own comment).
//
// teamApi itself is NOT module-mocked — parsePastedEmails/parseInvitesCsv stay
// their real, already-tested selves, and only the network-calling methods are
// stubbed via vi.spyOn.
//
// `useWorkspace`'s mock returns STABLE object references (module-scoped, via
// vi.hoisted), not fresh literals per call: TeamSettings.tsx has a
// `useEffect(..., [activeWorkspace])` that sets state when `activeWorkspace`
// is truthy, and a mock returning a new object on every render makes that
// effect see a "new" dependency every time, looping forever. The real
// context's `activeWorkspace` is `useState`-backed and referentially stable,
// so this is fidelity to the real hook, not a workaround for a bug in it.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const OWNER_ID = "user-owner"

// Hoisted, STABLE references (vi.hoisted so vi.mock's factory — itself hoisted
// above regular imports — can see them). TeamSettings.tsx has
// `useEffect(() => { if (activeWorkspace) setInviteWorkspaceIds([...]) },
// [activeWorkspace])` — the real context's `activeWorkspace` is `useState`, so
// it only changes identity when actually reassigned. A mock returning a fresh
// object literal on every call breaks that: the effect sees a "new"
// dependency on every render and loops forever setting state, which is
// exactly what hung this file until traced down to this. Stable references
// keep the mock's identity as stable as the real hook's.
const { ACTIVE_WORKSPACE, WORKSPACES } = vi.hoisted(() => {
  const activeWorkspace = { id: "ws-1", name: "Main workspace" }
  return { ACTIVE_WORKSPACE: activeWorkspace, WORKSPACES: [activeWorkspace] }
})

vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed", user: { id: OWNER_ID, email: "owner@co.com" } }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    workspaces: WORKSPACES,
    activeWorkspace: ACTIVE_WORKSPACE,
  }),
}))

import { TeamSettings } from "../TeamSettings"
import { teamApi } from "../../../../../lib/teamApi"

let listMembers: MockInstance<typeof teamApi.listMembers>
let inviteSpy: MockInstance<typeof teamApi.invite>

beforeEach(() => {
  listMembers = vi.spyOn(teamApi, "listMembers").mockResolvedValue({
    members: [{ user_id: OWNER_ID, role: "owner", display_name: "Owner", email: "owner@co.com", avatar_url: null }],
  })
  vi.spyOn(teamApi, "listInvites").mockResolvedValue({ invites: [] })
  inviteSpy = vi.spyOn(teamApi, "invite").mockResolvedValue({
    id: "inv-x", email: "x@acme.com", role: "member", created_at: null, email_sent: true,
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function mount() {
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(React.createElement(TeamSettings))
  })
  await waitFor(() => expect(listMembers).toHaveBeenCalled())
  return utils
}

describe("TeamSettings — bulk invite (mounted)", () => {
  it("the disclosure is closed by default and opens on 'Add multiple at once'", async () => {
    await mount()
    expect(screen.queryByPlaceholderText(/alex@company.com/)).toBeNull()
    fireEvent.click(screen.getByText("Add multiple at once"))
    expect(screen.getByPlaceholderText(/alex@company.com/)).not.toBeNull()
  })

  it("a paste sends one teamApi.invite per valid row, targeting the active workspace", async () => {
    await mount()
    fireEvent.click(screen.getByText("Add multiple at once"))
    fireEvent.change(screen.getByPlaceholderText(/alex@company.com/), {
      target: { value: "a@acme.com, b@acme.com" },
    })
    fireEvent.click(screen.getByText("Send invites"))

    await waitFor(() => expect(inviteSpy).toHaveBeenCalledTimes(2))
    expect(inviteSpy).toHaveBeenCalledWith("a@acme.com", "member", ["ws-1"], expect.any(String))
    expect(inviteSpy).toHaveBeenCalledWith("b@acme.com", "member", ["ws-1"], expect.any(String))
    await waitFor(() => expect(screen.getByText("2 invites sent.")).not.toBeNull())
    // The field clears after a successful send.
    expect((screen.getByPlaceholderText(/alex@company.com/) as HTMLTextAreaElement).value).toBe("")
  })

  it("a bad address among good ones is best-effort: the good rows still send", async () => {
    inviteSpy.mockImplementation((email: string) =>
      email === "bad@acme.com"
        ? Promise.reject(new Error("refused"))
        : Promise.resolve({ id: "i", email, role: "member" as const, created_at: null }),
    )
    await mount()
    fireEvent.click(screen.getByText("Add multiple at once"))
    fireEvent.change(screen.getByPlaceholderText(/alex@company.com/), {
      target: { value: "good@acme.com, bad@acme.com" },
    })
    fireEvent.click(screen.getByText("Send invites"))

    await waitFor(() => expect(inviteSpy).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByText(/Sent 1\/2 — couldn't invite bad@acme.com\./)).not.toBeNull(),
    )
  })

  it("pasting nothing valid shows a notice and sends nothing", async () => {
    await mount()
    fireEvent.click(screen.getByText("Add multiple at once"))
    fireEvent.change(screen.getByPlaceholderText(/alex@company.com/), {
      target: { value: "not-an-email" },
    })
    fireEvent.click(screen.getByText("Send invites"))
    expect(screen.getByText("No valid email addresses in that paste.")).not.toBeNull()
    expect(inviteSpy).not.toHaveBeenCalled()
  })

  it("'Import CSV' opens the hidden file picker", async () => {
    // The read-and-send path (parse → teamApi.invite per row) is the SAME
    // code the paste test above already exercises via `sendBulkInvites`, and
    // `parseInvitesCsv` itself is covered in teamApi.test.ts. What's left to
    // prove here is the click wiring — and NOT the full round trip through a
    // real `File`: this repo's jsdom (pinned ^25.0.1) implements neither
    // `File.text()` nor `Blob.text()`/`arrayBuffer()`, so `onPickBulkCsv`'s
    // `await file.text()` cannot resolve under jsdom regardless of correct
    // code, matching why the retired onboarding InviteStep test never drove a
    // real File through this same handler either.
    await mount()
    fireEvent.click(screen.getByText("Add multiple at once"))
    const input = screen.getByLabelText("Import teammates CSV") as HTMLInputElement
    const clickSpy = vi.spyOn(input, "click")
    fireEvent.click(screen.getByText("Import CSV"))
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })
})

// @vitest-environment jsdom
//
// The @-mention people picker in ProjectGroupChat's composer: the distinct
// token (@name opens the picker, @sprntly does NOT), candidate rows from
// `candidateSearch`, the invite-by-email row, the tier-appropriate affordance
// from `tagCandidate` (added / invite sent / copy-link fallback / generic
// refuse), the picker's loading/empty/error states, and mention chips rendered
// in message bubbles. All network is mocked (the presentational tier — the
// live add/invite/notify is a later real-path gate).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const groupTurnsMock = vi.fn()
const postGroupTurnMock = vi.fn()
const candidateSearchMock = vi.fn()
const tagCandidateMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
      postGroupTurn: (...a: unknown[]) => postGroupTurnMock(...a),
      candidateSearch: (...a: unknown[]) => candidateSearchMock(...a),
      tagCandidate: (...a: unknown[]) => tagCandidateMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed", user: { id: "u1" } }),
}))

import { ProjectGroupChat } from "../ProjectGroupChat"
import type { GroupTurn } from "../../../../../lib/api"

const turn = (overrides: Partial<GroupTurn>): GroupTurn => ({
  id: 1,
  role: "user",
  content: "hello",
  author_user_id: "u1",
  author_name: "Ada",
  author_job_role: "PM",
  created_at: new Date().toISOString(),
  ...overrides,
})

/** Type `value` into the composer textarea (caret at the end). */
async function typeDraft(value: string) {
  const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(ta).toBeTruthy()
  await act(async () => {
    fireEvent.change(ta, { target: { value, selectionStart: value.length } })
    await Promise.resolve()
  })
  return ta
}

beforeEach(() => {
  groupTurnsMock.mockReset()
  postGroupTurnMock.mockReset()
  candidateSearchMock.mockReset()
  tagCandidateMock.mockReset()
  groupTurnsMock.mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectGroupChat — @-mention people picker", () => {
  it("test_typing_at_opens_people_picker", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Fortune Ade", email: "fortune@acme.com" },
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@For")

    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, "For"))
    const picker = await screen.findByTestId("gc-mention-picker")
    const row = await within(picker).findByTestId("gc-mention-candidate")
    expect(row.textContent).toContain("Fortune Ade")
    expect(row.textContent).toContain("fortune@acme.com")
    expect(within(row).getByTestId("gc-mention-kind").textContent).toBe("Member")
  })

  it("test_sprntly_does_not_open_picker_and_invokes_agent", async () => {
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    // @sprntly is the agent token — no people picker, no candidate search.
    await typeDraft("@sprntly")
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200))
    })
    expect(screen.queryByTestId("gc-mention-picker")).toBeNull()
    expect(candidateSearchMock).not.toHaveBeenCalled()

    // A non-sprntly token DOES open the picker (and does not route to the agent).
    await typeDraft("@Fortune")
    await waitFor(() => expect(candidateSearchMock).toHaveBeenCalledWith(101, "Fortune"))
    expect(await screen.findByTestId("gc-mention-picker")).toBeTruthy()
  })

  it("the agent-invoke path (invokedBy) is unchanged for an @Sprntly-triggered reply", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly help" }),
      turn({ id: 2, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "on it" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    // Redesign: the invoke-only `gc-invoker` tag became the always-present agent
    // `gc-state-badge` ("invoked by <first name>" here). Same invoked-by
    // semantics.
    const badge = await screen.findByTestId("gc-state-badge")
    expect(badge.textContent).toContain("invoked by")
    expect(badge.textContent).toContain("Shristi")
  })

  it("test_select_member_inserts_chip_no_network", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Mabel", email: "mabel@acme.com" },
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@Mab")
    const row = await screen.findByTestId("gc-mention-candidate")
    await act(async () => {
      fireEvent.click(row)
    })

    const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(ta.value).toContain("@Mabel")
    expect(tagCandidateMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId("gc-mention-picker")).toBeNull()
  })

  it("test_select_non_member_calls_tag_and_shows_added", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "workspace", user_id: "u3", name: "Nadia", email: "nadia@acme.com" },
    ])
    tagCandidateMock.mockResolvedValue({ tier: "t_workspace", added: { user_id: "u3" } })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@Nad")
    const row = await screen.findByTestId("gc-mention-candidate")
    expect(within(row).getByTestId("gc-mention-kind").textContent).toBe("Not on project")
    await act(async () => {
      fireEvent.click(row)
    })

    await waitFor(() => expect(tagCandidateMock).toHaveBeenCalledTimes(1))
    expect(tagCandidateMock).toHaveBeenCalledWith(101, "nadia@acme.com")
    const affordance = await screen.findByTestId("gc-mention-affordance")
    expect(affordance.textContent).toContain("added")
  })

  it("test_invite_by_email_row_present_and_drives_invite", async () => {
    candidateSearchMock.mockResolvedValue([]) // no directory match
    tagCandidateMock.mockResolvedValue({ tier: "t_newuser", invited: true, email_status: "sent" })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@jane@acme.com")
    const invite = await screen.findByTestId("gc-mention-invite")
    expect(invite.textContent).toContain("Invite jane@acme.com by email")
    await act(async () => {
      fireEvent.click(invite)
    })

    await waitFor(() => expect(tagCandidateMock).toHaveBeenCalledWith(101, "jane@acme.com"))
    const affordance = await screen.findByTestId("gc-mention-affordance")
    expect(affordance.textContent).toContain("Invite sent")
  })

  it("test_email_failed_shows_reinvite_hint_no_dead_link", async () => {
    // The /tag route returns no accept link, so a failed email degrades to a
    // plain re-invite hint — never a dead copy-link affordance (AD-TNM6). The
    // vestigial copy-link button is gone.
    candidateSearchMock.mockResolvedValue([])
    tagCandidateMock.mockResolvedValue({
      tier: "t_company",
      invited: true,
      email_status: "failed",
    })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@zoe@acme.com")
    const invite = await screen.findByTestId("gc-mention-invite")
    await act(async () => {
      fireEvent.click(invite)
    })

    const affordance = await screen.findByTestId("gc-mention-affordance")
    expect(affordance.textContent).toContain("email didn't send")
    expect(affordance.textContent).toContain("re-invite from Team settings")
    // No dead copy-link affordance remains.
    expect(screen.queryByTestId("gc-copy-invite-link")).toBeNull()
    // Composer stays usable — no thrown error, textarea still there.
    expect(document.querySelector(".cx-input")).toBeTruthy()
  })

  it("test_tag_rejected_shows_generic_no_disclosure", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "company", user_id: "u9", name: "Otherco Person", email: "person@otherco.com" },
    ])
    tagCandidateMock.mockRejectedValue(
      Object.assign(new Error("That person can't be added to this project"), { status: 403 }),
    )
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@Person")
    const row = await screen.findByTestId("gc-mention-candidate")
    await act(async () => {
      fireEvent.click(row)
    })

    const affordance = await screen.findByTestId("gc-mention-affordance")
    expect(affordance.textContent).toBe("Couldn't add that person")
    // No disclosure of the refuse reason (cross-tenant / other-company / 403).
    expect(affordance.textContent?.toLowerCase()).not.toContain("company")
    expect(affordance.textContent).not.toContain("403")
    expect(document.querySelector(".cx-input")).toBeTruthy()
  })

  it("test_picker_loading_empty_error_states", async () => {
    // Loading: the picker shows a loading hint immediately on an active query.
    let resolveSearch: (rows: unknown[]) => void = () => {}
    candidateSearchMock.mockReturnValue(new Promise((r) => (resolveSearch = r)))
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")
    await typeDraft("@Load")
    expect(await screen.findByTestId("gc-mention-loading")).toBeTruthy()
    await act(async () => {
      resolveSearch([])
      await Promise.resolve()
    })
    cleanup()

    // Empty: an empty directory yields the "No matches — invite by email" row.
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")
    await typeDraft("@zzz")
    const invite = await screen.findByTestId("gc-mention-invite")
    expect(invite.textContent).toContain("No matches")
    cleanup()

    // Error: a rejected search renders the error state and never throws.
    candidateSearchMock.mockRejectedValue(new Error("boom"))
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")
    await typeDraft("@Err")
    expect(await screen.findByTestId("gc-mention-error")).toBeTruthy()
  })

  it("test_mention_chip_rendered_in_bubble", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "hey @Fortune take a look" }),
      turn({ id: 2, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly summarise" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const others = await screen.findAllByTestId("gc-msg-other")

    // The @name mention is a chip; @sprntly is NOT chipped as a person.
    const firstChip = within(others[0]).getByTestId("gc-mention-chip")
    expect(firstChip.textContent).toBe("@Fortune")
    expect(within(others[1]).queryByTestId("gc-mention-chip")).toBeNull()
    // Non-mention text survives.
    expect(others[0].textContent).toContain("take a look")
  })

  it("test_agent_row_leads_and_inserts_token_no_write — a partial-prefix query leads with the Agent row; selecting it inserts @Sprntly with NO network call", async () => {
    candidateSearchMock.mockResolvedValue([
      { kind: "member", user_id: "u2", name: "Sprocket", email: "sprocket@acme.com" },
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    // "spr" is a partial prefix of BOTH "Sprntly" (the agent) and "Sprocket"
    // (a real candidate) — the agent row must lead the list.
    await typeDraft("@spr")
    const picker = await screen.findByTestId("gc-mention-picker")
    const agentRow = await within(picker).findByTestId("gc-mention-agent")
    const rows = within(picker).getAllByRole("option")
    expect(rows[0].getAttribute("data-testid")).toBe("gc-mention-agent")
    expect(rows[0].textContent).toContain("Sprntly")
    expect(within(agentRow).getByText("Agent")).toBeTruthy()

    await act(async () => {
      fireEvent.click(agentRow)
    })

    const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(ta.value).toContain("@Sprntly")
    expect(candidateSearchMock).toHaveBeenCalled() // the debounced search still ran…
    expect(tagCandidateMock).not.toHaveBeenCalled() // …but selecting the agent NEVER calls tagCandidate
    expect(screen.queryByTestId("gc-mention-picker")).toBeNull()
  })

  it("an empty '@' query (no typed prefix yet) still leads with the Agent row", async () => {
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    await typeDraft("@")
    const picker = await screen.findByTestId("gc-mention-picker")
    expect(await within(picker).findByTestId("gc-mention-agent")).toBeTruthy()
  })

  it("regression guard: does NOT regress the base @sprntly-invokes-agent-no-picker guard even with the agent row wired", async () => {
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")

    // The COMPLETE word "@sprntly" still routes to the agent-invoke path —
    // detectMentionQuery returns null for it, so mentionItems (and the new
    // agent row) never even compute; no picker opens.
    await typeDraft("@sprntly")
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200))
    })
    expect(screen.queryByTestId("gc-mention-picker")).toBeNull()
    expect(candidateSearchMock).not.toHaveBeenCalled()
  })

  it("the sent draft, when it carries @Sprntly, is recognized by the backend's _MENTION_RE shape (word-boundary, case-insensitive)", async () => {
    candidateSearchMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("group-chat-scroll")
    await typeDraft("@spr")
    const agentRow = await screen.findByTestId("gc-mention-agent")
    await act(async () => {
      fireEvent.click(agentRow)
    })
    const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
    // The backend's `_MENTION_RE = re.compile(r"@sprntly\b", re.I)` — the
    // inserted token satisfies the same shape a hand-typed one would.
    expect(/@sprntly\b/i.test(ta.value)).toBe(true)
    const draftBeforeSend = ta.value.trim()

    postGroupTurnMock.mockResolvedValue({ id: 9, role: "user", content: draftBeforeSend } as never)
    groupTurnsMock.mockResolvedValueOnce([])
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await waitFor(() => expect(postGroupTurnMock).toHaveBeenCalledWith(101, draftBeforeSend))
  })

  it("test_existing_group_chat_behaviour_unchanged", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 2, author_user_id: "u2", author_name: "Shristi", author_job_role: "Design", content: "@Sprntly help" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "on it" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    // Multi-author bubbles + agent invoked-by state badge intact.
    const other = await screen.findByTestId("gc-msg-other")
    expect(within(other).getByText("Shristi")).toBeTruthy()
    expect(screen.getByTestId("gc-msg-me")).toBeTruthy()
    expect(screen.getByTestId("gc-msg-agent")).toBeTruthy()
    expect(screen.getByTestId("gc-state-badge").textContent).toContain("Shristi")

    // Send path unaffected: a plain message posts through postGroupTurn.
    postGroupTurnMock.mockResolvedValue(turn({ id: 5, content: "hi team" }))
    groupTurnsMock.mockResolvedValueOnce([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly help" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "on it" }),
    ])
    await typeDraft("hi team")
    const sendBtn = screen.getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })
    await waitFor(() => expect(postGroupTurnMock).toHaveBeenCalledWith(101, "hi team"))
  })
})

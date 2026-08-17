// @vitest-environment jsdom
//
// ProjectPrivateChat — persisted-history load + render. The gap this
// closes: the thread used to render only turns produced in the CURRENT
// browser session and started empty on every reload, so a brief delivered
// by delegation (a durable `role: "assistant"` turn with no paired
// question) landed in the DB but was never visible. These tests cover the
// on-open fetch, the standalone-agent-turn render, the send-flow-preserved
// guarantee, and the best-effort degrade — the session-flow assertions
// themselves stay in `ProjectPrivateChat.test.tsx`.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

const runAskGenerationMock = vi.fn()
const resumeAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)
const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()
const chatSuggestionsNextMock = vi.fn((..._a: unknown[]) => Promise.resolve({ suggestions: [] as string[] }))
// Mutable so a test can supply a caller profile (name/initials derivation)
// without a per-file re-mock; default null keeps every existing test byte-
// identical (callerName falls through to null, as before).
let workspaceProfile: { first_name?: string; last_name?: string } | null = null

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: (...a: unknown[]) => resumeAskGenerationMock(...a),
    getPendingAsk: (...a: unknown[]) => getPendingAskMock(...a),
  }
})

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
    },
    chatSuggestionsApi: {
      ...actual.chatSuggestionsApi,
      next: (...a: unknown[]) => chatSuggestionsNextMock(...(a as [number, { prdId?: number | null }])),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))

// Pre-existing fixture gap fixed in passing (blocked EVERY test in this
// file, including the ones this ticket adds below): the component reads
// `useWorkspace()` for the classifier flag, and this file was missing the
// mock `ProjectPrivateChat.test.tsx` already carries — mirrored verbatim
// here (flag OFF keeps every existing assertion in this file
// byte-identical: plain `/v1/ask`-only sends).
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: workspaceProfile,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))

// New on this ticket: the component now subscribes to the caller's own
// per-user realtime channel, which needs a resolvable user id — mock
// `useAuth` so mount doesn't throw for lack of a real `AuthProvider`. The
// realtime wiring itself is covered end-to-end in
// `ProjectPrivateChat.realtime.dom.test.tsx`.
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))

import { ProjectPrivateChat } from "../ProjectPrivateChat"

const individualChatRecord = (id: number, projectId: number) => ({
  id,
  project_id: projectId,
  user_id: "u1",
  kind: "individual" as const,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
})

const reply = (answer: string) => ({ answer, key_points: [], citations: [], confidence: 1, unanswered: "" })

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resumeAskGenerationMock.mockReset()
  getPendingAskMock.mockReset()
  getPendingAskMock.mockReturnValue(null)
  individualChatMock.mockReset()
  individualChatMock.mockImplementation((id: number) => Promise.resolve(individualChatRecord(9001, id)))
  individualTurnsMock.mockReset()
  individualTurnsMock.mockResolvedValue([])
  chatSuggestionsNextMock.mockReset()
  chatSuggestionsNextMock.mockResolvedValue({ suggestions: [] })
  workspaceProfile = null
})
afterEach(() => cleanup())

describe("ProjectPrivateChat — loads history on open (AC5)", () => {
  it("fetches the project's individual turns on mount and renders prior user + assistant turns in the right bubbles", async () => {
    individualTurnsMock.mockResolvedValue([
      { id: 1, role: "user", content: "what did we decide on pricing?", created_at: "2026-08-10T10:00:00Z" },
      { id: 2, role: "assistant", content: "Flat $49/mo, decided last week.", created_at: "2026-08-10T10:01:00Z" },
    ])

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))

    expect(individualTurnsMock).toHaveBeenCalledWith(202)
    await waitFor(() => expect(screen.getAllByTestId("ic-history-you")).toHaveLength(1))
    expect(screen.getByTestId("ic-history-you").textContent).toContain("what did we decide on pricing?")
    expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1)
    expect(screen.getByTestId("ic-history-agent").textContent).toContain("Flat $49/mo, decided last week.")
    // Never renders through the empty-state affordance once history exists.
    expect(screen.queryByTestId("individual-chat-empty")).toBeNull()
  })

  it("does not gate the history fetch on the get-or-create conversation call", async () => {
    // ensureConversationId is only ever invoked lazily on a SEND — mounting
    // alone (no send) must load history without also creating a
    // conversation row.
    render(React.createElement(ProjectPrivateChat, { projectId: 303, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledWith(303))
    expect(individualChatMock).not.toHaveBeenCalled()
  })
})

describe("ProjectPrivateChat — standalone agent turn (AC6, the delegated-brief case)", () => {
  it("renders a lone assistant turn with no paired question as an agent-markdown bubble", async () => {
    individualTurnsMock.mockResolvedValue([
      { id: 5, role: "assistant", content: "**Brief:** ship the onboarding flow by Friday.", created_at: "2026-08-11T09:00:00Z" },
    ])

    render(React.createElement(ProjectPrivateChat, { projectId: 404, openPrdId: null }))

    await waitFor(() => expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1))
    expect(screen.getByTestId("ic-history-agent").textContent).toContain("ship the onboarding flow by Friday")
    // Not dropped for lack of a preceding user turn — no "you" bubble exists.
    expect(screen.queryByTestId("ic-history-you")).toBeNull()
  })
})

describe("ProjectPrivateChat — send flow preserved (AC7)", () => {
  it("composing + sending still runs runAskGeneration and optimistically appends the new turn, unaffected by loaded history", async () => {
    individualTurnsMock.mockResolvedValue([
      { id: 1, role: "assistant", content: "earlier brief", created_at: "2026-08-10T10:00:00Z" },
    ])
    runAskGenerationMock.mockResolvedValue(reply("here's the answer"))

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(screen.getAllByTestId("ic-history-agent")).toHaveLength(1))

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "a brand new question" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(individualChatMock).toHaveBeenCalledWith(202)
    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "a brand new question",
      "acme",
      "project-individual-202",
      expect.objectContaining({ project_id: 202, conversation_id: 9001 }),
    )
    expect(screen.getByTestId("ic-msg-you").textContent).toContain("a brand new question")
    await waitFor(() => expect(document.querySelector(".ai-bar-reply-answer")).toBeTruthy())
    // The loaded history turn is still there, unreplaced by the new send.
    expect(screen.getByTestId("ic-history-agent").textContent).toContain("earlier brief")
  })
})

describe("ProjectPrivateChat — history fetch failure degrades (AC8)", () => {
  it("a failed history fetch renders an empty history and never blocks the composer or throws", async () => {
    individualTurnsMock.mockRejectedValue(new Error("network blip"))
    runAskGenerationMock.mockResolvedValue(reply("still works"))

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalled())

    // Degrades to the empty-state affordance (no history, no session turns yet).
    expect(screen.getByTestId("individual-chat-empty")).toBeTruthy()

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "composer still usable" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await waitFor(() => expect(document.querySelector(".ai-bar-reply-answer")).toBeTruthy())
  })
})

describe("ProjectPrivateChat — empty state (AC9)", () => {
  it("renders the existing empty-state affordance when both persisted history and session turns are empty", async () => {
    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalled())
    expect(screen.getByTestId("individual-chat-empty")).toBeTruthy()
  })
})

describe("ProjectPrivateChat — user head name + avatar via shared props (AC6/AC7)", () => {
  it("test_private_user_head_shows_first_name_only — the user head shows the caller's FIRST name only, not the full name", async () => {
    workspaceProfile = { first_name: "Babajide", last_name: "Okusanya" }
    individualTurnsMock.mockResolvedValue([
      { id: 1, role: "user", content: "what did we decide?", created_at: "2026-08-10T10:00:00Z" },
    ])
    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(screen.getByTestId("ic-history-you")).toBeTruthy())
    const name = document.querySelector(".bc-user-name")
    expect(name?.textContent).toBe("Babajide")
    expect(document.querySelector(".bc-user-name")?.textContent).not.toContain("Okusanya")
  })

  it("test_private_user_avatar_shows_initials — the avatar chip shows the caller's initials (BO), same rule as main", async () => {
    workspaceProfile = { first_name: "Babajide", last_name: "Okusanya" }
    individualTurnsMock.mockResolvedValue([
      { id: 1, role: "user", content: "what did we decide?", created_at: "2026-08-10T10:00:00Z" },
    ])
    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(screen.getByTestId("ic-history-you")).toBeTruthy())
    // The user-head avatar carries the "BO" monogram (not empty).
    const avatars = [...document.querySelectorAll(".bc-user-head .bc-avatar")]
    expect(avatars.some((a) => a.textContent === "BO")).toBe(true)
  })

  it("test_private_agent_turn_has_no_timestamp — a private agent turn shows no agentTime node, matching main (AC5)", async () => {
    individualTurnsMock.mockResolvedValue([
      { id: 2, role: "assistant", content: "Flat $49/mo.", created_at: "2026-08-10T10:01:00Z" },
    ])
    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    const agent = await screen.findByTestId("ic-history-agent")
    expect(agent.querySelector("[class*='agentTime']")).toBeNull()
  })
})

describe("ProjectPrivateChat — next-prompt pills in the dock (AC8/AC9)", () => {
  it("test_private_pills_render_in_dock_after_settle — pills render after a turn settles with non-empty suggestions; absent when suggestions are []", async () => {
    chatSuggestionsNextMock.mockResolvedValue({ suggestions: ["Ask about pricing", "Review the brief"] })
    runAskGenerationMock.mockResolvedValue(reply("here's the answer"))

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalled())

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    // Before any settle: no strip (no empty container).
    expect(screen.queryByTestId("next-prompt-suggestions")).toBeNull()

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "what did we decide?" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    const strip = await screen.findByTestId("next-prompt-suggestions")
    expect(strip.textContent).toContain("Ask about pricing")
    expect(strip.textContent).toContain("Review the brief")
  })

  it("test_private_pills_absent_when_empty — an empty suggestions result renders no strip", async () => {
    chatSuggestionsNextMock.mockResolvedValue({ suggestions: [] })
    runAskGenerationMock.mockResolvedValue(reply("here's the answer"))

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalled())

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "what did we decide?" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    await waitFor(() => expect(document.querySelector(".ai-bar-reply-answer")).toBeTruthy())
    // Empty suggestions → nothing rendered (the negative contract).
    expect(screen.queryByTestId("next-prompt-suggestions")).toBeNull()
  })

  it("test_private_pills_call_shared_chat_suggestions_endpoint — the adapter calls chatSuggestionsApi.next(convId, { prdId: null }) (AC9)", async () => {
    chatSuggestionsNextMock.mockResolvedValue({ suggestions: ["Ask about pricing"] })
    runAskGenerationMock.mockResolvedValue(reply("answer"))

    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalled())

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "what did we decide?" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    await waitFor(() => expect(chatSuggestionsNextMock).toHaveBeenCalled())
    expect(chatSuggestionsNextMock).toHaveBeenCalledWith(9001, { prdId: null })
  })
})

describe("ProjectPrivateChat — on-join greeting <!--more--> marker (AC6)", () => {
  it("test_private_join_greeting_more_marker_stripped — a <!--more--> turn renders the FULL content through the shared ladder with the marker stripped and no literal comment leaking", async () => {
    individualTurnsMock.mockResolvedValue([
      {
        id: 9,
        role: "assistant",
        content: "Hey — you're on Dark Mode Launch now. Here's what I know so far:\n\nThe lead gist.<!--more-->\n\nThe rest of the summary.",
        created_at: "2026-08-13T00:00:00Z",
      },
    ])

    render(React.createElement(ProjectPrivateChat, { projectId: 505, openPrdId: null }))
    const agent = await screen.findByTestId("ic-history-agent")
    // The greeting renders through the native ladder's AskReplyBody. With
    // `AgentTurnBody` gone, the whole content shows (no show-more toggle) and —
    // the guaranteed-regression fix — the `<!--more-->` marker is replaced by a
    // paragraph break, so the literal HTML comment NEVER leaks as text
    // (AskReplyBody runs react-markdown without rehype-raw).
    expect(agent.textContent).toContain("The lead gist.")
    expect(agent.textContent).toContain("The rest of the summary.")
    expect(agent.textContent).not.toContain("<!--more-->")
    expect(agent.innerHTML).not.toContain("<!--more-->")
    // The show-more toggle is retired — main has no history show-more.
    expect(screen.queryByTestId("ic-agent-show-more")).toBeNull()
  })

  it("test_no_marker_renders_unchanged — a plain assistant turn (no marker) renders through the shared ladder, no toggle", async () => {
    individualTurnsMock.mockResolvedValue([
      { id: 2, role: "assistant", content: "Flat $49/mo, decided last week.", created_at: "2026-08-10T10:01:00Z" },
    ])
    render(React.createElement(ProjectPrivateChat, { projectId: 202, openPrdId: null }))
    const agent = await screen.findByTestId("ic-history-agent")
    expect(agent.textContent).toContain("Flat $49/mo, decided last week.")
    expect(screen.queryByTestId("ic-agent-show-more")).toBeNull()
  })
})

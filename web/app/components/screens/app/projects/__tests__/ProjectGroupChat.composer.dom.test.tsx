// @vitest-environment jsdom
//
// ProjectGroupChat — composer unblock during a backgrounded agent reply.
//
// The backend already backgrounds the group reply
// (`routes/projects.py:post_group_turn_route` returns once the human turn is
// persisted + broadcast + the gate has decided, never after the reply
// generates) — this file proves the FRONT END no longer waits on anything
// past that POST before letting the next message go: the composer's Send
// button must never be swapped for the (no-op, group has no Stop UI) Stop
// button, a second, DIFFERENT draft must be sendable immediately, and a
// double-submit of the exact SAME draft must still be blocked.
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

const groupTurnsMock = vi.fn()
const postGroupTurnMock = vi.fn()
const saveChatArtifactMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
      postGroupTurn: (...a: unknown[]) => postGroupTurnMock(...a),
      saveChatArtifact: (...a: unknown[]) => saveChatArtifactMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
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

beforeEach(() => {
  groupTurnsMock.mockReset()
  postGroupTurnMock.mockReset()
  saveChatArtifactMock.mockReset()
})
afterEach(() => cleanup())

describe("ProjectGroupChat — composer not blocked while a reply generates in the background", () => {
  it("the Send button never becomes the (no-op) Stop button — busy is never fed by the send round-trip", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {})

    let resolvePost: (v: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve
      }),
    )
    groupTurnsMock.mockResolvedValue([])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "first message" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // The POST is still in flight — Send must still be labeled "Send", not
    // swapped for "Stop generating".
    expect(screen.queryByLabelText("Stop generating")).toBeNull()
    expect(screen.getByLabelText("Send")).toBeTruthy()

    await act(async () => {
      resolvePost(turn({ id: 5, content: "first message" }))
      await Promise.resolve()
    })
  })

  it("a second, DIFFERENT message is typeable and sendable immediately after the first send, before it settles", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {})

    let resolveFirstPost: (v: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirstPost = resolve
      }),
    )
    groupTurnsMock.mockResolvedValue([])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "first message" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    // Composer cleared optimistically — the user can type the next message
    // right away, WHILE the first send's reconcile poll is still pending.
    expect(textarea.value).toBe("")

    postGroupTurnMock.mockResolvedValueOnce(turn({ id: 6, content: "second message" }))
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "second message" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(postGroupTurnMock).toHaveBeenCalledWith(101, "second message", expect.objectContaining({ client_message_id: expect.any(String) }))
    expect(postGroupTurnMock).toHaveBeenCalledTimes(2)

    await act(async () => {
      resolveFirstPost(turn({ id: 5, content: "first message" }))
      await Promise.resolve()
    })
  })

  it("every send still routes through postGroupTurn — no client-side gate, no synchronous reply", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {})

    postGroupTurnMock.mockResolvedValueOnce(turn({ id: 5, content: "@Sprntly summarize this" }))
    groupTurnsMock.mockResolvedValueOnce([
      turn({ id: 5, content: "@Sprntly summarize this", author_user_id: "u1", author_name: "Me" }),
    ])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "@Sprntly summarize this" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    expect(postGroupTurnMock).toHaveBeenCalledWith(101, "@Sprntly summarize this", expect.objectContaining({ client_message_id: expect.any(String) }))
    // The POST resolves the HUMAN turn only — no agent-reply payload comes
    // back synchronously on it (the mock above resolves with the human turn,
    // matching the real route's return type); any reply is a SEPARATE turn
    // that would arrive via the realtime broadcast or the reconcile poll,
    // never inline on this call.
    await expect(postGroupTurnMock.mock.results[0].value).resolves.toEqual(
      expect.objectContaining({ content: "@Sprntly summarize this" }),
    )
  })

  it("test_group_never_block_sends_during_pending_reply — an identical RETYPE while the first send is in flight is NOT silently eaten by the shell's clear (Fable #10)", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {})

    // The first send stays in flight (never resolves) so the same-content guard
    // is armed for the retype below.
    postGroupTurnMock.mockReturnValue(new Promise(() => {}))

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    // Cleared optimistically after the first send.
    expect(textarea.value).toBe("")

    // The user retypes the SAME text and hits Send again — the engine's guard
    // rejects the duplicate POST, but the draft must be RESTORED (not eaten):
    // the shell clears unconditionally, so the engine re-seats it on a microtask.
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
      await Promise.resolve()
    })

    // Exactly one POST (the duplicate was guarded) …
    expect(postGroupTurnMock).toHaveBeenCalledTimes(1)
    // … and the retyped text survives — it was not silently swallowed.
    await waitFor(() =>
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("hi team"),
    )
  })

  it("double-submit of the SAME draft is still prevented — a rapid re-send before the POST settles is a no-op", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {})

    let resolvePost: (v: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve
      }),
    )

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    const sendBtn = screen.getByLabelText("Send")
    // Two rapid clicks BEFORE the draft has cleared (simulated by firing
    // the click twice inside the same act — the composer's optimistic
    // `setDraft("")` hasn't landed between them).
    await act(async () => {
      fireEvent.click(sendBtn)
      fireEvent.click(sendBtn)
    })

    expect(postGroupTurnMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolvePost(turn({ id: 5, content: "hi team" }))
      await Promise.resolve()
    })
  })
})

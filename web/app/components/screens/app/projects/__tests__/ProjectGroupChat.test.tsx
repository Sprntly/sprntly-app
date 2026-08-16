// @vitest-environment jsdom
//
// ProjectGroupChat — the multi-author thread. AD-P13 reuse (source
// scan, no bespoke primitives), multi-author bubble rendering, the "stayed
// out" affordance, the agent-turn invoker tag, artifact-chip wiring, the
// post→refetch→clear cycle, and focus-gated polling.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's typing-animation hook reads prefers-reduced-motion on mount;
// jsdom has no matchMedia. Same stub `ChatScreen.composer.dom.test.tsx` uses.
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
let authState: { kind: "authed"; user: { id: string } } | { kind: "anonymous" } = {
  kind: "authed",
  user: { id: "u1" },
}

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
  useAuth: () => authState,
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
  authState = { kind: "authed", user: { id: "u1" } }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectGroupChat — thin host delegates to ChatShell + the T3a engines (AD-P13 reuse, post-fold)", () => {
  it("is a thin host: renders through ChatShell, composes the two engines, and defines no bespoke markdown/chip/skeleton/transport implementation", () => {
    const src = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    // The fold: presentation through the shared shell, data + picker through the
    // T3a engines. No second implementation of any primitive lives here.
    expect(src).toContain('from "../../../shared/chat-shell/ChatShell"')
    expect(src).toContain('from "./useProjectGroupThread"')
    expect(src).toContain('from "./useMentionPicker"')
    // The shared render primitives it still uses are imported, never reimplemented.
    expect(src).toContain('from "../../../shared/AskReplyBody"')
    expect(src).toContain('from "../../../shared/OpenArtifactChips"')
    expect(src).toContain('from "../../../shared/AssistantThinkingSkeleton"')
    // No bespoke primitives, and — post-fold — no transport/dedup/mention logic
    // (all in the engines): the host imports neither react-markdown nor the
    // realtime channel nor the mentions helpers directly.
    expect(src).not.toMatch(/function\s+AskReplyBody/)
    expect(src).not.toMatch(/function\s+OpenArtifactChips/)
    expect(src).not.toContain('from "react-markdown"')
    expect(src).not.toContain('from "./useRealtimeChannel"')
    expect(src).not.toContain("postGroupTurn")
    expect(src).not.toContain("applyTurns")
  })

  it("the composer is extracted to shared/ChatComposer.tsx; the SHELL owns it post-fold (the host no longer imports it directly)", () => {
    const composerSrc = readFileSync(join(__dirname, "../../../../shared/ChatComposer.tsx"), "utf8")
    expect(composerSrc).toContain("export function ChatComposer(")

    const chatScreenSrc = readFileSync(join(__dirname, "../../ChatScreen.tsx"), "utf8")
    expect(chatScreenSrc).toContain('from "../../shared/ChatComposer"')
    expect(chatScreenSrc).not.toMatch(/^function ChatComposer\(/m)

    // Post-fold the group composer is constructed by ChatShell from the
    // descriptor — the thin host does NOT import ChatComposer itself.
    const shellSrc = readFileSync(join(__dirname, "../../../../shared/chat-shell/ChatShell.tsx"), "utf8")
    expect(shellSrc).toContain('from "../ChatComposer"')
    const groupChatSrc = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    expect(groupChatSrc).not.toContain('from "../../../shared/ChatComposer"')
  })
})

describe("ProjectGroupChat — relocated group CSS is tokens only", () => {
  it("GroupChatExtras.module.css resolves every color to a globals.css custom property — no new palette", () => {
    // The pre-fold `ProjectGroupChat.module.css` is deleted; its retained group
    // families (roster/status/state/typing/error/posting-wait) now live here.
    const css = readFileSync(join(__dirname, "../GroupChatExtras.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    expect(found).toEqual([])
  })
})

describe("ProjectGroupChat — thin host renders through ChatShell (AC1/AC5)", () => {
  it("test_group_host_renders_through_chatshell_project_group — mounts the shared shell with the group surface; the 868px bc-thread column carries the rows", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "hi" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "yo" }),
    ])
    const { container } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    // The group turns render inside the shell's shared 868px thread column.
    const thread = await waitFor(() => {
      const el = container.querySelector(".bc-thread")
      expect(el).toBeTruthy()
      return el as HTMLElement
    })
    expect(within(thread).getByTestId("gc-msg-other")).toBeTruthy()
    expect(within(thread).getByTestId("gc-msg-me")).toBeTruthy()
  })

  it("test_group_host_wires_draftApiRef_via_onDraftApiReady — the picker reads the lazily-populated draft API, so typing @ opens the picker (proves the ref handoff)", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByLabelText("Send")
    // If onDraftApiReady never populated the ref, onInputCapture would stay
    // undefined and the picker would be dead — typing @ would do nothing.
    const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(ta, { target: { value: "@", selectionStart: 1 } })
      await Promise.resolve()
    })
    expect(await screen.findByTestId("gc-mention-picker")).toBeTruthy()
  })
})

describe("ProjectGroupChat — multi-author bubbles", () => {
  it("renders other/you/agent turns distinctly, with name+role+time on an other-turn", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", author_job_role: "Design" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", author_job_role: null, content: "agent reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    const other = await screen.findByTestId("gc-msg-other")
    expect(within(other).getByText("Shristi")).toBeTruthy()
    expect(within(other).getByText("Design")).toBeTruthy()

    expect(screen.getByTestId("gc-msg-me")).toBeTruthy()
    const agent = screen.getByTestId("gc-msg-agent")
    // NAMED INTENDED CHANGE (fold): the pre-fold literal `gc-msg--ai` global
    // class became the shell-owned `gcMsgAgent` module class (same agent-lane
    // intent, same `gc-msg-agent` testid). The AGENT badge still renders.
    expect(agent.className).toMatch(/gcMsgAgent/)
    expect(within(agent).getByText("AGENT")).toBeTruthy()
  })

  it("a human-to-human aside with no agent reply shows the QUIET stayed-out marker", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "no mention here" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("gc-msg-me")
    // The alarming "Sprntly stayed out — no reply yet" pill is gone; the interim
    // `showStayedOut` stay-out case now renders the QUIET declined treatment
    // (visually distinct from a failure).
    expect(screen.getByTestId("gc-stayed-out-quiet")).toBeTruthy()
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()
  })

  it("does not show the stayed-out marker right after an agent turn", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly help" }),
      turn({ id: 2, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "sure" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("gc-msg-agent")
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()
  })

  it("an agent turn triggered by an @Sprntly mention shows the invoked-by state badge", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly can you help?" }),
      turn({ id: 2, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "on it" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    // The redesign replaced the invoke-only `gc-invoker` tag with an
    // always-present agent state badge (`gc-state-badge`): an @Sprntly-triggered
    // turn reads "invoked by <first name>"; a mention-less one reads "detected
    // this was for it". Same smart-interjection semantics, one durable hook.
    const badge = await screen.findByTestId("gc-state-badge")
    expect(badge.textContent).toContain("invoked by")
    expect(badge.textContent).toContain("Shristi")
  })

  it("renders OpenArtifactChips on an agent turn and fires the open callback on click", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({
        id: 1,
        role: "assistant",
        author_user_id: null,
        author_name: "Sprntly",
        content: "here's the PRD",
        open_candidates: [
          { type: "prd", id: 9, title: "Instant-quote flow", status: "ready", prd_id: 9, brief_id: null, insight_index: null } as never,
        ],
      }),
    ])
    const onOpenArtifact = vi.fn()
    render(React.createElement(ProjectGroupChat, { projectId: 101, onOpenArtifact }))
    const chip = await screen.findByTestId("open-artifact-chip")
    fireEvent.click(chip)
    expect(onOpenArtifact).toHaveBeenCalledWith(expect.objectContaining({ id: 9, type: "prd" }))
  })
})

describe("ProjectGroupChat — send + refetch", () => {
  it("posting a turn calls projectsApi.postGroupTurn, refetches, and clears the composer", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    postGroupTurnMock.mockResolvedValue(turn({ id: 5, content: "hi team" }))
    groupTurnsMock.mockResolvedValueOnce([turn({ id: 5, content: "hi team", author_user_id: "u1", author_name: "Me" })])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    const sendBtn = screen.getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    await waitFor(() => expect(postGroupTurnMock).toHaveBeenCalledWith(101, "hi team"))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")
    })
  })

  it("test_composer_clears_optimistically_on_send — the draft empties the instant send starts, BEFORE the POST resolves", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

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
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // Cleared IMMEDIATELY — postGroupTurn has not resolved yet.
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")
    expect(postGroupTurnMock).toHaveBeenCalledWith(101, "hi team")

    await act(async () => {
      resolvePost(turn({ id: 5, content: "hi team" }))
      await Promise.resolve()
    })
  })

  it("test_composer_restores_only_if_still_empty_on_fail — a failed send restores the text; a message typed during the wait is NOT clobbered", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    let rejectPost: (e: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectPost = reject
      }),
    )

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")

    await act(async () => {
      rejectPost(new Error("network blip"))
      await Promise.resolve()
    })
    // Box was empty at the moment the failure landed → restored.
    await waitFor(() =>
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("hi team"),
    )
    expect(screen.getByTestId("gc-error")).toBeTruthy()
  })

  it("a message typed during the wait is not clobbered by a failed-send restore", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    let rejectPost: (e: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectPost = reject
      }),
    )

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "original message" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")

    // The user starts typing something new WHILE the first send is in flight.
    await act(async () => {
      fireEvent.change(document.querySelector(".cx-input") as HTMLTextAreaElement, {
        target: { value: "a different message I'm typing now" },
      })
    })

    await act(async () => {
      rejectPost(new Error("network blip"))
      await Promise.resolve()
    })

    // The restore must NOT clobber what the user is now typing.
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe(
      "a different message I'm typing now",
    )
  })

  it("the composer carries the group-chat placeholder, not the individual-chat one", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(ta.placeholder).toBe("Message the team, or @Sprntly to hand it a task…")
    })
  })
})

describe("ProjectGroupChat — optimistic own-message send", () => {
  it("renders the sender's own turn immediately, before the POST resolves", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

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
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // Rendered synchronously — the POST has not resolved yet.
    const optimistic = screen.getByTestId("gc-msg-me")
    expect(optimistic.textContent).toContain("hi team")
    expect(groupTurnsMock).toHaveBeenCalledTimes(1) // no refetch triggered yet

    await act(async () => {
      resolvePost(turn({ id: 5, content: "hi team" }))
      await Promise.resolve()
    })
  })

  it("is not duplicated when the poster's own real turn arrives via the post-send reconcile", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    postGroupTurnMock.mockResolvedValue(turn({ id: 5, content: "hi team" }))
    groupTurnsMock.mockResolvedValueOnce([
      turn({ id: 5, content: "hi team", author_user_id: "u1", author_name: "Me" }),
    ])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // Optimistic placeholder present immediately.
    expect(screen.getAllByTestId("gc-msg-me")).toHaveLength(1)

    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(2))
    // The real turn replaced the placeholder — still exactly one bubble, not two.
    await waitFor(() => {
      const bubbles = screen.getAllByTestId("gc-msg-me")
      expect(bubbles).toHaveLength(1)
      expect(bubbles[0].textContent).toContain("hi team")
    })
  })

  it("rolls back the optimistic turn and restores the draft on a failed send", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    let rejectPost: (e: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectPost = reject
      }),
    )

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // Optimistic placeholder present while the POST is in flight.
    expect(screen.getByTestId("gc-msg-me")).toBeTruthy()

    await act(async () => {
      rejectPost(new Error("network blip"))
      await Promise.resolve()
    })

    // No ghost turn left behind, and the draft is restored.
    await waitFor(() => expect(screen.queryByTestId("gc-msg-me")).toBeNull())
    await waitFor(() =>
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("hi team"),
    )
    expect(screen.getByTestId("gc-error")).toBeTruthy()
  })
})

describe("ProjectGroupChat — stayed-out badge suppressed while posting", () => {
  it("hides the stayed-out badge during posting, and shows it once posting settles with no reply", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(groupTurnsMock).toHaveBeenCalledTimes(1))

    let resolvePost: (v: unknown) => void = () => {}
    postGroupTurnMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve
      }),
    )
    // The refetch after the POST resolves shows only the human turn — no
    // agent reply landed (a genuine "stayed out" outcome).
    groupTurnsMock.mockResolvedValueOnce([
      turn({ id: 5, content: "hi team", author_user_id: "u1", author_name: "Me" }),
    ])

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "hi team" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })

    // While posting: the optimistic turn is the last (user-role) turn, but
    // the marker must stay hidden — a reply may still be generating.
    expect(screen.getByTestId("gc-msg-me")).toBeTruthy()
    expect(screen.queryByTestId("gc-stayed-out-quiet")).toBeNull()
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()

    await act(async () => {
      resolvePost(turn({ id: 5, content: "hi team" }))
      await Promise.resolve()
    })

    // Posting has settled and no agent reply arrived — NOW the QUIET stay-out
    // marker shows (the old alarming pill is gone).
    await waitFor(() => expect(screen.getByTestId("gc-stayed-out-quiet")).toBeTruthy())
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()
  })
})

describe("ProjectGroupChat — focus-gated polling (AD-P4)", () => {
  it("polls on an interval while focused, stops on blur, and clears on unmount", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    groupTurnsMock.mockResolvedValue([])

    const { unmount } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterMount = groupTurnsMock.mock.calls.length
    expect(callsAfterMount).toBeGreaterThan(0)

    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
    })
    expect(groupTurnsMock.mock.calls.length).toBeGreaterThan(callsAfterMount)

    // Blur stops the interval — no further calls even after time passes.
    hasFocusSpy.mockReturnValue(false)
    await act(async () => {
      window.dispatchEvent(new Event("blur"))
    })
    const callsAtBlur = groupTurnsMock.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(20_000)
      await Promise.resolve()
    })
    expect(groupTurnsMock.mock.calls.length).toBe(callsAtBlur)

    // Unmounting while focused again leaves no leaked timer.
    hasFocusSpy.mockReturnValue(true)
    unmount()
    const callsAtUnmount = groupTurnsMock.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(20_000)
    })
    expect(groupTurnsMock.mock.calls.length).toBe(callsAtUnmount)
  })
})

describe("ProjectGroupChat — no save-as-artifact affordance (removed)", () => {
  it("test_group_chat_renders_no_save_artifact_affordance — gc-save-artifact/gc-saved-artifact never render on any turn; agent turns still render AskReplyBody + OpenArtifactChips", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "agent reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const agent = await screen.findByTestId("gc-msg-agent")
    const other = screen.getByTestId("gc-msg-other")
    const me = screen.getByTestId("gc-msg-me")

    for (const row of [agent, other, me]) {
      expect(within(row).queryByTestId("gc-save-artifact")).toBeNull()
      expect(within(row).queryByTestId("gc-saved-artifact")).toBeNull()
    }
    expect(screen.queryByTestId("gc-save-artifact")).toBeNull()
    expect(screen.queryByTestId("gc-saved-artifact")).toBeNull()
    expect(screen.queryByTestId("gc-save-error")).toBeNull()

    // The agent turn's body still renders (AskReplyBody's real markdown
    // output — this ticket removes ONLY the save affordance, not the body).
    expect(agent.textContent).toContain("agent reply")
    expect(saveChatArtifactMock).not.toHaveBeenCalled()
  })
})

// Was `voiceSupported={false}` — the composer's shared default-on wiring now
// offers the mic here like every other composer consumer.
describe("ProjectGroupChat — voice (shared composer default)", () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).webkitSpeechRecognition = class {
      start() {}
      stop() {}
      abort() {}
    }
  })
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition
  })

  it("renders a live mic — no more voiceSupported={false} hard-disable", async () => {
    const src = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    expect(src).not.toContain("voiceSupported={false}")
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => expect(screen.getByLabelText("Dictate your question")).toBeTruthy())
  })
})

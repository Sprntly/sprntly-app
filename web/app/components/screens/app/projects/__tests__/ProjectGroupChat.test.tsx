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

describe("ProjectGroupChat — AD-P13 reuse (source scan)", () => {
  it("imports the shared primitives and defines no bespoke markdown/chip/skeleton implementation", () => {
    const src = readFileSync(
      join(__dirname, "../ProjectGroupChat.tsx"),
      "utf8",
    )
    expect(src).toContain('from "../../../shared/AskReplyBody"')
    expect(src).toContain('from "react-markdown"')
    expect(src).toContain('from "remark-gfm"')
    expect(src).toContain('from "../../../shared/AssistantThinkingSkeleton"')
    expect(src).toContain('from "../../../shared/AssistantWaitState"')
    expect(src).toContain('from "../../../shared/OpenArtifactChips"')
    expect(src).toContain('from "../../../shared/app-icons"')
    expect(src).toContain('from "../../../shared/ChatComposer"')
    // No second implementation of any of these.
    expect(src).not.toMatch(/function\s+AskReplyBody/)
    expect(src).not.toMatch(/function\s+OpenArtifactChips/)
    expect(src).not.toMatch(/function\s+AssistantThinkingSkeleton/)
  })

  it("the composer is extracted to shared/ChatComposer.tsx and BOTH ChatScreen and ProjectGroupChat import it", () => {
    const composerSrc = readFileSync(
      join(__dirname, "../../../../shared/ChatComposer.tsx"),
      "utf8",
    )
    expect(composerSrc).toContain("export function ChatComposer(")

    const chatScreenSrc = readFileSync(
      join(__dirname, "../../ChatScreen.tsx"),
      "utf8",
    )
    expect(chatScreenSrc).toContain('from "../../shared/ChatComposer"')
    expect(chatScreenSrc).not.toMatch(/^function ChatComposer\(/m)

    const groupChatSrc = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    expect(groupChatSrc).toContain('from "../../../shared/ChatComposer"')
  })
})

describe("ProjectGroupChat — component-scoped CSS is tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../ProjectGroupChat.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    expect(found).toEqual([])
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
    expect(agent.className).toContain("gc-msg--ai")
    expect(within(agent).getByText("AGENT")).toBeTruthy()
  })

  it("a human-to-human aside with no agent reply shows the stayed-out marker", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "no mention here" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await screen.findByTestId("gc-msg-me")
    expect(screen.getByTestId("gc-stayed-out")).toBeTruthy()
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

  it("an agent turn triggered by an @Sprntly mention shows the invoker tag", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "@Sprntly can you help?" }),
      turn({ id: 2, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "on it" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const invoker = await screen.findByTestId("gc-invoker")
    expect(invoker.textContent).toContain("Shristi")
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

  it("the composer carries the group-chat placeholder, not the individual-chat one", async () => {
    groupTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(ta.placeholder).toBe("Message the team, or @Sprntly to hand it a task…")
    })
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

describe("ProjectGroupChat — save as artifact (agent turns only, v1)", () => {
  it("test_agent_turn_shows_save_control — an agent turn renders the save control", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", author_job_role: null, content: "agent reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const agent = await screen.findByTestId("gc-msg-agent")
    expect(within(agent).getByTestId("gc-save-artifact")).toBeTruthy()
  })

  it("test_save_control_absent_on_human_turns — gc-msg-me/gc-msg-other have no save control", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const other = await screen.findByTestId("gc-msg-other")
    const me = screen.getByTestId("gc-msg-me")
    expect(within(other).queryByTestId("gc-save-artifact")).toBeNull()
    expect(within(me).queryByTestId("gc-save-artifact")).toBeNull()
    expect(screen.queryByTestId("gc-save-artifact")).toBeNull()
  })

  it("test_click_calls_save_chat_artifact — click calls saveChatArtifact once with { content }, no sourceConversationId", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "agent reply" }),
    ])
    saveChatArtifactMock.mockResolvedValue({ artifact_type: "report", artifact_id: 9, project_id: 101 })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const btn = await screen.findByTestId("gc-save-artifact")
    await act(async () => {
      fireEvent.click(btn)
    })
    await waitFor(() => expect(saveChatArtifactMock).toHaveBeenCalledTimes(1))
    const [calledProjectId, payload] = saveChatArtifactMock.mock.calls[0]
    expect(calledProjectId).toBe(101)
    expect(payload).toEqual({ content: "agent reply" })
    expect(Object.prototype.hasOwnProperty.call(payload, "sourceConversationId")).toBe(false)
  })

  it("test_saving_disables_control — control disabled + Saving… while the promise is pending", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "agent reply" }),
    ])
    let resolveSave: (v: unknown) => void = () => {}
    saveChatArtifactMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve
      }),
    )
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const btn = await screen.findByTestId("gc-save-artifact")
    await act(async () => {
      fireEvent.click(btn)
    })
    const pending = screen.getByTestId("gc-save-artifact") as HTMLButtonElement
    expect(pending.disabled).toBe(true)
    expect(pending.textContent).toBe("Saving…")

    await act(async () => {
      resolveSave({ artifact_type: "report", artifact_id: 9, project_id: 101 })
      await Promise.resolve()
    })
  })

  it("test_success_shows_saved_state — resolved promise shows gc-saved-artifact, no re-save", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "agent reply" }),
    ])
    saveChatArtifactMock.mockResolvedValue({ artifact_type: "report", artifact_id: 9, project_id: 101 })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const btn = await screen.findByTestId("gc-save-artifact")
    await act(async () => {
      fireEvent.click(btn)
    })
    const saved = await screen.findByTestId("gc-saved-artifact")
    expect(saved.textContent).toBe("Saved to artifacts")
    expect(screen.queryByTestId("gc-save-artifact")).toBeNull()
    expect(saveChatArtifactMock).toHaveBeenCalledTimes(1)
  })

  it("test_per_turn_state_isolated — saving turn A leaves turn B's control clickable/unsaved", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "reply A" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "human turn" }),
      turn({ id: 4, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "reply B" }),
    ])
    let resolveSave: (v: unknown) => void = () => {}
    saveChatArtifactMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve
      }),
    )
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const buttons = await screen.findAllByTestId("gc-save-artifact")
    expect(buttons).toHaveLength(2)

    await act(async () => {
      fireEvent.click(buttons[0])
    })

    // Turn A's control is still rendered (in-flight), but disabled/"Saving…";
    // turn B's control is a wholly separate instance, untouched.
    const afterClick = screen.getAllByTestId("gc-save-artifact") as HTMLButtonElement[]
    expect(afterClick).toHaveLength(2)
    expect(afterClick[0].disabled).toBe(true)
    expect(afterClick[0].textContent).toBe("Saving…")
    expect(afterClick[1].disabled).toBe(false)
    expect(afterClick[1].textContent).toBe("Save as artifact")

    await act(async () => {
      resolveSave({ artifact_type: "report", artifact_id: 9, project_id: 101 })
      await Promise.resolve()
    })
  })

  it("test_save_failure_shows_inline_alert — rejected promise shows role=alert error, control returns to clickable, no toast", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", content: "agent reply" }),
    ])
    saveChatArtifactMock.mockRejectedValue(new Error("boom"))
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const btn = await screen.findByTestId("gc-save-artifact")
    await act(async () => {
      fireEvent.click(btn)
    })

    const alert = await screen.findByTestId("gc-save-error")
    expect(alert.getAttribute("role")).toBe("alert")
    expect(alert.textContent).toBe("Couldn't save that as an artifact. Try again.")

    const retryBtn = screen.getByTestId("gc-save-artifact") as HTMLButtonElement
    expect(retryBtn.disabled).toBe(false)
    expect(retryBtn.textContent).toBe("Save as artifact")
  })
})

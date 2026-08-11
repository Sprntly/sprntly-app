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

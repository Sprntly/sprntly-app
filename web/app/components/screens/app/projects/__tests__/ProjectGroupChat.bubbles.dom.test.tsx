// @vitest-environment jsdom
//
// ProjectGroupChat — bubble fidelity + viewport behaviour the redesign tightened:
//   • own turns right-align (msgMe / row-reverse) with the dark teal-green
//     `--nav` bubble fill; others left; the agent turn its own lane.
//   • an @-mention inside the OWN dark bubble reads as GREEN inline text
//     (`--accent-2`), never the light-bubble blue pill (`--info`).
//   • the viewport pins to the newest turn AFTER the async history load resolves
//     — not on the initial empty render (which would leave it stuck at the top).
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
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
let authState: { kind: "authed"; user: { id: string } } | { kind: "anonymous" } = { kind: "authed", user: { id: "u1" } }

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, groupTurns: (...a: unknown[]) => groupTurnsMock(...a) },
  }
})
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => authState }))

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
  authState = { kind: "authed", user: { id: "u1" } }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectGroupChat — bubble alignment lanes", () => {
  it("own turns land in the right-aligned msgMe lane with the bubbleMe fill; others left; agent its own lane", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", author_job_role: "Design", content: "hey" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", author_job_role: null, content: "agent reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    const me = await screen.findByTestId("gc-msg-me")
    // The own row is the right-align (row-reverse) lane, and its bubble carries
    // the dark `--nav` fill class — the redesign's own-message fidelity fix.
    expect(me.className).toMatch(/msgMe/)
    expect(within(me).getByText("my reply").closest("[class*='bubbleMe']")).toBeTruthy()

    const other = screen.getByTestId("gc-msg-other")
    expect(other.className).toMatch(/msgOther/)

    const agent = screen.getByTestId("gc-msg-agent")
    expect(agent.className).toMatch(/msgAi/)
    expect(within(agent).getByText("AGENT")).toBeTruthy()
  })

  it("an @-mention inside the OWN bubble renders as a mention chip (styled green on the dark bubble, not the blue pill)", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u1", author_name: "Me", content: "Ping @David about the quote" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const me = await screen.findByTestId("gc-msg-me")
    const chip = within(me).getByTestId("gc-mention-chip")
    expect(chip.textContent).toContain("@David")
    expect(chip.className).toMatch(/mentionChip/)

    // The colour split is enforced in the component-scoped CSS: the base chip is
    // the blue `--info` pill (light bubbles), but inside the own dark bubble the
    // chip is overridden to the green `--accent-2` inline text — never blue.
    const css = readFileSync(join(__dirname, "../ProjectGroupChat.module.css"), "utf8")
    expect(css).toMatch(/\.bubbleMe\s*\{[^}]*background:\s*var\(--nav\)/)
    const ownChipBlock = css.match(/\.bubbleMe\s+\.mentionChip\s*\{[^}]*\}/)?.[0] ?? ""
    expect(ownChipBlock).toContain("var(--accent-2)")
    expect(ownChipBlock).not.toContain("var(--info)")
    // The base (top-level, newline-anchored — NOT the `.bubbleMe` override)
    // `.mentionChip` rule is the blue `--info` pill.
    expect(css).toMatch(/\n\.mentionChip\s*\{[^}]*color:\s*var\(--info\)/)
  })
})

describe("ProjectGroupChat — viewport pins to newest turn after history load", () => {
  it("scrolls to the bottom once the async history resolves, not on the empty first render", async () => {
    let resolveTurns: (v: GroupTurn[]) => void = () => {}
    groupTurnsMock.mockReturnValue(
      new Promise<GroupTurn[]>((resolve) => {
        resolveTurns = resolve
      }),
    )
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    const scroll = screen.getByTestId("group-chat-scroll")
    // jsdom reports 0 for layout metrics — give the viewport a content height so
    // a bottom-pin is observable.
    Object.defineProperty(scroll, "scrollHeight", { configurable: true, value: 820 })
    // While the history is still loading the auto-scroll effect early-returns:
    // the viewport is NOT yet pinned (this is the bug the effect's `loading`
    // gate fixes — scrolling on the empty render would strand it at the top).
    expect(scroll.scrollTop).toBe(0)

    await act(async () => {
      resolveTurns([turn({ id: 1, content: "first" }), turn({ id: 2, content: "the newest turn" })])
      await Promise.resolve()
    })
    await screen.findByText("the newest turn")

    // After the messages paint, the effect pins the viewport to the bottom.
    expect(scroll.scrollTop).toBe(820)
  })
})

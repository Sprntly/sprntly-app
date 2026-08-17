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

describe("ProjectGroupChat — bubble alignment lanes (folded shell)", () => {
  it("test_group_thread_column_is_bc_thread_868 — group rows render inside the shared 868px bc-thread column, not the old capped bubbles (NAMED INTENDED CHANGE #3/#4/#8)", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", content: "hey" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
    ])
    const { container } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const me = await screen.findByTestId("gc-msg-me")
    // The rows live inside the shell's shared `bc-thread` column (the 868px
    // width David asked for — feedback #3/#4/#8), which the shell renders for
    // every surface. The old component's `.scroll`/`group-chat-scroll` wrapper
    // is gone.
    const thread = container.querySelector(".bc-thread")
    expect(thread).toBeTruthy()
    expect(thread!.contains(me)).toBe(true)
    expect(screen.queryByTestId("group-chat-scroll")).toBeNull()
  })

  it("test_group_multiparty_lanes_light_fill_agent_bubbleless_testids — self=me/other=peer both carry the LIGHT gcBubbleOther fill (self distinguished by alignment, not colour); the agent turn is bubble-less via bc-agent-body; gc-msg-* testids + AGENT badge preserved", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u2", author_name: "Shristi", author_job_role: "Design", content: "hey" }),
      turn({ id: 2, author_user_id: "u1", author_name: "Me", content: "my reply" }),
      turn({ id: 3, role: "assistant", author_user_id: null, author_name: "Sprntly", author_job_role: null, content: "agent reply" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    // Own turn: the row-reverse `gcMsgMe` lane, bubble carries the SAME light
    // `gcBubbleOther` fill as peers (NAMED INTENDED CHANGE: main/private
    // parity — self is distinguished by right-alignment, not a dark
    // colour-coded bubble).
    const me = await screen.findByTestId("gc-msg-me")
    expect(me.className).toMatch(/gcMsgMe/)
    expect(within(me).getByText("my reply").closest("[class*='gcBubbleOther']")).toBeTruthy()
    expect(me.querySelector("[class*='gcBubbleMe']")).toBeNull()

    // Peer turn: the left `gcMsgOther` lane, second fill `gcBubbleOther`.
    const other = screen.getByTestId("gc-msg-other")
    expect(other.className).toMatch(/gcMsgOther/)
    expect(within(other).getByText("hey").closest("[class*='gcBubbleOther']")).toBeTruthy()

    // Agent turn: its own `gcMsgAgent` lane, bubble-LESS (renders through
    // `bc-agent-body`, matching main — no invented green agent bubble).
    const agent = screen.getByTestId("gc-msg-agent")
    expect(agent.className).toMatch(/gcMsgAgent/)
    expect(within(agent).getByText("Product Coworker")).toBeTruthy()
    expect(agent.querySelector(".bc-agent-body")).toBeTruthy()
    expect(agent.querySelector("[class*='gcBubble']")).toBeNull()
  })

  it("test_group_own_message_mention_chip_has_contrast_class — an own-message @-mention chip carries the gc-mention-chip marker inside the LIGHT own bubble; the base blue --info chip styling holds AA contrast on the light fill", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({ id: 1, author_user_id: "u1", author_name: "Me", content: "Ping @David about the quote" }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    const me = await screen.findByTestId("gc-msg-me")
    const chip = within(me).getByTestId("gc-mention-chip")
    expect(chip.textContent).toContain("@David")
    // The stable GLOBAL marker class override selectors can target.
    expect(chip.classList.contains("gc-mention-chip")).toBe(true)
    // The chip sits inside the own bubble — which now wears the SAME light
    // `gcBubbleOther` fill as peers (self is alignment-distinguished, not
    // colour-coded), so the dark-bubble AA override no longer applies to it.
    expect(chip.closest("[class*='gcBubbleOther']")).toBeTruthy()
    expect(chip.closest("[class*='gcBubbleMe']")).toBeNull()

    // On the light fill the chip keeps the base blue `--info` pill styling
    // (mention-picker.module.css) — the same treatment peer bubbles get.
    const pickerCss = readFileSync(join(__dirname, "../mention-picker.module.css"), "utf8")
    expect(pickerCss).toMatch(/\.mentionChip\s*\{[^}]*color:\s*var\(--info\)/)
  })

  it("test_group_agent_turn_renders_open_artifact_chip_and_fires_callback — an agent turn with open_candidates renders OpenArtifactChips; clicking fires onOpenArtifact with the matching candidate (1:1 with the pre-fold live feature)", async () => {
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

describe("ProjectGroupChat — persisted reply citations never render as raw source cards", () => {
  it("test_group_agent_reply_citations_stripped — an agent turn whose persisted reply carries citations renders NO citation cards (raw retrieval-source keys are storage identifiers, not user-facing names); the answer and artifact-list cards still render", async () => {
    groupTurnsMock.mockResolvedValue([
      turn({
        id: 1,
        role: "assistant",
        author_user_id: null,
        author_name: "Sprntly",
        author_job_role: null,
        content: "Here's what the team discussed.",
        reply: {
          answer: "Here's what the team discussed.",
          key_points: ["launch slipped a week"],
          citations: [
            { source: "slack_channels", evidence: "…" },
            { source: "communication/finding", evidence: "…" },
            { source: "communication/incident", evidence: "…" },
          ],
          confidence: 1,
          unanswered: "",
          artifact_list: [
            {
              type: "prd",
              id: 12,
              title: "Instant-quote flow",
              status: "ready",
              created_at: null,
              brief_anchored: false,
              source: {},
            },
          ],
        } as never,
      }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    const agent = await screen.findByTestId("gc-msg-agent")
    // The reply settled (full answer text on screen) — so the assertion below
    // isn't vacuously green off a still-streaming state.
    expect(within(agent).getByText("Here's what the team discussed.")).toBeTruthy()
    // NO citation source cards, and none of the raw storage keys as text.
    expect(document.querySelector(".ai-bar-reply-cite-src")).toBeNull()
    expect(document.querySelector(".ai-bar-reply-cites")).toBeNull()
    for (const raw of ["slack_channels", "communication/finding", "communication/incident"]) {
      expect(screen.queryByText(raw)).toBeNull()
    }
    // The card data riding the same persisted reply is intact.
    const cards = within(agent).getByTestId("artifact-list-cards")
    expect(within(cards).getByText("Instant-quote flow")).toBeTruthy()
  })
})

describe("ProjectGroupChat — viewport pins to newest turn after history load (shell-owned scroll)", () => {
  it("scrolls the shell viewport to the bottom once the async history resolves, not on the empty first render", async () => {
    let resolveTurns: (v: GroupTurn[]) => void = () => {}
    groupTurnsMock.mockReturnValue(
      new Promise<GroupTurn[]>((resolve) => {
        resolveTurns = resolve
      }),
    )
    const { container } = render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    // The scroll region is now the shell's standalone viewport (it owns project
    // scrolling post-fold), not the old `group-chat-scroll` div.
    const scroll = container.querySelector('[class*="standaloneViewport"]') as HTMLElement
    expect(scroll).toBeTruthy()
    // jsdom reports 0 for layout metrics — give the viewport a content height so
    // a bottom-pin is observable.
    Object.defineProperty(scroll, "scrollHeight", { configurable: true, value: 820 })
    // Not yet pinned while the history loads (scrollHeight was 0 at mount).
    expect(scroll.scrollTop).toBe(0)

    await act(async () => {
      resolveTurns([turn({ id: 1, content: "first" }), turn({ id: 2, content: "the newest turn" })])
      await Promise.resolve()
    })
    await screen.findByText("the newest turn")

    // After the messages paint, the shell's pinned-follow effect lands it at the
    // bottom.
    expect(scroll.scrollTop).toBe(820)
  })
})

describe("ProjectGroupChat — styled group nodes carry classes, not bare divs (AC2)", () => {
  it("test_group_styled_nodes_not_bare — the presence roster and (via the engine) error/typing nodes carry their GroupChatExtras classes; the stay-out case renders the QUIET declined node", async () => {
    groupTurnsMock.mockResolvedValue([
      // A SETTLED history turn (created well past the stay-out grace window)
      // — a fresh turn would rightly hold the note back while a reply may
      // still be generating.
      turn({
        id: 1, author_user_id: "u1", author_name: "Me", content: "solo aside",
        created_at: new Date(Date.now() - 60_000).toISOString(),
      }),
    ])
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))
    // A human-only, long-settled last turn with no reply → the QUIET declined
    // node renders (the honest replacement); the OLD alarming `gc-stayed-out`
    // pill and its `.stayedOut*` classes are gone from the DOM.
    const quiet = await screen.findByTestId("gc-stayed-out-quiet")
    expect(quiet.className).toMatch(/run-quiet/)
    expect(screen.queryByTestId("gc-stayed-out")).toBeNull()
    expect(document.querySelector("[class*='stayedOutDot']")).toBeNull()
    // The still-used CSS families remain in GroupChatExtras; the retired
    // trigger-badge rule (`.stateBadge`) was deleted along with the badge
    // node itself.
    const extrasCss = readFileSync(join(__dirname, "../GroupChatExtras.module.css"), "utf8")
    for (const cls of [".roster", ".rosterDot", ".typingIndicator", ".error", ".postingWait"]) {
      expect(extrasCss).toContain(cls)
    }
    expect(extrasCss).not.toContain(".stateBadge")
  })
})

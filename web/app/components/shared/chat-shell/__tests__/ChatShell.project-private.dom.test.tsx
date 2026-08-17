// @vitest-environment jsdom
//
// ChatShell — the `surface !== "main"` (project_private) rendering path. These
// extend the shell's unit coverage without touching T1's byte-identical
// `ChatShell.unit.dom.test.tsx` (which must re-run green unmodified, AC10):
// author-kind + state-flag mapping (named head, main's bubble chrome,
// `timestamps:"fromTurn"`), `pickOptions` in the agent-body footer position,
// `turnFooter` delegation actions, the `leading` insight banner, esc-to-stop +
// the Stop control, and the main-path no-op re-proof (the new branch is
// unreachable on `surface:"main"`).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

import { ChatShell } from "../ChatShell"
import type { ChatSurfaceDescriptor, ShellTurn } from "../types"
import type { ChatTranscriptTurn } from "../../ChatTranscript"
import type { AskResponse } from "../../../../lib/api"

const AGENT = "Sprntly"

function reply(answer: string): AskResponse {
  return { answer, key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
}

/** A complete surface:"project_private" descriptor; overrides merge shallowly. */
function privateDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "project_private",
    projectId: 202,
    testIdPrefix: "ic",
    frame: { mode: "thread", viewportClassName: "od-standalone", ...(over.frame ?? {}) },
    transcript: {
      agentName: AGENT,
      agentBadge: null,
      timestamps: "fromTurn",
      userHead: "named",
      renderUserBody: (t) => <div data-testid="ic-msg-you">{t.content}</div>,
      // NO base `renderAgentBody`: agent turns render through ChatBubble's
      // NATIVE ladder (the new default). Individual tests below that exercise
      // the retained escape-hatch branch pass their OWN `renderAgentBody`
      // override.
      ...(over.transcript ?? {}),
    },
    composer: { busyMode: "block-while-asking", ...(over.composer ?? {}) },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true, ...(over.send ?? {}) },
    ...over,
  }
}

/** A complete surface:"main" descriptor for the no-op re-proof. */
function mainDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "main",
    frame: { mode: "thread", viewportClassName: "od-center-scroll", ...(over.frame ?? {}) },
    transcript: { agentName: AGENT, agentBadge: "Product Coworker", timestamps: "none", ...(over.transcript ?? {}) },
    composer: { busyMode: "block-while-asking", ...(over.composer ?? {}) },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true, ...(over.send ?? {}) },
    ...over,
  }
}

const selfTurn = (id: string, content: string, answer: string): ShellTurn => ({
  id,
  author: { kind: "self", name: "Ada" },
  content,
  reply: reply(answer),
  createdAt: 1_700_000_000_000,
})

afterEach(() => cleanup())

describe("ChatShell project_private — author-kind + state-flag mapping (AC3)", () => {
  it("test_shell_project_private_maps_author_kinds_and_state_flags", () => {
    const turns: ShellTurn[] = [
      selfTurn("s1", "what's the plan?", "here's the plan"),
      { id: "history-9", author: { kind: "agent", name: AGENT }, content: "a delivered brief", createdAt: 1_700_000_100_000 },
    ]
    const { container } = render(<ChatShell descriptor={privateDescriptor()} turns={turns} />)

    // Named user head + main's bubble chrome for the self turn.
    expect(container.querySelector(".bc-user-head")).toBeTruthy()
    expect(container.querySelector(".bc-user-name")?.textContent).toBe("Ada")
    expect(container.querySelector(".bc-user-bubble")).toBeTruthy()
    expect(screen.getByTestId("ic-msg-you").textContent).toContain("what's the plan?")
    // The self turn's reply renders through the native ladder's AskReplyBody
    // (the first `.ai-bar-reply-answer`); the history agent turn is the second.
    expect(container.querySelectorAll(".ai-bar-reply-answer")[0]?.textContent).toContain("here's the plan")

    // timestamps:"fromTurn" — the agent head shows the turn's own clock.
    const expected = new Date(1_700_000_000_000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    expect(container.textContent).toContain(expected)

    // The pure agent-authored history turn carries the wrapper testid.
    expect(screen.getByTestId("ic-history-agent").textContent).toContain("a delivered brief")
  })
})

describe("ChatShell project_private — pickOptions via the native ChatBubble prop (AC4)", () => {
  it("test_shell_project_private_renders_pickOptions_in_agent_footer", () => {
    const onPickOption = vi.fn()
    const turn: ShellTurn = {
      ...selfTurn("s1", "tighten the problem statement", "which PRD?"),
      pickOptions: [
        { id: "501", title: "Onboarding", instruction: "tighten the problem statement" },
        { id: "502", title: "Billing", instruction: "tighten the problem statement" },
      ],
    }
    const { container } = render(
      <ChatShell descriptor={privateDescriptor()} turns={[turn]} onPickOption={onPickOption} />,
    )

    // Rendered via ChatBubble's UNCONDITIONAL native pick prop — native testids
    // (`mutation-pick-option-<id>`), NOT the retired shell-owned `ic-clarify-*`.
    const options = screen.getByTestId("mutation-pick-options")
    expect(options).toBeTruthy()
    // The pick card sits inside the same turn as the agent reply, positioned
    // like the pending-mutation card (a sibling of `.bc-agent-body`).
    const turnEl = container.querySelector('[data-turn-id="s1"]')!
    expect(turnEl.contains(options)).toBe(true)
    expect(container.querySelector(".ai-bar-reply-answer")?.textContent).toContain("which PRD?")

    fireEvent.click(screen.getByTestId("mutation-pick-option-502"))
    expect(onPickOption).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ id: "502", title: "Billing", instruction: "tighten the problem statement" }),
    )
  })
})

describe("ChatShell project_private — reply cards via the native ladder (AC2)", () => {
  it("test_private_reply_cards_via_ladder", () => {
    // A seeded `openCandidates`/`artifactList` on a private agent turn renders
    // OpenArtifactChips / ArtifactListCards through ChatBubble's NATIVE ladder
    // (same as group), with no host `renderAgentBody` — the reply body renders
    // via AskReplyBody in the same block.
    const onOpenCandidate = vi.fn()
    const onOpenArtifactItem = vi.fn()
    const turn: ShellTurn = {
      ...selfTurn("s1", "open the checkout PRD", "I found a couple — pick one."),
      openCandidates: [
        { type: "prd", id: 501, title: "Checkout v1", status: "ready", prd_id: 501, brief_id: null, insight_index: null, brief_anchored: false, week_label: null },
      ] as ShellTurn["openCandidates"],
      artifactList: [
        { type: "prd", id: 777, title: "Dark mode", status: "ready", created_at: "2026-08-15T00:00:00Z", brief_anchored: false, source: {}, open: { prd_id: 777 } },
      ] as ShellTurn["artifactList"],
    }
    const { container } = render(
      <ChatShell
        descriptor={privateDescriptor({
          transcript: {
            agentName: AGENT,
            timestamps: "fromTurn",
            userHead: "named",
            renderUserBody: (t) => <div data-testid="ic-msg-you">{t.content}</div>,
            onOpenCandidate,
            onOpenArtifactItem,
          },
        })}
        turns={[turn]}
      />,
    )
    expect(screen.getByTestId("open-artifact-chips")).toBeTruthy()
    expect(screen.getByTestId("artifact-list-cards")).toBeTruthy()
    expect(container.querySelector(".ai-bar-reply-answer")?.textContent).toContain("I found a couple")
    fireEvent.click(screen.getAllByTestId("open-artifact-chip")[0])
    expect(onOpenCandidate).toHaveBeenCalledTimes(1)
  })
})

describe("ChatShell project_private — turnFooter + leading (AC5)", () => {
  it("test_shell_project_private_turnFooter_renders_delegation_actions", () => {
    const turns: ShellTurn[] = [
      { id: "history-42", author: { kind: "agent", name: AGENT }, content: "You've been handed a task.", createdAt: 1, footerData: { delegation_id: 5 } },
    ]
    render(
      <ChatShell
        descriptor={privateDescriptor({
          transcript: {
            agentName: AGENT,
            timestamps: "fromTurn",
            userHead: "named",
            renderAgentBody: (t) => <div>{t.content}</div>,
            turnFooter: (t) =>
              t.footerData ? <div data-testid="ic-brief-delegation-actions">actions</div> : null,
          },
        })}
        turns={turns}
      />,
    )
    const affordance = screen.getByTestId("ic-brief-delegation-actions")
    const agentTurn = screen.getByTestId("ic-history-agent")
    // The footer reads as contained within the delivered-brief turn.
    expect(agentTurn.contains(affordance)).toBe(true)
  })

  it("test_shell_project_private_leading_renders_insight_banner", () => {
    const { container } = render(
      <ChatShell
        descriptor={privateDescriptor({
          transcript: {
            agentName: AGENT,
            timestamps: "fromTurn",
            userHead: "named",
            leading: <div data-testid="cross-chat-insight">INSIGHT</div>,
            renderUserBody: (t) => <div data-testid="ic-msg-you">{t.content}</div>,
            renderAgentBody: (t) => <div data-testid="ic-msg-agent">{t.reply?.answer}</div>,
          },
        })}
        turns={[selfTurn("s1", "hi", "hello")]}
      />,
    )
    const banner = screen.getByTestId("cross-chat-insight")
    const thread = container.querySelector(".bc-thread")!
    // The banner renders above the turn list, inside the thread column.
    expect(thread.contains(banner)).toBe(true)
    expect(thread.firstChild).toBe(banner)
  })
})

describe("ChatShell project_private — esc-to-stop + Stop control (AC7)", () => {
  it("test_shell_escToStop_and_stop_wired_on_private", () => {
    const onStop = vi.fn()
    // A pending turn makes the composer show the Stop control (busy state).
    const pending: ShellTurn = { id: "s1", author: { kind: "self", name: "Ada" }, content: "stop me", pending: true }
    render(
      <ChatShell
        descriptor={privateDescriptor({
          composer: { busyMode: "block-while-asking", stop: { enabled: true, onStop }, escToStop: true },
        })}
        turns={[pending]}
      />,
    )

    // The Stop control invokes onStop.
    fireEvent.click(screen.getByLabelText("Stop generating"))
    expect(onStop).toHaveBeenCalledTimes(1)

    // Esc invokes onStop too (shell-owned listener on project surfaces).
    fireEvent.keyDown(window, { key: "Escape" })
    expect(onStop).toHaveBeenCalledTimes(2)
  })
})

describe("ChatShell project_private — main no-op re-proof (AC10)", () => {
  it("test_shell_main_path_byte_unchanged_when_private_branch_added", () => {
    const mainTurn: ChatTranscriptTurn = {
      turnId: "t1",
      user: { name: "Ada", initials: "A", query: "hi" },
      agentName: AGENT,
      agentBadge: "Product Coworker",
      isLast: true,
      reply: reply("hello there"),
    }
    // The main path renders identically whether or not project-only stray
    // fields are present — the new surface !== "main" branch is unreachable.
    const without = render(
      <ChatShell descriptor={mainDescriptor()} turns={[mainTurn]} composerNode={<div data-testid="c" />} />,
    )
    const a = without.container.querySelector("main.od-center")!.outerHTML
    cleanup()
    const withStray = render(
      <ChatShell
        descriptor={mainDescriptor({ projectId: 42, testIdPrefix: "ic", transcript: { agentName: AGENT, agentBadge: "Product Coworker", timestamps: "none", multiParty: true } })}
        turns={[mainTurn]}
        composerNode={<div data-testid="c" />}
      />,
    )
    const b = withStray.container.querySelector("main.od-center")!.outerHTML
    expect(b).toBe(a)
    // No project-private artifacts leak onto the main path.
    expect(withStray.container.querySelector('[data-testid="mutation-pick-options"]')).toBeNull()
  })
})

// @vitest-environment jsdom
//
// ChatShell — the structured generation-clarify seam (§A/§B): a project-path
// `ShellTurn.clarify` batch renders the SAME shared `ClarifyQuestionsCard`
// main renders, submit/skip invoke the two new `ChatShellProps` callbacks, and
// the pre-existing `pickOptions` render path stays untouched (a turn never
// carries both). Extends the shell suite WITHOUT touching the byte-identical
// `ChatScreen.shell-golden.dom.test.tsx` or the frozen `ChatShell.unit`/
// `ChatShell.module-graph` gates.
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

const AGENT = "Sprntly"

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
      // NO `renderAgentBody`: the agent reply + the clarify card render through
      // ChatBubble's NATIVE ladder (the new default), which is where the native
      // clarify props live — an escape-hatch body would suppress them.
      ...(over.transcript ?? {}),
    },
    composer: { busyMode: "block-while-asking", ...(over.composer ?? {}) },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true, ...(over.send ?? {}) },
    ...over,
  }
}

const QUESTIONS = [
  { prompt: "Who are the target users?", options: ["Admins", "End users"], skip_default: "all end users" },
  { prompt: "How will you measure success?", options: [] },
]

const clarifyTurn = (over: Partial<ShellTurn> = {}): ShellTurn => ({
  id: "t1",
  author: { kind: "self", name: "Ada" },
  content: "generate a PRD for dark mode",
  reply: { answer: "questions…", key_points: [], citations: [], confidence: 1, unanswered: "" } as ShellTurn["reply"],
  clarify: { questions: QUESTIONS },
  createdAt: 1_700_000_000_000,
  ...over,
})

afterEach(() => cleanup())

describe("ChatShell clarify seam — renders the shared card (AC2)", () => {
  it("test_shell_renders_shared_clarify_card_from_clarify_field", () => {
    render(<ChatShell descriptor={privateDescriptor()} turns={[clarifyTurn()]} />)
    expect(screen.getByTestId("clarify-questions")).toBeTruthy()
    expect(screen.getAllByTestId("clarify-question")).toHaveLength(2)
  })

  it("test_shell_clarify_submit_invokes_on_clarify_submit", () => {
    const onClarifySubmit = vi.fn()
    render(
      <ChatShell
        descriptor={privateDescriptor()}
        turns={[clarifyTurn()]}
        onClarifySubmit={onClarifySubmit}
        onClarifySkip={vi.fn()}
      />,
    )
    fireEvent.click(screen.getAllByTestId("clarify-choice")[0])
    fireEvent.click(screen.getByTestId("clarify-submit"))
    expect(onClarifySubmit).toHaveBeenCalledWith(
      "t1",
      expect.arrayContaining([expect.objectContaining({ prompt: "Who are the target users?", answer: "Admins" })]),
    )
  })

  it("test_shell_clarify_skip_invokes_on_clarify_skip", () => {
    const onClarifySkip = vi.fn()
    render(
      <ChatShell
        descriptor={privateDescriptor()}
        turns={[clarifyTurn()]}
        onClarifySubmit={vi.fn()}
        onClarifySkip={onClarifySkip}
      />,
    )
    fireEvent.click(screen.getByTestId("clarify-skip"))
    expect(onClarifySkip).toHaveBeenCalledWith("t1")
  })

  it("test_shell_clarify_resolved_renders_readonly_record", () => {
    const turn = clarifyTurn({
      clarify: { questions: QUESTIONS, resolved: { answers: [{ prompt: QUESTIONS[0].prompt, answer: "Admins" }], mode: "card" } },
    })
    render(<ChatShell descriptor={privateDescriptor()} turns={[turn]} />)
    expect(screen.getByTestId("clarify-questions-resolved")).toBeTruthy()
    // Read-only: no live inputs/buttons to answer with.
    expect(screen.queryByTestId("clarify-choice")).toBeNull()
    expect(screen.queryByTestId("clarify-submit")).toBeNull()
  })
})

describe("ChatShell clarify seam — pickOptions stays separate (AC4)", () => {
  it("test_shell_pickoptions_still_render_unchanged", () => {
    const onPickOption = vi.fn()
    const turn: ShellTurn = {
      id: "t2",
      author: { kind: "self", name: "Ada" },
      content: "tighten the problem statement",
      reply: { answer: "which PRD?", key_points: [], citations: [], confidence: 1, unanswered: "" } as ShellTurn["reply"],
      pickOptions: [{ id: "501", title: "Onboarding", instruction: "tighten the problem statement" }],
      createdAt: 1_700_000_000_000,
    }
    render(<ChatShell descriptor={privateDescriptor()} turns={[turn]} onPickOption={onPickOption} />)
    expect(screen.getByTestId("mutation-pick-options")).toBeTruthy()
    expect(screen.queryByTestId("clarify-questions")).toBeNull()
    fireEvent.click(screen.getByTestId("mutation-pick-option-501"))
    expect(onPickOption).toHaveBeenCalledWith("t2", expect.objectContaining({ id: "501" }))
  })

  it("test_shell_no_clarify_no_pickoptions_renders_no_card", () => {
    const turn: ShellTurn = {
      id: "t3",
      author: { kind: "self", name: "Ada" },
      content: "what's the status?",
      reply: { answer: "on track", key_points: [], citations: [], confidence: 1, unanswered: "" } as ShellTurn["reply"],
      createdAt: 1_700_000_000_000,
    }
    render(<ChatShell descriptor={privateDescriptor()} turns={[turn]} />)
    expect(screen.queryByTestId("clarify-questions")).toBeNull()
    expect(screen.queryByTestId("mutation-pick-options")).toBeNull()
  })
})

// @vitest-environment jsdom
//
// ChatShell — the pending-mutation seam (the confirmation gate on project
// content edits): a `ShellTurn.pendingMutation` maps onto ChatBubble's native
// confirm card through BOTH mappers (single-party/private and multi-party/
// group), and the card's Confirm/Cancel wire back through the descriptor's
// `reply.onConfirmMutation`/`reply.onCancelMutation` seams with the turn's
// own (turnId, token). Mirrors the clarify-seam suite's structure; touches
// neither the byte-identical golden nor the frozen unit/module-graph gates.
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

const REPLY = { answer: "Proposed: tighten the problem statement.", key_points: [], citations: [], confidence: 1, unanswered: "" }

function privateDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "project_private",
    projectId: 202,
    testIdPrefix: "ic",
    frame: { mode: "thread", viewportClassName: "od-standalone" },
    transcript: {
      agentName: AGENT,
      agentBadge: null,
      timestamps: "fromTurn",
      userHead: "named",
      renderUserBody: (t) => <div data-testid="ic-msg-you">{t.content}</div>,
      renderAgentBody: (t) => (t.reply ? <div data-testid="ic-msg-agent">{t.reply.answer}</div> : null),
    },
    composer: { busyMode: "block-while-asking" },
    reply: { mode: "streamed", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: true },
    ...over,
  }
}

function groupDescriptor(over: Partial<ChatSurfaceDescriptor> = {}): ChatSurfaceDescriptor {
  return {
    surface: "project_group",
    projectId: 101,
    testIdPrefix: "gc",
    frame: { mode: "thread", viewportClassName: "od-standalone" },
    transcript: {
      agentName: AGENT,
      agentBadge: "AGENT",
      multiParty: true,
      timestamps: "fromTurn",
    },
    composer: { busyMode: "never-block" },
    reply: { mode: "backgrounded", ...(over.reply ?? {}) },
    send: { onSubmit: () => {}, pendingSendBubble: false },
    ...over,
  }
}

const MUTATION = { token: "tok-abc", summary: "Proposed: tighten the problem statement.", sectionsChanged: ["Problem"] }

const privateTurn: ShellTurn = {
  id: "t1",
  author: { kind: "self", name: "Ada" },
  content: "tighten the problem statement",
  reply: REPLY as ShellTurn["reply"],
  pendingMutation: MUTATION,
  createdAt: 1_700_000_000_000,
}

const groupAgentTurn: ShellTurn = {
  id: "g9",
  author: { kind: "agent", name: AGENT },
  content: "Proposed: tighten the problem statement.",
  reply: REPLY as ShellTurn["reply"],
  pendingMutation: MUTATION,
  createdAt: 1_700_000_000_000,
}

afterEach(() => cleanup())

describe("ChatShell pending-mutation seam — both mappers + callback wiring", () => {
  it("test_shell_maps_pending_mutation_both_mappers_and_wires_callbacks", () => {
    // ── Single-party (private) mapper ──
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(
      <ChatShell
        descriptor={privateDescriptor({
          reply: { mode: "streamed", onConfirmMutation: onConfirm, onCancelMutation: onCancel },
        })}
        turns={[privateTurn]}
      />,
    )
    // The card renders even though this surface renders its agent body via
    // renderAgentBody (the escape hatch) — the seam is independent of it.
    expect(screen.getByTestId("ic-msg-agent")).toBeTruthy()
    expect(screen.getByTestId("mutation-confirm-card")).toBeTruthy()
    expect(screen.getByTestId("mutation-summary").textContent).toBe(MUTATION.summary)
    fireEvent.click(screen.getByTestId("mutation-confirm"))
    expect(onConfirm).toHaveBeenCalledWith("t1", "tok-abc")
    fireEvent.click(screen.getByTestId("mutation-cancel"))
    expect(onCancel).toHaveBeenCalledWith("t1", "tok-abc")
    cleanup()

    // ── Multi-party (group) mapper ──
    const onConfirmG = vi.fn()
    const onCancelG = vi.fn()
    render(
      <ChatShell
        descriptor={groupDescriptor({
          reply: { mode: "backgrounded", onConfirmMutation: onConfirmG, onCancelMutation: onCancelG },
        })}
        turns={[groupAgentTurn]}
      />,
    )
    expect(screen.getByTestId("gc-msg-agent")).toBeTruthy()
    expect(screen.getByTestId("mutation-confirm-card")).toBeTruthy()
    fireEvent.click(screen.getByTestId("mutation-confirm"))
    expect(onConfirmG).toHaveBeenCalledWith("g9", "tok-abc")
    fireEvent.click(screen.getByTestId("mutation-cancel"))
    expect(onCancelG).toHaveBeenCalledWith("g9", "tok-abc")
  })

  it("test_shell_no_pending_mutation_renders_no_card", () => {
    render(
      <ChatShell
        descriptor={privateDescriptor()}
        turns={[{ ...privateTurn, pendingMutation: undefined }]}
      />,
    )
    expect(screen.queryByTestId("mutation-confirm-card")).toBeNull()
  })
})

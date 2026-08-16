// @vitest-environment jsdom
//
// ChatShell — the folded-in Esc-cancels-answer fix (AC17, the one confirmed
// live bug). The project Esc listener must: (a) YIELD when a
// sibling overlay handled the Escape (`e.defaultPrevented`), (b) GATE on a
// pending turn (no stop when nothing is generating), and (c) still stop a
// generating answer on an unhandled Escape. Kept in its own file so the C2 fix
// commits cleanly apart from the C1 group config.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ChatShell } from "../ChatShell"
import type { ChatSurfaceDescriptor, ShellTurn } from "../types"

const AGENT = "Sprntly"

function privateDescriptor(onStop: () => void): ChatSurfaceDescriptor {
  return {
    surface: "project_private",
    projectId: 202,
    testIdPrefix: "ic",
    frame: { mode: "thread", viewportClassName: "od-standalone" },
    transcript: { agentName: AGENT, timestamps: "fromTurn", userHead: "named", renderUserBody: (t) => <div>{t.content}</div> },
    composer: { busyMode: "block-while-asking", escToStop: true, stop: { enabled: true, onStop } },
    reply: { mode: "streamed" },
    send: { onSubmit: () => {}, pendingSendBubble: true },
  }
}

const pendingTurn: ShellTurn = { id: "s1", author: { kind: "self", name: "Ada" }, content: "generating…", pending: true }
const settledTurn: ShellTurn = { id: "s1", author: { kind: "self", name: "Ada" }, content: "done", reply: { answer: "hi", key_points: [], citations: [], confidence: 1, unanswered: "" } }

afterEach(() => cleanup())

describe("ChatShell — project Esc guard (AC17)", () => {
  it("test_shell_project_esc_yields_to_overlay_and_gates_on_pending", () => {
    // (c) Unhandled Escape WITH a pending turn → stops the generating answer.
    const onStop = vi.fn()
    render(<ChatShell descriptor={privateDescriptor(onStop)} turns={[pendingTurn]} />)
    fireEvent.keyDown(window, { key: "Escape" })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it("does NOT stop when a sibling overlay handled the Escape (defaultPrevented)", () => {
    const onStop = vi.fn()
    render(<ChatShell descriptor={privateDescriptor(onStop)} turns={[pendingTurn]} />)
    // Simulate `useEscapeToClose`: a document-phase listener preventDefaults the
    // Escape BEFORE it reaches the shell's window listener.
    const overlay = (e: KeyboardEvent) => {
      if (e.key === "Escape") e.preventDefault()
    }
    document.addEventListener("keydown", overlay)
    try {
      fireEvent.keyDown(document.body, { key: "Escape", cancelable: true })
      expect(onStop).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener("keydown", overlay)
    }
  })

  it("does NOT stop on Escape when no turn is pending (busy-gate)", () => {
    const onStop = vi.fn()
    render(<ChatShell descriptor={privateDescriptor(onStop)} turns={[settledTurn]} />)
    fireEvent.keyDown(window, { key: "Escape" })
    expect(onStop).not.toHaveBeenCalled()
  })
})

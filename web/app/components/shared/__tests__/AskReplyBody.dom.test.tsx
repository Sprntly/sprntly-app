// @vitest-environment jsdom
//
// AskReplyBody renders only the answer body (plus citation cards where allowed).
// The key_points recap list and the "Gap: …" unanswered note still arrive on
// AskResponse but are deliberately NOT rendered — they read as grey boilerplate
// under every answer.
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime needs a global React before the component modules evaluate,
// and AskReplyBody's simulated-stream hook reads window.matchMedia (absent in jsdom).
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

import { AskReplyBody } from "../AskReplyBody"

afterEach(cleanup)

const REPLY = {
  answer: "Invite-flow friction is the top pain point this week.",
  key_points: ["23% of new users abandon at the invite screen", "$88k ARR at risk"],
  citations: [{ source: "support_themes_weekly", evidence: "17 tickets tagged invite flow" }],
  confidence: 0.9,
  unanswered: "No verbatim quotes for dashboard slowness.",
}

describe("AskReplyBody answer chrome", () => {
  it("renders the answer without the key_points recap or the Gap note", () => {
    const { container } = render(<AskReplyBody reply={REPLY} />)
    expect(container.textContent).toContain("Invite-flow friction")
    expect(container.querySelector(".ai-bar-reply-kp")).toBeNull()
    expect(container.querySelector(".ai-bar-reply-gap")).toBeNull()
    expect(container.textContent).not.toContain("Gap:")
    expect(container.textContent).not.toContain("23% of new users abandon")
  })

  it("still renders citation cards unless omitCitations", () => {
    const { container } = render(<AskReplyBody reply={REPLY} />)
    expect(container.querySelector(".ai-bar-reply-cite")).not.toBeNull()
    cleanup()
    const { container: omitted } = render(<AskReplyBody reply={REPLY} omitCitations />)
    expect(omitted.querySelector(".ai-bar-reply-cite")).toBeNull()
  })
})

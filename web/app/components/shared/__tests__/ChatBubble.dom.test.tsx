// @vitest-environment jsdom
//
// ChatBubble owns the turn-render ladder: every in-flight signal (generating,
// wait-skill, resumed, animated) is a prop, never a screen's own closure —
// and a group turn's speaker/role attribution must survive on the rendered
// DOM, not just in memory.
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

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

import { ChatBubble } from "../ChatBubble"

afterEach(cleanup)

const REPLY = {
  answer: "Here is the answer.",
  key_points: [], citations: [], confidence: 0.9, unanswered: "",
}

describe("ChatBubble in-flight signals as props", () => {
  it("renders the busy/generating wait state only when isGenerating is true", () => {
    const generating = render(
      <ChatBubble
        turnId="t1"
        agentName="Sprntly"
        isLast
        isGenerating
        waitStartedAt={Date.now() - 1000}
      />,
    )
    expect(generating.container.querySelector(".cw-status")).not.toBeNull()
    cleanup()

    const settled = render(
      <ChatBubble turnId="t2" agentName="Sprntly" isLast={false} isGenerating={false} reply={REPLY} />,
    )
    expect(settled.container.querySelector(".cw-status")).toBeNull()
    expect(settled.container.textContent).toContain("Here is the answer.")
  })

  it("renders the wait-skill label and the resumed note from props", () => {
    const { container } = render(
      <ChatBubble
        turnId="t3"
        agentName="Sprntly"
        isLast
        isGenerating
        waitStartedAt={Date.now() - 1000}
        waitSkill={{ label: "Competitive intelligence report", id: "competitive-intelligence-review" }}
        waitResumed
      />,
    )
    expect(container.textContent).toContain("Competitive intelligence report")
    expect(container.textContent).toContain("This answer was already running before you reloaded.")
  })

  it("drives the reply's animate-in purely from the isAnimated prop", () => {
    const animated = render(
      <ChatBubble turnId="t4" agentName="Sprntly" reply={REPLY} isAnimated />,
    )
    expect(animated.container.querySelector(".ai-bar-reply-answer")).not.toBeNull()
    cleanup()
    // Same reply, isAnimated withheld — still renders (animation is cosmetic,
    // never gates whether the content shows).
    const notAnimated = render(
      <ChatBubble turnId="t5" agentName="Sprntly" reply={REPLY} isAnimated={false} />,
    )
    expect(notAnimated.container.textContent).toContain("Here is the answer.")
  })

  it("imports no screen or project-shell module — no closure over caller state", () => {
    const here = path.dirname(fileURLToPath(import.meta.url))
    const src = readFileSync(path.join(here, "..", "ChatBubble.tsx"), "utf8")
    expect(src).not.toMatch(/from ["'][^"']*\/screens\//)
    expect(src).not.toMatch(/from ["'][^"']*\/projects\//)
  })

  it("attributes a group turn's speaker and role on the rendered head", () => {
    const { container } = render(
      <ChatBubble
        turnId="t6"
        agentName="Sprntly"
        showAgent={false}
        user={{ name: "Reader", query: "We should ship this Friday." }}
        speaker="Amara Okafor"
        role="Product Manager"
      />,
    )
    const head = container.querySelector(".bc-user-name")
    expect(head?.textContent).toBe("Amara Okafor (Product Manager)")
  })

  it("never flattens a group turn to a single identity when role is absent", () => {
    const { container } = render(
      <ChatBubble
        turnId="t7"
        agentName="Sprntly"
        showAgent={false}
        user={{ name: "Reader", query: "Noted." }}
        speaker="Devon Blake"
      />,
    )
    expect(container.querySelector(".bc-user-name")?.textContent).toBe("Devon Blake")
  })
})

describe("ChatBubble humanAlign — third-party turns in a multi-party thread", () => {
  it("a teammate's turn (humanAlign=start) renders left/avatar-flanked, never the right-aligned lane", () => {
    const { container } = render(
      <ChatBubble
        turnId="t8"
        agentName="Sprntly"
        showAgent={false}
        humanAlign="start"
        speaker="Fortune Adeyemi"
        role="Design"
        user={{ initials: "FA", bodyNode: "Ship it Friday." }}
      />,
    )
    // The right-aligned, 1:1-surface lane (bc-user-head/bc-user-bubble,
    // globals.css align-self: flex-end) must be absent entirely — a
    // teammate's turn is never rendered through it.
    expect(container.querySelector(".bc-user-bubble")).toBeNull()
    expect(container.querySelector(".bc-user-head")).toBeNull()
    // The dedicated left/avatar-flanked row is present instead, and still
    // carries the name (Invariant 4 attribution survives the new layout).
    const row = container.querySelector('[class*="otherRow"]')
    expect(row).not.toBeNull()
    expect(row?.querySelector(".bc-avatar")?.textContent).toBe("FA")
    expect(row?.textContent).toContain("Fortune Adeyemi")
    expect(row?.textContent).toContain("Ship it Friday.")
  })

  it("the reader's own turn (humanAlign unset) still renders through the right-aligned lane", () => {
    const { container } = render(
      <ChatBubble
        turnId="t9"
        agentName="Sprntly"
        showAgent={false}
        speaker="You"
        user={{ initials: "ME", bodyNode: "On it." }}
      />,
    )
    expect(container.querySelector(".bc-user-bubble")).not.toBeNull()
    expect(container.querySelector('[class*="otherRow"]')).toBeNull()
    expect(container.querySelector(".bc-user-bubble")?.textContent).toBe("On it.")
  })
})

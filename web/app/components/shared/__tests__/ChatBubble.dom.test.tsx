// @vitest-environment jsdom
//
// ChatBubble owns the turn-render ladder: every in-flight signal (generating,
// wait-skill, resumed, animated) is a prop, never a screen's own closure —
// and a group turn's speaker/role attribution must survive on the rendered
// DOM, not just in memory.
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { cleanup, fireEvent, render } from "@testing-library/react"
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

describe("ChatBubble pendingMutation — the native confirm card", () => {
  it("test_pending_mutation_renders_card_with_testids", () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const { container, getByTestId } = render(
      <ChatBubble
        turnId="m1"
        agentName="Sprntly"
        reply={REPLY}
        pendingMutation={{ summary: "Tighten the problem statement.", sectionsChanged: ["Problem"] }}
        onConfirmMutation={onConfirm}
        onCancelMutation={onCancel}
      />,
    )
    expect(getByTestId("mutation-confirm-card")).toBeTruthy()
    expect(getByTestId("mutation-summary").textContent).toBe("Tighten the problem statement.")
    expect(container.textContent).toContain("Problem")
    fireEvent.click(getByTestId("mutation-confirm"))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.click(getByTestId("mutation-cancel"))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("test_pending_mutation_renders_independent_of_agentBodyNode", () => {
    // A turn whose body arrives via the escape hatch (the project surfaces'
    // renderAgentBody path) must STILL show the card — it is not gated behind
    // the built-in ladder branch.
    const { getByTestId } = render(
      <ChatBubble
        turnId="m2"
        agentName="Sprntly"
        agentBodyNode={<div data-testid="host-body">host-rendered body</div>}
        pendingMutation={{ summary: "Proposed change." }}
      />,
    )
    expect(getByTestId("host-body")).toBeTruthy()
    expect(getByTestId("mutation-confirm-card")).toBeTruthy()
    expect(getByTestId("mutation-summary").textContent).toBe("Proposed change.")
  })

  it("test_no_pending_mutation_dom_unchanged", () => {
    // Unset and explicit-null both render byte-identical DOM to a bubble that
    // never heard of the prop — the main chat passes none.
    const withoutProp = render(<ChatBubble turnId="m3" agentName="Sprntly" reply={REPLY} />)
    const withoutHtml = withoutProp.container.innerHTML
    cleanup()
    const withNull = render(
      <ChatBubble turnId="m3" agentName="Sprntly" reply={REPLY} pendingMutation={null} />,
    )
    expect(withNull.container.innerHTML).toBe(withoutHtml)
    expect(withNull.container.querySelector('[data-testid="mutation-confirm-card"]')).toBeNull()
  })
})

describe("ChatBubble pickOptions — the native edit-target pick card", () => {
  it("test_native_pickOptions_renders_and_fires", () => {
    const onPickOption = vi.fn()
    const { getByTestId } = render(
      <ChatBubble
        turnId="p1"
        agentName="Sprntly"
        reply={REPLY}
        pickOptions={[
          { id: "501", title: "Onboarding", instruction: "tighten it" },
          { id: "502", title: "Billing", instruction: "tighten it" },
        ]}
        onPickOption={onPickOption}
      />,
    )
    expect(getByTestId("mutation-pick-options")).toBeTruthy()
    expect(getByTestId("mutation-pick-option-501").textContent).toBe("Onboarding")
    fireEvent.click(getByTestId("mutation-pick-option-502"))
    expect(onPickOption).toHaveBeenCalledTimes(1)
    expect(onPickOption).toHaveBeenCalledWith(
      expect.objectContaining({ id: "502", title: "Billing", instruction: "tighten it" }),
    )
  })

  it("test_no_pickOptions_dom_unchanged", () => {
    // Unset, null, and empty-array all render byte-identical DOM to a bubble
    // that never heard of the prop — the main chat passes none, so its golden
    // DOM stays byte-identical (AC1).
    const withoutProp = render(<ChatBubble turnId="p2" agentName="Sprntly" reply={REPLY} />)
    const withoutHtml = withoutProp.container.innerHTML
    cleanup()
    const withNull = render(
      <ChatBubble turnId="p2" agentName="Sprntly" reply={REPLY} pickOptions={null} />,
    )
    expect(withNull.container.innerHTML).toBe(withoutHtml)
    cleanup()
    const withEmpty = render(
      <ChatBubble turnId="p2" agentName="Sprntly" reply={REPLY} pickOptions={[]} />,
    )
    expect(withEmpty.container.innerHTML).toBe(withoutHtml)
    expect(withEmpty.container.querySelector('[data-testid="mutation-pick-options"]')).toBeNull()
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
    // The right-aligned 1:1 HEAD (`bc-user-head`, globals.css
    // align-self: flex-end) must be absent — a teammate's turn is never
    // rendered through the reader's own head layout.
    expect(container.querySelector(".bc-user-head")).toBeNull()
    // The bubble FILL is now single-sourced from the shared `bc-user-bubble`
    // skin (+ the `.otherBubble` left-lane geometry reset), so there is ONE
    // bubble system across surfaces — the peer bubble carries `bc-user-bubble`
    // but sits inside the left `otherRow` attribution layout, not the
    // right-aligned own-turn lane.
    const bubble = container.querySelector(".bc-user-bubble")
    expect(bubble).not.toBeNull()
    expect(bubble?.className).toMatch(/otherBubble/)
    // The dedicated left/avatar-flanked row is present, and still carries the
    // name (Invariant 4 attribution survives the single-sourced fill).
    const row = container.querySelector('[class*="otherRow"]')
    expect(row).not.toBeNull()
    expect(row?.contains(bubble)).toBe(true)
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
